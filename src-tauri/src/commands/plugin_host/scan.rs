//! 插件扫描命令：状态、跳过、取消、黑名单、扫描与报告。

use crate::commands::plugin_host::{require_enabled, scan_pipeline, PluginHostState};
use crate::commands::plugin_host::scan_engine::scan_plugins;
use serde_json::Value;
use tauri::{AppHandle, State};
use super::*;

#[tauri::command]
pub fn plugin_host_scan_status(state: State<'_, PluginHostState>, scan_id: String) -> Result<Value, String> {
    require_enabled()?;
    serde_json::to_value(state.scan_control.status(&scan_id)).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn plugin_host_skip_scan(app: AppHandle, state: State<'_, PluginHostState>, scan_id: String, path: String) -> Result<Value, String> {
    require_enabled()?;
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || skip_scan(&root, &state, &scan_id, &path))
        .await.map_err(|error| error.to_string())?
}

pub(crate) fn skip_scan(root: &Path, state: &PluginHostState, scan_id: &str, path: &str) -> Result<Value, String> {
    // Claim and persist in one storage order. A later restore must win over both actions.
    let _storage = state.scan_storage.lock().map_err(|_| "Plugin scan storage lock failed")?;
    let skipped = state.scan_control.skip(scan_id, path);
    if skipped {
        // Persist immediately, independently of later cache commit or whole-scan cancellation.
        blacklist::add(root, path, BlacklistReason::Manual)?;
    }
    Ok(json!({ "skipped": skipped }))
}

#[tauri::command]
pub async fn plugin_host_cancel_scan(state: State<'_, PluginHostState>, scan_id: String) -> Result<Value, String> {
    require_enabled()?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || json!({ "cancelled": state.scan_control.cancel(&scan_id) }))
        .await.map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn plugin_host_scan_blacklist(app: AppHandle, state: State<'_, PluginHostState>) -> Result<Value, String> {
    require_enabled()?;
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let _storage = state.scan_storage.lock().map_err(|_| "Plugin scan storage lock failed")?;
        serde_json::to_value(blacklist::load(&root)?).map_err(|error| error.to_string())
    }).await.map_err(|error| error.to_string())?
}

fn remove_cached_scan_entry(root: &Path, path: &str) -> Result<(), String> {
    let file = root.join("plugin-cache.json");
    let mut cache: Value = fs::File::open(&file).ok()
        .and_then(|file| serde_json::from_reader(file.take(MAX_CACHE_BYTES)).ok()).unwrap_or(Value::Null);
    if let Some(entries) = cache.get_mut("entries").and_then(Value::as_array_mut) {
        let previous = entries.len();
        entries.retain(|entry| !entry["path"].as_str().is_some_and(|candidate| blacklist::path_matches(candidate, path)));
        if entries.len() != previous {
            crate::storage::write_json_file(&file, &cache).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn plugin_host_remove_scan_blacklist(app: AppHandle, state: State<'_, PluginHostState>, path: String) -> Result<(), String> {
    require_enabled()?;
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || restore_scan_entry(&root, &state, &path))
        .await.map_err(|error| error.to_string())?
}

pub(crate) fn restore_scan_entry(root: &Path, state: &PluginHostState, path: &str) -> Result<(), String> {
    let _storage = state.scan_storage.lock().map_err(|_| "Plugin scan storage lock failed")?;
    state.scan_control.invalidate(path);
    // Invalidate first: a storage error must leave the exclusion active rather than reuse a failed cache.
    remove_cached_scan_entry(root, path)?;
    blacklist::remove(root, path)
}

#[tauri::command]
pub async fn plugin_host_scan(
    app: AppHandle,
    state: State<'_, PluginHostState>,
    paths: Vec<String>,
) -> Result<Vec<Value>, String> {
    Ok(scan_plugins(app, state.inner().clone(), paths, false, None)
        .await?
        .plugins)
}

#[tauri::command]
pub async fn plugin_host_cached_scan_report(
    app: AppHandle,
    state: State<'_, PluginHostState>,
    paths: Vec<String>,
) -> Result<Value, String> {
    require_enabled()?;
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        // Reading the last complete snapshot must not queue behind a long native scan.
        let _guard = state.scan_storage.lock().map_err(|_| "Plugin scan storage lock failed")?;
        scan_pipeline::cached_report(&root, &paths)
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn plugin_host_scan_report(
    app: AppHandle,
    state: State<'_, PluginHostState>,
    paths: Vec<String>,
    force: Option<bool>,
    scan_id: Option<String>,
) -> Result<Value, String> {
    serde_json::to_value(
        scan_plugins(app, state.inner().clone(), paths, force.unwrap_or(false), scan_id).await?,
    )
    .map_err(|error| error.to_string())
}
