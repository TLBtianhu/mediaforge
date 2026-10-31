//! 任务命令：分离、推理、媒体工具、operation 与 agent 任务。
//!
//! 从 app_cmd.rs 拆出；长任务统一走后台 worker + 任务台账。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::session_log;
use crate::state::AppState;
use crate::storage;
use serde_json::Value;
use tauri::{AppHandle, Emitter, State};
use crate::commands::tempo_key;
use crate::python::worker::kill_process_tree;
use crate::workflow_editors::{RegistrySnapshot, WorkflowEditors};

use crate::commands::app::common::chrono_like_timestamp;

#[tauri::command]
pub async fn start_separation(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing taskId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "task.start_requested",
        vec![("kind", "separation".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn start_workflow_inference(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing taskId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "task.start_requested",
        vec![("kind", "workflow".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer_workflow", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}


#[tauri::command]
pub fn get_workflow_editors(editors: State<'_, WorkflowEditors>) -> RegistrySnapshot {
    editors.registry().snapshot()
}


#[tauri::command]
pub async fn media_library(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "media_library", Some(payload))
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

#[tauri::command]
pub async fn agent_jobs(app: AppHandle) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let index_path = root.join("agent-jobs").join("index.json");
    let index = if index_path.is_file() {
        serde_json::from_str::<Value>(&std::fs::read_to_string(&index_path)?)?
    } else {
        serde_json::json!({ "version": 1, "jobs": [] })
    };
    Ok(serde_json::json!({
        "ok": true,
        "dataRoot": root,
        "workspace": root.join("agent-workspace.json"),
        "index": index,
    }))
}

#[tauri::command]
pub async fn cancel_agent_job(app: AppHandle, job_id: String) -> AppResult<Value> {
    if job_id.is_empty()
        || !job_id
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_' || character == '-')
    {
        return Err(AppError::Worker("invalid agent job id".into()));
    }
    let root = storage::data_root_dir(&app)?;
    let cancel_path = root.join("agent-jobs").join(format!("{job_id}.cancel"));
    std::fs::write(&cancel_path, b"cancel")?;
    Ok(serde_json::json!({ "ok": true, "jobId": job_id, "cancelPath": cancel_path }))
}

#[tauri::command]
pub async fn journal_job(app: AppHandle, job: Value) -> AppResult<Value> {
    let job_id = job
        .get("jobId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing journal job id".into()))?
        .to_string();
    if !job_id
        .chars()
        .all(|character| character.is_ascii_alphanumeric() || character == '_' || character == '-')
    {
        return Err(AppError::Worker("invalid journal job id".into()));
    }
    let root = storage::data_root_dir(&app)?;
    let jobs_root = root.join("agent-jobs");
    std::fs::create_dir_all(&jobs_root)?;
    let mut job = job;
    if let Some(object) = job.as_object_mut() {
        object
            .entry("source".to_string())
            .or_insert_with(|| Value::String("manual".into()));
    }
    let path = jobs_root.join(format!("{job_id}.json"));
    std::fs::write(&path, serde_json::to_string_pretty(&job)?)?;

    let mut entries = Vec::new();
    for entry in std::fs::read_dir(&jobs_root)? {
        let entry = entry?;
        let path = entry.path();
        let name = path.file_name().and_then(|value| value.to_str()).unwrap_or("");
        if !name.ends_with(".json") || name == "index.json" || name.ends_with(".json.tmp") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let Ok(value) = serde_json::from_str::<Value>(&text) else { continue };
        if value.get("jobId").and_then(Value::as_str).is_some() {
            entries.push(value);
        }
    }
    entries.sort_by(|left, right| {
        let left_time = left.get("createdAt").and_then(Value::as_f64).unwrap_or(0.0);
        let right_time = right.get("createdAt").and_then(Value::as_f64).unwrap_or(0.0);
        right_time.partial_cmp(&left_time).unwrap_or(std::cmp::Ordering::Equal)
    });
    entries.truncate(100);
    let index = serde_json::json!({
        "version": 1,
        "updatedAt": chrono::Utc::now().timestamp(),
        "jobs": entries,
    });
    std::fs::write(jobs_root.join("index.json"), serde_json::to_string_pretty(&index)?)?;
    Ok(serde_json::json!({ "ok": true, "jobId": job_id, "index": index }))
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
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
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
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
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
                    "type": "task_cancelled",
                    "taskId": cancelled_task_id,
                    "payload": { "message": "Cancelled" }
                }),
            );
        }
        Ok(true)
    } else {
        Ok(false)
    }
}
