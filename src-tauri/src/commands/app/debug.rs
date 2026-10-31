//! 运行时调试辅助：可用根目录、路径解析、备份与覆盖校验。
//!
//! 从 app_cmd.rs 拆出；debug_cmd 直接使用这里的数据结构与函数。

use crate::error::{AppError, AppResult};
use crate::session_log;
use crate::storage;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::{Path, PathBuf};
use tauri::AppHandle;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DebugRuntimeFileInfo {
    kind: String,
    source: String,
    path: String,
    exists: bool,
    editable: bool,
    content: Option<String>,
    backup_path: String,
    backup_exists: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DebugRuntimePointersPayload {
    pub(crate) runtime_envs_dir: String,
    pub(crate) active_runtime_file: String,
    pub(crate) files: Vec<DebugRuntimeFileInfo>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DebugRuntimeWriteRequest {
    pub(crate) path: String,
    pub(crate) content: String,
    pub(crate) backup: Option<bool>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DebugRuntimeRestoreRequest {
    pub(crate) path: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DebugActiveRuntimeOverrideRequest {
    pub(crate) backend: String,
    pub(crate) python_path: String,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DebugRuntimeBackup {
    pub(crate) existed: bool,
    pub(crate) content: Option<String>,
}

pub(crate) const DEBUG_RUNTIME_MAX_FILE_BYTES: usize = 256 * 1024;

pub(crate) fn debug_runtime_backup_path(path: &Path) -> AppResult<PathBuf> {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| AppError::Worker("invalid runtime debug path".into()))?;
    Ok(path.with_file_name(format!("{name}.debug-backup.json")))
}

fn debug_runtime_allowed_roots(app: &AppHandle) -> AppResult<Vec<(PathBuf, String)>> {
    Ok(vec![(storage::runtime_envs_dir(app)?, "application".to_string())])
}

pub(crate) fn require_runtime_debug_developer_mode(app: &AppHandle) -> AppResult<()> {
    let settings = storage::read_app_store(app, "app-settings")?;
    if settings.get("developerMode").and_then(Value::as_bool) == Some(true) {
        return Ok(());
    }
    Err(AppError::Worker("runtime debug commands require developer mode".into()))
}

fn debug_runtime_file_kind(root: &Path, path: &Path) -> Option<&'static str> {
    if path == root.join("active-runtime.json") {
        return Some("active-runtime");
    }
    let relative = path.strip_prefix(root).ok()?;
    let parts = relative.components().collect::<Vec<_>>();
    if parts.len() == 2 && relative.file_name().and_then(|value| value.to_str()) == Some("pyvenv.cfg") {
        return Some("pyvenv");
    }
    None
}

fn canonicalize_debug_runtime_path(path: &Path) -> AppResult<PathBuf> {
    if path.is_file() {
        Ok(std::fs::canonicalize(path)?)
    } else {
        let parent = path
            .parent()
            .ok_or_else(|| AppError::Worker("invalid runtime debug path".into()))?;
        let canonical_parent = std::fs::canonicalize(parent)?;
        Ok(canonical_parent.join(path.file_name().ok_or_else(|| AppError::Worker("invalid runtime debug path".into()))?))
    }
}

pub(crate) fn resolve_debug_runtime_path(app: &AppHandle, value: &str) -> AppResult<(PathBuf, String, String)> {
    let path = PathBuf::from(value.trim());
    if path.components().any(|component| matches!(component, std::path::Component::ParentDir)) {
        return Err(AppError::Worker("runtime debug path must not contain parent traversal".into()));
    }
    for (root, source) in debug_runtime_allowed_roots(app)? {
        if !path.starts_with(&root) {
            continue;
        }
        let canonical_root = std::fs::canonicalize(&root).unwrap_or_else(|_| root.clone());
        let canonical_path = canonicalize_debug_runtime_path(&path)?;
        if !canonical_path.starts_with(&canonical_root) {
            return Err(AppError::Worker("runtime debug path resolves outside allowed runtime files".into()));
        }
        if let Some(kind) = debug_runtime_file_kind(&canonical_root, &canonical_path) {
            return Ok((canonical_path, kind.to_string(), source));
        }
    }
    Err(AppError::Worker("runtime debug path is outside allowed runtime files".into()))
}

fn debug_runtime_file_info(kind: &str, source: &str, path: PathBuf) -> AppResult<DebugRuntimeFileInfo> {
    let backup = debug_runtime_backup_path(&path)?;
    let exists = path.is_file();
    let content = if exists { std::fs::read_to_string(&path).ok() } else { None };
    Ok(DebugRuntimeFileInfo {
        kind: kind.to_string(),
        source: source.to_string(),
        path: path.to_string_lossy().to_string(),
        exists,
        editable: !exists || content.is_some(),
        content,
        backup_path: backup.to_string_lossy().to_string(),
        backup_exists: backup.is_file(),
    })
}

pub(crate) fn collect_debug_runtime_files(app: &AppHandle) -> AppResult<Vec<DebugRuntimeFileInfo>> {
    let mut files = Vec::new();
    for (root, source) in debug_runtime_allowed_roots(app)? {
        files.push(debug_runtime_file_info("active-runtime", &source, root.join("active-runtime.json"))?);
        if let Ok(entries) = std::fs::read_dir(&root) {
            for entry in entries.flatten() {
                let env_dir = entry.path();
                if env_dir.is_dir() {
                    files.push(debug_runtime_file_info("pyvenv", &source, env_dir.join("pyvenv.cfg"))?);
                }
            }
        }
    }
    Ok(files)
}

pub(crate) fn create_debug_runtime_backup(path: &Path) -> AppResult<()> {
    let backup_path = debug_runtime_backup_path(path)?;
    if backup_path.is_file() {
        return Ok(());
    }
    let backup = if path.is_file() {
        DebugRuntimeBackup { existed: true, content: Some(std::fs::read_to_string(path)?) }
    } else {
        DebugRuntimeBackup { existed: false, content: None }
    };
    if let Some(parent) = backup_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(backup_path, serde_json::to_string_pretty(&backup)?)?;
    Ok(())
}




pub(crate) fn validate_debug_runtime_override(backend: &str, python_path: &str) -> AppResult<(String, String)> {
    let backend = backend.trim().to_lowercase();
    if !matches!(backend.as_str(), "cpu" | "cuda" | "rocm" | "mlx") {
        return Err(AppError::Worker("runtime debug backend is unsupported".into()));
    }
    let python_path = python_path.trim();
    if python_path.is_empty() {
        return Err(AppError::Worker("runtime debug python path is required".into()));
    }
    let python = PathBuf::from(python_path);
    if !python.is_absolute() {
        return Err(AppError::Worker("runtime debug python path must be absolute".into()));
    }
    let python = python
        .canonicalize()
        .map_err(|_| AppError::Worker("runtime debug python path does not exist".into()))?;
    if !python.is_file() {
        return Err(AppError::Worker("runtime debug python path is not a file".into()));
    }
    let path_backend = python
        .parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|value| value.to_str());
    if !path_backend.is_some_and(|value| value.eq_ignore_ascii_case(&backend)) {
        return Err(AppError::Worker("runtime debug python path does not match the selected backend".into()));
    }
    Ok((backend, python.to_string_lossy().to_string()))
}

