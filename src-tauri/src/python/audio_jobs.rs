//! Durable audio queue requests. Shared requests live only in the primary job file.
use super::task_journal::{self, FileLock};
use crate::error::{AppError, AppResult};
use crate::storage;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

fn invalid(message: &str) -> AppError {
    AppError::Worker(format!(
        "invalid audio job: {message}; original files preserved"
    ))
}
fn object(value: &Value) -> AppResult<&Map<String, Value>> {
    value.as_object().ok_or_else(|| invalid("expected object"))
}
fn only(value: &Value, allowed: &[&str]) -> AppResult<()> {
    if object(value)?
        .keys()
        .any(|key| !allowed.contains(&key.as_str()))
    {
        return Err(invalid("unknown field"));
    }
    Ok(())
}
fn id(value: &Value) -> AppResult<&str> {
    value
        .as_str()
        .filter(|value| task_journal::valid_id(value))
        .ok_or_else(|| invalid("invalid id"))
}
fn text(value: &Value) -> bool {
    value.as_str().is_some_and(|s| !s.trim().is_empty())
}

fn validate_request(value: &Value) -> AppResult<()> {
    only(
        value,
        &[
            "input",
            "output",
            "inputIndex",
            "inputs",
            "workbenchOutputs",
        ],
    )?;
    if !value.get("input").is_some_and(|v| v.is_null() || text(v)) || !text(&value["output"]) {
        return Err(invalid("leaf input/output"));
    }
    if value
        .get("inputIndex")
        .is_some_and(|v| !v.as_u64().is_some_and(|n| n > 0))
    {
        return Err(invalid("input index"));
    }
    for key in ["inputs", "workbenchOutputs"] {
        if value.get(key).is_some_and(|v| {
            !v.as_object()
                .is_some_and(|map| map.values().all(Value::is_string))
        }) {
            return Err(invalid("bindings"));
        }
    }
    Ok(())
}

fn validate_presentation(value: &Value) -> AppResult<()> {
    only(value, &["workflowId", "workbench", "sourceUpdatedAt"])?;
    if value.get("workflowId").is_some_and(|v| !text(v)) {
        return Err(invalid("workflow id"));
    }
    if value
        .get("sourceUpdatedAt")
        .is_some_and(|v| !v.as_f64().is_some_and(f64::is_finite))
    {
        return Err(invalid("workflow revision"));
    }
    if let Some(workbench) = value.get("workbench") {
        only(workbench, &["nativeGraph"])?;
        if !workbench.get("nativeGraph").is_some_and(Value::is_boolean) {
            return Err(invalid("workbench marker"));
        }
    }
    Ok(())
}

fn validate_common(command: &str, value: &Value) -> AppResult<()> {
    let map = object(value)?;
    if value
        .get("inferenceParamsVersion")
        .is_some_and(|version| version.as_u64() != Some(3))
    {
        return Err(invalid(
            "unsupported inference parameter version; expected integer 3",
        ));
    }
    if [
        "taskId",
        "requestId",
        "tasks",
        "input",
        "inputs",
        "workbenchOutputs",
    ]
    .iter()
    .any(|key| map.contains_key(*key))
    {
        return Err(invalid("shared payload contains per-input fields"));
    }
    if !text(&value["output"])
        || match command {
            "infer" => !text(&value["model"]),
            "infer_workflow" => {
                !value.get("workflow").is_some_and(Value::is_object)
                    || !text(&value["workflowName"])
            }
            _ => true,
        }
    {
        return Err(invalid("shared worker request"));
    }
    Ok(())
}

