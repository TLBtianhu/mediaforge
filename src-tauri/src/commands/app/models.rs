//! 模型命令：目录、自定义模型导入/卸载、删除与残留清理。
//!
//! 从 app_cmd.rs 拆出；长任务仍然走后台 worker + 任务台账。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::state::AppState;
use serde_json::Value;
use tauri::{AppHandle, State};

use super::super::app_cmd::chrono_like_timestamp;

#[tauri::command]
pub async fn get_model_info(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "model_info", Some(payload))
}


#[tauri::command]
pub async fn inspect_custom_model(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "inspect_custom_model", Some(payload))
}

/// Import a local model. Backgrounded because copying multi-GB weights and verifying them by
/// really loading the model both take long enough to need progress and cancellation.
#[tauri::command]
pub async fn start_custom_model_import(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let name = payload
        .get("name")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    if name.trim().is_empty() {
        return Err(AppError::Worker("missing custom model name".into()));
    }
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("custom_model_import_{}", chrono_like_timestamp()));
    // One import at a time. pymss registers by rewriting the whole registry file (read, append,
    // write), so two concurrent imports would race and silently drop one of the registrations.
    if let Ok(tasks) = state.tasks.lock() {
        if tasks.keys().any(|id| id.starts_with("custom_model_import_")) {
            return Err(AppError::Worker("a custom model import is already running".into()));
        }
    }
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        object.insert("taskId".to_string(), Value::String(task_id.clone()));
    }
    spawn_worker_background(app, state, "import_custom_model", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn unregister_custom_model(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "unregister_custom_model", Some(payload))
}

#[tauri::command]
pub async fn relink_custom_model(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "relink_custom_model", Some(payload))
}

/// Rebase custom model registrations after the model directory moved. Called once a migration
/// finishes; the registry stores absolute paths and would otherwise point at the old location.
#[tauri::command]
pub async fn remap_custom_model_paths(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "remap_custom_model_paths", Some(payload))
}

#[tauri::command]
pub async fn start_model_delete(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let model = payload
        .get("model")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing model".into()))?;
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| {
            format!(
                "delete_{}_{}",
                model.replace(|c: char| !c.is_ascii_alphanumeric(), "_"),
                chrono_like_timestamp()
            )
        });
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        object.insert("taskId".to_string(), Value::String(task_id.clone()));
    }
    spawn_worker_background(app, state, "delete_model", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn get_model_storage_summary(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "model_storage_summary", Some(payload))
}

#[tauri::command]
pub async fn delete_tool_model(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "delete_tool_model", Some(payload))
}

#[tauri::command]
pub async fn cleanup_model_residual_files(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "cleanup_model_residual_files", Some(payload))
}

#[tauri::command]
pub async fn start_cleanup_model_residual_files(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("cleanup_residual_{}", chrono_like_timestamp()));
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        object.insert("taskId".to_string(), Value::String(task_id.clone()));
    }
    spawn_worker_background(
        app,
        state,
        "cleanup_model_residual_files",
        task_id.clone(),
        payload,
    )?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}
