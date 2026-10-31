//! 诊断/调试用的 Tauri 命令。
//!
//! 这些命令只在排查问题时被调用（日志、目录指针、运行时覆盖、目录快照），
//! 与业务主链路无关，因此单独成文件，避免继续堆在 app_cmd.rs 里。

use super::app::debug::{
    collect_debug_runtime_files, create_debug_runtime_backup, debug_runtime_backup_path,
    require_runtime_debug_developer_mode, resolve_debug_runtime_path,
    validate_debug_runtime_override, DebugActiveRuntimeOverrideRequest, DebugRuntimeBackup,
    DebugRuntimePointersPayload, DebugRuntimeRestoreRequest, DebugRuntimeWriteRequest,
    DEBUG_RUNTIME_MAX_FILE_BYTES,
};
use super::app::runtime::require_windows_runtime_management;

use crate::error::{AppError, AppResult};
use crate::session_log::{self, DebugLogContent, DebugLogInfo, DebugLogReport};
use crate::storage;
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use serde_json::Value;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, State};

#[tauri::command]
pub async fn debug_editor_playback(app: AppHandle, snapshot: Value) -> AppResult<()> {
    if session_log::developer_mode_enabled() {
        session_log::append(&app, "DEBUG", "editor.playback", vec![("snapshot", snapshot.to_string())]);
    }
    Ok(())
}

#[tauri::command]
pub async fn debug_log_info(app: AppHandle) -> AppResult<DebugLogInfo> {
    require_runtime_debug_developer_mode(&app)?;
    session_log::info(&app)
}

#[tauri::command]
pub async fn debug_log_read(app: AppHandle) -> AppResult<DebugLogContent> {
    require_runtime_debug_developer_mode(&app)?;
    session_log::read_tail(&app)
}

#[tauri::command]
pub async fn debug_log_clear(app: AppHandle) -> AppResult<DebugLogInfo> {
    require_runtime_debug_developer_mode(&app)?;
    session_log::clear(&app)
}

#[tauri::command]
pub async fn debug_log_create_report(app: AppHandle) -> AppResult<DebugLogReport> {
    require_runtime_debug_developer_mode(&app)?;
    session_log::create_diagnostic_report(&app)
}

#[tauri::command]
pub async fn debug_catalog_info(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "debug_catalog_info", None)
}

#[tauri::command]
pub async fn debug_catalog_save(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "debug_catalog_save", Some(payload))
}

#[tauri::command]
pub async fn debug_catalog_reset(app: AppHandle) -> AppResult<Value> {
    run_worker_with_payload(&app, "debug_catalog_reset", None)
}

#[tauri::command]
pub async fn debug_model_config(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "debug_model_config", Some(payload))
}

#[tauri::command]
pub async fn debug_runtime_pointers(app: AppHandle) -> AppResult<DebugRuntimePointersPayload> {
    require_runtime_debug_developer_mode(&app)?;
    Ok(DebugRuntimePointersPayload {
        runtime_envs_dir: storage::runtime_envs_dir(&app)?.to_string_lossy().to_string(),
        active_runtime_file: storage::active_runtime_file(&app)?.to_string_lossy().to_string(),
        files: collect_debug_runtime_files(&app)?,
    })
}

#[tauri::command]
pub async fn debug_runtime_write_file(app: AppHandle, payload: DebugRuntimeWriteRequest) -> AppResult<DebugRuntimePointersPayload> {
    require_windows_runtime_management()?;
    require_runtime_debug_developer_mode(&app)?;
    if payload.content.len() > DEBUG_RUNTIME_MAX_FILE_BYTES {
        return Err(AppError::Worker("runtime debug file is too large".into()));
    }
    let (path, _kind, _source) = resolve_debug_runtime_path(&app, &payload.path)?;
    if payload.backup.unwrap_or(true) {
        create_debug_runtime_backup(&path)?;
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path, payload.content)?;
    debug_runtime_pointers(app).await
}

#[tauri::command]
pub async fn debug_runtime_restore_file(app: AppHandle, payload: DebugRuntimeRestoreRequest) -> AppResult<DebugRuntimePointersPayload> {
    require_windows_runtime_management()?;
    require_runtime_debug_developer_mode(&app)?;
    let (path, _kind, _source) = resolve_debug_runtime_path(&app, &payload.path)?;
    let backup_path = debug_runtime_backup_path(&path)?;
    if !backup_path.is_file() {
        return Err(AppError::Worker("runtime debug backup does not exist".into()));
    }
    let backup: DebugRuntimeBackup = serde_json::from_str(&std::fs::read_to_string(&backup_path)?)?;
    if backup.existed {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&path, backup.content.unwrap_or_default())?;
    } else if path.is_file() {
        std::fs::remove_file(&path)?;
    }
    std::fs::remove_file(backup_path)?;
    debug_runtime_pointers(app).await
}

#[tauri::command]
pub async fn debug_runtime_override_active(app: AppHandle, payload: DebugActiveRuntimeOverrideRequest) -> AppResult<DebugRuntimePointersPayload> {
    require_windows_runtime_management()?;
    require_runtime_debug_developer_mode(&app)?;
    let (backend, python_path) = validate_debug_runtime_override(&payload.backend, &payload.python_path)?;
    let path = storage::active_runtime_file(&app)?;
    create_debug_runtime_backup(&path)?;
    let content = serde_json::json!({
        "backend": backend,
        "pythonPath": python_path,
        "debugOverride": true,
    });
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path, serde_json::to_string_pretty(&content)?)?;
    debug_runtime_pointers(app).await
}
