//! The Rust and Python journals share these on-disk removal phases.
use super::{audio_jobs, task_journal};
use crate::error::{AppError, AppResult};
use serde_json::Value;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

const PENDING: &str = ".removal-pending";
const COMMITTED: &str = ".removal-committed";
const FLAT_SUFFIX: &str = ".removing";

fn invalid(reason: &str) -> AppError {
    AppError::Worker(format!("invalid job removal transaction: {reason}; files preserved"))
}

pub(super) fn is_residue(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return path.extension().is_some_and(|extension| extension == "removing");
    };
    if cfg!(windows) {
        let name = name.to_ascii_lowercase();
        return matches!(name.as_str(), PENDING | COMMITTED) || name.ends_with(FLAT_SUFFIX);
    }
    matches!(name, PENDING | COMMITTED) || name.ends_with(FLAT_SUFFIX)
}

fn require_plain(path: &Path, directory: bool) -> AppResult<()> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(invalid("reparse point in transaction"));
        }
    }
    if !(if directory { metadata.is_dir() } else { metadata.is_file() }) {
        return Err(invalid("unexpected transaction file type"));
    }
    Ok(())
}

fn require_absent(path: &Path) -> AppResult<()> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
        Ok(_) => Err(invalid("transaction destination already exists")),
    }
}

fn check_scope(root: &Path) -> AppResult<()> {
    for path in [root.join("agent-jobs"), root.join("agent-jobs/.owners"), root.join("agent-jobs/.owners/reaping"), root.join("agent-jobs/.writes")] {
        super::result_jobs::plain_path(&path).map_err(invalid)?;
    }
    Ok(())
}