pub(super) fn validate_record(value: &Value) -> AppResult<()> {
    if value["source"] != "manual" {
        return Err(invalid("audio source"));
    }
    let audio = &value["audio"];
    only(
        audio,
        &[
            "groupId",
            "primaryJobId",
            "index",
            "request",
            "memberJobIds",
        ],
    )?;
    id(&audio["groupId"])?;
    let primary = id(&audio["primaryJobId"])?;
    let index = audio["index"]
        .as_u64()
        .ok_or_else(|| invalid("leaf order"))?;
    validate_request(&audio["request"])?;
    if value["jobId"] == primary {
        if index != 0 || value.get("payloadRef").is_some() {
            return Err(invalid("primary identity"));
        }
        validate_common(value["command"].as_str().unwrap_or(""), &value["payload"])?;
        if let Some(presentation) = value.get("presentation") {
            validate_presentation(presentation)?;
        }
        let members = audio["memberJobIds"]
            .as_array()
            .ok_or_else(|| invalid("members"))?;
        let ids = members.iter().map(id).collect::<AppResult<Vec<_>>>()?;
        if ids.is_empty()
            || ids[0] != primary
            || ids.iter().collect::<BTreeSet<_>>().len() != ids.len()
        {
            return Err(invalid("members"));
        }
    } else {
        if index == 0
            || id(&value["payloadRef"])? != primary
            || value.get("payload").is_some()
            || value.get("presentation").is_some()
            || audio.get("memberJobIds").is_some()
        {
            return Err(invalid("child shared-payload copy or reference"));
        }
        if !matches!(value["command"].as_str(), Some("infer" | "infer_workflow")) {
            return Err(invalid("audio command"));
        }
    }
    Ok(())
}

fn validate_audio_reference(
    job: &Value,
    rows: &BTreeMap<&str, &Value>,
    validate_members: bool,
) -> AppResult<bool> {
    let audio = &job["audio"];
    let primary_id = audio["primaryJobId"].as_str().unwrap();
    let primary = rows
        .get(primary_id)
        .ok_or_else(|| invalid("missing primary reference"))?;
    if primary
        .pointer("/audio/primaryJobId")
        .and_then(Value::as_str)
        != Some(primary_id)
        || primary["command"] != job["command"]
        || primary["audio"]["groupId"] != audio["groupId"]
    {
        return Err(invalid("reference crosses groups"));
    }
    let members = primary["audio"]["memberJobIds"]
        .as_array()
        .ok_or_else(|| invalid("primary members"))?;
    let index = audio["index"].as_u64().unwrap() as usize;
    if members.get(index) != Some(&job["jobId"]) {
        return Err(invalid("member order"));
    }
    if validate_members {
        for (index, member) in members.iter().enumerate() {
            let row = rows
                .get(member.as_str().unwrap())
                .ok_or_else(|| invalid("missing child reference"))?;
            if row["audio"]["primaryJobId"] != primary_id
                || row["audio"]["index"] != index
                || row["audio"]["groupId"] != audio["groupId"]
            {
                return Err(invalid("member reference"));
            }
        }
    }
    Ok(primary_id == job["jobId"].as_str().unwrap())
}

