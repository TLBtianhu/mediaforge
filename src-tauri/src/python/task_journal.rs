//! Durable manual media jobs. Individual job files are the shared source of truth.
use crate::error::{AppError, AppResult};
use crate::python::protocol::WorkerEnvelope;
use crate::storage;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

const OWNER_KIND: &str = "tauri-worker";
const WRITE_INTERVAL: Duration = Duration::from_millis(250);
pub(crate) type JournalHandle = Arc<Mutex<WorkerJournal>>;

pub(super) fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub(crate) fn valid_id(id: &str) -> bool {
    let reserved = id.split('.').next().unwrap_or("").to_ascii_uppercase();
    !id.eq_ignore_ascii_case("index")
        && !matches!(reserved.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        && !(reserved.len() == 4
            && (reserved.starts_with("COM") || reserved.starts_with("LPT"))
            && matches!(reserved.as_bytes()[3], b'1'..=b'9'))
        && !id.is_empty()
        && id.len() <= 200
        && id.as_bytes()[0].is_ascii_alphanumeric()
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
}

pub(crate) fn terminal(value: &Value) -> bool {
    matches!(
        value.get("status").and_then(Value::as_str),
        Some("completed" | "failed" | "cancelled" | "interrupted")
    )
}

pub(super) fn read_job(path: &Path) -> AppResult<Option<Value>> {
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let value = super::job_schema::parse(&text)?;
    super::job_schema::validate(&value)?;
    Ok(Some(value))
}

/// Windows sharing exclusion also excludes Python's msvcrt lease; Unix uses its flock.
/// Never unlink these lock files: every process must keep locking the same inode.
pub(super) struct FileLock(File);
impl FileLock {
    pub(super) fn acquire(path: &Path, timeout: Duration) -> AppResult<Self> {
        Self::acquire_inner(path, timeout, false)
    }

    pub(super) fn acquire_plain(path: &Path, timeout: Duration) -> AppResult<Self> {
        Self::acquire_inner(path, timeout, true)
    }

    fn acquire_inner(path: &Path, timeout: Duration, deny_reparse: bool) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let deadline = Instant::now() + timeout;
        loop {
            let mut options = OpenOptions::new();
            options.create(true).truncate(false).read(true).write(true);
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                options.share_mode(0);
                if deny_reparse { options.custom_flags(0x00200000); } // FILE_FLAG_OPEN_REPARSE_POINT
            }
            #[cfg(unix)]
            if deny_reparse {
                use std::os::unix::fs::OpenOptionsExt;
                options.custom_flags(libc::O_NOFOLLOW);
            }
            let attempt = options.open(path).and_then(|file| {
                if deny_reparse {
                    let metadata = file.metadata()?;
                    #[cfg(windows)]
                    {
                        use std::os::windows::fs::MetadataExt;
                        if metadata.file_attributes() & 0x400 != 0 { return Err(std::io::Error::other("asset lease cannot be a reparse point")); }
                    }
                    if !metadata.is_file() || metadata.file_type().is_symlink() { return Err(std::io::Error::other("asset lease must be a plain file")); }
                }
                #[cfg(unix)]
                {
                    use std::os::fd::AsRawFd;
                    if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0
                    {
                        return Err(std::io::Error::last_os_error());
                    }
                }
                Ok(Self(file))
            });
            match attempt {
                Ok(lock) => return Ok(lock),
                Err(error) => {
                    let busy = matches!(
                        error.kind(),
                        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::PermissionDenied
                    ) || matches!(error.raw_os_error(), Some(32 | 33));
                    if !busy || Instant::now() >= deadline {
                        return Err(error.into());
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
        }
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            unsafe {
                libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
            }
        }
        #[cfg(not(unix))]
        {
            let _ = &self.0;
        }
    }
}

pub(super) fn job_path(root: &Path, id: &str) -> AppResult<PathBuf> {
    if !valid_id(id) {
        return Err(AppError::Worker("invalid journal job id".into()));
    }
    Ok(root.join("agent-jobs").join(format!("{id}.json")))
}

pub(super) fn write_lock(root: &Path, id: &str) -> AppResult<FileLock> {
    job_path(root, id)?;
    FileLock::acquire(
        &root.join("agent-jobs/.writes").join(format!("{id}.lock")),
        Duration::from_secs(2),
    )
}

pub(super) fn catalog_lock(root: &Path) -> AppResult<FileLock> {
    FileLock::acquire(&root.join("agent-jobs/.catalog.lock"), Duration::from_secs(5))
}

pub(super) fn owner_lock(root: &Path, id: &str) -> AppResult<FileLock> {
    job_path(root, id)?;
    let primary = FileLock::acquire_plain(&root.join("agent-jobs/.owners").join(format!("{id}.lock")), Duration::ZERO)?;
    let _probe = FileLock::acquire_plain(&owner_reap_path(root, id), Duration::ZERO)?;
    Ok(primary)
}

fn owner_reap_path(root: &Path, id: &str) -> PathBuf {
    root.join("agent-jobs/.owners/reaping").join(format!("{id}.lock"))
}

/// Identity of one job file revision.
///
/// `len` alone is not enough: two atomic replacements within the same clock
/// tick can produce the same length and the same coarse modification time.
/// Every replacement creates a new file, so its creation time (and on Windows
/// its file index) changes too; a stamp is only trusted when all of them match.
#[derive(PartialEq, Eq)]
struct JobStamp {
    len: u64,
    modified: Option<SystemTime>,
    created: Option<SystemTime>,
}

fn job_stamp(metadata: &fs::Metadata) -> JobStamp {
    JobStamp {
        len: metadata.len(),
        modified: metadata.modified().ok(),
        created: metadata.created().ok(),
    }
}

/// Job files are immutable between atomic replacements, so an unchanged
/// (len, modified) stamp identifies the same validated record. The cache never
/// outlives the file it describes: any replacement changes the stamp, and a
/// removed file is pruned from the map.
fn job_snapshots() -> &'static Mutex<HashMap<PathBuf, (JobStamp, Value)>> {
    static CACHE: OnceLock<Mutex<HashMap<PathBuf, (JobStamp, Value)>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(test)]
fn parse_counts() -> &'static Mutex<HashMap<PathBuf, usize>> {
    static COUNTS: OnceLock<Mutex<HashMap<PathBuf, usize>>> = OnceLock::new();
    COUNTS.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(test)]
fn bump_parse_count(root: &Path) {
    *parse_counts()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .entry(root.to_path_buf())
        .or_default() += 1;
}

#[cfg(test)]
pub(super) fn journal_parse_count(root: &Path) -> usize {
    parse_counts()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .get(root)
        .copied()
        .unwrap_or(0)
}

/// Read one job through the stamp cache. Returns false when the file vanished.
fn ensure_cached(root: &Path, path: &Path, id: &str, metadata: &fs::Metadata) -> AppResult<bool> {
    let stamp = job_stamp(metadata);
    if let Some((known, _)) = job_snapshots()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .get(path)
    {
        if *known == stamp {
            return Ok(true);
        }
    }
    // Progress writers intentionally do not take the catalog lock. Coordinate
    // the actual open/read with their per-job atomic replacement on Windows.
    let _read_guard = write_lock(root, id)?;
    let Some(parsed) = read_job(path)? else {
        job_snapshots()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .remove(path);
        return Ok(false);
    };
    #[cfg(test)]
    bump_parse_count(root);
    job_snapshots()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .insert(path.to_path_buf(), (stamp, parsed));
    Ok(true)
}

fn cached_value(path: &Path) -> Option<Value> {
    job_snapshots()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .get(path)
        .map(|(_, value)| value.clone())
}

fn prune_cache(directory: &Path, present: &HashSet<PathBuf>) {
    job_snapshots()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .retain(|cached, _| !cached.starts_with(directory) || present.contains(cached));
}

pub(super) type CatalogMember = (String, PathBuf, fs::Metadata);

fn scan_catalog(root: &Path) -> AppResult<(Vec<CatalogMember>, Vec<PathBuf>)> {
    let directory = root.join("agent-jobs");
    if !directory.is_dir() { return Ok((Vec::new(), Vec::new())); }
    let mut members = Vec::new();
    let mut removal_residues = Vec::new();
    for entry in fs::read_dir(&directory)? {
        let path = entry?.path();
        if super::job_removal::is_residue(&path) {
            removal_residues.push(path);
            continue;
        }
        if path.extension().and_then(|s| s.to_str()) != Some("json") { continue; }
        let id = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        if !valid_id(id) {
            // `index.json` is the legacy task-history index, never a job record.
            if id.eq_ignore_ascii_case("index") { continue; }
            return Err(AppError::Worker(format!(
                "unsupported job filename {}; original file preserved",
                path.file_name().and_then(|name| name.to_str()).unwrap_or("?")
            )));
        }
        let metadata = fs::symlink_metadata(&path)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { return Err(AppError::Worker("job reparse point is not allowed".into())); }
        }
        if metadata.file_type().is_symlink() { return Err(AppError::Worker("job symlink is not allowed".into())); }
        members.push((id.to_string(), path, metadata));
    }
    Ok((members, removal_residues))
}

