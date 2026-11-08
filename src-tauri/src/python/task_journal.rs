//! Durable manual media jobs. Individual job files are the shared source of truth.
use crate::error::{AppError, AppResult};
use crate::python::protocol::WorkerEnvelope;
use crate::storage;
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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
    FileLock::acquire(&root.join("agent-jobs/.owners").join(format!("{id}.lock")), Duration::ZERO)
}

pub(super) fn read_all_unlocked(root: &Path) -> AppResult<Vec<Value>> {
    let directory = root.join("agent-jobs");
    if !directory.is_dir() { return Ok(Vec::new()); }
    let mut jobs = Vec::new();
    for entry in fs::read_dir(&directory)? {
        let path = entry?.path();
        if path.extension().and_then(|s| s.to_str()) == Some("removing") {
            return Err(AppError::Worker("incomplete job removal transaction; original files preserved".into()));
        }
        if path.extension().and_then(|s| s.to_str()) != Some("json") { continue; }
        let id = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        if !valid_id(id) { return Err(AppError::Worker("unsupported job filename; original file preserved".into())); }
        let metadata = fs::symlink_metadata(&path)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { return Err(AppError::Worker("job reparse point is not allowed".into())); }
        }
        if metadata.file_type().is_symlink() { return Err(AppError::Worker("job symlink is not allowed".into())); }
        // Progress writers intentionally do not take the catalog lock. Coordinate
        // the actual open/read with their per-job atomic replacement on Windows.
        let _read_guard = write_lock(root, id)?;
        if let Some(value) = read_job(&path)? {
            if value["jobId"] != id { return Err(AppError::Worker("job id does not match its filename".into())); }
            jobs.push(value);
        }
    }
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
                let owner = FileLock::acquire(
                    &root.join("agent-jobs/.owners").join(format!("{id}.lock")),
                    Duration::ZERO,
                )
                .map_err(|error| {
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
        let kind = event.event_type.as_str();
        if !matches!(
            kind,
            "task.started"
                | "task.stage"
                | "task.progress"
                | "workflow_execution"
                | "task.result"
                | "editor_mix_exported"
                | "error"
                | "task.cancelled"
                | "task.log"
                | "worker_log"
        ) {
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
            } else if kind == "task.cancelled" {
                self.finish_job(index, "cancelled", message, Some(&event.payload))?;
            } else if matches!(
                kind,
                "task.result" | "editor_mix_exported"
            ) {
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
                self.persist(index, event.payload.get("outputs").is_some())?;
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
    if row["source"] != "manual" || row["status"] != "queued" || !row["owner"].is_null() { return Ok(false); }
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

/// A typed owner is live only while its shared lifetime lease remains held.
fn recover(root: &Path, id: &str, value: Value) -> AppResult<Value> {
    if value["owner"].is_null() || terminal(&value) {
        return Ok(value);
    }
    let Ok(_owner) = FileLock::acquire(
        &root.join("agent-jobs/.owners").join(format!("{id}.lock")),
        Duration::ZERO,
    ) else {
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


#[cfg(test)]
mod tests {
    use super::*;

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
    fn unsupported_index_is_reported_and_preserved_without_a_fallback() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "fresh_1");
        fs::write(root.path().join("agent-jobs/index.json"), b"not-json").unwrap();
        assert!(list(root.path()).is_err());
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
