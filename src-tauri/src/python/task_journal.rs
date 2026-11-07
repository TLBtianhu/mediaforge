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

fn now() -> f64 {
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

fn terminal(value: &Value) -> bool {
    matches!(
        value.get("status").and_then(Value::as_str),
        Some("completed" | "failed" | "cancelled" | "interrupted")
    )
}

fn backend_owned(value: &Value) -> bool {
    value.pointer("/owner/kind").and_then(Value::as_str) == Some(OWNER_KIND)
}

fn read_job(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path).ok()?;
    serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()
}

/// Windows sharing exclusion also excludes Python's msvcrt lease; Unix uses its flock.
/// Never unlink these lock files: every process must keep locking the same inode.
struct FileLock(File);
impl FileLock {
    fn acquire(path: &Path, timeout: Duration) -> AppResult<Self> {
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
            }
            let attempt = options.open(path).and_then(|file| {
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

fn job_path(root: &Path, id: &str) -> AppResult<PathBuf> {
    if !valid_id(id) {
        return Err(AppError::Worker("invalid journal job id".into()));
    }
    Ok(root.join("agent-jobs").join(format!("{id}.json")))
}

fn write_lock(root: &Path, id: &str) -> AppResult<FileLock> {
    job_path(root, id)?;
    FileLock::acquire(
        &root.join("agent-jobs/.writes").join(format!("{id}.lock")),
        Duration::from_secs(2),
    )
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

pub(crate) fn tracks(command: &str) -> bool {
    matches!(
        command,
        "infer"
            | "infer_workflow"
            | "audio_tools"
            | "media_tool"
            | "run_operation"
            | "export_editor_mix"
    )
}

struct Job {
    snapshot: Value,
    _owner: FileLock,
    saved: Option<Instant>,
}

pub(crate) struct WorkerJournal {
    root: PathBuf,
    primary: String,
    jobs: Vec<Job>,
}

impl WorkerJournal {
    pub(crate) fn start(
        root: &Path,
        command: &str,
        primary: &str,
        payload: &Value,
    ) -> AppResult<JournalHandle> {
        if !payload.is_object() {
            return Err(AppError::Worker("task payload must be an object".into()));
        }
        let mut journal = Self {
            root: root.into(),
            primary: primary.into(),
            jobs: Vec::new(),
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
                let previous = read_job(&path);
                if previous.as_ref().is_some_and(|value| {
                    terminal(value) || value.get("source").and_then(Value::as_str) == Some("agent")
                }) {
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
                    "jobId": id, "command": command, "source": "manual", "cancelTaskId": id,
                    "status": "queued", "fraction": 0.0, "message": "queued", "error": null, "result": null,
                    "createdAt": previous.as_ref().and_then(|value| value.get("createdAt")).and_then(Value::as_f64).unwrap_or(created),
                    "finishedAt": null, "payload": snapshot_payload,
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
        if let Some(current) = read_job(&path) {
            // A cancel command may have won the terminal race while stdout was draining.
            if terminal(&current) {
                job.snapshot = current;
                return Ok(());
            }
        }
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
        job.snapshot["message"] = json!(message);
        job.snapshot["finishedAt"] = json!(now());
        if status == "completed" {
            job.snapshot["fraction"] = json!(1.0);
        }
        if status == "failed" || status == "interrupted" {
            job.snapshot["error"] = json!(message);
        }
        if let Some(result) = result {
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
                || (targeted.is_none() && kind != "error" && id != self.primary)
            {
                continue;
            }
            if terminal(&self.jobs[index].snapshot) {
                continue;
            }
            if kind == "error" {
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
                    self.jobs[index].snapshot["result"] = event.payload.clone();
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
    let _write = write_lock(root, id)?;
    let path = job_path(root, id)?;
    let Some(mut value) = read_job(&path) else {
        return Ok(false);
    };
    if !backend_owned(&value) || terminal(&value) {
        return Ok(false);
    }
    value["status"] = json!("cancelled");
    value["message"] = json!("Cancelled");
    value["finishedAt"] = json!(now());
    storage::write_json_file(&path, &value)?;
    Ok(true)
}

/// Recover only our manual supervisors. MCP owners and legacy ownerless manual rows are untouched.
fn recover(root: &Path, id: &str, value: Value) -> AppResult<Value> {
    if !backend_owned(&value) || terminal(&value) {
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
    let mut latest = read_job(&path).unwrap_or(value);
    if backend_owned(&latest) && !terminal(&latest) {
        latest["status"] = json!("interrupted");
        latest["message"] = json!("应用退出时任务被中断");
        latest["error"] = json!("manual worker supervisor exited before the job finished");
        latest["finishedAt"] = json!(now());
        storage::write_json_file(&path, &latest)?;
    }
    Ok(latest)
}

pub(crate) fn list(root: &Path) -> AppResult<Vec<Value>> {
    let directory = root.join("agent-jobs");
    if !directory.is_dir() {
        return Ok(Vec::new());
    }
    let mut jobs = Vec::new();
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        let Some(id) = path
            .file_stem()
            .and_then(|name| name.to_str())
            .filter(|id| valid_id(id) && *id != "index")
        else {
            continue;
        };
        if path.extension().and_then(|name| name.to_str()) != Some("json") {
            continue;
        }
        let Some(value) =
            read_job(&path).filter(|value| value.get("jobId").and_then(Value::as_str) == Some(id))
        else {
            continue;
        };
        jobs.push(recover(root, id, value)?);
    }
    jobs.sort_by(|a, b| {
        b["createdAt"]
            .as_f64()
            .unwrap_or(0.0)
            .partial_cmp(&a["createdAt"].as_f64().unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    // Active jobs must not disappear just because newer short tasks finished.
    let mut retained_terminal = 0;
    jobs.retain(|job| {
        if !terminal(job) {
            return true;
        }
        retained_terminal += 1;
        retained_terminal <= 100
    });
    Ok(jobs)
}


#[cfg(test)]
mod tests {
    use super::*;

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
        read_job(&job_path(root, id).unwrap()).unwrap()
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
    fn live_owner_is_never_recovered_and_dead_manual_owner_does_not_affect_mcp() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "live_1");
        journal.lock().unwrap().running(123).unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "running");
        let jobs = root.path().join("agent-jobs");
        storage::write_json_file(&jobs.join("mcp_1.json"), &json!({"jobId": "mcp_1", "source": "agent", "status": "running", "owner": {"kernelId": "foreign"}})).unwrap();
        storage::write_json_file(
            &jobs.join("legacy_1.json"),
            &json!({"jobId": "legacy_1", "source": "manual", "status": "running"}),
        )
        .unwrap();
        drop(journal);
        let _ = list(root.path()).unwrap();
        assert_eq!(snapshot(root.path(), "live_1")["status"], "interrupted");
        assert_eq!(snapshot(root.path(), "mcp_1")["status"], "running");
        assert_eq!(snapshot(root.path(), "legacy_1")["status"], "running");
    }

    #[test]
    fn files_are_authoritative_even_when_shared_index_is_corrupt_or_stale() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "fresh_1");
        fs::write(root.path().join("agent-jobs/index.json"), b"not-json").unwrap();
        assert_eq!(list(root.path()).unwrap()[0]["status"], "queued");
        journal
            .lock()
            .unwrap()
            .observe(&event(
                "task.result",
                None,
                json!({"outputs": [{"path": "out.png"}]}),
            ))
            .unwrap();
        assert_eq!(
            list(root.path()).unwrap()[0]["status"],
            "completed"
        );
        assert_eq!(
            fs::read(root.path().join("agent-jobs/index.json")).unwrap(),
            b"not-json"
        );
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
    fn listing_retains_old_active_jobs_in_addition_to_the_newest_hundred_terminal_jobs() {
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "old_active_1");
        for index in 0..105 {
            let id = format!("finished_{index}");
            storage::write_json_file(
                &job_path(root.path(), &id).unwrap(),
                &json!({"jobId": id, "status": "completed", "createdAt": now() + f64::from(index)}),
            ).unwrap();
        }
        let listed = list(root.path()).unwrap();
        let jobs = listed;
        assert_eq!(jobs.len(), 101);
        assert!(jobs.iter().any(|job| job["jobId"] == "old_active_1"));
        assert!(jobs.iter().any(|job| job["jobId"] == "finished_104"));
        assert!(!jobs.iter().any(|job| job["jobId"] == "finished_0"));
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
        use std::process::{Command, Stdio};
        let root = tempfile::tempdir().unwrap();
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "python::task_journal::tests::owned_journal_child",
            ])
            .env("MEDIAFORGE_TEST_JOURNAL_ROOT", root.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
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
        // System Python, stdlib only: never select an installed user runtime or load models.
        let root = tempfile::tempdir().unwrap();
        let journal = started(root.path(), "interop_1");
        journal.lock().unwrap().running(123).unwrap();
        let python_source = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("python");
        let script = "import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[2]); from core.operation_kernel import OperationKernel; root=Path(sys.argv[1]); kernel=OperationKernel(root); value=json.loads((root/'agent-jobs/interop_1.json').read_text(encoding='utf-8')); assert value['status']==sys.argv[3], value['status']";
        let run = |expected| {
            std::process::Command::new("python")
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