/// The caller holds the catalog lock. Resolve any interrupted removal before
/// projecting either a full snapshot or an incremental reply.
fn catalog_members(root: &Path) -> AppResult<Vec<CatalogMember>> {
    let (members, residues) = scan_catalog(root)?;
    if residues.is_empty() { return Ok(members); }
    super::job_removal::recover_unlocked(root, &members, &residues)?;
    let (members, residues) = scan_catalog(root)?;
    if !residues.is_empty() {
        return Err(AppError::Worker("unexpected job removal state after recovery".into()));
    }
    Ok(members)
}

fn read_members(root: &Path, members: &[CatalogMember]) -> AppResult<Vec<Value>> {
    let directory = root.join("agent-jobs");
    let mut jobs = Vec::new();
    let mut present: HashSet<PathBuf> = HashSet::new();
    for (id, path, metadata) in members {
        present.insert(path.clone());
        if !ensure_cached(root, path, id, metadata)? { continue; }
        let Some(value) = cached_value(path) else { continue };
        if value["jobId"] != id.as_str() { return Err(AppError::Worker("job id does not match its filename".into())); }
        jobs.push(value);
    }
    prune_cache(&directory, &present);
    Ok(jobs)
}

pub(super) fn read_all_unlocked(root: &Path) -> AppResult<Vec<Value>> {
    let jobs = read_members(root, &catalog_members(root)?)?;
    super::audio_jobs::validate_library(&jobs)?;
    Ok(jobs)
}

pub(crate) fn registered_ids(primary: &str, payload: &Value) -> Vec<String> {
    let mut ids = vec![primary.to_string()];
    if let Some(tasks) = payload.get("tasks").and_then(Value::as_array) {
        for task in tasks {
            if let Some(id) = task.get("taskId").and_then(Value::as_str) {
                if !ids.iter().any(|known| known == id) {
                    ids.push(id.to_string());
                }
            }
        }
    }
    ids
}

#[cfg(test)]
pub(crate) fn tracks(command: &str) -> bool { super::execution_quota::applies(command).unwrap() }

struct Job {
    snapshot: Value,
    _owner: FileLock,
    saved: Option<Instant>,
}

pub(crate) struct WorkerJournal {
    root: PathBuf,
    primary: String,
    jobs: Vec<Job>,
    _quota: Option<super::execution_quota::QuotaLease>,
}

pub(crate) enum ClaimAttempt {
    Ready(JournalHandle, Value, String),
    Waiting,
    Finished,
}

fn pending_row<'a>(library: &'a [Value], command: &str, requested: &str) -> AppResult<Option<(&'a Value, &'a Value)>> {
    let Some(row) = library.iter().find(|row| row["jobId"] == requested) else { return Ok(None); };
    if row["command"] != command || row["source"] != "manual" {
        return Err(AppError::Worker("queued task command/source mismatch".into()));
    }
    if let Some(anchor) = row.pointer("/audio/primaryJobId").and_then(Value::as_str) {
        let primary = library.iter().find(|row| row["jobId"] == anchor)
            .ok_or_else(|| AppError::Worker("queued task anchor is missing".into()))?;
        let members = library.iter().filter(|row| row.pointer("/audio/primaryJobId").and_then(Value::as_str) == Some(anchor)).collect::<Vec<_>>();
        if members.iter().any(|row| !terminal(row) && !row["owner"].is_null()) { return Ok(None); }
        return Ok(members.into_iter().filter(|row| row["status"] == "queued" && row["owner"].is_null())
            .min_by_key(|row| row.pointer("/audio/index").and_then(Value::as_u64).unwrap())
            .map(|queued| (primary, queued)));
    }
    Ok((row["status"] == "queued" && row["owner"].is_null()).then_some((row, row)))
}

/// Stable identity for an idempotent, in-memory dispatcher request. A cancelled
/// audio anchor still identifies its waiting siblings; it is never duplicated.
pub(crate) fn pending_identity(root: &Path, command: &str, requested: &str) -> AppResult<Option<(String, f64)>> {
    job_path(root, requested)?;
    let _catalog = catalog_lock(root)?;
    let library = read_all_unlocked(root)?;
    if !library.iter().any(|row| row["jobId"] == requested) { return Err(AppError::Worker("queued task is missing".into())); }
    Ok(pending_row(&library, command, requested)?.map(|(primary, _)| (
        primary["jobId"].as_str().unwrap().to_string(), primary["createdAt"].as_f64().unwrap(),
    )))
}

pub(crate) fn enqueue_manual(root: &Path, command: &str, id: &str, payload: &Value) -> AppResult<()> {
    if !payload.is_object() { return Err(AppError::Worker("task payload must be an object".into())); }
    let path = job_path(root, id)?;
    let _catalog = catalog_lock(root)?;
    let library = read_all_unlocked(root)?;
    if let Some(previous) = library.iter().find(|row| row["jobId"] == id) {
        if previous["command"] == command && previous["source"] == "manual" && previous["payload"] == *payload { return Ok(()); }
        return Err(AppError::Worker("task journal ID already exists with another request".into()));
    }
    let _write = write_lock(root, id)?;
    let created = now();
    let snapshot = json!({"schemaVersion": 2, "jobId": id, "command": command, "source": "manual", "cancelTaskId": id,
        "status": "queued", "fraction": 0.0, "message": "waiting for worker capacity", "error": null, "result": null,
        "createdAt": created, "updatedAt": created, "startedAt": null, "finishedAt": null, "payload": payload,
        "owner": null, "runtime": super::job_schema::empty_runtime(), "logs": []});
    super::job_schema::validate(&snapshot)?;
    storage::write_json_file(&path, &snapshot)
}

pub(crate) fn try_claim_pending(root: &Path, command: &str, requested: &str) -> AppResult<ClaimAttempt> {
    let _catalog = catalog_lock(root)?;
    let library = read_all_unlocked(root)?;
    let Some((primary, queued)) = pending_row(&library, command, requested)? else { return Ok(ClaimAttempt::Finished); };
    let Some(quota) = super::execution_quota::try_acquire(root)? else { return Ok(ClaimAttempt::Waiting); };
    let mut journal;
    let payload;
    if primary.get("audio").is_some() {
        let claim = super::audio_jobs::claim_unlocked(root, command, queued["jobId"].as_str().unwrap(), &library)?;
        payload = claim.payload;
        journal = WorkerJournal { root: root.into(), primary: claim.primary,
            jobs: claim.records.into_iter().map(|(snapshot, owner)| Job { snapshot, _owner: owner, saved: None }).collect(),
            _quota: Some(quota) };
    } else {
        let id = primary["jobId"].as_str().unwrap();
        let owner = owner_lock(root, id)?;
        let mut snapshot = primary.clone();
        snapshot["owner"] = json!({"kind": OWNER_KIND, "pid": std::process::id()});
        payload = snapshot["payload"].clone();
        journal = WorkerJournal { root: root.into(), primary: id.into(),
            jobs: vec![Job { snapshot, _owner: owner, saved: None }], _quota: Some(quota) };
    }
    for index in 0..journal.jobs.len() {
        let snapshot = &mut journal.jobs[index].snapshot;
        snapshot["status"] = json!("running");
        snapshot["startedAt"] = json!(now());
        snapshot["message"] = json!("starting worker");
        journal.persist(index, true)?;
    }
    let id = journal.primary.clone();
    Ok(ClaimAttempt::Ready(Arc::new(Mutex::new(journal)), payload, id))
}

pub(crate) fn fail_unclaimed(root: &Path, command: &str, requested: &str, message: &str) -> AppResult<()> {
    let _catalog = catalog_lock(root)?;
    let library = read_all_unlocked(root)?;
    let Some((primary, _)) = pending_row(&library, command, requested)? else { return Ok(()); };
    let anchor = primary["jobId"].as_str().unwrap();
    let selected = library.iter().filter(|row| row["jobId"] == anchor || row.pointer("/audio/primaryJobId").and_then(Value::as_str) == Some(anchor));
    for row in selected {
        if row["status"] != "queued" || !row["owner"].is_null() { continue; }
        let id = row["jobId"].as_str().unwrap();
        let _write = write_lock(root, id)?;
        let mut row = row.clone();
        row["status"] = json!("failed"); row["message"] = json!(message); row["error"] = json!(message);
        row["finishedAt"] = json!(now()); row["updatedAt"] = json!(now());
        super::job_runtime::finish(&mut row, "failed");
        storage::write_json_file(&job_path(root, id)?, &row)?;
    }
    Ok(())
}

