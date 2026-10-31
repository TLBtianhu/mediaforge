use crate::error::{AppError, AppResult};
use crate::model_dir_migration::{
    self, CancelModelDirMigrationRequest, ConfirmModelDirMigrationSwitchRequest,
    PrepareModelDirChangeRequest, RespondModelDirMigrationConflictRequest,
    StartModelDirMigrationRequest,
};
use crate::python::worker::{
    bundled_bin_dirs, cleanup_editor_export_temp_files, editor_export_task_token,
    run_worker_with_payload, spawn_worker_background, kill_process_tree,
};
use crate::session_log::{self, DebugLogContent, DebugLogInfo, DebugLogReport};
use crate::state::AppState;
use crate::storage;
use crate::commands::app::editor::{EditorRecordingHandle, EditorRecordingResult, LinkedEditorAsset, editor_recordings_dir, file_name_from_path, now_millis, pcm_wav_header, validate_recording_id};
use crate::workflow_editors::{self, RegistrySnapshot, WorkflowEditorKind, WorkflowEditors};
use base64::Engine as _;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;













pub(crate) fn chrono_like_timestamp() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or_default()
}

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

pub(crate) fn value_type(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}


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
    if super::tempo_key::cancel_native_task(&state, &task_id) {
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

#[tauri::command]
pub async fn pick_audio_files(app: AppHandle) -> AppResult<Vec<String>> {
    let files = app
        .dialog()
        .file()
        .add_filter(
            "Audio",
            &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"],
        )
        .blocking_pick_files()
        .unwrap_or_default();
    Ok(files.into_iter().map(|p| p.to_string()).collect())
}

/// Pick a model weights file. Extensions mirror what pymss can actually load with `torch.load`;
/// `.safetensors` is deliberately absent because pymss has no safetensors path at all.
///
/// `title` is supplied by the caller so it can be localised, and so that back-to-back weights
/// and config dialogs are told apart by more than their file filter.
#[tauri::command]
pub async fn pick_model_weights_file(app: AppHandle, title: Option<String>) -> AppResult<Option<String>> {
    let mut builder = app
        .dialog()
        .file()
        .add_filter("Model weights", &["ckpt", "pth", "th", "pt", "bin"]);
    if let Some(title) = title.as_deref().filter(|value| !value.trim().is_empty()) {
        builder = builder.set_title(title);
    }
    Ok(builder.blocking_pick_file().map(|p| p.to_string()))
}

#[tauri::command]
pub async fn pick_model_config_file(app: AppHandle, title: Option<String>) -> AppResult<Option<String>> {
    let mut builder = app.dialog().file().add_filter("Model config", &["yaml", "yml"]);
    if let Some(title) = title.as_deref().filter(|value| !value.trim().is_empty()) {
        builder = builder.set_title(title);
    }
    Ok(builder.blocking_pick_file().map(|p| p.to_string()))
}

#[tauri::command]
pub async fn pick_single_audio_file(app: AppHandle) -> AppResult<Option<String>> {
    Ok(app
        .dialog()
        .file()
        .add_filter(
            "Audio",
            &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"],
        )
        .blocking_pick_file()
        .map(|p| p.to_string()))
}

pub(crate) fn path_to_string(path: &Path) -> String {
    path.to_string_lossy().to_string()
}

pub(crate) fn normalize_path_key(value: &str) -> String {
    if cfg!(windows) {
        value.replace('\\', "/").to_ascii_lowercase()
    } else {
        value.to_string()
    }
}

fn normalized_relative_path(path: &Path) -> Option<String> {
    let parts: Vec<String> = path
        .components()
        .filter_map(|component| match component {
            std::path::Component::Normal(value) => value.to_str().map(str::to_string),
            _ => None,
        })
        .filter(|part| !part.is_empty())
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join("/"))
    }
}

fn relative_path_from_root(root: &Path, target: &Path) -> Option<String> {
    target
        .strip_prefix(root)
        .ok()
        .and_then(normalized_relative_path)
}

pub(crate) fn linked_asset_from_path(
    path: &Path,
    origin_kind: &str,
    origin_root: Option<&Path>,
    relative_path: Option<&Path>,
) -> LinkedEditorAsset {
    let relative = relative_path
        .and_then(normalized_relative_path)
        .or_else(|| origin_root.and_then(|root| relative_path_from_root(root, path)))
        .or_else(|| path.file_name().and_then(|value| value.to_str()).map(str::to_string));
    LinkedEditorAsset {
        path: path_to_string(path),
        name: file_name_from_path(&path_to_string(path)),
        origin_kind: origin_kind.to_string(),
        origin_root: origin_root.map(path_to_string),
        relative_path: relative,
        missing: !path.is_file(),
    }
}

