//! Cross-process compute-worker admission. The job files remain the only queue.
use super::task_journal::FileLock;
use crate::error::{AppError, AppResult};
use serde::Deserialize;
use std::path::Path;
use std::sync::OnceLock;
use std::time::Duration;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Contract {
    schema_version: u8,
    default_limit: usize,
    max_slots: usize,
    setting_key: String,
    commands: Vec<String>,
}

fn contract() -> AppResult<&'static Contract> {
    static CONTRACT: OnceLock<Result<Contract, String>> = OnceLock::new();
    CONTRACT
        .get_or_init(|| {
            let value: Contract = serde_json::from_str(include_str!(
                "../../../manifest/execution-quota.json"
            ))
            .map_err(|error| error.to_string())?;
            if value.schema_version != 1 || value.max_slots == 0 || value.max_slots > 32
                || !(1..=value.max_slots).contains(&value.default_limit)
                || value.setting_key.is_empty() || value.commands.is_empty()
            {
                return Err("unsupported execution quota contract".into());
            }
            Ok(value)
        })
        .as_ref()
        .map_err(|error| AppError::Worker(error.clone()))
}

pub(super) fn applies(command: &str) -> AppResult<bool> {
    Ok(contract()?.commands.iter().any(|item| item == command))
}

pub(super) fn read_limit(root: &Path) -> AppResult<usize> {
    let contract = contract()?;
    let path = root.join("settings/app.json");
    let metadata = match std::fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(contract.default_limit),
        Err(error) => return Err(error.into()),
    };
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 { return Err(AppError::Worker("quota settings reparse point is not allowed".into())); }
    }
    if metadata.file_type().is_symlink() { return Err(AppError::Worker("quota settings symlink is not allowed".into())); }
    let text = std::fs::read_to_string(&path)?;
    let settings = super::job_schema::parse(&text)?;
    let object = settings.as_object().ok_or_else(|| AppError::Worker(
        "invalid application settings for execution quota; original file preserved".into(),
    ))?;
    match object.get(&contract.setting_key) {
        None => Ok(contract.default_limit),
        Some(value) => value.as_u64()
            .filter(|limit| *limit >= 1 && *limit <= contract.max_slots as u64)
            .map(|limit| limit as usize)
            .ok_or_else(|| AppError::Worker("invalid compute-worker limit; original file preserved".into())),
    }
}

pub(super) struct QuotaLease {
    _slot: FileLock,
}

fn busy(error: &std::io::Error) -> bool {
    #[cfg(windows)]
    { matches!(error.raw_os_error(), Some(32 | 33)) }
    #[cfg(not(windows))]
    { error.kind() == std::io::ErrorKind::WouldBlock }
}