impl WorkerJournal {
    pub(super) fn reap_fences(&self) -> (Option<&Path>, Vec<PathBuf>) {
        let quota = self._quota.as_ref().map(super::execution_quota::QuotaLease::fence_path);
        let owners = self.jobs.iter().map(|job| {
            let id = job.snapshot["jobId"].as_str().expect("validated journal job ID");
            owner_reap_path(&self.root, id)
        }).collect();
        (quota, owners)
    }

    pub(crate) fn data_root(&self) -> &Path { &self.root }
    #[cfg(test)]
    pub(crate) fn start(
        root: &Path,
        command: &str,
        primary: &str,
        payload: &Value,
    ) -> AppResult<JournalHandle> {
        if !payload.is_object() {
            return Err(AppError::Worker("task payload must be an object".into()));
        }
        let _catalog = catalog_lock(root)?;
        read_all_unlocked(root)?;
        let mut journal = Self {
            root: root.into(),
            primary: primary.into(),
            jobs: Vec::new(),
            _quota: None,
        };
        let created = now();
        let result = (|| -> AppResult<()> {
            for id in registered_ids(primary, payload) {
                let path = job_path(root, &id)?;
                let owner = owner_lock(root, &id).map_err(|error| {
                    AppError::Worker(format!("task journal already owned ({id}): {error}"))
                })?;
                let _write = write_lock(root, &id)?;
                let previous = read_job(&path)?;
                if previous.is_some() {
                    return Err(AppError::Worker(format!(
                        "task journal already exists: {id}"
                    )));
                }
                // One complete batch snapshot plus one effective request per child
                // keeps a large batch's journal linear in the number of inputs.
                let mut snapshot_payload = if id == primary {
                    payload.clone()
                } else {
                    Value::Object(
                        payload
                            .as_object()
                            .unwrap()
                            .iter()
                            .filter(|(key, _)| key.as_str() != "tasks")
                            .map(|(key, value)| (key.clone(), value.clone()))
                            .collect(),
                    )
                };
                if let Some(child) =
                    payload
                        .get("tasks")
                        .and_then(Value::as_array)
                        .and_then(|tasks| {
                            tasks.iter().find(|task| {
                                task.get("taskId").and_then(Value::as_str) == Some(&id)
                            })
                        })
                {
                    if let Some(input) = child.get("input") {
                        snapshot_payload["input"] = input.clone();
                    }
                    if id != primary {
                        snapshot_payload["tasks"] = json!([child]);
                        snapshot_payload["taskId"] = json!(id);
                        snapshot_payload["batchTaskId"] = json!(primary);
                    }
                }
                let snapshot = json!({
                    "schemaVersion": 2,
                    "jobId": id, "command": command, "source": "manual", "cancelTaskId": id,
                    "status": "queued", "fraction": 0.0, "message": "queued", "error": null, "result": null,
                    "createdAt": created, "updatedAt": created, "startedAt": null,
                    "finishedAt": null, "payload": snapshot_payload, "runtime": super::job_schema::empty_runtime(), "logs": [],
                    "owner": { "kind": OWNER_KIND, "pid": std::process::id() }
                });
                storage::write_json_file(&path, &snapshot)?;
                journal.jobs.push(Job {
                    snapshot,
                    _owner: owner,
                    saved: Some(Instant::now()),
                });
            }
            Ok(())
        })();
        if let Err(error) = result {
            let _ = journal.fail_all(&error.to_string());
            return Err(error);
        }
        Ok(Arc::new(Mutex::new(journal)))
    }

    #[cfg(test)]
    pub(crate) fn claim_audio(root: &Path, command: &str, primary: &str) -> AppResult<(JournalHandle, Value)> {
        let claim = super::audio_jobs::claim(root, command, primary)?;
        let journal = Self { root: root.into(), primary: claim.primary, jobs: claim.records.into_iter().map(|(snapshot, owner)| Job { snapshot, _owner: owner, saved: Some(Instant::now()) }).collect(), _quota: None };
        Ok((Arc::new(Mutex::new(journal)), claim.payload))
    }

    fn persist(&mut self, index: usize, force: bool) -> AppResult<()> {
        let job = &mut self.jobs[index];
        if !force
            && job
                .saved
                .is_some_and(|last| last.elapsed() < WRITE_INTERVAL)
        {
            return Ok(());
        }
        let id = job.snapshot["jobId"].as_str().unwrap();
        let _write = write_lock(&self.root, id)?;
        let path = job_path(&self.root, id)?;
        if let Some(current) = read_job(&path)? {
            // A cancel command may have won the terminal race while stdout was draining.
            if terminal(&current) {
                let mut current = current;
                if current["logs"] != job.snapshot["logs"] {
                    current["logs"] = job.snapshot["logs"].clone();
                    current["updatedAt"] = json!(now());
                    storage::write_json_file(&path, &current)?;
                }
                job.snapshot = current;
                return Ok(());
            }
        }
        job.snapshot["updatedAt"] = json!(now());
        storage::write_json_file(&path, &job.snapshot)?;
        job.saved = Some(Instant::now());
        Ok(())
    }

    fn finish_job(
        &mut self,
        index: usize,
        status: &str,
        message: &str,
        result: Option<&Value>,
    ) -> AppResult<()> {
        let job = &mut self.jobs[index];
        if terminal(&job.snapshot) {
            return self.persist(index, true);
        }
        job.snapshot["status"] = json!(status);
        super::job_runtime::finish(&mut job.snapshot, status);
        job.snapshot["message"] = json!(message);
        job.snapshot["finishedAt"] = json!(now());
        if status == "completed" {
            job.snapshot["fraction"] = json!(1.0);
            job.snapshot["error"] = Value::Null;
        }
        if status == "failed" || status == "interrupted" {
            job.snapshot["error"] = json!(message);
        }
        if let Some(result) = result {
            let mut canonical = result.clone();
            if canonical.get("outputs").is_some() { canonical.as_object_mut().unwrap().remove("files"); }
            let result = &canonical;
            if status != "completed" {
                if let (Some(previous), Some(incoming)) =
                    (job.snapshot["result"].as_object(), result.as_object())
                {
                    let mut merged = previous.clone();
                    merged.extend(incoming.clone());
                    if incoming
                        .get("outputs")
                        .and_then(Value::as_array)
                        .is_some_and(Vec::is_empty)
                    {
                        if let Some(outputs) = previous.get("outputs") {
                            merged.insert("outputs".into(), outputs.clone());
                        }
                    }
                    job.snapshot["result"] = Value::Object(merged);
                } else {
                    job.snapshot["result"] = result.clone();
                }
            } else {
                job.snapshot["result"] = result.clone();
            }
        }
        self.persist(index, true)
    }

    pub(crate) fn running(&mut self, worker_pid: u32) -> AppResult<()> {
        for index in 0..self.jobs.len() {
            self.jobs[index].snapshot["status"] = json!("running");
            if self.jobs[index].snapshot["startedAt"].is_null() { self.jobs[index].snapshot["startedAt"] = json!(now()); }
            self.jobs[index].snapshot["message"] = json!("starting worker");
            self.jobs[index].snapshot["owner"]["workerPid"] = json!(worker_pid);
            self.persist(index, true)?;
        }
        Ok(())
    }

    pub(crate) fn fail_all(&mut self, message: &str) -> AppResult<()> {
        for index in 0..self.jobs.len() {
            self.finish_job(index, "failed", message, Some(&json!({"message": message})))?;
        }
        Ok(())
    }

    pub(crate) fn observe(&mut self, event: &WorkerEnvelope) -> AppResult<()> {
        self.observe_with_result(event, |_| {})
    }