fn detect_editor_source_origin(project: &Value, source: &Value) -> (String, Option<String>, Option<String>) {
    let role = source.get("role").and_then(Value::as_str).unwrap_or("reference");
    let path = source.get("path").and_then(Value::as_str).unwrap_or_default();
    let source_path = PathBuf::from(path);

    let stored_kind = source
        .get("originKind")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let stored_root = source
        .get("originRoot")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let stored_relative = source
        .get("relativePath")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());

    if stored_kind.is_some() || stored_root.is_some() || stored_relative.is_some() {
        return (
            stored_kind.unwrap_or_else(|| if role == "stem" { "task-result" } else { "external" }.to_string()),
            stored_root,
            stored_relative.or_else(|| source_path.file_name().and_then(|value| value.to_str()).map(str::to_string)),
        );
    }

    if role == "stem" {
        if let Some(result_dir) = project
            .get("sourceResultDir")
            .and_then(Value::as_str)
            .map(PathBuf::from)
        {
            let relative = relative_path_from_root(&result_dir, &source_path)
                .or_else(|| source_path.file_name().and_then(|value| value.to_str()).map(str::to_string));
            return ("task-result".to_string(), Some(path_to_string(&result_dir)), relative);
        }
    }

    let parent = source_path.parent().map(path_to_string);
    let relative = source_path.file_name().and_then(|value| value.to_str()).map(str::to_string);
    ("legacy".to_string(), parent, relative)
}

pub(crate) fn enrich_editor_project_sources(project: &mut Value) -> bool {
    let project_snapshot = project.clone();
    let Some(sources) = project.get_mut("sources").and_then(Value::as_array_mut) else {
        return false;
    };

    let mut changed = false;
    for source in sources.iter_mut() {
        let Some(source_object) = source.as_object_mut() else {
            continue;
        };
        let path = source_object
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let source_path = PathBuf::from(&path);
        let missing = !source_path.is_file();
        if source_object.get("missing").and_then(Value::as_bool) != Some(missing) {
            source_object.insert("missing".into(), Value::Bool(missing));
            changed = true;
        }

        let source_value = Value::Object(source_object.clone());
        let (origin_kind, origin_root, relative_path) =
            detect_editor_source_origin(&project_snapshot, &source_value);

        if source_object
            .get("originKind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
        {
            source_object.insert("originKind".into(), Value::String(origin_kind));
            changed = true;
        }

        if !source_object.contains_key("originRoot") {
            source_object.insert(
                "originRoot".into(),
                origin_root.map(Value::String).unwrap_or(Value::Null),
            );
            changed = true;
        }

        if !source_object.contains_key("relativePath") {
            source_object.insert(
                "relativePath".into(),
                relative_path.map(Value::String).unwrap_or(Value::Null),
            );
            changed = true;
        }
    }

    changed
}

fn source_relative_parts(relative_path: &str) -> Vec<String> {
    relative_path
        .split(['/', '\\'])
        .filter(|part| !part.trim().is_empty())
        .map(str::to_string)
        .collect()
}

fn path_tail_matches(path: &Path, relative_path: &str) -> bool {
    let rel_parts = source_relative_parts(relative_path);
    if rel_parts.is_empty() {
        return false;
    }
    let path_parts: Vec<String> = path
        .components()
        .filter_map(|component| match component {
            std::path::Component::Normal(value) => value.to_str().map(str::to_string),
            _ => None,
        })
        .collect();
    if path_parts.len() < rel_parts.len() {
        return false;
    }
    let tail = &path_parts[path_parts.len() - rel_parts.len()..];
    tail.iter()
        .map(|part| part.to_ascii_lowercase())
        .eq(rel_parts.iter().map(|part| part.to_ascii_lowercase()))
}

pub(crate) fn derive_relink_root(picked_path: &Path, relative_path: Option<&str>) -> Option<PathBuf> {
    let relative_path = relative_path?;
    if !path_tail_matches(picked_path, relative_path) {
        return None;
    }
    let component_count = source_relative_parts(relative_path).len();
    if component_count == 0 {
        return None;
    }
    let mut cursor = picked_path.to_path_buf();
    for _ in 0..component_count {
        cursor = cursor.parent()?.to_path_buf();
    }
    Some(cursor)
}

pub(crate) fn build_file_name_index(root: &Path) -> std::collections::HashMap<String, Vec<PathBuf>> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    collect_audio_files(root, &mut files, &mut warnings);
    let mut index = std::collections::HashMap::<String, Vec<PathBuf>>::new();
    for file in files {
        let path = PathBuf::from(&file);
        if let Some(name) = path.file_name().and_then(|value| value.to_str()) {
            index.entry(name.to_ascii_lowercase()).or_default().push(path);
        }
    }
    index
}

pub(crate) fn preferred_match_by_relative_path<'a>(candidates: &'a [PathBuf], relative_path: Option<&str>) -> Option<&'a PathBuf> {
    let relative_path = relative_path?;
    let mut filtered = candidates.iter().filter(|path| path_tail_matches(path, relative_path));
    let first = filtered.next()?;
    if filtered.next().is_some() {
        None
    } else {
        Some(first)
    }
}

pub(crate) fn build_relink_candidate(
    relink_root: &Path,
    relative_path: Option<&str>,
    file_name_index: &std::collections::HashMap<String, Vec<PathBuf>>,
) -> Option<PathBuf> {
    if let Some(relative) = relative_path {
        // Join segment-by-segment so PathBuf inserts the platform separator,
        // instead of hardcoding a Windows backslash.
        let mut direct = relink_root.to_path_buf();
        for part in source_relative_parts(relative) {
            direct.push(part);
        }
        if direct.is_file() {
            return Some(direct);
        }
    }

    let file_name = relative_path
        .and_then(|value| source_relative_parts(value).last().cloned())
        .map(|value| value.to_ascii_lowercase())?;
    let candidates = file_name_index.get(&file_name)?;
    if let Some(preferred) = preferred_match_by_relative_path(candidates, relative_path) {
        return Some(preferred.clone());
    }
    if candidates.len() == 1 {
        return candidates.first().cloned();
    }
    None
}