/// Validate the complete reference closure before callers recover or modify anything.
pub(super) fn validate_library(jobs: &[Value]) -> AppResult<()> {
    let rows: BTreeMap<&str, &Value> = jobs
        .iter()
        .map(|v| (v["jobId"].as_str().unwrap(), v))
        .collect();
    for job in jobs.iter().filter(|v| v.get("audio").is_some()) {
        if validate_audio_reference(job, &rows, false)? {
            validate_audio_reference(job, &rows, true)?;
        }
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Enqueue {
    schema_version: u32,
    command: String,
    group_id: String,
    primary_job_id: String,
    payload: Value,
    presentation: Option<Value>,
    tasks: Vec<Leaf>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Leaf {
    job_id: String,
    request: Value,
}

pub(crate) fn enqueue(root: &Path, request: Value) -> AppResult<Vec<Value>> {
    let request: Enqueue = serde_json::from_value(request)?;
    if request.schema_version != 2
        || !task_journal::valid_id(&request.group_id)
        || request.tasks.is_empty()
        || request.tasks[0].job_id != request.primary_job_id
    {
        return Err(invalid("enqueue envelope"));
    }
    validate_common(&request.command, &request.payload)?;
    if let Some(presentation) = &request.presentation {
        validate_presentation(presentation)?;
    }
    let members = request
        .tasks
        .iter()
        .map(|leaf| leaf.job_id.clone())
        .collect::<Vec<_>>();
    if members.iter().collect::<BTreeSet<_>>().len() != members.len() {
        return Err(invalid("duplicate member id"));
    }
    for leaf in &request.tasks {
        id(&json!(leaf.job_id))?;
        validate_request(&leaf.request)?;
    }
    let _catalog = task_journal::catalog_lock(root)?;
    let existing = task_journal::read_all_unlocked(root)?;
    if existing.iter().any(|job| {
        members.iter().any(|id| job["jobId"] == *id)
            || job.pointer("/audio/groupId").and_then(Value::as_str) == Some(&request.group_id)
    }) {
        return Err(invalid("job or group id already exists"));
    }
    let mut sorted = members.clone();
    sorted.sort();
    let _owners = sorted
        .iter()
        .map(|id| task_journal::owner_lock(root, id))
        .collect::<AppResult<Vec<_>>>()?;
    let _writes = sorted
        .iter()
        .map(|id| task_journal::write_lock(root, id))
        .collect::<AppResult<Vec<_>>>()?;
    for id in &sorted {
        if task_journal::job_path(root, id)?.exists() {
            return Err(invalid("job path already exists"));
        }
    }
    let time = task_journal::now();
    let mut jobs = Vec::new();
    for (index, leaf) in request.tasks.iter().enumerate() {
        let mut job = json!({"schemaVersion": 2, "jobId": leaf.job_id, "source": "manual", "command": request.command,
            "cancelTaskId": request.primary_job_id, "status": "queued", "fraction": 0.0, "message": "queued",
            "error": null, "result": null, "createdAt": time, "updatedAt": time, "startedAt": null, "finishedAt": null,
            "owner": null, "runtime": super::job_schema::empty_runtime(), "logs": [],
            "audio": {"groupId": request.group_id, "primaryJobId": request.primary_job_id, "index": index, "request": leaf.request}});
        if index == 0 {
            job["payload"] = request.payload.clone();
            if let Some(presentation) = &request.presentation {
                job["presentation"] = presentation.clone();
            }
            job["audio"]["memberJobIds"] = json!(members);
        } else {
            job["payloadRef"] = json!(request.primary_job_id);
        }
        super::job_schema::validate(&job)?;
        jobs.push(job);
    }
    validate_library(&jobs)?;
    let mut created = Vec::new();
    for job in &jobs {
        let path = task_journal::job_path(root, job["jobId"].as_str().unwrap())?;
        if let Err(error) = storage::write_json_file(&path, job) {
            for path in created {
                let _ = fs::remove_file(path);
            }
            return Err(error);
        }
        created.push(path);
    }
    Ok(jobs)
}

pub(super) struct Claimed {
    pub primary: String,
    pub payload: Value,
    pub records: Vec<(Value, FileLock)>,
}

#[cfg(test)]
pub(super) fn claim(root: &Path, command: &str, primary_id: &str) -> AppResult<Claimed> {
    let _catalog = task_journal::catalog_lock(root)?;
    let library = task_journal::read_all_unlocked(root)?;
    claim_unlocked(root, command, primary_id, &library)
}

pub(super) fn claim_unlocked(root: &Path, command: &str, primary_id: &str, library: &[Value]) -> AppResult<Claimed> {
    let requested = library
        .iter()
        .find(|v| v["jobId"] == primary_id)
        .ok_or_else(|| invalid("queued job is missing"))?;
    if requested["status"] != "queued" || !requested["owner"].is_null() {
        return Err(invalid("requested job is no longer queued"));
    }
    let anchor = requested
        .pointer("/audio/primaryJobId")
        .and_then(Value::as_str)
        .ok_or_else(|| invalid("claim audio job"))?;
    let primary = library.iter().find(|v| v["jobId"] == anchor).unwrap();
    if primary["command"] != command {
        return Err(invalid("claim primary/command"));
    }
    let members = primary["audio"]["memberJobIds"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect::<Vec<_>>();
    let mut sorted = members.clone();
    sorted.sort();
    let mut owners = BTreeMap::new();
    for id in &sorted {
        owners.insert(id.clone(), task_journal::owner_lock(root, id)?);
    }
    let _writes = sorted
        .iter()
        .map(|id| task_journal::write_lock(root, id))
        .collect::<AppResult<Vec<_>>>()?;
    let mut all_rows = Vec::new();
    for id in &members {
        let row = task_journal::read_job(&task_journal::job_path(root, id)?)?
            .ok_or_else(|| invalid("claim member missing"))?;
        if !task_journal::terminal(&row) && (row["status"] != "queued" || !row["owner"].is_null()) {
            return Err(invalid("group already has an execution owner"));
        }
        all_rows.push(row);
    }
    validate_library(&all_rows)?;
    let mut payload = all_rows[0]["payload"].clone();
    let rows = all_rows
        .into_iter()
        .filter(|row| row["status"] == "queued")
        .collect::<Vec<_>>();
    let worker_primary = rows.first().ok_or_else(|| invalid("no queued leaves"))?["jobId"]
        .as_str()
        .unwrap()
        .to_string();
    payload["taskId"] = json!(worker_primary);
    payload["tasks"] = Value::Array(
        rows.iter()
            .map(|row| {
                let mut request = row["audio"]["request"].clone();
                request["taskId"] = row["jobId"].clone();
                request
            })
            .collect(),
    );
    let mut records = Vec::new();
    for mut row in rows {
        let id = row["jobId"].as_str().unwrap().to_string();
        row["owner"] = json!({"kind": "tauri-worker", "pid": std::process::id()});
        row["cancelTaskId"] = json!(worker_primary);
        row["updatedAt"] = json!(task_journal::now());
        storage::write_json_file(&task_journal::job_path(root, &id)?, &row)?;
        records.push((row, owners.remove(&id).unwrap()));
    }
    Ok(Claimed {
        primary: worker_primary,
        payload,
        records,
    })
}

#[cfg(test)]
mod tests {
    use super::super::{protocol::WorkerEnvelope, task_journal::WorkerJournal};
    use super::*;

    fn remove(root: &Path, ids: &[String]) -> AppResult<super::super::result_jobs::RemovalResult> {
        super::super::result_jobs::remove_audio(root, ids, false, false, &super::super::result_jobs::Protection { roots: vec![] }, || false)
    }

    fn check_remove(root: &Path, ids: &[String]) -> AppResult<super::super::result_jobs::RemovalResult> {
        super::super::result_jobs::remove_audio(root, ids, false, true, &super::super::result_jobs::Protection { roots: vec![] }, || false)
    }

    fn request() -> Value {
        json!({"schemaVersion": 2, "command": "infer_workflow", "groupId": "group_1", "primaryJobId": "a_1",
            "payload": {"workflowName": "fixture", "workflow": {"fixtureGraph": true}, "output": "batch/root", "device": "cpu"},
            "tasks": [{"jobId": "a_1", "request": {"input": "a.wav", "output": "batch/root/a", "inputIndex": 1}},
                      {"jobId": "b_1", "request": {"input": "b.wav", "output": "batch/root/b", "inputIndex": 2}}]})
    }
    fn event(id: &str, path: &str) -> WorkerEnvelope {
        WorkerEnvelope {
            event_type: "task.result".into(),
            task_id: Some(id.into()),
            request_id: None,
            timestamp: None,
            payload: json!({"outputs": [{"path": path}], "files": [path]}),
        }
    }

    #[test]
    fn waiting_batch_uses_one_slot_and_a_cancelled_anchor_keeps_its_sibling() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        let busy = {
            let _catalog = task_journal::catalog_lock(root.path()).unwrap();
            super::super::execution_quota::try_acquire(root.path()).unwrap().unwrap()
        };
        assert!(matches!(task_journal::try_claim_pending(root.path(), "infer_workflow", "a_1").unwrap(), task_journal::ClaimAttempt::Waiting));
        let rows = task_journal::list(root.path()).unwrap();
        assert!(rows.iter().all(|row| row["owner"].is_null() && row["startedAt"].is_null()));
        assert!(task_journal::request_cancel(root.path(), "a_1").unwrap());
        assert_eq!(task_journal::pending_identity(root.path(), "infer_workflow", "a_1").unwrap().unwrap().0, "a_1");
        drop(busy);
        let task_journal::ClaimAttempt::Ready(journal, payload, id) = task_journal::try_claim_pending(root.path(), "infer_workflow", "a_1").unwrap() else { panic!("sibling not admitted"); };
        assert_eq!(id, "b_1"); assert_eq!(payload["taskId"], "b_1");
        assert_eq!(payload["tasks"].as_array().unwrap().len(), 1);
        assert_eq!(payload["output"], "batch/root");
        assert_eq!(task_journal::list(root.path()).unwrap().iter().find(|row| row["jobId"] == "a_1").unwrap()["status"], "cancelled");
        let _catalog = task_journal::catalog_lock(root.path()).unwrap();
        assert!(super::super::execution_quota::try_acquire(root.path()).unwrap().is_none());
        drop(journal);
        assert!(super::super::execution_quota::try_acquire(root.path()).unwrap().is_some());
    }

    #[test]
    fn queued_records_survive_reload_without_an_owner_or_duplicate_graph() {
        let root = tempfile::tempdir().unwrap();
        let jobs = enqueue(root.path(), request()).unwrap();
        assert!(jobs
            .iter()
            .all(|row| row["owner"].is_null() && row["status"] == "queued"));
        let reloaded = task_journal::list(root.path()).unwrap();
        assert_eq!(reloaded.len(), 2);
        let child = reloaded.iter().find(|row| row["jobId"] == "b_1").unwrap();
        assert!(child.get("payload").is_none());
        assert!(child.get("presentation").is_none());
        assert_eq!(child["payloadRef"], "a_1");
        assert!(
            !fs::read_to_string(task_journal::job_path(root.path(), "b_1").unwrap())
                .unwrap()
                .contains("fixtureGraph")
        );
    }

    #[test]
    fn claim_uses_only_durable_parameters_and_keeps_each_leaf_result_once() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        let (journal, payload) =
            WorkerJournal::claim_audio(root.path(), "infer_workflow", "a_1").unwrap();
        assert_eq!(payload["output"], "batch/root");
        assert_eq!(payload["tasks"][1]["output"], "batch/root/b");
        assert_eq!(payload["workflow"]["fixtureGraph"], true);
        assert!(WorkerJournal::claim_audio(root.path(), "infer_workflow", "b_1").is_err());
        journal.lock().unwrap().running(42).unwrap();
        journal
            .lock()
            .unwrap()
            .observe(&event("a_1", "a-out.wav"))
            .unwrap();
        journal
            .lock()
            .unwrap()
            .observe(&event("b_1", "b-out.wav"))
            .unwrap();
        journal.lock().unwrap().exited(true, false, "").unwrap();
        let rows = task_journal::list(root.path()).unwrap();
        for row in &rows {
            assert_eq!(row["result"]["outputs"].as_array().unwrap().len(), 1);
            assert!(row["result"].get("files").is_none());
            assert!(row["result"].get("tasks").is_none());
        }
    }

    #[test]
    fn cancelling_the_queued_anchor_does_not_prevent_the_other_leaf_from_running() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        assert!(task_journal::cancel_queued(root.path(), "a_1").unwrap());
        let (journal, payload) =
            WorkerJournal::claim_audio(root.path(), "infer_workflow", "b_1").unwrap();
        assert_eq!(payload["taskId"], "b_1");
        assert_eq!(payload["tasks"].as_array().unwrap().len(), 1);
        assert_eq!(payload["output"], "batch/root");
        assert_eq!(payload["tasks"][0]["output"], "batch/root/b");
        journal.lock().unwrap().running(43).unwrap();
        journal
            .lock()
            .unwrap()
            .observe(&event("b_1", "b.wav"))
            .unwrap();
        assert_eq!(
            task_journal::read_job(&task_journal::job_path(root.path(), "a_1").unwrap())
                .unwrap()
                .unwrap()["status"],
            "cancelled"
        );
        assert!(remove(root.path(), &["a_1".into()]).is_err());
        drop(journal);
        fs::write(root.path().join("agent-jobs/a_1.cancel"), b"cancel").unwrap();
        assert_eq!(
            remove(root.path(), &["b_1".into()]).unwrap().removed_job_ids,
            vec!["a_1", "b_1"]
        );
        assert!(task_journal::list(root.path()).unwrap().is_empty());
        assert!(!root.path().join("agent-jobs/a_1.cancel").exists());
        assert!(root.path().join("agent-jobs/.owners/a_1.lock").is_file());
    }

    #[test]
    fn delete_requires_every_member_to_be_terminal_and_unowned() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        assert!(task_journal::cancel_queued(root.path(), "a_1").unwrap());
        assert!(remove(root.path(), &["a_1".into()]).is_err());
        assert!(task_journal::cancel_queued(root.path(), "b_1").unwrap());
        let _owner = task_journal::owner_lock(root.path(), "b_1").unwrap();
        assert!(remove(root.path(), &["a_1".into()]).is_err());
        assert!(check_remove(root.path(), &["a_1".into()]).is_err());
        assert_eq!(task_journal::list(root.path()).unwrap().len(), 2);
        drop(_owner);
        assert_eq!(
            check_remove(root.path(), &["a_1".into()]).unwrap().checked_job_ids,
            vec!["a_1", "b_1"]
        );
        assert_eq!(task_journal::list(root.path()).unwrap().len(), 2);
    }

    #[test]
    fn invalid_later_record_prevents_recovery_enqueue_and_delete_without_changes() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        let (journal, _) =
            WorkerJournal::claim_audio(root.path(), "infer_workflow", "a_1").unwrap();
        drop(journal);
        let before = fs::read(task_journal::job_path(root.path(), "a_1").unwrap()).unwrap();
        let bad = root.path().join("agent-jobs/z_bad.json");
        fs::write(&bad, b"{\"jobId\":\"z_bad\"}").unwrap();
        assert!(task_journal::list(root.path()).is_err());
        assert!(remove(root.path(), &["a_1".into()]).is_err());
        assert!(enqueue(root.path(), request()).is_err());
        assert_eq!(
            fs::read(task_journal::job_path(root.path(), "a_1").unwrap()).unwrap(),
            before
        );
        assert_eq!(fs::read(bad).unwrap(), b"{\"jobId\":\"z_bad\"}");
    }

    #[test]
    fn missing_references_and_interrupted_removal_are_not_empty_libraries() {
        let root = tempfile::tempdir().unwrap();
        enqueue(root.path(), request()).unwrap();
        let primary = task_journal::job_path(root.path(), "a_1").unwrap();
        fs::rename(&primary, primary.with_file_name(".a_1.fixture.removing")).unwrap();
        assert!(task_journal::list(root.path()).is_err());
        fs::remove_file(primary.with_file_name(".a_1.fixture.removing")).unwrap();
        assert!(task_journal::list(root.path()).is_err());
    }

    #[test]
    fn catalogue_lock_hides_a_normal_in_progress_multi_file_create() {
        let root = tempfile::tempdir().unwrap();
        let rows: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/jobs/current-contract.json"
        ))
        .unwrap();
        let expected = rows["records"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|row| row.get("audio").is_some())
            .count();
        let _catalog = task_journal::catalog_lock(root.path()).unwrap();
        let root_copy = root.path().to_path_buf();
        let (tx, rx) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || {
            tx.send(task_journal::list(&root_copy)).unwrap();
        });
        for row in rows["records"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|row| row.get("audio").is_some())
        {
            storage::write_json_file(
                &task_journal::job_path(root.path(), row["jobId"].as_str().unwrap()).unwrap(),
                row,
            )
            .unwrap();
        }
        assert!(rx
            .recv_timeout(std::time::Duration::from_millis(100))
            .is_err());
        drop(_catalog);
        assert_eq!(
            rx.recv_timeout(std::time::Duration::from_secs(3))
                .unwrap()
                .unwrap()
                .len(),
            expected
        );
        reader.join().unwrap();
    }
}