    pub(crate) fn observe_with_result(&mut self, event: &WorkerEnvelope, mut on_result: impl FnMut(&Value)) -> AppResult<()> {
        let kind = event.event_type.as_str();
        if !super::protocol::is_journal_event(kind) {
            return Ok(());
        }
        let explicit = event.task_id.as_deref().or(event.request_id.as_deref());
        let targeted = explicit.filter(|id| {
            self.jobs
                .iter()
                .any(|job| job.snapshot["jobId"].as_str() == Some(id))
        });
        if explicit.is_some() && targeted.is_none() && kind != "error" {
            return Ok(());
        }
        let message = event
            .payload
            .get("message")
            .or_else(|| event.payload.get("current"))
            .and_then(Value::as_str)
            .unwrap_or(kind);
        for index in 0..self.jobs.len() {
            let id = self.jobs[index].snapshot["jobId"].as_str().unwrap();
            if targeted.is_some_and(|target| target != id)
                || (targeted.is_none() && !matches!(kind, "error" | "task.log" | "worker_log") && id != self.primary)
            {
                continue;
            }
            if matches!(kind, "task.log" | "worker_log") {
                super::job_runtime::log(&mut self.jobs[index].snapshot, event);
                self.persist(index, false)?;
                continue;
            }
            if terminal(&self.jobs[index].snapshot) {
                continue;
            }
            super::job_runtime::observe(&mut self.jobs[index].snapshot, event);
            if kind == "error" {
                super::job_runtime::log(&mut self.jobs[index].snapshot, event);
                if event.payload.get("recoverable").and_then(Value::as_bool) == Some(true) {
                    self.jobs[index].snapshot["message"] = json!(message);
                    self.persist(index, true)?;
                    continue;
                }
                self.finish_job(index, "failed", message, Some(&event.payload))?;
                if event.payload.get("outputs").is_some() { on_result(&self.jobs[index].snapshot); }
            } else if kind == "task.cancelled" {
                debug_assert!(
                    super::protocol::is_terminal_event(kind),
                    "manifest must declare task.cancelled as terminal"
                );
                self.finish_job(index, "cancelled", message, Some(&event.payload))?;
                if event.payload.get("outputs").is_some() { on_result(&self.jobs[index].snapshot); }
            } else if matches!(
                kind,
                "task.result" | "editor_mix_exported"
            ) {
                debug_assert!(
                    kind == "editor_mix_exported" || super::protocol::is_terminal_event(kind),
                    "manifest must declare task.result as terminal"
                );
                let failed = event
                    .payload
                    .get("failed")
                    .and_then(Value::as_array)
                    .filter(|items| !items.is_empty());
                let empty_media = kind == "task.result"
                    && self.jobs[index]
                        .snapshot
                        .pointer("/payload/operation")
                        .and_then(Value::as_str)
                        .is_some_and(|op| op.starts_with("media_"))
                    && event
                        .payload
                        .get("outputs")
                        .and_then(Value::as_array)
                        .is_some_and(Vec::is_empty);
                let detail = failed
                    .and_then(|items| items.first())
                    .and_then(|first| first.get("error").or_else(|| first.get("message")))
                    .and_then(Value::as_str)
                    .unwrap_or(if empty_media {
                        "media operation produced no outputs"
                    } else {
                        "completed with failures"
                    });
                self.jobs[index].snapshot["fraction"] = json!(1.0);
                self.finish_job(
                    index,
                    if failed.is_some() || empty_media {
                        "failed"
                    } else {
                        "completed"
                    },
                    if failed.is_some() || empty_media {
                        detail
                    } else {
                        "completed"
                    },
                    Some(&event.payload),
                )?;
                on_result(&self.jobs[index].snapshot);
            } else {
                if event.payload.get("outputs").is_some() {
                    let mut result = event.payload.clone();
                    result.as_object_mut().unwrap().remove("files");
                    self.jobs[index].snapshot["result"] = result;
                }
                let fraction = event
                    .payload
                    .get("fraction")
                    .or_else(|| event.payload.get("overallFraction"))
                    .and_then(Value::as_f64)
                    .or_else(|| {
                        let done = event
                            .payload
                            .get("done")
                            .or_else(|| event.payload.get("completed"))
                            .and_then(Value::as_f64)?;
                        let total = event.payload.get("total").and_then(Value::as_f64)?;
                        (total > 0.0).then_some(done / total)
                    });
                if let Some(fraction) = fraction {
                    self.jobs[index].snapshot["fraction"] = json!(fraction.clamp(0.0, 1.0));
                }
                self.jobs[index].snapshot["status"] = json!("running");
                self.jobs[index].snapshot["message"] = json!(message);
                // Output discovery is durable immediately, so cancellation cannot
                // lose a completed file inside the ordinary progress throttle.
                let discovered_output = event.payload.get("outputs").is_some();
                self.persist(index, discovered_output)?;
                if discovered_output { on_result(&self.jobs[index].snapshot); }
            }
        }
        Ok(())
    }

    pub(crate) fn exited(
        &mut self,
        success: bool,
        cancelled: bool,
        message: &str,
    ) -> AppResult<()> {
        // A batch wrapper ID need not itself receive an event. Its children do;
        // finish the wrapper from all child terminals without inventing a failure.
        if !self.jobs.iter().any(|job| job.snapshot.get("audio").is_some()) {
        if let Some(primary) = self
            .jobs
            .iter()
            .position(|job| job.snapshot["jobId"].as_str() == Some(&self.primary))
        {
            if self.jobs.len() > 1
                && !terminal(&self.jobs[primary].snapshot)
                && self
                    .jobs
                    .iter()
                    .enumerate()
                    .all(|(index, job)| index == primary || terminal(&job.snapshot))
            {
                let children = self
                    .jobs
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| *index != primary)
                    .map(|(_, job)| job.snapshot.clone())
                    .collect::<Vec<_>>();
                let failed = children
                    .iter()
                    .any(|job| matches!(job["status"].as_str(), Some("failed" | "interrupted")));
                let cancelled = children.iter().any(|job| job["status"] == "cancelled");
                let outputs = children
                    .iter()
                    .flat_map(|job| {
                        job.pointer("/result/outputs")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    })
                    .collect::<Vec<_>>();
                self.finish_job(
                    primary,
                    if failed {
                        "failed"
                    } else if cancelled {
                        "cancelled"
                    } else {
                        "completed"
                    },
                    "batch finished",
                    Some(&json!({"outputs": outputs, "tasks": children})),
                )?;
            }
        }
        }
        for index in 0..self.jobs.len() {
            if !terminal(&self.jobs[index].snapshot) {
                self.finish_job(
                    index,
                    if cancelled { "cancelled" } else { "failed" },
                    if cancelled {
                        "Cancelled"
                    } else if success {
                        "worker exited without a result"
                    } else {
                        message
                    },
                    None,
                )?;
            } else {
                self.persist(index, true)?;
            }
        }
        Ok(())
    }
}

pub(crate) fn cancel(root: &Path, id: &str) -> AppResult<bool> {
    let _catalog = catalog_lock(root)?;
    read_all_unlocked(root)?;
    let _write = write_lock(root, id)?;
    let path = job_path(root, id)?;
    let Some(mut value) = read_job(&path)? else {
        return Ok(false);
    };
    if (value["source"] != "manual") || terminal(&value) {
        return Ok(false);
    }
    value["status"] = json!("cancelled");
    value["message"] = json!("Cancelled");
    value["finishedAt"] = json!(now());
    value["updatedAt"] = json!(now());
    super::job_runtime::finish(&mut value, "cancelled");
    storage::write_json_file(&path, &value)?;
    Ok(true)
}

pub(crate) fn cancel_queued(root: &Path, id: &str) -> AppResult<bool> {
    if !valid_id(id) { return Err(AppError::Worker("invalid job id".into())); }
    let _catalog = catalog_lock(root)?;
    let rows = read_all_unlocked(root)?;
    let Some(row) = rows.iter().find(|row| row["jobId"] == id) else { return Ok(false); };
    if row["status"] != "queued" || !row["owner"].is_null() { return Ok(false); }
    let _write = write_lock(root, id)?;
    let mut row = read_job(&job_path(root, id)?)?.ok_or_else(|| AppError::Worker("queued job disappeared".into()))?;
    if row["status"] != "queued" || !row["owner"].is_null() { return Ok(false); }
    row["status"] = json!("cancelled"); row["message"] = json!("Cancelled");
    row["finishedAt"] = json!(now()); row["updatedAt"] = json!(now());
    super::job_runtime::finish(&mut row, "cancelled");
    storage::write_json_file(&job_path(root, id)?, &row)?;
    Ok(true)
}

pub(crate) fn request_cancel(root: &Path, id: &str) -> AppResult<bool> {
    if cancel_queued(root, id)? { return Ok(true); }
    let _catalog = catalog_lock(root)?;
    let rows = read_all_unlocked(root)?;
    if !rows.iter().any(|row| row["jobId"] == id && !terminal(row)) { return Ok(false); }
    fs::write(root.join("agent-jobs").join(format!("{id}.cancel")), b"cancel")?;
    Ok(true)
}

/// Either lifetime lease keeps a typed owner active until its worker tree exits.
fn recover(root: &Path, id: &str, value: Value) -> AppResult<Value> {
    if value["owner"].is_null() || terminal(&value) {
        return Ok(value);
    }
    let Ok(_owner) = owner_lock(root, id) else {
        return Ok(value);
    };
    let _write = write_lock(root, id)?;
    let path = job_path(root, id)?;
    let mut latest = read_job(&path)?.unwrap_or(value);
    if !latest["owner"].is_null() && !terminal(&latest) {
        latest["status"] = json!("interrupted");
        latest["message"] = json!("应用退出时任务被中断");
        latest["error"] = json!("job owner exited before the job finished");
        latest["finishedAt"] = json!(now());
        latest["updatedAt"] = json!(now());
        super::job_runtime::finish(&mut latest, "interrupted");
        storage::write_json_file(&path, &latest)?;
    }
    Ok(latest)
}