#[tauri::command]
pub async fn pick_media_files(app: AppHandle) -> AppResult<Vec<String>> {
    let files = app
        .dialog()
        .file()
        .add_filter(
            "Media / 媒体",
            &[
                "wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mkv", "mov", "avi",
                "webm", "flv",
            ],
        )
        .blocking_pick_files()
        .unwrap_or_default();
    Ok(files.into_iter().map(|p| p.to_string()).collect())
}

#[tauri::command]
pub async fn pick_input_folder(app: AppHandle) -> AppResult<Option<String>> {
    Ok(app
        .dialog()
        .file()
        .blocking_pick_folder()
        .map(|p| p.to_string()))
}

#[tauri::command]
pub async fn pick_output_folder(app: AppHandle) -> AppResult<Option<String>> {
    Ok(app
        .dialog()
        .file()
        .blocking_pick_folder()
        .map(|p| p.to_string()))
}

/// Opens a "Save As" dialog seeded with `default_name` and writes `content` to
/// the chosen path. Returns the saved path, or `None` if the user cancelled.
#[tauri::command]
pub async fn save_text_file_dialog(
    app: AppHandle,
    default_name: String,
    content: String,
) -> AppResult<Option<String>> {
    let mut builder = app.dialog().file();
    if !default_name.trim().is_empty() {
        builder = builder.set_file_name(&default_name);
        if let Some(ext) = std::path::Path::new(&default_name)
            .extension()
            .and_then(|value| value.to_str())
        {
            builder = builder.add_filter(ext.to_uppercase(), &[ext]);
        }
    }
    let Some(path) = builder.blocking_save_file() else {
        return Ok(None);
    };
    let path_string = path.to_string();
    let path_buf = PathBuf::from(&path_string);
    if let Some(parent) = path_buf.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path_buf, content.as_bytes())?;
    Ok(Some(path_string))
}

#[tauri::command]
pub async fn prepare_model_dir_change(
    app: AppHandle,
    payload: PrepareModelDirChangeRequest,
) -> AppResult<model_dir_migration::PrepareModelDirChangePayload> {
    model_dir_migration::prepare_model_dir_change(&app, payload)
}

#[tauri::command]
pub async fn start_model_dir_migration(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: StartModelDirMigrationRequest,
) -> AppResult<Value> {
    model_dir_migration::start_model_dir_migration(app, state, payload)
}

#[tauri::command]
pub async fn respond_model_dir_migration_conflict(
    state: State<'_, AppState>,
    payload: RespondModelDirMigrationConflictRequest,
) -> AppResult<Value> {
    model_dir_migration::respond_model_dir_migration_conflict(state, payload)
}

#[tauri::command]
pub async fn confirm_model_dir_migration_switch(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: ConfirmModelDirMigrationSwitchRequest,
) -> AppResult<Value> {
    model_dir_migration::confirm_model_dir_migration_switch(app, state, payload)
}

#[tauri::command]
pub async fn cancel_model_dir_migration(
    state: State<'_, AppState>,
    payload: CancelModelDirMigrationRequest,
) -> AppResult<Value> {
    model_dir_migration::cancel_model_dir_migration(state, payload)
}

const AUDIO_EXTENSIONS: &[&str] = &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"];
const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mkv", "mov", "avi", "webm", "flv"];

#[derive(Debug, Serialize)]
pub struct ScanAudioPathsResult {
    pub files: Vec<String>,
    pub warnings: Vec<String>,
}

pub(crate) fn is_audio_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| AUDIO_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn is_media_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| {
            let ext = ext.to_lowercase();
            AUDIO_EXTENSIONS.contains(&ext.as_str()) || VIDEO_EXTENSIONS.contains(&ext.as_str())
        })
        .unwrap_or(false)
}

fn collect_audio_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_audio_files(&path, results, warnings);
                } else if path.is_file() && is_audio_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn collect_media_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_media_files(&path, results, warnings);
                } else if path.is_file() && is_media_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn collect_audio_files_shallow(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_file() && is_audio_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn stable_dedup(paths: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut deduped = Vec::with_capacity(paths.len());
    for path in paths {
        if seen.insert(path.clone()) {
            deduped.push(path);
        }
    }
    deduped
}

/// 图片扩展名：与 `scan_media_paths` 的音视频集合分开，
/// 否则把一张 png 拖进图片页会被判成"不支持的文件"。
const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"];

fn is_image_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| IMAGE_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn collect_image_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_image_files(&path, results, warnings);
                } else if path.is_file() && is_image_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

/// 判断拖进来的路径是文件还是目录。
///
/// 拖放事件只给路径字符串，"这是文件夹"与否必须查文件系统——用它来决定
/// 是"引用这个目录"还是"把文件加进来"。
#[tauri::command]
pub async fn path_kinds(paths: Vec<String>) -> AppResult<Vec<(String, String)>> {
    let mut result = Vec::new();
    for raw in paths {
        let path = Path::new(&raw);
        let kind = if path.is_dir() {
            "directory"
        } else if path.is_file() {
            "file"
        } else {
            "missing"
        };
        result.push((raw, kind.to_string()));
    }
    Ok(result)
}

