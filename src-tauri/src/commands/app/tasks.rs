//! 任务命令：分离、推理、媒体工具、operation 与 agent 任务。
//!
//! 从 app_cmd.rs 拆出；长任务统一走后台 worker + 任务台账。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::python::{audio_jobs, task_journal};
use crate::session_log;
use crate::state::AppState;
use crate::storage;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter, Manager, State};
use crate::python::worker::kill_process_tree;
use crate::workflow_editors::{RegistrySnapshot, WorkflowEditors};

#[tauri::command]
pub async fn start_separation(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("jobId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing queued jobId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "worker.start_requested",
        vec![("kind", "separation".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}

#[tauri::command]
pub async fn start_workflow_inference(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("jobId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing queued jobId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "worker.start_requested",
        vec![("kind", "workflow".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer_workflow", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}


#[tauri::command]
pub fn get_workflow_editors(editors: State<'_, WorkflowEditors>) -> RegistrySnapshot {
    editors.registry().snapshot()
}


#[tauri::command]
pub async fn media_library(app: AppHandle, mut payload: Value) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    payload = scoped_library_payload(&root, payload)?;
    // The worker's cross-process asset lease owns serialization. Do not hold
    // the editor/settings JSON guard while probing media or making thumbnails.
    run_worker_with_payload(&app, "media_library", Some(payload))
}

fn scoped_library_payload(root: &Path, mut payload: Value) -> AppResult<Value> {
    payload.as_object_mut().ok_or_else(|| AppError::Worker("invalid library request".into()))?
        .insert("dataRoot".into(), serde_json::json!(root));
    Ok(payload)
}

#[cfg(test)]
mod library_scope_tests {
    use super::*;

    #[test]
    fn library_requests_cannot_override_the_application_profile() {
        let root = std::env::temp_dir().join("owned-library-profile");
        let payload = scoped_library_payload(&root, serde_json::json!({"operation":"list","dataRoot":"outside-profile"})).unwrap();
        assert_eq!(payload["dataRoot"], serde_json::json!(root));
        assert_eq!(payload["operation"], "list");
        for invalid in [Value::Null, serde_json::json!([]), serde_json::json!("outside-profile")] {
            assert!(scoped_library_payload(&root, invalid).is_err());
        }
    }
}

#[tauri::command]
pub async fn model_hub(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "model_hub", Some(payload))
}

#[tauri::command]
pub async fn media_capabilities(app: AppHandle, payload: Option<Value>) -> AppResult<Value> {
    // modelDir 要透传：可用性检查必须看用户选的目录，否则界面永远显示"没有可用模型"
    run_worker_with_payload(&app, "media_capabilities", payload)
}

/// Journal snapshot for the UI.
///
/// `known` is the caller's `{jobId: updatedAt}` map. With it the backend returns
/// only changed records plus the ids that disappeared; without it the caller
/// gets the authoritative full snapshot (first load, recovery, diagnostics).
#[tauri::command]
pub async fn agent_jobs(app: AppHandle, known: Option<Value>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let known = parse_known_jobs(known)?;
    let delta = task_journal::read_delta(&root, known.as_ref())?;
    Ok(serde_json::json!({
        "schemaVersion": 3,
        "full": delta.full,
        "dataRoot": root,
        "jobsDir": root.join("agent-jobs"),
        "jobs": delta.jobs,
        "removed": delta.removed,
    }))
}

fn parse_known_jobs(known: Option<Value>) -> AppResult<Option<HashMap<String, f64>>> {
    let Some(known) = known.filter(|value| !value.is_null()) else { return Ok(None) };
    let object = known
        .as_object()
        .ok_or_else(|| AppError::Worker("known journal state must be an object".into()))?;
    let mut map = HashMap::with_capacity(object.len());
    for (id, value) in object {
        if !task_journal::valid_id(id) {
            return Err(AppError::Worker("invalid job id in known journal state".into()));
        }
        let updated = value
            .as_f64()
            .filter(|updated| updated.is_finite())
            .ok_or_else(|| AppError::Worker("invalid updatedAt in known journal state".into()))?;
        map.insert(id.clone(), updated);
    }
    Ok(Some(map))
}

#[tauri::command]
pub async fn enqueue_audio_jobs(app: AppHandle, payload: Value) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let jobs = audio_jobs::enqueue(&root, payload)?;
    Ok(serde_json::json!({
        "schemaVersion": 3,
        "full": false,
        "dataRoot": root,
        "jobsDir": root.join("agent-jobs"),
        "jobs": jobs,
        "removed": [],
    }))
}

#[tauri::command]
pub async fn remove_audio_jobs(app: AppHandle, job_ids: Vec<String>, delete_files: Option<bool>, check_only: Option<bool>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let policy = result_protection(&app)?;
    let result = crate::python::result_jobs::remove_audio(&root, &job_ids, delete_files == Some(true), check_only == Some(true), &policy,
        || app.webview_windows().keys().any(|label| label.starts_with("editor-")))?;
    Ok(serde_json::to_value(result)?)
}

fn result_protection(app: &AppHandle) -> AppResult<crate::python::result_jobs::Protection> {
    let mut roots = vec![storage::models_dir(&app)?, storage::runtime_envs_dir(&app)?, storage::runtime_root_dir(&app)?];
    let executable = std::env::current_exe()?;
    roots.push(executable.clone());
    let mut program_roots = executable.parent().map(PathBuf::from).into_iter().collect::<Vec<_>>();
    if let Ok(resource) = app.path().resource_dir() { program_roots.extend(storage::resource_roots(&resource)); }
    roots.extend(program_roots);
    Ok(crate::python::result_jobs::Protection { roots })
}

#[tauri::command]
pub async fn remove_result_jobs(app: AppHandle, job_ids: Vec<String>, delete_files: Option<bool>, check_only: Option<bool>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let policy = result_protection(&app)?;
    let result = crate::python::result_jobs::remove(&root, &job_ids, delete_files == Some(true), check_only == Some(true), &policy,
        || app.webview_windows().keys().any(|label| label.starts_with("editor-")))?;
    Ok(serde_json::to_value(result)?)
}

#[tauri::command]
pub async fn cancel_agent_job(app: AppHandle, job_id: String) -> AppResult<Value> {
    if !task_journal::valid_id(&job_id) {
        return Err(AppError::Worker("invalid agent job id".into()));
    }
    let root = storage::data_root_dir(&app)?;
    let requested = task_journal::request_cancel(&root, &job_id)?;
    Ok(serde_json::json!({ "ok": requested, "jobId": job_id }))
}

#[tauri::command]
pub async fn start_model_hub_download(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("requestId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing model hub download request id".into()))?
        .to_string();
    spawn_worker_background(app, state, "model_download", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

/// 计划预检：把步骤与输入交给 worker 判断"这计划能不能跑"。
/// 只读、无副作用，所以不像 start_audio_tool 那样需要 requestId。
#[tauri::command]
pub async fn media_precheck(app: AppHandle, payload: Option<Value>) -> AppResult<Value> {
    run_worker_with_payload(&app, "media_precheck", payload)
}

#[tauri::command]
pub async fn start_audio_tool(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("requestId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing audio tool request id".into()))?
        .to_string();
    spawn_worker_background(app, state, "audio_tools", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}

#[tauri::command]
pub async fn start_operation_task(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("requestId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing operation request id".into()))?
        .to_string();
    spawn_worker_background(app, state, "run_operation", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}

#[tauri::command]
pub async fn cancel_task(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
) -> AppResult<bool> {
    if state
        .terminal_tasks
        .lock()
        .map(|terminal| terminal.contains(&task_id))
        .unwrap_or(false)
    {
        return Ok(false);
    }
    if crate::commands::tempo_key::cancel_native_task(&state, &task_id) {
        return Ok(true);
    }
    let child = state
        .tasks
        .lock()
        .ok()
        .and_then(|mut tasks| tasks.remove(&task_id));
    if let Some(child) = child {
        let mut cancelled_task_ids = vec![task_id.clone()];
        if let Ok(mut tasks) = state.tasks.lock() {
            let linked_ids: Vec<String> = tasks
                .iter()
                .filter_map(|(id, registered)| {
                    if std::sync::Arc::ptr_eq(registered, &child) {
                        Some(id.clone())
                    } else {
                        None
                    }
                })
                .collect();
            for id in linked_ids {
                tasks.remove(&id);
                cancelled_task_ids.push(id);
            }
        }
        if let Ok(mut cancelled) = state.cancelled_tasks.lock() {
            cancelled.extend(cancelled_task_ids.iter().cloned());
        }
        if let Ok(root) = storage::data_root_dir(&app) {
            for id in &cancelled_task_ids {
                if let Err(error) = task_journal::cancel(&root, id) {
                    session_log::append(&app, "WARN", "worker.journal_failed", vec![("taskId", id.clone()), ("message", error.to_string())]);
                }
            }
        }
        if let Ok(mut child) = child.lock() {
            let pid = child.id();
            #[cfg(unix)]
            if task_id.starts_with("plugin_manage_") {
                // Plugin workers get a dedicated process group, including pip build children.
                unsafe { libc::kill(-(pid as i32), libc::SIGKILL); }
            }
            kill_process_tree(pid);
            let _ = child.kill();
            let _ = child.wait();
        }
        for cancelled_task_id in cancelled_task_ids {
            // Plugin cancellation is emitted by the worker supervisor after releasing
            // the exclusive runtime lease, so the UI can immediately refresh safely.
            if cancelled_task_id.starts_with("plugin_manage_") {
                continue;
            }
            session_log::append(&app, "INFO", "task.cancelled", vec![("taskId", cancelled_task_id.clone()), ("message", "Task cancelled".into())]);
            let _ = app.emit(
                "pymss://worker-event",
                serde_json::json!({
                    "type": "task.cancelled",
                    "taskId": cancelled_task_id,
                    "payload": { "message": "Cancelled" }
                }),
            );
        }
        Ok(true)
    } else {
        let root = storage::data_root_dir(&app)?;
        task_journal::request_cancel(&root, &task_id)
    }
}