/// Keep catalog membership stable while a caller applies a domain operation to
/// the validated snapshot. Callers must acquire any JSON guard inside this scope.
pub(crate) fn with_catalog_records<T>(root: &Path, action: impl FnOnce(&[Value]) -> AppResult<T>) -> AppResult<T> {
    let _catalog = catalog_lock(root)?;
    let rows = read_all_unlocked(root)?;
    action(&rows)
}

pub(crate) fn list(root: &Path) -> AppResult<Vec<Value>> {
    let _catalog = catalog_lock(root)?;
    let rows = read_all_unlocked(root)?;
    let mut jobs = Vec::new();
    for value in rows {
        let id = value["jobId"].as_str().unwrap().to_string();
        jobs.push(recover(root, &id, value)?);
    }
    jobs.sort_by(|a, b| b["createdAt"].as_f64().unwrap().total_cmp(&a["createdAt"].as_f64().unwrap()));
    Ok(jobs)
}

/// Result of one journal read: the changed records, the ids the caller must
/// drop, and whether this was an authoritative full snapshot.
pub(crate) struct JournalDelta {
    pub jobs: Vec<Value>,
    pub removed: Vec<String>,
    pub full: bool,
}

/// Read the catalog incrementally.
///
/// `known` maps the job ids a caller already holds to their `updatedAt`. Only
/// records whose `updatedAt` differs are returned, and ids the caller knows but
/// the disk no longer has are reported as removed. Passing `None` returns the
/// authoritative full snapshot used for first load, recovery and tests; the
/// catalog itself stays the single source of truth in both modes.
pub(crate) fn read_delta(root: &Path, known: Option<&HashMap<String, f64>>) -> AppResult<JournalDelta> {
    let Some(known) = known else {
        return Ok(JournalDelta { jobs: list(root)?, removed: Vec::new(), full: true });
    };
    let _catalog = catalog_lock(root)?;
    let directory = root.join("agent-jobs");
    let members = catalog_members(root)?;
    for (id, path, metadata) in &members {
        ensure_cached(root, path, id, metadata)?;
    }
    let present: HashSet<PathBuf> = members.iter().map(|(_, path, _)| path.clone()).collect();
    prune_cache(&directory, &present);

    // A live owner is only trusted while its lease is held. Recovering the few
    // non-terminal rows keeps an exited process from pinning a job as running.
    let owned: Vec<(String, PathBuf)> = {
        let cache = job_snapshots().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        members
            .iter()
            .filter_map(|(id, path, _)| {
                let (_, value) = cache.get(path)?;
                (!value["owner"].is_null() && !terminal(value)).then(|| (id.clone(), path.clone()))
            })
            .collect()
    };
    for (id, path) in owned {
        let Some(value) = cached_value(&path) else { continue };
        let recovered = recover(root, &id, value)?;
        if let Ok(metadata) = fs::metadata(&path) {
            job_snapshots()
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .insert(path, (job_stamp(&metadata), recovered));
        }
    }

    // Group closure only involves audio records; cloning just those keeps an
    // idle poll from copying the whole history.
    let audio: Vec<Value> = {
        let cache = job_snapshots().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        members
            .iter()
            .filter_map(|(_, path, _)| cache.get(path).map(|(_, value)| value))
            .filter(|value| value.get("audio").is_some())
            .cloned()
            .collect()
    };
    super::audio_jobs::validate_library(&audio)?;

    let cache = job_snapshots().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut changed = Vec::new();
    let mut present_ids: HashSet<String> = HashSet::new();
    for (_, path, _) in &members {
        let Some((_, value)) = cache.get(path) else { continue };
        let Some(id) = value["jobId"].as_str() else { continue };
        present_ids.insert(id.to_string());
        let updated = value["updatedAt"].as_f64().unwrap_or_default();
        if known.get(id) != Some(&updated) {
            changed.push(value.clone());
        }
    }
    drop(cache);
    let removed = known
        .keys()
        .filter(|id| !present_ids.contains(*id))
        .cloned()
        .collect();
    Ok(JournalDelta { jobs: changed, removed, full: false })
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn journal_cache_reflects_replaced_and_removed_job_files() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "cached_job", &json!({"operation": "fixture"})).unwrap();
        let path = job_path(root.path(), "cached_job").unwrap();
        assert_eq!(list(root.path()).unwrap().len(), 1);
        // An atomic replacement is the only way this process writes a job.
        let mut row = read_job(&path).unwrap().unwrap();
        row["message"] = json!("replaced");
        row["updatedAt"] = json!(row["updatedAt"].as_f64().unwrap() + 1.0);
        storage::write_json_file(&path, &row).unwrap();
        let rows = list(root.path()).unwrap();
        assert_eq!(rows[0]["message"], "replaced", "a replaced file must be re-read");
        // A removed record leaves the snapshot instead of lingering in the cache.
        fs::remove_file(&path).unwrap();
        assert!(list(root.path()).unwrap().is_empty());
        // Re-creating it is a new file, not the cached one.
        enqueue_manual(root.path(), "run_operation", "cached_job", &json!({"operation": "fixture"})).unwrap();
        assert_eq!(list(root.path()).unwrap().len(), 1);
    }

    #[test]
    fn unchanged_job_files_are_not_reparsed_between_polls() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "steady_job", &json!({"operation": "fixture"})).unwrap();
        // The enqueue path may already have populated the cache; what matters is
        // that repeated polls do not parse the same unchanged file again.
        assert_eq!(list(root.path()).unwrap().len(), 1);
        let settled = journal_parse_count(root.path());
        for _ in 0..4 {
            assert_eq!(list(root.path()).unwrap().len(), 1);
        }
        assert_eq!(
            journal_parse_count(root.path()),
            settled,
            "an unchanged job file must not be parsed once per poll"
        );
        // Exactly one reparse after a real write, then steady again.
        let path = job_path(root.path(), "steady_job").unwrap();
        let mut row = read_job(&path).unwrap().unwrap();
        row["fraction"] = json!(0.5);
        row["updatedAt"] = json!(row["updatedAt"].as_f64().unwrap() + 1.0);
        storage::write_json_file(&path, &row).unwrap();
        list(root.path()).unwrap();
        let after_write = journal_parse_count(root.path());
        assert_eq!(after_write, settled + 1, "a replaced file is parsed exactly once");
        list(root.path()).unwrap();
        assert_eq!(journal_parse_count(root.path()), after_write);
    }


    #[test]
    fn legacy_index_json_is_ignored_but_other_bad_names_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "real_job", &json!({"operation": "fixture"})).unwrap();
        std::fs::write(root.path().join("agent-jobs").join("index.json"), b"{}").unwrap();
        assert_eq!(list(root.path()).unwrap().len(), 1, "index.json is not a job");
        std::fs::write(root.path().join("agent-jobs").join("bad name.json"), b"{}").unwrap();
        let error = list(root.path()).unwrap_err().to_string();
        assert!(error.contains("bad name.json"), "{error}");
    }

    #[test]
    fn thousand_job_histories_are_read_once_and_delta_polls_stay_cheap() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "bulk-template", &json!({"operation": "fixture"})).unwrap();
        let template_path = job_path(root.path(), "bulk-template").unwrap();
        let template = read_job(&template_path).unwrap().unwrap();
        fs::remove_file(&template_path).unwrap();
        for index in 0..1000 {
            let id = format!("bulk-{index}");
            let mut row = template.clone();
            row["jobId"] = json!(id);
            row["createdAt"] = json!((index + 1) as f64);
            row["updatedAt"] = json!((index + 1) as f64);
            // The test writes the file directly: this case measures read cost,
            // not the enqueue path (which has its own tests).
            fs::write(job_path(root.path(), &id).unwrap(), serde_json::to_vec(&row).unwrap()).unwrap();
        }
        let full = read_delta(root.path(), None).unwrap();
        assert!(full.full);
        assert_eq!(full.jobs.len(), 1000, "a full read must return every record");
        let known: HashMap<String, f64> = full
            .jobs
            .iter()
            .map(|row| (row["jobId"].as_str().unwrap().to_string(), row["updatedAt"].as_f64().unwrap()))
            .collect();
        let parsed = journal_parse_count(root.path());
        let delta = read_delta(root.path(), Some(&known)).unwrap();
        assert!(!delta.full);
        assert!(delta.removed.is_empty());
        assert_eq!(delta.jobs.len(), 0, "an unchanged history must not be resent");
        assert_eq!(
            journal_parse_count(root.path()),
            parsed,
            "1000 unchanged job files must not be re-parsed by a delta poll"
        );
    }

    #[test]
    fn journal_delta_reports_only_changed_records_and_removed_ids() {
        let root = tempfile::tempdir().unwrap();
        for id in ["delta_a", "delta_b"] {
            enqueue_manual(root.path(), "run_operation", id, &json!({"operation": "fixture"})).unwrap();
        }
        let full = read_delta(root.path(), None).unwrap();
        assert!(full.full, "no known state must return the authoritative snapshot");
        assert_eq!(full.jobs.len(), 2);
        assert!(full.removed.is_empty());
        let mut known: HashMap<String, f64> = full
            .jobs
            .iter()
            .map(|job| (job["jobId"].as_str().unwrap().to_string(), job["updatedAt"].as_f64().unwrap()))
            .collect();

        let quiet = read_delta(root.path(), Some(&known)).unwrap();
        assert!(!quiet.full);
        assert!(quiet.jobs.is_empty(), "an unchanged catalog must send no records");
        assert!(quiet.removed.is_empty());

        let path = job_path(root.path(), "delta_a").unwrap();
        let mut row = read_job(&path).unwrap().unwrap();
        row["message"] = json!("changed");
        row["updatedAt"] = json!(row["updatedAt"].as_f64().unwrap() + 5.0);
        storage::write_json_file(&path, &row).unwrap();
        let changed = read_delta(root.path(), Some(&known)).unwrap();
        assert_eq!(changed.jobs.len(), 1);
        assert_eq!(changed.jobs[0]["jobId"], "delta_a");

        known = read_delta(root.path(), None)
            .unwrap()
            .jobs
            .iter()
            .map(|job| (job["jobId"].as_str().unwrap().to_string(), job["updatedAt"].as_f64().unwrap()))
            .collect();
        fs::remove_file(job_path(root.path(), "delta_b").unwrap()).unwrap();
        let removed = read_delta(root.path(), Some(&known)).unwrap();
        assert!(removed.jobs.is_empty());
        assert_eq!(removed.removed, vec!["delta_b".to_string()]);
    }

    #[test]
    fn a_long_history_delta_never_loses_or_duplicates_records() {
        let root = tempfile::tempdir().unwrap();
        let total = 240usize;
        for index in 0..total {
            enqueue_manual(root.path(), "run_operation", &format!("history_{index:03}"), &json!({"operation": "fixture"})).unwrap();
        }
        let full = read_delta(root.path(), None).unwrap();
        assert!(full.full);
        assert_eq!(full.jobs.len(), total);
        assert!(full.removed.is_empty());
        let known: HashMap<String, f64> = full
            .jobs
            .iter()
            .map(|job| (job["jobId"].as_str().unwrap().to_string(), job["updatedAt"].as_f64().unwrap()))
            .collect();

        // An idle poll moves zero records.
        let idle = read_delta(root.path(), Some(&known)).unwrap();
        assert!(idle.jobs.is_empty());
        assert!(idle.removed.is_empty());

        // Every fourth record changes; exactly those come back, once each.
        for index in (0..total).step_by(4) {
            let id = format!("history_{index:03}");
            let path = job_path(root.path(), &id).unwrap();
            let mut row = read_job(&path).unwrap().unwrap();
            row["message"] = json!("changed");
            row["updatedAt"] = json!(row["updatedAt"].as_f64().unwrap() + 3.0);
            storage::write_json_file(&path, &row).unwrap();
        }
        let changed = read_delta(root.path(), Some(&known)).unwrap();
        assert_eq!(changed.jobs.len(), total / 4);
        let mut ids: Vec<&str> = changed.jobs.iter().map(|job| job["jobId"].as_str().unwrap()).collect();
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), total / 4, "no changed record may repeat");

        // Deleting a tenth of the history reports exactly those ids and keeps the rest.
        let mut current: HashMap<String, f64> = known.clone();
        for job in &changed.jobs {
            current.insert(job["jobId"].as_str().unwrap().to_string(), job["updatedAt"].as_f64().unwrap());
        }
        let mut deleted = Vec::new();
        for index in (0..total).step_by(10) {
            let id = format!("history_{index:03}");
            fs::remove_file(job_path(root.path(), &id).unwrap()).unwrap();
            deleted.push(id);
        }
        let removed = read_delta(root.path(), Some(&current)).unwrap();
        assert!(removed.jobs.is_empty());
        let mut removed_ids = removed.removed;
        removed_ids.sort();
        deleted.sort();
        assert_eq!(removed_ids, deleted);
        assert_eq!(read_delta(root.path(), None).unwrap().jobs.len(), total - deleted.len());
    }


    #[test]
    fn durable_manual_enqueue_is_ownerless_and_idempotent_until_admission() {
        let root = tempfile::tempdir().unwrap();
        let payload = json!({"operation": "fixture", "outputDir": "out"});
        enqueue_manual(root.path(), "run_operation", "queued_once", &payload).unwrap();
        let path = job_path(root.path(), "queued_once").unwrap();
        let bytes = fs::read(&path).unwrap();
        enqueue_manual(root.path(), "run_operation", "queued_once", &payload).unwrap();
        assert_eq!(fs::read(&path).unwrap(), bytes);
        let row = read_job(&path).unwrap().unwrap();
        assert_eq!(row["status"], "queued"); assert!(row["owner"].is_null()); assert!(row["startedAt"].is_null());
        assert!(enqueue_manual(root.path(), "run_operation", "queued_once", &json!({"operation": "another"})).is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        let ClaimAttempt::Ready(journal, resolved, id) = try_claim_pending(root.path(), "run_operation", "queued_once").unwrap() else { panic!("not admitted"); };
        assert_eq!(resolved, payload); assert_eq!(id, "queued_once");
        assert!(matches!(try_claim_pending(root.path(), "run_operation", "queued_once").unwrap(), ClaimAttempt::Finished));
        assert_eq!(read_job(&path).unwrap().unwrap()["status"], "running");
        journal.lock().unwrap().exited(true, false, "finished").unwrap();
    }

    #[test]
    fn queued_cancel_never_claims_a_worker_or_a_slot() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "cancel_before_run", &json!({"operation": "fixture"})).unwrap();
        assert!(request_cancel(root.path(), "cancel_before_run").unwrap());
        assert!(matches!(try_claim_pending(root.path(), "run_operation", "cancel_before_run").unwrap(), ClaimAttempt::Finished));
        let row = read_job(&job_path(root.path(), "cancel_before_run").unwrap()).unwrap().unwrap();
        assert_eq!(row["status"], "cancelled"); assert!(row["owner"].is_null()); assert!(row["startedAt"].is_null());
        assert!(!root.path().join("agent-jobs/.quota").exists());
    }

    #[test]
    fn queued_agent_cancel_is_terminal_without_creating_a_cancel_file() {
        let root = tempfile::tempdir().unwrap();
        enqueue_manual(root.path(), "run_operation", "agent_queued", &json!({"operation": "fixture"})).unwrap();
        let path = job_path(root.path(), "agent_queued").unwrap();
        let mut row = read_job(&path).unwrap().unwrap();
        row["source"] = json!("agent");
        storage::write_json_file(&path, &row).unwrap();
        assert!(request_cancel(root.path(), "agent_queued").unwrap());
        let cancelled = read_job(&path).unwrap().unwrap();
        assert_eq!(cancelled["status"], "cancelled");
        assert!(cancelled["owner"].is_null());
        assert!(cancelled["startedAt"].is_null());
        assert!(!root.path().join("agent-jobs/agent_queued.cancel").exists());
    }

    #[test]
    fn terminal_result_keeps_its_slot_until_the_supervisor_is_reaped_and_dropped() {
        let root = tempfile::tempdir().unwrap();
        for id in ["first", "second"] { enqueue_manual(root.path(), "run_operation", id, &json!({"operation": "fixture"})).unwrap(); }
        let ClaimAttempt::Ready(first, _, _) = try_claim_pending(root.path(), "run_operation", "first").unwrap() else { panic!("first not admitted"); };
        assert!(matches!(try_claim_pending(root.path(), "run_operation", "second").unwrap(), ClaimAttempt::Waiting));
        let before = read_job(&job_path(root.path(), "second").unwrap()).unwrap().unwrap();
        assert_eq!(before["status"], "queued"); assert!(before["owner"].is_null()); assert!(before["startedAt"].is_null());
        first.lock().unwrap().observe(&event("task.result", Some("first"), json!({"outputs": [{"path": "result.wav"}]}))).unwrap();
        assert!(matches!(try_claim_pending(root.path(), "run_operation", "second").unwrap(), ClaimAttempt::Waiting));
        drop(first);
        let ClaimAttempt::Ready(second, _, _) = try_claim_pending(root.path(), "run_operation", "second").unwrap() else { panic!("released capacity not reused"); };
        second.lock().unwrap().fail_all("spawn failed fixture").unwrap();
        drop(second);
        let _catalog = catalog_lock(root.path()).unwrap();
        assert!(super::super::execution_quota::try_acquire(root.path()).unwrap().is_some());
    }

    fn event(kind: &str, id: Option<&str>, payload: Value) -> WorkerEnvelope {
        WorkerEnvelope {
            event_type: kind.into(),
            task_id: id.map(str::to_string),
            request_id: None,
            timestamp: None,
            payload,
        }
    }
    fn snapshot(root: &Path, id: &str) -> Value {
        read_job(&job_path(root, id).unwrap()).unwrap().unwrap()
    }
    fn started(root: &Path, id: &str) -> JournalHandle {
        WorkerJournal::start(root, "audio_tools", id, &json!({"requestId": id, "operation": "media_video", "inputs": ["input.mp4"], "outputDir": "outputs", "params": {"crf": 17}})).unwrap()
    }

    #[test]
    fn queued_running_and_result_persist_without_a_frontend_listener() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "video_1");
        let queued = snapshot(root.path(), "video_1");
        assert_eq!(queued["status"], "queued");
        assert_eq!(queued["source"], "manual");
        assert_eq!(queued["cancelTaskId"], "video_1");
        assert_eq!(queued["payload"]["params"]["crf"], 17);
        assert!(queued["createdAt"].as_f64().unwrap() > 1_000_000_000.0);
        journal.lock().unwrap().running(123).unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "running");
        let result = json!({"outputs": [{"path": "outputs/output.mp4", "metadata": {"frames": 90}}], "completed": 1, "failed": [], "custom": {"retain": true}});
        journal
            .lock()
            .unwrap()
            .observe(&event("task.result", None, result.clone()))
            .unwrap();
        let stored = list(root.path()).unwrap()[0].clone();
        assert_eq!(stored["status"], "completed");
        assert_eq!(stored["result"], result);
        assert!(stored["finishedAt"].as_f64().unwrap() >= queued["createdAt"].as_f64().unwrap());
        assert!(!root.path().join("agent-jobs/index.json").exists());
    }

    #[test]
    fn preview_publication_observes_only_the_accepted_durable_result() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "preview_job");
        let mut journal = journal.lock().unwrap();
        let mut publications = Vec::new();
        let mut publish = |row: &Value| {
            let persisted = snapshot(root.path(), "preview_job");
            assert_eq!(row["result"], persisted["result"]);
            publications.push(row["result"]["outputs"][0]["path"].as_str().unwrap().to_string());
        };
        for (kind, id, path) in [
            ("task.progress", "other_job", "unowned.mp4"),
            ("task.progress", "preview_job", "partial.mp4"),
            ("task.result", "preview_job", "final.mp4"),
            ("task.result", "preview_job", "late.mp4"),
        ] {
            journal.observe_with_result(&event(kind, Some(id), json!({"outputs": [{"path": path}]})), &mut publish).unwrap();
        }
        assert_eq!(publications, ["partial.mp4", "final.mp4"]);
    }

    #[test]
    fn native_runtime_nodes_and_structured_logs_survive_without_frontend_snapshots() {
        let root = tempfile::tempdir().unwrap(); let journal = started(root.path(), "runtime_1");
        let mut writer = journal.lock().unwrap(); writer.running(41).unwrap();
        writer.observe(&event("task.stage", Some("runtime_1"), json!({"stage": "loading_model", "progress": 12, "message": "loading"}))).unwrap();
        writer.observe(&event("workflow_execution", Some("runtime_1"), json!({"version": 1, "event": "node_started", "nodeId": "node-a", "nodeProgress": 0.25, "message": "active"}))).unwrap();
        writer.observe(&event("task.progress", Some("runtime_1"), json!({"stage": "separating", "progress": 47, "done": 3, "total": 10, "unit": "seconds"}))).unwrap();
        writer.observe(&event("worker_log", Some("runtime_1"), json!({"schemaVersion": 1, "id": "log-1", "level": "info", "message": "native log", "nodeId": "node-a"}))).unwrap();
        writer.observe(&event("error", Some("runtime_1"), json!({"recoverable": true, "message": "retrying decoder"}))).unwrap();
        assert_eq!(snapshot(root.path(), "runtime_1")["status"], "running");
        writer.observe(&event("task.result", Some("runtime_1"), json!({"outputs": [{"path": "out.wav"}]}))).unwrap();
        let row = snapshot(root.path(), "runtime_1");
        assert_eq!(row["status"], "completed"); assert!(row["error"].is_null());
        assert_eq!(row["runtime"]["progress"]["progress"], 47);
        assert_eq!(row["runtime"]["nodes"]["node-a"]["progress"], 25.0);
        assert_eq!(row["runtime"]["order"], json!(["node-a"]));
        assert_eq!(row["logs"][0]["nodeId"], "node-a"); assert_eq!(row["logs"].as_array().unwrap().len(), 2);
        assert!(row["logs"][0]["timestamp"].is_string());
    }

    #[test]
    fn errors_cancellation_and_late_events_preserve_partial_outputs_and_terminal_state() {
        for cancelled in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let journal = started(root.path(), "partial_1");
            {
                let mut job = journal.lock().unwrap();
                job.running(123).unwrap();
                job.observe(&event(
                    "task.progress",
                    None,
                    json!({"fraction": 0.5, "outputs": [{"path": "partial.png"}]}),
                ))
                .unwrap();
            }
            if cancelled {
                assert!(cancel(root.path(), "partial_1").unwrap());
            } else {
                journal
                    .lock()
                    .unwrap()
                    .observe(&event(
                        "error",
                        None,
                        json!({"code": "DECODE_FAILED", "message": "bad next input"}),
                    ))
                    .unwrap();
            }
            journal
                .lock()
                .unwrap()
                .observe(&event(
                    "task.result",
                    None,
                    json!({"outputs": [{"path": "late.png"}]}),
                ))
                .unwrap();
            journal.lock().unwrap().exited(true, false, "").unwrap();
            let stored = snapshot(root.path(), "partial_1");
            assert_eq!(
                stored["status"],
                if cancelled { "cancelled" } else { "failed" }
            );
            assert_eq!(stored["result"]["outputs"][0]["path"], "partial.png");
        }
    }

    #[test]
    fn operation_task_result_keeps_the_full_worker_payload() {
        let root = tempfile::tempdir().unwrap();
        let journal = WorkerJournal::start(
            root.path(),
            "run_operation",
            "op_1",
            &json!({"operation": "tool", "inputs": {"in": "input.wav"}}),
        )
        .unwrap();
        let result = json!({"outputs": {"out": {"kind": "audio_file", "path": "out.wav"}}, "operation": "tool"});
        journal
            .lock()
            .unwrap()
            .observe(&event("task.result", None, result.clone()))
            .unwrap();
        assert_eq!(snapshot(root.path(), "op_1")["result"], result);
        let journal = started(root.path(), "partial_2");
        let result = json!({"outputs": [{"path": "ok.png"}], "completed": 1, "failed": [{"path": "bad.png", "error": "bad decode"}]});
        journal
            .lock()
            .unwrap()
            .observe(&event("task.result", None, result.clone()))
            .unwrap();
        let stored = snapshot(root.path(), "partial_2");
        assert_eq!(stored["status"], "failed");
        assert_eq!(stored["result"], result);
    }

    #[test]
    fn batch_children_keep_individual_inputs_and_do_not_finish_each_other() {
        let root = tempfile::tempdir().unwrap();
        let payload = json!({"taskId": "batch_1", "model": "model", "inferenceParams": {"batch_size": 3}, "tasks": [{"taskId": "a_1", "input": "a.wav"}, {"taskId": "b_1", "input": "b.wav"}]});
        let journal = WorkerJournal::start(root.path(), "infer", "batch_1", &payload).unwrap();
        journal.lock().unwrap().running(123).unwrap();
        assert_eq!(snapshot(root.path(), "b_1")["payload"]["input"], "b.wav");
        assert_eq!(
            snapshot(root.path(), "batch_1")["payload"]["tasks"],
            payload["tasks"]
        );
        assert_eq!(
            snapshot(root.path(), "b_1")["payload"]["tasks"],
            json!([payload["tasks"][1]])
        );
        assert_eq!(
            snapshot(root.path(), "b_1")["payload"]["batchTaskId"],
            "batch_1"
        );
        assert_eq!(
            snapshot(root.path(), "b_1")["payload"]["inferenceParams"]["batch_size"],
            3
        );
        journal
            .lock()
            .unwrap()
            .observe(&event(
                "task.result",
                Some("a_1"),
                json!({"outputs": [{"path": "a-out.wav"}]}),
            ))
            .unwrap();
        assert_eq!(snapshot(root.path(), "b_1")["status"], "running");
        journal
            .lock()
            .unwrap()
            .observe(&event(
                "task.result",
                Some("b_1"),
                json!({"outputs": [{"path": "b-out.wav"}]}),
            ))
            .unwrap();
        journal.lock().unwrap().exited(true, false, "").unwrap();
        let stored = snapshot(root.path(), "batch_1");
        assert_eq!(stored["status"], "completed");
        assert_eq!(stored["result"]["outputs"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn spawn_failure_and_successful_exit_without_result_are_failed_not_running() {
        let root = tempfile::tempdir().unwrap();
        let failed = started(root.path(), "spawn_1");
        failed
            .lock()
            .unwrap()
            .fail_all("cannot start interpreter")
            .unwrap();
        assert_eq!(snapshot(root.path(), "spawn_1")["status"], "failed");
        let empty = started(root.path(), "empty_1");
        empty.lock().unwrap().exited(true, false, "").unwrap();
        assert_eq!(
            snapshot(root.path(), "empty_1")["error"],
            "worker exited without a result"
        );
    }

    #[test]
    fn invalid_legacy_rows_block_recovery_without_rewriting_any_record() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "live_1");
        journal.lock().unwrap().running(123).unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "running");
        drop(journal);
        let path = job_path(root.path(), "live_1").unwrap();
        let before = fs::read(&path).unwrap();
        let legacy = root.path().join("agent-jobs/legacy_1.json");
        fs::write(&legacy, b"{\"jobId\":\"legacy_1\",\"status\":\"running\"}").unwrap();
        assert!(list(root.path()).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        fs::remove_file(legacy).unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "interrupted");
    }

    #[test]
    fn live_and_dead_operation_kernel_owners_use_the_same_lease_without_pid_guesses() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "kernel_1");
        journal.lock().unwrap().running(42).unwrap();
        let mut row = snapshot(root.path(), "kernel_1");
        row["source"] = json!("agent");
        row["owner"] = json!({"kind": "operation-kernel", "kernelId": "fixture-owner", "pid": std::process::id()});
        storage::write_json_file(&job_path(root.path(), "kernel_1").unwrap(), &row).unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "running");
        drop(journal);
        let interrupted = list(root.path()).unwrap();
        assert_eq!(interrupted[0]["status"], "interrupted");
        assert!(interrupted[0]["finishedAt"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn orphan_recovery_waits_for_the_supervisor_after_primary_owner_release() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "kernel.reaping");
        journal.lock().unwrap().running(42).unwrap();
        let fence = FileLock::acquire_plain(&owner_reap_path(root.path(), "kernel.reaping"), Duration::ZERO).unwrap();
        drop(journal);

        assert_eq!(list(root.path()).unwrap()[0]["status"], "running");
        assert!(owner_lock(root.path(), "kernel.reaping").is_err());
        // Dotted IDs have distinct primary locks and cannot alias the fence.
        assert!(owner_lock(root.path(), "kernel.reaping.reap").is_ok());
        drop(fence);
        assert_eq!(list(root.path()).unwrap()[0]["status"], "interrupted");
    }

    #[test]
    fn legacy_index_is_ignored_and_preserved_without_a_fallback() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "fresh_1");
        fs::write(root.path().join("agent-jobs/index.json"), b"not-json").unwrap();
        let rows = list(root.path()).unwrap();
        assert_eq!(rows.len(), 1, "index.json must not be treated as a job");
        assert_eq!(rows[0]["jobId"], "fresh_1");
        journal.lock().unwrap().observe(&event("task.result", None, json!({"outputs": [{"path": "out.png"}]}))).unwrap();
        assert_eq!(snapshot(root.path(), "fresh_1")["status"], "completed");
        assert_eq!(fs::read(root.path().join("agent-jobs/index.json")).unwrap(), b"not-json");
    }

    #[test]
    fn progress_writes_are_throttled_but_terminal_write_is_not() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "progress_1");
        let before = fs::read(job_path(root.path(), "progress_1").unwrap()).unwrap();
        for count in 0..20 {
            journal
                .lock()
                .unwrap()
                .observe(&event(
                    "task.progress",
                    None,
                    json!({"done": count, "total": 20}),
                ))
                .unwrap();
        }
        assert_eq!(
            fs::read(job_path(root.path(), "progress_1").unwrap()).unwrap(),
            before
        );
        journal
            .lock()
            .unwrap()
            .observe(&event(
                "task.result",
                None,
                json!({"outputs": [{"path": "out.mp4"}]}),
            ))
            .unwrap();
        assert_eq!(snapshot(root.path(), "progress_1")["status"], "completed");
    }

    #[test]
    fn empty_media_results_are_failed_but_keep_the_complete_payload() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "empty_media_1");
        let result = json!({"outputs": [], "completed": 0, "failed": [], "custom": {"kept": true}});
        journal
            .lock()
            .unwrap()
            .observe(&event("task.result", None, result.clone()))
            .unwrap();
        let stored = snapshot(root.path(), "empty_media_1");
        assert_eq!(stored["status"], "failed");
        assert_eq!(stored["result"], result);
    }

    #[test]
    fn listing_returns_all_history_without_an_implicit_terminal_limit() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "old_active_1");
        let template = snapshot(root.path(), "old_active_1");
        for index in 0..105 {
            let mut row = template.clone();
            row["jobId"] = json!(format!("finished_{index}"));
            row["cancelTaskId"] = row["jobId"].clone();
            row["status"] = json!("completed"); row["fraction"] = json!(1.0);
            row["createdAt"] = json!(1_000_000_000.0 + index as f64); row["finishedAt"] = json!(now());
            storage::write_json_file(&job_path(root.path(), row["jobId"].as_str().unwrap()).unwrap(), &row).unwrap();
        }
        let jobs = list(root.path()).unwrap();
        assert_eq!(jobs.len(), 106);
        assert!(jobs.iter().any(|job| job["jobId"] == "old_active_1"));
        assert!(jobs.iter().any(|job| job["jobId"] == "finished_0"));
        drop(journal);
    }

    #[test]
    fn job_ids_cannot_escape_the_directory_or_replace_the_index_and_media_filter_excludes_management(
    ) {
        let root = tempfile::tempdir().unwrap();
        for id in [
            "",
            "../outside",
            "a/b",
            "C:\\out",
            "index",
            "INDEX",
            "CON",
            "NUL.a",
            "LPT1",
        ] {
            assert!(
                WorkerJournal::start(root.path(), "infer", id, &json!({})).is_err(),
                "{id}"
            );
        }
        for command in [
            "infer",
            "infer_workflow",
            "audio_tools",
            "media_tool",
            "run_operation",
        ] {
            assert!(tracks(command));
        }
        for command in [
            "model_download",
            "install_runtime",
            "update_runtime_core",
            "manage_plugin",
            "manage_optional_package",
        ] {
            assert!(!tracks(command));
        }
    }

    #[test]
    fn owned_journal_child() {
        let Some(root) = std::env::var_os("MEDIAFORGE_TEST_JOURNAL_ROOT") else {
            return;
        };
        let root = PathBuf::from(root);
        let journal = started(&root, "process_1");
        journal.lock().unwrap().running(std::process::id()).unwrap();
        fs::write(root.join("ready"), b"owned").unwrap();
        let mut line = String::new();
        let _ = std::io::stdin().read_line(&mut line);
    }

    #[test]
    fn owner_lease_excludes_a_real_second_process_until_the_owner_is_killed() {
        use std::process::Stdio;
        let root = tempfile::tempdir().unwrap();
        let mut command = crate::process_command::hidden_command(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "python::task_journal::tests::owned_journal_child",
            ])
            .env("MEDIAFORGE_TEST_JOURNAL_ROOT", root.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let mut child = command.spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(8);
        while !root.path().join("ready").exists() && Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let ready = root.path().join("ready").exists();
        let live = list(root.path());
        let duplicate = WorkerJournal::start(root.path(), "infer", "process_1", &json!({}));
        let _ = child.kill();
        child.wait().unwrap();
        assert!(ready);
        assert_eq!(live.unwrap()[0]["status"], "running");
        assert!(duplicate.is_err());
        assert_eq!(
            list(root.path()).unwrap()[0]["status"],
            "interrupted"
        );
    }

    #[test]
    fn python_kernel_cannot_recover_a_live_rust_owner_but_can_recover_after_release() {
        // Isolated current bootstrap, stdlib only; no user runtime or models.
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "interop_1");
        journal.lock().unwrap().running(123).unwrap();
        let python_source = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("python");
        let script = "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[2]); from core.operation_kernel import OperationKernel; root=Path(sys.argv[1]); kernel=OperationKernel(root); value=json.loads((root/'agent-jobs/interop_1.json').read_text(encoding='utf-8')); assert value['status']==sys.argv[3], value['status']";
        let run = |expected| {
            crate::process_command::isolated_test_python(root.path())
                .args(["-B", "-S", "-c", script])
                .arg(root.path())
                .arg(&python_source)
                .arg(expected)
                .output()
                .unwrap()
        };
        let live = run("running");
        assert!(
            live.status.success(),
            "{}",
            String::from_utf8_lossy(&live.stderr)
        );
        drop(journal);
        let dead = run("interrupted");
        assert!(
            dead.status.success(),
            "{}",
            String::from_utf8_lossy(&dead.stderr)
        );
    }
}