/// 拖放/选择到的路径 → 图片文件列表（目录会递归展开）。
#[tauri::command]
pub async fn scan_image_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_image_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported image file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_image_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_audio_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_audio_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths_with_options(
    paths: Vec<String>,
    recursive: Option<bool>,
    sort_files: Option<bool>,
) -> AppResult<ScanAudioPathsResult> {
    let recursive = recursive.unwrap_or(true);
    let sort_files = sort_files.unwrap_or(true);
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_audio_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            if recursive {
                collect_audio_files(target, &mut files, &mut warnings);
            } else {
                collect_audio_files_shallow(target, &mut files, &mut warnings);
            }
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    if sort_files {
        files.sort();
        files.dedup();
    } else {
        files = stable_dedup(files);
    }
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_media_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_media_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_media_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}


fn resolve_reveal_path(app: &AppHandle, path: &str) -> AppResult<PathBuf> {
    let target = PathBuf::from(path);
    if target.is_absolute() {
        return Ok(target);
    }

    // Worker output paths may be entered as a relative directory. Keep this
    // resolution identical to the worker's PYMSS_STUDIO_DEFAULT_OUTPUT_DIR
    // handling so Explorer opens the directory where inference wrote files.
    let output_root = storage::outputs_dir(app)?;
    Ok(output_root
        .parent()
        .unwrap_or(output_root.as_path())
        .join(target))
}

#[tauri::command]
pub async fn reveal_path(app: AppHandle, path: String) -> AppResult<()> {
    let resolved = resolve_reveal_path(&app, &path)?;
    let target = resolved.as_path();
    let reveal_target = reveal_target_path(target);
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer").arg(reveal_target).spawn()?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open").arg(reveal_target).spawn()?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open").arg(reveal_target).spawn()?;
    }
    Ok(())
}

#[tauri::command]
pub async fn begin_editor_recording(
    app: AppHandle,
    project_id: String,
) -> AppResult<EditorRecordingHandle> {
    let recordings_dir = editor_recordings_dir(&app, &project_id)?;
    std::fs::create_dir_all(&recordings_dir)?;
    let base = now_millis();
    for suffix in 0..1000u16 {
        let recording_id = if suffix == 0 {
            format!("recording_{base}")
        } else {
            format!("recording_{base}_{suffix}")
        };
        let partial_path = recordings_dir.join(format!("{recording_id}.pcm.partial"));
        match OpenOptions::new().write(true).create_new(true).open(partial_path) {
            Ok(_) => return Ok(EditorRecordingHandle { recording_id }),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    Err(AppError::Worker("could not allocate a recording file".into()))
}

#[tauri::command]
pub async fn append_editor_recording_chunk(
    app: AppHandle,
    project_id: String,
    recording_id: String,
    data_base64: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data_base64)
        .map_err(|error| AppError::Worker(format!("invalid recording chunk: {error}")))?;
    if bytes.is_empty() || bytes.len() > 2 * 1024 * 1024 || bytes.len() % 2 != 0 {
        return Err(AppError::Worker("invalid recording chunk size".into()));
    }
    let partial_path = editor_recordings_dir(&app, &project_id)?
        .join(format!("{recording_id}.pcm.partial"));
    let mut file = OpenOptions::new().append(true).open(partial_path)?;
    file.write_all(&bytes)?;
    Ok(())
}

#[tauri::command]
pub async fn finish_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
    sample_rate: u32,
    channels: u16,
) -> AppResult<EditorRecordingResult> {
    let recording_id = validate_recording_id(&recording_id)?;
    let recordings_dir = editor_recordings_dir(&app, &project_id)?;
    let partial_path = recordings_dir.join(format!("{recording_id}.pcm.partial"));
    let data_len_u64 = std::fs::metadata(&partial_path)?.len();
    if data_len_u64 == 0 || data_len_u64 % 2 != 0 || data_len_u64 > u32::MAX as u64 - 36 {
        return Err(AppError::Worker("recording does not contain valid PCM audio".into()));
    }
    let data_len = data_len_u64 as u32;
    let output_path = recordings_dir.join(format!("{recording_id}.wav"));
    let output_partial_path = recordings_dir.join(format!("{recording_id}.wav.partial"));
    let mut input = std::fs::File::open(&partial_path)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output_partial_path)?;
    let write_result = (|| -> AppResult<()> {
        output.write_all(&pcm_wav_header(data_len, sample_rate, channels)?)?;
        std::io::copy(&mut input, &mut output)?;
        output.flush()?;
        output.sync_all()?;
        Ok(())
    })();
    drop(output);
    if let Err(error) = write_result {
        let _ = std::fs::remove_file(&output_partial_path);
        return Err(error);
    }
    std::fs::rename(&output_partial_path, &output_path)?;
    std::fs::remove_file(partial_path)?;

    let duration = data_len as f64 / (sample_rate as f64 * channels as f64 * 2.0);
    Ok(EditorRecordingResult {
        path: output_path.to_string_lossy().to_string(),
        name: format!("{recording_id}.wav"),
        duration,
        sample_rate,
        channels,
    })
}