fn check_lock(path: &Path) -> AppResult<()> {
    match require_plain(path, false) {
        Err(AppError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

/// Callers hold the catalog and all affected owner/write leases. The staged
/// directory is atomically renamed only after every job has moved into it.
pub(super) fn remove_unlocked(root: &Path, ids: &BTreeSet<String>) -> AppResult<()> {
    if ids.is_empty() { return Ok(()); }
    check_scope(root)?;
    let jobs = root.join("agent-jobs");
    let pending = jobs.join(PENDING);
    let committed = jobs.join(COMMITTED);
    require_absent(&committed)?;
    let originals = ids.iter().map(|id| {
        let path = task_journal::job_path(root, id)?;
        require_plain(&path, false)?;
        Ok(path)
    }).collect::<AppResult<Vec<_>>>()?;

    fs::create_dir(&pending)?;
    let mut moved = Vec::new();
    let commit = (|| -> AppResult<()> {
        for original in &originals {
            let staged = pending.join(original.file_name().unwrap());
            fs::rename(original, &staged)?;
            moved.push((original, staged));
        }
        fs::rename(&pending, &committed)?;
        Ok(())
    })();
    if let Err(error) = commit {
        // If rollback is interrupted too, the next catalog read resumes it.
        for (original, staged) in moved.into_iter().rev() {
            require_absent(original)?;
            fs::rename(staged, original)?;
        }
        fs::remove_dir(pending)?;
        return Err(error);
    }

    let cleanup = originals.iter()
        .try_for_each(|path| fs::remove_file(committed.join(path.file_name().unwrap())))
        .and_then(|()| fs::remove_dir(committed));
    if let Err(error) = cleanup {
        eprintln!("[journal] committed job removal cleanup deferred: {error}");
    }
    Ok(())
}

fn rust_nonce(nonce: &str) -> bool {
    nonce.split_once('-').is_some_and(|(pid, bits)| {
        pid.bytes().all(|byte| byte.is_ascii_digit()) && pid.parse::<u32>().is_ok()
            && bits.bytes().all(|byte| byte.is_ascii_digit()) && bits.parse::<u64>().is_ok()
    })
}

struct Staged {
    id: String,
    path: PathBuf,
    flat_nonce: Option<String>,
}

fn staged_path(path: PathBuf, flat: bool) -> AppResult<Staged> {
    let name = path.file_name().and_then(|name| name.to_str()).ok_or_else(|| invalid("invalid staged filename"))?;
    let (id, nonce) = if flat {
        let (id, nonce) = name.strip_prefix('.').and_then(|name| name.strip_suffix(FLAT_SUFFIX))
            .and_then(|name| name.rsplit_once('.')).ok_or_else(|| invalid("unrecognized .removing filename"))?;
        let python_nonce = nonce.len() == 32 && nonce.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'));
        if !rust_nonce(nonce) && !python_nonce {
            return Err(invalid("unrecognized .removing nonce"));
        }
        (id, Some(nonce.to_string()))
    } else {
        (name.strip_suffix(".json").ok_or_else(|| invalid("unexpected file in transaction directory"))?, None)
    };
    if !task_journal::valid_id(id) { return Err(invalid("invalid staged job ID")); }
    require_plain(&path, false)?;
    Ok(Staged { id: id.to_string(), path, flat_nonce: nonce })
}

/// Flat sidecars from the earlier writer lack a commit point. Restore the
/// recoverable rows. A staged-only audio group can already be missing records
/// because the old writer unlinked rows after staging the entire group.
fn incomplete_flat_groups<'a>(
    live: &[Value], staged: &'a [(Staged, Value)],
) -> AppResult<HashSet<&'a str>> {
    let all: HashMap<&str, &Value> = live.iter().chain(staged.iter().map(|(_, row)| row))
        .map(|row| (row["jobId"].as_str().unwrap(), row)).collect();
    let mut groups = HashMap::<&str, Vec<(&Staged, &Value)>>::new();
    for (file, row) in staged {
        if let Some(primary) = row["audio"]["primaryJobId"].as_str() {
            groups.entry(primary).or_default().push((file, row));
        }
    }
    let mut discard = HashSet::new();
    for (primary, group) in groups {
        let members = all.get(primary).map(|row| {
            row["audio"]["memberJobIds"].as_array().ok_or_else(|| invalid("invalid flat audio primary"))
        }).transpose()?;
        if members.is_some_and(|members| members.iter().all(|id| all.contains_key(id.as_str().unwrap()))) {
            continue; // The full catalog validation will check this group.
        }

        let (first, row) = group[0];
        let group_id = &row["audio"]["groupId"];
        let nonce = first.flat_nonce.as_deref().filter(|nonce| rust_nonce(nonce))
            .ok_or_else(|| invalid("partial audio group has no Rust transaction nonce"))?;
        // The previous Rust writer unlinked staged rows in sorted job-ID order.
        // A missing ID after any surviving row in that transaction cannot be
        // explained by interrupted cleanup.
        let first_survivor = staged.iter()
            .filter(|(file, _)| file.flat_nonce.as_deref() == Some(nonce))
            .map(|(file, _)| file.id.as_str())
            .min()
            .unwrap();
        let missing_is_prefix = match members {
            Some(members) => members.iter().map(|id| id.as_str().unwrap())
                .filter(|id| !all.contains_key(id))
                .all(|id| id < first_survivor),
            None => primary < first_survivor,
        };
        if !missing_is_prefix { return Err(invalid("flat removal does not match the original cleanup order")); }

        let ids: HashSet<&str> = group.iter().map(|(file, _)| file.id.as_str()).collect();
        let mut indices = HashSet::new();
        if all.iter().any(|(id, row)| {
            !ids.contains(id) && (row["audio"]["primaryJobId"] == primary || row["audio"]["groupId"] == *group_id)
        })
            || group.iter().any(|(file, member)| {
                file.flat_nonce.as_deref() != Some(nonce) || member["audio"]["groupId"] != *group_id
                    || member["command"] != row["command"] || !indices.insert(member["audio"]["index"].as_u64().unwrap())
                    || members.is_some_and(|members| {
                        let index = usize::try_from(member["audio"]["index"].as_u64().unwrap()).ok();
                        index.and_then(|index| members.get(index)) != Some(&member["jobId"])
                    })
            })
            || members.is_some_and(|members| members.iter().any(|id| {
                let id = id.as_str().unwrap();
                all.contains_key(id) && !ids.contains(id)
            }))
        {
            return Err(invalid("ambiguous flat audio group"));
        }
        discard.extend(ids);
    }
    Ok(discard)
}

pub(super) fn recover_unlocked(root: &Path, members: &[task_journal::CatalogMember], residues: &[PathBuf]) -> AppResult<()> {
    check_scope(root)?;
    let jobs = root.join("agent-jobs");
    check_lock(&jobs.join(".catalog.lock"))?;
    let directory = match residues {
        [path] if matches!(path.file_name().and_then(|name| name.to_str()), Some(PENDING | COMMITTED)) => Some(path),
        paths if paths.iter().all(|path| path.extension().is_some_and(|ext| ext == "removing")) => None,
        _ => return Err(invalid("conflicting transaction phases")),
    };
    let committed = directory.is_some_and(|path| path.ends_with(COMMITTED));
    let mut paths = if let Some(directory) = directory {
        require_plain(directory, true)?;
        fs::read_dir(directory)?.map(|entry| staged_path(entry?.path(), false)).collect::<AppResult<Vec<_>>>()?
    } else {
        residues.iter().map(|path| staged_path(path.clone(), true)).collect::<AppResult<Vec<_>>>()?
    };
    paths.sort_unstable_by(|a, b| a.id.cmp(&b.id));
    let mut distinct = HashSet::new();
    for file in &paths {
        let key = if cfg!(windows) { file.id.to_ascii_lowercase() } else { file.id.clone() };
        if !distinct.insert(key) { return Err(invalid("duplicate staged job ID")); }
        require_absent(&task_journal::job_path(root, &file.id)?)?;
        check_lock(&jobs.join(".owners").join(format!("{}.lock", file.id)))?;
        check_lock(&jobs.join(".writes").join(format!("{}.lock", file.id)))?;
    }
    let live = members.iter().map(|(id, path, _)| {
        require_plain(path, false)?;
        check_lock(&jobs.join(".writes").join(format!("{id}.lock")))?;
        let _write = task_journal::write_lock(root, id)?;
        let row = task_journal::read_job(path)?.ok_or_else(|| invalid("missing live job"))?;
        if row["jobId"] != *id { return Err(invalid("live job identity")); }
        Ok(row)
    }).collect::<AppResult<Vec<_>>>()?;
    let _owners = paths.iter().map(|file| task_journal::owner_lock(root, &file.id)).collect::<AppResult<Vec<_>>>()?;
    let _writes = paths.iter().map(|file| task_journal::write_lock(root, &file.id)).collect::<AppResult<Vec<_>>>()?;
    let staged = paths.into_iter().map(|file| {
        let row = task_journal::read_job(&file.path)?.ok_or_else(|| invalid("missing staged job"))?;
        if row["jobId"] != file.id || !task_journal::terminal(&row) {
            return Err(invalid("staged job identity or status"));
        }
        Ok((file, row))
    }).collect::<AppResult<Vec<_>>>()?;

    let discard = if directory.is_none() { incomplete_flat_groups(&live, &staged)? } else { HashSet::new() };
    let mut result = live;
    if !committed {
        result.extend(staged.iter().filter(|(file, _)| !discard.contains(file.id.as_str())).map(|(_, row)| row.clone()));
    }
    audio_jobs::validate_library(&result)?;

    for (file, _) in &staged {
        if committed || discard.contains(file.id.as_str()) {
            fs::remove_file(&file.path)?;
        } else {
            fs::rename(&file.path, task_journal::job_path(root, &file.id)?)?;
        }
    }
    if let Some(directory) = directory { fs::remove_dir(directory)?; }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fixture {
        root: tempfile::TempDir,
        jobs: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let root = tempfile::tempdir().unwrap();
            let jobs = root.path().join("agent-jobs");
            fs::create_dir(&jobs).unwrap();
            let contract: Value = serde_json::from_str(include_str!("../../../tests/fixtures/jobs/current-contract.json")).unwrap();
            for record in contract["records"].as_array().unwrap() {
                let mut record = record.clone();
                record["status"] = json!("completed");
                record["finishedAt"] = json!(1_700_000_010.0);
                let id = record["jobId"].as_str().unwrap();
                fs::write(jobs.join(format!("{id}.json")), serde_json::to_vec_pretty(&record).unwrap()).unwrap();
            }
            Self { root, jobs }
        }

        fn stage(&self, directory: &str, ids: &[&str]) -> PathBuf {
            let directory = self.jobs.join(directory);
            fs::create_dir(&directory).unwrap();
            for id in ids {
                let name = format!("{id}.json");
                fs::rename(self.jobs.join(&name), directory.join(&name)).unwrap();
            }
            directory
        }

        fn list(&self) -> AppResult<Vec<Value>> {
            task_journal::list(self.root.path())
        }

        fn flat(&self, id: &str, nonce: &str) -> PathBuf {
            let sidecar = self.jobs.join(format!(".{id}.{nonce}.removing"));
            fs::rename(self.jobs.join(format!("{id}.json")), &sidecar).unwrap();
            sidecar
        }

        fn run_python(&self, script: &str) -> std::process::Output {
            let source = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("python");
            crate::process_command::isolated_test_python(self.root.path())
                .args(["-I", "-B", "-c", script])
                .arg(source)
                .arg(&self.jobs)
                .output()
                .unwrap()
        }
    }

    const PRIMARY: &str = "audio-primary";
    const CHILD: &str = "audio-child";

    #[test]
    fn interrupted_staging_restores_exact_bytes_and_incremental_membership() {
        let fixture = Fixture::new();
        let before = fixture.list().unwrap();
        let known = before.iter().map(|row| (row["jobId"].as_str().unwrap().to_string(), row["updatedAt"].as_f64().unwrap())).collect();
        let original = fixture.jobs.join(format!("{PRIMARY}.json"));
        let bytes = fs::read(&original).unwrap();
        let pending = fixture.stage(PENDING, &[PRIMARY]);

        let delta = task_journal::read_delta(fixture.root.path(), Some(&known)).unwrap();
        assert!(delta.jobs.is_empty());
        assert!(delta.removed.is_empty());
        assert_eq!(fs::read(original).unwrap(), bytes);
        assert!(!pending.exists());
        assert_eq!(fixture.list().unwrap().len(), before.len());
    }

    #[test]
    fn interrupted_committed_cleanup_reports_the_whole_group_removed() {
        let fixture = Fixture::new();
        let before = fixture.list().unwrap();
        let known = before.iter().map(|row| (row["jobId"].as_str().unwrap().to_string(), row["updatedAt"].as_f64().unwrap())).collect();
        let pending = fixture.stage(PENDING, &[PRIMARY, CHILD]);
        let committed = fixture.jobs.join(COMMITTED);
        fs::rename(pending, &committed).unwrap();
        fs::remove_file(committed.join(format!("{PRIMARY}.json"))).unwrap();

        let delta = task_journal::read_delta(fixture.root.path(), Some(&known)).unwrap();
        assert!(delta.jobs.is_empty());
        assert_eq!(delta.removed.into_iter().collect::<BTreeSet<_>>(), BTreeSet::from([PRIMARY.to_string(), CHILD.to_string()]));
        assert!(!committed.exists());
        assert_eq!(fixture.list().unwrap().len(), before.len() - 2);
    }

    #[test]
    fn flat_sidecars_restore_complete_rows_from_both_writers() {
        let fixture = Fixture::new();
        let before = fixture.list().unwrap();
        let generic = before.iter().find(|row| row["source"] == "agent").unwrap()["jobId"].as_str().unwrap();
        let rust = fixture.flat(PRIMARY, "431-4745269663279448064");
        let python = fixture.flat(generic, "0123456789abcdef0123456789abcdef");
        let expected = fs::read(&rust).unwrap();

        assert_eq!(fixture.list().unwrap().len(), before.len());
        assert_eq!(fs::read(fixture.jobs.join(format!("{PRIMARY}.json"))).unwrap(), expected);
        assert!(!rust.exists());
        assert!(!python.exists());
    }

    #[test]
    fn flat_cleanup_finishes_only_the_proven_incomplete_audio_group() {
        let fixture = Fixture::new();
        let before = fixture.list().unwrap();
        let primary = fixture.flat(PRIMARY, "431-4745269663279448064");
        let child = fixture.flat(CHILD, "431-4745269663279448064");
        fs::remove_file(child).unwrap();

        let jobs = fixture.list().unwrap();
        assert_eq!(jobs.len(), before.len() - 2);
        assert!(jobs.iter().all(|row| row["jobId"] != PRIMARY && row["jobId"] != CHILD));
        assert!(!primary.exists());
    }

    #[test]
    fn flat_audio_loss_that_cannot_be_a_cleanup_prefix_preserves_survivors() {
        let fixture = Fixture::new();
        let primary = fixture.flat(PRIMARY, "431-4745269663279448064");
        let child = fixture.flat(CHILD, "431-4745269663279448064");
        let before = fs::read(&child).unwrap();
        fs::remove_file(primary).unwrap();

        assert!(fixture.list().is_err());
        assert_eq!(fs::read(child).unwrap(), before);
    }

    #[test]
    fn recovery_preflights_all_files_and_never_overwrites_a_live_row() {
        let fixture = Fixture::new();
        let pending = fixture.stage(PENDING, &[PRIMARY, CHILD]);
        let staged = pending.join(format!("{CHILD}.json"));
        let live = fixture.jobs.join(format!("{CHILD}.json"));
        fs::copy(&staged, &live).unwrap();
        let first = pending.join(format!("{PRIMARY}.json"));
        let before = fs::read(&first).unwrap();
        assert!(fixture.list().is_err());
        assert_eq!(fs::read(&first).unwrap(), before);
        assert!(staged.is_file() && live.is_file());
        assert!(!fixture.jobs.join(format!("{PRIMARY}.json")).exists());

        fs::remove_file(live).unwrap();
        let unknown = pending.join("unrelated.txt");
        fs::write(&unknown, b"preserve").unwrap();
        assert!(fixture.list().is_err());
        assert_eq!(fs::read(unknown).unwrap(), b"preserve");
        assert_eq!(fs::read(first).unwrap(), before);
    }

    #[test]
    fn recovery_waits_for_staged_owner_lease() {
        let fixture = Fixture::new();
        let pending = fixture.stage(PENDING, &[PRIMARY]);
        let owner = task_journal::owner_lock(fixture.root.path(), PRIMARY).unwrap();
        assert!(fixture.list().is_err());
        assert!(pending.join(format!("{PRIMARY}.json")).is_file());
        drop(owner);
        assert!(fixture.list().is_ok());
        assert!(!pending.exists());
    }

    #[test]
    fn rust_and_python_recover_the_same_removal_protocol() {
        let fixture = Fixture::new();
        let total = fixture.list().unwrap().len();
        let interrupted = fixture.run_python(r#"
import os, sys
from contextlib import ExitStack
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core import job_store
from core.worker_runtime_lock import runtime_lock

root = Path(sys.argv[2])
rename = Path.rename
def stop_after_first_move(path, destination):
    result = rename(path, destination)
    if destination.parent.name == ".removal-pending":
        os._exit(73)
    return result

Path.rename = stop_after_first_move
ids = ["audio-child", "audio-primary"]
with job_store.catalog_lock(root), ExitStack() as locks:
    for job_id in ids:
        locks.enter_context(runtime_lock(root / ".owners" / f"{job_id}.lock", timeout=0))
    for job_id in ids:
        locks.enter_context(job_store.write_lock(root, job_id))
    job_store.remove_jobs_unlocked(root, ids)
"#);
        assert_eq!(interrupted.status.code(), Some(73), "{}", String::from_utf8_lossy(&interrupted.stderr));
        assert!(fixture.jobs.join(PENDING).join("audio-child.json").is_file());
        assert_eq!(fixture.list().unwrap().len(), total);

        let pending = fixture.stage(PENDING, &[PRIMARY, CHILD]);
        let committed = fixture.jobs.join(COMMITTED);
        fs::rename(pending, &committed).unwrap();
        fs::remove_file(committed.join("audio-primary.json")).unwrap();
        let recovered = fixture.run_python(r#"
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.job_store import read_jobs

jobs = read_jobs(Path(sys.argv[2]))
assert "audio-child" not in jobs and "audio-primary" not in jobs, jobs.keys()
"#);
        assert!(recovered.status.success(), "{}", String::from_utf8_lossy(&recovered.stderr));
        assert!(!committed.exists());
        assert_eq!(fixture.list().unwrap().len(), total - 2);
    }
}