/// The caller holds the job catalog lock. Probe *all* fixed slots so lowering
/// the limit cannot admit a new worker while higher-numbered slots remain live.
pub(super) fn try_acquire(root: &Path) -> AppResult<Option<QuotaLease>> {
    let limit = read_limit(root)?;
    let mut free = Vec::new();
    let mut occupied = 0;
    for index in 0..contract()?.max_slots {
        let path = root.join("agent-jobs/.quota").join(format!("global-{index}.lock"));
        match FileLock::acquire(&path, Duration::ZERO) {
            Ok(slot) => free.push(slot),
            Err(AppError::Io(error)) if busy(&error) => occupied += 1,
            Err(error) => return Err(error),
        }
    }
    if occupied >= limit { return Ok(None); }
    Ok(free.into_iter().next().map(|slot| QuotaLease { _slot: slot }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn set_limit(root: &Path, limit: usize) {
        std::fs::create_dir_all(root.join("settings")).unwrap();
        std::fs::write(root.join("settings/app.json"), json!({"maxConcurrentSeparations": limit}).to_string()).unwrap();
    }

    fn take(root: &Path) -> Option<QuotaLease> {
        let _catalog = super::super::task_journal::catalog_lock(root).unwrap();
        try_acquire(root).unwrap()
    }

    #[test]
    fn leases_bound_workers_and_released_capacity_is_reusable() {
        let root = tempfile::tempdir().unwrap();
        set_limit(root.path(), 2);
        let first = take(root.path()).unwrap();
        let second = take(root.path()).unwrap();
        assert!(take(root.path()).is_none());
        drop(first);
        let replacement = take(root.path()).unwrap();
        assert!(take(root.path()).is_none());
        drop((replacement, second));
        assert!(take(root.path()).is_some());
    }

    #[test]
    fn lowering_limit_counts_live_high_slots_until_they_are_released() {
        let root = tempfile::tempdir().unwrap();
        set_limit(root.path(), 4);
        let mut leases = (0..4).map(|_| take(root.path()).unwrap()).collect::<Vec<_>>();
        let last = leases.pop().unwrap();
        drop(leases);
        set_limit(root.path(), 1);
        assert!(take(root.path()).is_none());
        drop(last);
        assert!(take(root.path()).is_some());
    }

    #[test]
    fn settings_are_current_integers_and_bad_files_are_preserved() {
        let root = tempfile::tempdir().unwrap();
        assert_eq!(read_limit(root.path()).unwrap(), 1);
        std::fs::create_dir_all(root.path().join("settings")).unwrap();
        let path = root.path().join("settings/app.json");
        for text in ["{", "[]", "{\"maxConcurrentSeparations\":0}", "{\"maxConcurrentSeparations\":5}",
            "{\"maxConcurrentSeparations\":true}", "{\"maxConcurrentSeparations\":2.0}",
            "{\"maxConcurrentSeparations\":\"2\"}", "{\"maxConcurrentSeparations\":1,\"maxConcurrentSeparations\":2}"] {
            std::fs::write(&path, text).unwrap();
            assert!(read_limit(root.path()).is_err(), "{text}");
            assert_eq!(std::fs::read_to_string(&path).unwrap(), text);
        }
        for limit in 1..=4 { set_limit(root.path(), limit); assert_eq!(read_limit(root.path()).unwrap(), limit); }
        assert!(applies("infer").unwrap());
        assert!(applies("run_operation").unwrap());
        assert!(!applies("model_download").unwrap());
        assert!(!applies("health").unwrap());
    }

    #[test]
    fn shared_quota_settings_contract() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!("../../../tests/fixtures/jobs/quota-contract.json")).unwrap();
        for case in fixture["settingsCases"].as_array().unwrap() {
            let root = tempfile::tempdir().unwrap();
            let path = root.path().join("settings/app.json");
            if let Some(raw) = case["raw"].as_str() {
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(&path, raw).unwrap();
            }
            let outcome = read_limit(root.path());
            if case["invalid"] == true { assert!(outcome.is_err(), "{}", case["name"]); }
            else { assert_eq!(outcome.unwrap() as u64, case["expected"].as_u64().unwrap(), "{}", case["name"]); }
            if let Some(raw) = case["raw"].as_str() { assert_eq!(std::fs::read_to_string(path).unwrap(), raw); }
        }
    }

    #[test]
    fn python_and_rust_share_slots_and_process_exit_releases_them() {
        use std::io::{BufRead, BufReader, Write};
        use std::process::Stdio;
        let root = tempfile::tempdir().unwrap();
        let project = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let script = r#"import sys
assert sys.version_info[:2] == (3, 12), 'quota tests require actual Python 3.12'
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core import job_quota, job_store
root = Path(sys.argv[2])
with job_store.catalog_lock(root / 'agent-jobs'):
    lease = job_quota.try_acquire(root / 'agent-jobs', job_quota.read_limit(root))
print('acquired' if lease is not None else 'blocked', flush=True)
if lease is not None:
    if sys.argv[3] == 'hold': sys.stdin.buffer.read(1)
    lease.release()
"#;
        let command = |mode: &str| {
            let mut command = crate::process_command::isolated_test_python(root.path());
            command.args(["-I", "-B", "-c", script]).arg(project.join("python")).arg(root.path()).arg(mode)
                .current_dir(root.path()).env("HOME", root.path()).env("USERPROFILE", root.path())
                .env("APPDATA", root.path()).env("LOCALAPPDATA", root.path()).env("TEMP", root.path()).env("TMP", root.path());
            command
        };
        // A real Rust lease excludes Python, then Python can take the same slot.
        let rust_lease = take(root.path()).unwrap();
        let output = command("try").output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "blocked");
        drop(rust_lease);
        let output = command("try").output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "acquired");

        // A Python-held lease excludes Rust. Kill only this explicitly owned
        // fixture process; the OS must release its capacity without deleting locks.
        struct ReapedChild(std::process::Child);
        impl Drop for ReapedChild { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }
        let mut child = ReapedChild(command("hold").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap());
        let stdout = child.0.stdout.take().unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || { let mut line = String::new(); let outcome = BufReader::new(stdout).read_line(&mut line).map(|_| line); let _ = sender.send(outcome); });
        assert_eq!(receiver.recv_timeout(Duration::from_secs(15)).unwrap().unwrap().trim(), "acquired");
        assert!(take(root.path()).is_none());
        child.0.stdin.as_mut().unwrap().flush().unwrap();
        child.0.kill().unwrap();
        child.0.wait().unwrap();
        reader.join().unwrap();
        assert!(take(root.path()).is_some());
        assert!(root.path().join("agent-jobs/.quota/global-0.lock").is_file());
    }
}
