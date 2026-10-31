//! 插件与 operation 列表命令。
//!
//! 从 app_cmd.rs 拆出；这些命令只做参数透传或设置插件视图根目录。

use crate::commands::app_cmd::chrono_like_timestamp;
use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::state::AppState;
use serde_json::Value;
use tauri::{AppHandle, State};

#[tauri::command]
pub async fn plugin_marketplace(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "plugin_marketplace", None)
}

#[tauri::command]
pub async fn manage_plugin(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = format!("plugin_manage_{}", chrono_like_timestamp());
    let mut payload = payload;
    let object = payload.as_object_mut()
        .ok_or_else(|| AppError::Worker("invalid plugin request".into()))?;
    // The frontend assigns its id before invoking, so even fast failures are correlated.
    let task_id = object.get("taskId").and_then(Value::as_str)
        .filter(|id| id.starts_with("plugin_manage_") && id.len() <= 160)
        .map(str::to_string).unwrap_or(task_id);
    object.insert("taskId".into(), Value::String(task_id.clone()));
    spawn_worker_background(app, state, "manage_plugin", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn set_plugin_view_root(root: String) -> AppResult<Value> {
    let path = std::path::PathBuf::from(root.trim());
    if path.is_absolute() {
        crate::plugin_views::set_plugins_root(path);
    }
    Ok(serde_json::json!({ "ok": true }))
}

#[tauri::command]
pub async fn list_operations(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "list_operations", None)
}

#[tauri::command]
pub async fn describe_operation(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "describe_operation", Some(payload))
}

#[tauri::command]
pub async fn list_nodes(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "list_nodes", None)
}