#[tauri::command]
pub async fn cancel_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let partial_path = editor_recordings_dir(&app, &project_id)?
        .join(format!("{recording_id}.pcm.partial"));
    let wav_partial_path = editor_recordings_dir(&app, &project_id)?
        .join(format!("{recording_id}.wav.partial"));
    let mut first_error = None;
    for path in [partial_path, wav_partial_path] {
        if let Err(error) = std::fs::remove_file(path) {
            if error.kind() != std::io::ErrorKind::NotFound && first_error.is_none() {
                first_error = Some(error);
            }
        }
    }
    match first_error {
        Some(error) => Err(error.into()),
        None => Ok(()),
    }
}

#[tauri::command]
pub async fn discard_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let output_path = editor_recordings_dir(&app, &project_id)?
        .join(format!("{recording_id}.wav"));
    match std::fs::remove_file(output_path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

fn reveal_target_path(path: &Path) -> &Path {
    if path.is_file() {
        return path.parent().unwrap_or(path);
    }
    if path.is_dir() {
        return path;
    }
    path.ancestors()
        .skip(1)
        .find(|ancestor| ancestor.is_dir())
        .unwrap_or_else(|| path.parent().unwrap_or(path))
}

#[derive(Serialize)]
pub struct TrashResult {
    pub trashed: Vec<String>,
    pub failed: Vec<String>,
}


fn is_ignored_empty_dir_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name == ".DS_Store")
}

pub(crate) fn is_link_metadata(metadata: &std::fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}

fn is_effectively_empty_dir(path: &Path) -> bool {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if !metadata.is_dir() || is_link_metadata(&metadata) {
        return false;
    }
    let entries = match std::fs::read_dir(path) {
        Ok(entries) => entries,
        Err(_) => return false,
    };
    for entry in entries {
        let Ok(entry) = entry else {
            return false;
        };
        let child = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&child) else {
            return false;
        };
        if is_link_metadata(&metadata) {
            return false;
        }
        if metadata.is_dir() {
            if !is_effectively_empty_dir(&child) {
                return false;
            }
            continue;
        }
        if !metadata.is_file() || !is_ignored_empty_dir_file(&child) {
            return false;
        }
    }
    true
}

fn normalize_trash_empty_dirs(paths: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut dirs: Vec<String> = paths
        .into_iter()
        .filter(|path| !path.trim().is_empty())
        .filter(|path| seen.insert(normalize_path_key(path)))
        .collect();
    dirs.sort_by_key(|path| {
        std::cmp::Reverse(Path::new(path).components().count())
    });
    dirs
}

#[tauri::command]
pub async fn move_paths_to_trash(paths: Vec<String>, empty_dirs: Option<Vec<String>>) -> AppResult<TrashResult> {
    let mut trashed = Vec::new();
    let mut failed = Vec::new();
    for path in paths {
        let target = Path::new(&path);
        if !target.is_absolute() {
            failed.push(path);
            continue;
        }
        // 已不存在的路径视为已删除，无需报错
        if !target.exists() {
            trashed.push(path);
            continue;
        }
        if !target.is_file() {
            failed.push(path);
            continue;
        }
        match trash::delete(target) {
            Ok(()) => trashed.push(path),
            Err(_) => failed.push(path),
        }
    }
    for path in normalize_trash_empty_dirs(empty_dirs.unwrap_or_default()) {
        let target = Path::new(&path);
        if !target.exists() {
            trashed.push(path);
            continue;
        }
        if !target.is_absolute() || target.parent().is_none() || !target.is_dir() {
            continue;
        }
        if !is_effectively_empty_dir(target) {
            continue;
        }
        match trash::delete(target) {
            Ok(()) => trashed.push(path),
            Err(_) => failed.push(path),
        }
    }
    Ok(TrashResult { trashed, failed })
}

