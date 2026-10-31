//! 运行时命令：安装、切换、删除、可选组件。
//!
//! 从 app_cmd.rs 拆出；app_cmd 通过 pub use 继续暴露同名命令，
//! 因此 main.rs 的注册路径不需要改。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::state::AppState;
use serde_json::Value;
use tauri::{AppHandle, State};

use super::super::app::tasks::cancel_task;
use super::super::app_cmd::chrono_like_timestamp;

#[tauri::command]
pub async fn runtime_info(app: AppHandle, payload: Option<Value>) -> AppResult<Value> {
    run_worker_with_payload(&app, "runtime_info", payload)
}

#[tauri::command]
pub async fn runtime_env_sizes(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "runtime_env_sizes", None)
}

#[tauri::command]
pub async fn runtime_core_versions(app: AppHandle, payload: Option<Value>) -> AppResult<Value> {
    run_worker_with_payload(&app, "runtime_core_versions", payload)
}

#[tauri::command]
pub async fn optional_runtime_package_status(
    app: AppHandle,
    package: String,
) -> AppResult<Value> {
    run_worker_with_payload(
        &app,
        "optional_package_status",
        Some(serde_json::json!({ "package": package })),
    )
}

#[tauri::command]
pub async fn manage_optional_runtime_package(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let package = payload
        .get("package")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing optional runtime package".into()))?;
    let task_prefix = format!("optional_package_{package}_");
    if let Ok(tasks) = state.tasks.lock() {
        if tasks.keys().any(|id| id.starts_with(&task_prefix)) {
            return Err(AppError::Worker(format!(
                "optional package operation is already running: {package}"
            )));
        }
    }
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("{task_prefix}{}", chrono_like_timestamp()));
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        object.insert("taskId".to_string(), Value::String(task_id.clone()));
    }
    spawn_worker_background(
        app,
        state,
        "manage_optional_package",
        task_id.clone(),
        payload,
    )?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

pub(crate) fn require_windows_runtime_management() -> AppResult<()> {
    if cfg!(windows) {
        Ok(())
    } else {
        Err(AppError::Worker("runtime management is only available on Windows".into()))
    }
}

#[tauri::command]
pub async fn start_runtime_install(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    require_windows_runtime_management()?;
    if let Ok(tasks) = state.tasks.lock() {
        if tasks.keys().any(|id| id.starts_with("runtime_install_")) {
            return Err(AppError::Worker("runtime installation is already running".into()));
        }
    }
    let backend = payload
        .get("backend")
        .and_then(Value::as_str)
        .unwrap_or("cpu")
        .to_string();
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("runtime_install_{}", chrono_like_timestamp()));
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        object.insert("taskId".to_string(), Value::String(task_id.clone()));
        object.insert("backend".to_string(), Value::String(backend.clone()));
    }
    spawn_worker_background(app, state, "install_runtime", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn cancel_runtime_install(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
) -> AppResult<bool> {
    cancel_task(app, state, task_id).await
}


#[tauri::command]
pub async fn activate_runtime(app: AppHandle, payload: Value) -> AppResult<Value> {
    require_windows_runtime_management()?;
    run_worker_with_payload(&app, "activate_runtime", Some(payload))
}

#[tauri::command]
pub async fn delete_runtime(app: AppHandle, payload: Value) -> AppResult<Value> {
    require_windows_runtime_management()?;
    run_worker_with_payload(&app, "delete_runtime", Some(payload))
}