#[cfg(test)]
mod tests {
    use crate::commands::app::editor::{decode_editor_preview_chunk, editor_project_id_for_task_id, editor_project_ids_for_task_ids, orphaned_editor_projects, remove_editor_project_dirs, safe_file_name, validate_editor_project_id};
    use crate::commands::app::debug::validate_debug_runtime_override;
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_test_dir(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time should be after unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!("pymss-studio-{name}-{unique}"))
    }

    #[test]
    fn editor_project_id_validation_preserves_normal_project_identity() {
        for project_id in [
            "edit_task_1720000000000_abc12",
            "edit_blank_1720000000000",
            "project-with.dots",
        ] {
            assert_eq!(validate_editor_project_id(project_id).unwrap(), project_id);
        }
        let longest_id = "a".repeat(96);
        assert_eq!(validate_editor_project_id(&longest_id).unwrap(), longest_id);
    }

    #[test]
    fn editor_project_id_validation_rejects_path_and_label_aliases() {
        for project_id in [
            "", " ", ".", "..", "../project", "project/name", "project\\name",
            "project:name", "project?name", "project.", "_project", "project_", "目录",
        ] {
            assert!(validate_editor_project_id(project_id).is_err(), "accepted {project_id}");
        }
        let long_a = format!("{}a", "x".repeat(96));
        let long_b = format!("{}b", "x".repeat(96));
        assert_eq!(safe_file_name(&long_a), safe_file_name(&long_b));
        assert!(validate_editor_project_id(&long_a).is_err());
        assert!(validate_editor_project_id(&long_b).is_err());
    }

    #[test]
    fn effectively_empty_dir_allows_ds_store_and_empty_children() {
        let root = temp_test_dir("empty-dir-ds-store");
        let child = root.join("album").join("track");
        fs::create_dir_all(&child).expect("create nested test dirs");
        fs::write(root.join(".DS_Store"), b"finder metadata").expect("write ds store");
        fs::write(child.join(".DS_Store"), b"finder metadata").expect("write nested ds store");

        assert!(is_effectively_empty_dir(&root));

        fs::remove_dir_all(&root).expect("remove test dir");
    }

    #[test]
    fn effectively_empty_dir_rejects_user_files() {
        let root = temp_test_dir("empty-dir-user-file");
        fs::create_dir_all(&root).expect("create test dir");
        fs::write(root.join("vocals.wav"), b"audio").expect("write user file");

        assert!(!is_effectively_empty_dir(&root));

        fs::remove_dir_all(&root).expect("remove test dir");
    }

    #[test]
    fn editor_export_temp_cleanup_only_removes_the_cancelled_task_files() {
        let root = temp_test_dir("editor-export-temp-cleanup");
        fs::create_dir_all(&root).expect("create export temp root");
        let cancelled = root.join(".pymss-export-task_one-abcd.wav.part");
        let mix_buffer = root.join(".pymss-export-task_one-abcd.mix.part");
        let cancel_token = root.join(".pymss-export-task_one.cancel");
        let other = root.join(".pymss-export-task_two-abcd.wav.part");
        let completed = root.join("mix.wav");
        fs::write(&cancelled, b"partial").expect("write cancelled export temp");
        fs::write(&mix_buffer, b"partial mix").expect("write cancelled mix temp");
        fs::write(&cancel_token, b"cancel").expect("write cancelled export token");
        fs::write(&other, b"partial").expect("write other export temp");
        fs::write(&completed, b"audio").expect("write completed export");

        cleanup_editor_export_temp_files(&root, "task:one");

        assert!(!cancelled.exists());
        assert!(!mix_buffer.exists());
        assert!(!cancel_token.exists());
        assert!(other.exists());
        assert!(completed.exists());
        fs::remove_dir_all(&root).expect("remove export temp root");
    }

    #[test]
    fn reveal_target_falls_back_to_existing_parent_for_missing_file() {
        let root = temp_test_dir("reveal-missing-file");
        let missing = root.join("runtime").join("install.log");
        fs::create_dir_all(missing.parent().unwrap()).expect("create reveal parent");

        assert_eq!(reveal_target_path(&missing), missing.parent().unwrap());

        fs::remove_dir_all(&root).expect("remove reveal test dir");
    }

    #[test]
    fn reveal_target_uses_the_file_parent_for_existing_file() {
        let root = temp_test_dir("reveal-existing-file");
        let file = root.join("install.log");
        fs::create_dir_all(&root).expect("create reveal root");
        fs::write(&file, b"log").expect("write reveal file");

        assert_eq!(reveal_target_path(&file), root.as_path());

        fs::remove_dir_all(&root).expect("remove reveal test dir");
    }

    #[test]
    fn pcm_wav_header_describes_mono_pcm16_audio() {
        let header = pcm_wav_header(96_000, 48_000, 1).expect("create wav header");

        assert_eq!(&header[0..4], b"RIFF");
        assert_eq!(u32::from_le_bytes(header[4..8].try_into().unwrap()), 96_036);
        assert_eq!(&header[8..12], b"WAVE");
        assert_eq!(u16::from_le_bytes(header[20..22].try_into().unwrap()), 1);
        assert_eq!(u16::from_le_bytes(header[22..24].try_into().unwrap()), 1);
        assert_eq!(u32::from_le_bytes(header[24..28].try_into().unwrap()), 48_000);
        assert_eq!(u32::from_le_bytes(header[28..32].try_into().unwrap()), 96_000);
        assert_eq!(u16::from_le_bytes(header[34..36].try_into().unwrap()), 16);
        assert_eq!(u32::from_le_bytes(header[40..44].try_into().unwrap()), 96_000);
    }

    #[test]
    fn recording_id_validation_rejects_path_components() {
        assert!(validate_recording_id("recording_1720000000000").is_ok());
        assert!(validate_recording_id("../recording_1720000000000").is_err());
        assert!(validate_recording_id("recording_foo/bar").is_err());
        assert!(validate_recording_id("other_1720000000000").is_err());
    }

    #[test]
    fn debug_runtime_override_validates_the_interpreter_and_backend() {
        let root = temp_test_dir("runtime-debug-override");
        let python = root.join("cuda").join("bin").join("python");
        fs::create_dir_all(python.parent().unwrap()).expect("create runtime environment");
        fs::write(&python, b"stub").expect("write runtime interpreter");

        let (backend, resolved) = validate_debug_runtime_override(" CUDA ", &python.to_string_lossy())
            .expect("validate runtime override");
        assert_eq!(backend, "cuda");
        assert_eq!(PathBuf::from(resolved), python.canonicalize().unwrap());
        assert!(validate_debug_runtime_override("cpu", &python.to_string_lossy()).is_err());
        assert!(validate_debug_runtime_override("cuda", "missing/python").is_err());

        fs::remove_dir_all(&root).expect("remove runtime override test root");
    }

    #[test]
    fn editor_project_ids_for_tasks_are_safe_and_deduplicated() {
        let task_ids = vec![
            " task/one ".to_string(),
            "task/one".to_string(),
            "".to_string(),
            "task-two".to_string(),
        ];

        assert_eq!(
            editor_project_ids_for_task_ids(&task_ids),
            vec!["edit_task_one".to_string(), "edit_task-two".to_string()]
        );
    }

    #[test]
    fn editor_project_cleanup_removes_existing_dirs_and_reports_missing_ones() {
        let root = temp_test_dir("editor-project-cleanup");
        let existing_id = "edit_existing".to_string();
        let missing_id = "edit_missing".to_string();
        let recordings = root.join(&existing_id).join("recordings");
        fs::create_dir_all(&recordings).expect("create editor project directory");
        fs::write(recordings.join("take.wav"), b"audio").expect("write editor recording");
        fs::write(
            root.join(&existing_id).join("project.json"),
            r#"{"id":"edit_existing","sourceTaskId":"existing"}"#,
        ).expect("write editor project ownership");

        let result = remove_editor_project_dirs(
            &root,
            &[existing_id.clone(), missing_id.clone()],
            &["existing".to_string()],
        );

        assert_eq!(result.deleted_project_ids, vec![existing_id.clone()]);
        assert_eq!(result.missing_project_ids, vec![missing_id]);
        assert!(result.blocked_project_ids.is_empty());
        assert!(result.failed_project_ids.is_empty());
        assert!(!root.join(existing_id).exists());
        fs::remove_dir_all(&root).expect("remove editor cleanup test root");
    }

    #[test]
    fn orphaned_editor_projects_only_returns_unlinked_task_projects() {
        let root = temp_test_dir("orphaned-editor-projects");
        let active_dir = root.join("edit_active");
        let orphan_dir = root.join("edit_orphan");
        let blank_dir = root.join("edit_blank_1");
        fs::create_dir_all(&active_dir).expect("create active editor project");
        fs::create_dir_all(&orphan_dir).expect("create orphaned editor project");
        fs::create_dir_all(&blank_dir).expect("create blank editor project");
        fs::write(
            active_dir.join("project.json"),
            r#"{"id":"edit_active","name":"Active","sourceTaskId":"task-active"}"#,
        )
        .expect("write active editor project");
        fs::write(
            orphan_dir.join("project.json"),
            r#"{"id":"edit_orphan","name":"Orphan","sourceTaskId":"task-orphan"}"#,
        )
        .expect("write orphaned editor project");
        fs::write(
            blank_dir.join("project.json"),
            r#"{"id":"edit_blank_1","name":"Blank"}"#,
        )
        .expect("write blank editor project");

        let projects = orphaned_editor_projects(&root, &["task-active".to_string()])
            .expect("list orphaned editor projects");

        assert_eq!(projects.len(), 1);
        assert_eq!(projects[0].project_id, "edit_orphan");
        assert_eq!(projects[0].source_task_id, "task-orphan");
        fs::remove_dir_all(&root).expect("remove orphaned project test root");
    }

    #[test]
    fn editor_project_cleanup_rejects_lossy_task_id_collisions_and_unknown_ownership() {
        let root = temp_test_dir("editor-project-ownership");
        let project_id = editor_project_id_for_task_id("a:b");
        assert_eq!(project_id, editor_project_id_for_task_id("a/b"));
        let project_dir = root.join(&project_id);
        fs::create_dir_all(&project_dir).expect("create project directory");
        let project_path = project_dir.join("project.json");
        fs::write(&project_path, r#"{"id":"edit_a_b","sourceTaskId":"a:b"}"#).unwrap();
        let result = remove_editor_project_dirs(&root, &[project_id.clone()], &["a/b".to_string()]);
        assert_eq!(result.failed_project_ids, vec![project_id.clone()]);
        assert!(project_dir.is_dir());
        for content in [r#"{"id":"edit_a_b"}"#, r#"{"id":"other","sourceTaskId":"a:b"}"#, "invalid"] {
            fs::write(&project_path, content).unwrap();
            let result = remove_editor_project_dirs(&root, &[project_id.clone()], &["a:b".to_string()]);
            assert_eq!(result.failed_project_ids, vec![project_id.clone()]);
            assert!(project_dir.is_dir());
        }
        fs::write(&project_path, r#"{"id":"edit_a_b","sourceTaskId":"a:b"}"#).unwrap();
        let result = remove_editor_project_dirs(&root, &[project_id.clone()], &[" a:b ".to_string()]);
        assert_eq!(result.deleted_project_ids, vec![project_id]);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn native_path_keys_preserve_posix_case_and_literal_backslashes() {
        if cfg!(windows) {
            assert_eq!(normalize_path_key("C:\\Audio\\Song.wav"), normalize_path_key("c:/audio/song.wav"));
        } else {
            assert_ne!(normalize_path_key("/audio/Foo.wav"), normalize_path_key("/audio/foo.wav"));
            assert_ne!(normalize_path_key("/audio/a\\b.wav"), normalize_path_key("/audio/a/b.wav"));
            assert_eq!(normalize_trash_empty_dirs(vec!["/audio/Foo".into(), "/audio/foo".into()]).len(), 2);
        }
    }

    #[test]
    fn empty_directory_checks_do_not_follow_links_or_directory_cycles() {
        let root = temp_test_dir("empty-directory-links");
        fs::create_dir_all(&root).unwrap();
        assert!(is_effectively_empty_dir(&root));
        fs::write(root.join(".DS_Store"), b"metadata").unwrap();
        assert!(is_effectively_empty_dir(&root));
        let link = root.join("loop");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&root, &link).unwrap();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let status = std::process::Command::new("cmd.exe")
                .raw_arg(format!("/c mklink /J \"{}\" \"{}\"", link.display(), root.display()))
                .output().expect("create directory junction");
            assert!(status.status.success());
        }
        assert!(!is_effectively_empty_dir(&link));
        assert!(!is_effectively_empty_dir(&root));
        let result = remove_editor_project_dirs(&root, &["loop".to_string()], &["task".to_string()]);
        assert_eq!(result.failed_project_ids, vec!["loop".to_string()]);
        assert!(root.is_dir());
        #[cfg(unix)]
        fs::remove_file(&link).unwrap();
        #[cfg(windows)]
        fs::remove_dir(&link).unwrap();
        assert!(is_effectively_empty_dir(&root));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn invalid_requests_are_rejected_before_spawning() {
        for (rate, channels, index) in [(0, 2, 0), (48_000, 3, 0), (48_000, 2, u64::MAX)] {
            assert!(decode_editor_preview_chunk(Path::new("not-a-program"), Path::new("/missing"), index, rate, channels).is_err());
        }
        assert!(decode_editor_preview_chunk(Path::new("not-a-program"), Path::new("relative.wav"), 0, 48_000, 2).is_err());
    }

    fn preview_wave_fixture(rate: u32) -> PathBuf {
        let mut pcm = Vec::new();
        for frame in 0..rate * 9 {
            let sample = ((frame % 997) as i16 - 498) * 40;
            pcm.extend_from_slice(&sample.to_le_bytes());
            pcm.extend_from_slice(&(-sample).to_le_bytes());
        }
        let mut wav = b"RIFF".to_vec();
        wav.extend_from_slice(&(pcm.len() as u32 + 36).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&rate.to_le_bytes());
        wav.extend_from_slice(&(rate * 4).to_le_bytes());
        wav.extend_from_slice(&4u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
        wav.extend_from_slice(&pcm);
        let path = temp_test_dir("preview-chunks").with_extension("wav");
        std::fs::write(&path, wav).unwrap();
        path
    }

    #[test]
    fn consecutive_chunks_match_a_full_decode_without_missing_or_repeating_samples() {
        let ffmpeg = Path::new("ffmpeg");
        if Command::new(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping native codec check");
            return;
        }
        for source_rate in [44_100, 48_000, 96_000] {
            let path = preview_wave_fixture(source_rate);
            for rate in [44_100, 48_000] {
                let full = Command::new(ffmpeg).args(["-v", "error", "-i"]).arg(&path)
                    .arg("-ar").arg(rate.to_string())
                    .args(["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]).output().unwrap();
                let pieces: Vec<_> = (0..3).map(|index| decode_editor_preview_chunk(ffmpeg, &path, index, rate, 2).unwrap()).collect();
                assert!(full.status.success());
                assert_eq!(pieces[0].len(), rate as usize * 4 * 2 * 4);
                assert_eq!(pieces[2].len(), rate as usize * 2 * 4);
                assert_eq!(pieces.concat(), full.stdout, "resampling {source_rate} to {rate} changed chunk samples");
            }
            std::fs::remove_file(path).unwrap();
        }
    }

    #[test]
    fn compressed_preview_chunks_keep_gapless_timing_and_sample_alignment() {
        let ffmpeg = Path::new("ffmpeg");
        if Command::new(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping compressed codec check");
            return;
        }
        let wave = preview_wave_fixture(44_100);
        for (codec, extension) in [("libmp3lame", "mp3"), ("aac", "m4a"), ("aac", "aac"), ("libopus", "opus")] {
            let encoded = wave.with_extension(extension);
            let mut encoder = Command::new(ffmpeg);
            encoder.args(["-v", "error", "-y", "-i"]).arg(&wave).arg("-c:a").arg(codec);
            // Make the AAC fixture deterministic, without random perceptual noise.
            if codec == "aac" { encoder.args(["-aac_pns", "0"]); }
            let encoded_result = encoder.arg(&encoded).output().unwrap();
            if !encoded_result.status.success() {
                eprintln!("Encoder {codec} unavailable; skipping its fixture");
                continue;
            }
            for rate in [44_100, 48_000] {
                let full = Command::new(ffmpeg).args(["-v", "error", "-threads", "1", "-i"]).arg(&encoded)
                    .arg("-ar").arg(rate.to_string())
                    .args(["-ac", "2", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"])
                    .output().unwrap();
                assert!(full.status.success());
                let pieces: Vec<_> = (0..3).map(|index| decode_editor_preview_chunk(ffmpeg, &encoded, index, rate, 2).unwrap()).collect();
                let joined = pieces.concat();
                assert_eq!(joined.len(), full.stdout.len(), "{extension} at {rate} lost samples at a chunk boundary");
                let max_error = joined.chunks_exact(4).zip(full.stdout.chunks_exact(4))
                    .map(|(a, b)| (f32::from_le_bytes(a.try_into().unwrap()) - f32::from_le_bytes(b.try_into().unwrap())).abs())
                    .fold(0.0f32, f32::max);
                assert!(max_error < 0.00001, "{extension} at {rate} chunk alignment error: {max_error}");
            }
            fs::remove_file(&encoded).unwrap();
        }
        fs::remove_file(wave).unwrap();
    }
}
