//! 编辑器域：项目 CRUD、素材导入与重链、音频预览、混音导出。
//!
//! 从 app_cmd.rs 拆出；命令形状与路径不变，main.rs 只改注册路径。

use crate::error::{AppError, AppResult};
use crate::python::worker::{
    bundled_bin_dirs, cleanup_editor_export_temp_files, editor_export_task_token, run_worker_once,
    run_worker_with_payload, spawn_worker_background,
};
use crate::session_log;
use crate::state::AppState;
use crate::storage;
use crate::workflow_editors::{self, WorkflowEditorKind, WorkflowEditors};
use base64::Engine as _;
use serde::Serialize;
use std::fs::OpenOptions;
use std::io::Write;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindowBuilder};


use crate::commands::app::dialogs::path_to_string;
use crate::commands::app::tasks::cancel_task;
use crate::commands::app::trash::is_link_metadata;
use crate::commands::app::scan::{is_audio_file, scan_audio_paths};
use crate::commands::app_cmd::{
    build_file_name_index, build_relink_candidate, derive_relink_root, enrich_editor_project_sources,
    linked_asset_from_path, normalize_path_key,
    preferred_match_by_relative_path,
};

fn editor_projects_root(app: &AppHandle) -> AppResult<PathBuf> {
    storage::editor_projects_dir(app)
}

pub(crate) fn safe_file_name(value: &str) -> String {
    let name: String = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.') {
                ch
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = name.trim_matches('_');
    if trimmed.is_empty() {
        "project".to_string()
    } else {
        trimmed.chars().take(96).collect()
    }
}

pub(crate) fn validate_editor_project_id(project_id: &str) -> AppResult<&str> {
    if project_id.is_empty()
        || matches!(project_id, "." | "..")
        || project_id.ends_with('.')
        || safe_file_name(project_id) != project_id
    {
        return Err(AppError::Worker("invalid editor project id".into()));
    }
    Ok(project_id)
}

fn editor_project_dir(app: &AppHandle, project_id: &str) -> AppResult<PathBuf> {
    Ok(editor_projects_root(app)?.join(validate_editor_project_id(project_id)?))
}

pub(crate) fn editor_project_id_for_task_id(task_id: &str) -> String {
    format!("edit_{}", safe_file_name(task_id))
}

pub(crate) fn editor_project_ids_for_task_ids(task_ids: &[String]) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    task_ids
        .iter()
        .map(|task_id| task_id.trim())
        .filter(|task_id| !task_id.is_empty())
        .map(editor_project_id_for_task_id)
        .filter(|project_id| seen.insert(project_id.clone()))
        .collect()
}

fn editor_project_path(app: &AppHandle, project_id: &str) -> AppResult<PathBuf> {
    Ok(editor_project_dir(app, project_id)?.join("project.json"))
}

pub(crate) fn editor_recordings_dir(app: &AppHandle, project_id: &str) -> AppResult<PathBuf> {
    Ok(editor_project_dir(app, project_id)?.join("recordings"))
}

pub(crate) fn validate_recording_id(recording_id: &str) -> AppResult<&str> {
    if recording_id.starts_with("recording_")
        && recording_id.len() <= 96
        && safe_file_name(recording_id) == recording_id
    {
        Ok(recording_id)
    } else {
        Err(AppError::Worker("invalid recording id".into()))
    }
}

pub(crate) fn pcm_wav_header(data_len: u32, sample_rate: u32, channels: u16) -> AppResult<[u8; 44]> {
    if !(8_000..=192_000).contains(&sample_rate) || !(1..=2).contains(&channels) {
        return Err(AppError::Worker("invalid recording audio format".into()));
    }
    let byte_rate = sample_rate
        .checked_mul(channels as u32)
        .and_then(|value| value.checked_mul(2))
        .ok_or_else(|| AppError::Worker("recording audio format is too large".into()))?;
    let riff_size = data_len
        .checked_add(36)
        .ok_or_else(|| AppError::Worker("recording is too large".into()))?;
    let block_align = channels * 2;
    let mut header = [0u8; 44];
    header[0..4].copy_from_slice(b"RIFF");
    header[4..8].copy_from_slice(&riff_size.to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");
    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes());
    header[20..22].copy_from_slice(&1u16.to_le_bytes());
    header[22..24].copy_from_slice(&channels.to_le_bytes());
    header[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&block_align.to_le_bytes());
    header[34..36].copy_from_slice(&16u16.to_le_bytes());
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_len.to_le_bytes());
    Ok(header)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct EditorRecordingHandle {
    pub(crate) recording_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct EditorRecordingResult {
    pub(crate) path: String,
    pub(crate) name: String,
    pub(crate) duration: f64,
    pub(crate) sample_rate: u32,
    pub(crate) channels: u16,
}

#[derive(Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditorProjectCleanupResult {
    pub(crate) deleted_project_ids: Vec<String>,
    pub(crate) missing_project_ids: Vec<String>,
    pub(crate) blocked_project_ids: Vec<String>,
    pub(crate) failed_project_ids: Vec<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OrphanedEditorProject {
    pub(crate) project_id: String,
    pub(crate) source_task_id: String,
    pub(crate) name: String,
}

pub(crate) fn remove_editor_project_dirs(root: &Path, project_ids: &[String], task_ids: &[String]) -> EditorProjectCleanupResult {
    let mut result = EditorProjectCleanupResult::default();
    let task_ids: std::collections::HashSet<&str> = task_ids.iter().map(|task_id| task_id.trim()).filter(|task_id| !task_id.is_empty()).collect();
    for project_id in project_ids {
        if validate_editor_project_id(project_id).is_err() {
            result.failed_project_ids.push(project_id.clone());
            continue;
        }
        let project_dir = root.join(project_id);
        let metadata = match std::fs::symlink_metadata(&project_dir) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                result.missing_project_ids.push(project_id.clone());
                continue;
            }
            Err(_) => {
                result.failed_project_ids.push(project_id.clone());
                continue;
            }
        };
        if !metadata.is_dir() || is_link_metadata(&metadata) {
            result.failed_project_ids.push(project_id.clone());
            continue;
        }
        let project_path = project_dir.join("project.json");
        let project = std::fs::symlink_metadata(&project_path)
            .ok()
            .filter(|metadata| metadata.is_file() && !is_link_metadata(metadata))
            .and_then(|_| std::fs::read_to_string(&project_path).ok())
            .and_then(|content| serde_json::from_str::<Value>(&content).ok());
        let owned = project.as_ref().is_some_and(|project| {
            project.get("id").and_then(Value::as_str) == Some(project_id.as_str())
                && project.get("sourceTaskId").and_then(Value::as_str)
                    .is_some_and(|task_id| !task_id.is_empty() && task_ids.contains(task_id))
        });
        if !owned {
            result.failed_project_ids.push(project_id.clone());
            continue;
        }
        match std::fs::remove_dir_all(project_dir) {
            Ok(()) => result.deleted_project_ids.push(project_id.clone()),
            Err(_) => result.failed_project_ids.push(project_id.clone()),
        }
    }
    result
}

fn open_editor_project_ids(app: &AppHandle, project_ids: &[String]) -> Vec<String> {
    project_ids
        .iter()
        .filter(|project_id| {
            let label = format!("editor-{}", safe_file_name(project_id));
            app.get_webview_window(&label).is_some()
        })
        .cloned()
        .collect()
}

pub(crate) fn orphaned_editor_projects(
    root: &Path,
    active_task_ids: &[String],
) -> AppResult<Vec<OrphanedEditorProject>> {
    let active_task_ids: std::collections::HashSet<&str> = active_task_ids
        .iter()
        .map(|task_id| task_id.trim())
        .filter(|task_id| !task_id.is_empty())
        .collect();
    if !root.is_dir() {
        return Ok(Vec::new());
    }

    let mut projects = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let project_id = entry.file_name().to_string_lossy().to_string();
        let project_path = entry.path().join("project.json");
        let Ok(content) = std::fs::read_to_string(project_path) else {
            continue;
        };
        let Ok(project) = serde_json::from_str::<Value>(&content) else {
            continue;
        };
        let Some(source_task_id) = project
            .get("sourceTaskId")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|source_task_id| !source_task_id.is_empty())
        else {
            continue;
        };
        if active_task_ids.contains(source_task_id) {
            continue;
        }
        let name = project
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .unwrap_or(&project_id)
            .to_string();
        projects.push(OrphanedEditorProject {
            project_id,
            source_task_id: source_task_id.to_string(),
            name,
        });
    }
    projects.sort_by(|left, right| {
        left.name
            .cmp(&right.name)
            .then_with(|| left.project_id.cmp(&right.project_id))
    });
    Ok(projects)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditorProjectSummary {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) source_task_id: Option<String>,
    pub(crate) source_result_dir: Option<String>,
    pub(crate) created_at: u64,
    pub(crate) updated_at: u64,
    #[serde(rename = "type")]
    pub(crate) project_type: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct LinkedEditorAsset {
    pub(crate) path: String,
    pub(crate) name: String,
    pub(crate) origin_kind: String,
    pub(crate) origin_root: Option<String>,
    pub(crate) relative_path: Option<String>,
    pub(crate) missing: bool,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportEditorAssetsResult {
    pub files: Vec<LinkedEditorAsset>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RelinkEditorSourcesResult {
    pub project: Value,
    pub relinked: usize,
    pub unresolved: Vec<String>,
}

pub(crate) fn now_millis() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or_default()
}

pub(crate) fn file_name_from_path(path: &str) -> String {
    Path::new(path)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(path)
        .to_string()
}

fn stem_from_path(path: &str) -> String {
    if let Some(stem) = Path::new(path).file_stem().and_then(|value| value.to_str()) {
        stem.to_string()
    } else {
        file_name_from_path(path)
    }
}

fn summary_from_project_value(project: &Value, fallback_id: &str) -> EditorProjectSummary {
    let id = project
        .get("id")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(fallback_id)
        .to_string();
    let source_task_id = project
        .get("sourceTaskId")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let source_result_dir = project
        .get("sourceResultDir")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let project_type = if source_task_id.is_some() || source_result_dir.is_some() {
        "task"
    } else {
        "blank"
    }
    .to_string();

    EditorProjectSummary {
        id: id.clone(),
        name: project
            .get("name")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .unwrap_or("Untitled Project")
            .to_string(),
        source_task_id,
        source_result_dir,
        created_at: project
            .get("createdAt")
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        updated_at: project
            .get("updatedAt")
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        project_type,
    }
}

fn stem_rank(stem: &str) -> usize {
    let lower = stem.to_ascii_lowercase();
    if lower.contains("vocal") || lower.contains("voice") {
        0
    } else if lower.contains("instrument")
        || lower.contains("accompaniment")
        || lower.contains("karaoke")
    {
        1
    } else if lower.contains("drum") {
        2
    } else if lower.contains("bass") {
        3
    } else if lower.contains("other") {
        4
    } else {
        9
    }
}

fn write_editor_project(app: &AppHandle, project: &Value) -> AppResult<Value> {
    let project_id = project
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing editor project id".into()))?;
    let dir = editor_project_dir(app, project_id)?;
    storage::write_editor_project_file(&dir.join("project.json"), project)?;
    session_log::record_editor_project(project);
    Ok(project.clone())
}


#[tauri::command]
pub async fn open_workflow_node_editor_window(
    app: AppHandle,
    editors: State<'_, WorkflowEditors>,
    payload: Value,
) -> AppResult<Value> {
    open_workflow_editor_window(app, editors, payload, WorkflowEditorKind::Advanced).await
}

#[tauri::command]
pub async fn open_workflow_simple_editor_window(
    app: AppHandle,
    editors: State<'_, WorkflowEditors>,
    payload: Value,
) -> AppResult<Value> {
    open_workflow_editor_window(app, editors, payload, WorkflowEditorKind::Simple).await
}

async fn open_workflow_editor_window(
    app: AppHandle,
    editors: State<'_, WorkflowEditors>,
    payload: Value,
    kind: WorkflowEditorKind,
) -> AppResult<Value> {
    let _application_access = crate::python::worker::acquire_application_operation()?;
    let _creation_guard = editors.creation_gate.lock().await;
    let workflow_id = payload
        .get("workflowId")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or("")
        .to_string();
    let new_workflow = payload
        .get("newWorkflow")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    let (prefix, route, title) = match kind {
        WorkflowEditorKind::Simple => (
            "workflow-simple-editor",
            "workflow-simple-editor",
            "Pymss Studio Simple Workflow Editor",
        ),
        WorkflowEditorKind::Advanced => (
            "workflow-node-editor",
            "workflow-node-editor",
            "Pymss Studio Workflow Node Editor",
        ),
    };
    let base_label = if workflow_id.is_empty() {
        format!("{prefix}-new")
    } else {
        format!("{prefix}-{}", safe_file_name(&workflow_id))
    };
    // Window labels remain fixed after saving a new workflow or a copy. An
    // occupied old label must not cause a different workflow to be reused.
    let (label, snapshot) = loop {
        let existing = editors.registry().editor_for_request(&workflow_id, kind);
        if let Some(existing) = existing {
            if let Some(window) = app.get_webview_window(&existing.label) {
                let snapshot = {
                    let registry = editors.registry();
                    if registry.editor_for_request(&workflow_id, kind).as_ref() != Some(&existing) {
                        continue;
                    }
                    registry.snapshot()
                };
                let _ = window.unminimize();
                let _ = window.set_focus();
                return Ok(serde_json::json!({
                    "workflowId": existing.workflow_id,
                    "label": existing.label,
                    "kind": existing.kind,
                    "opened": true,
                    "reused": true,
                    "editorSnapshot": snapshot,
                }));
            }
            let changed = editors.registry().release(&existing.label);
            if let Some(snapshot) = changed {
                workflow_editors::emit_snapshot(&app, &snapshot);
            }
            continue;
        }

        let mut sequence = 0u64;
        let label = loop {
            let base = if sequence == 0 {
                base_label.clone()
            } else {
                format!("{base_label}-{sequence}")
            };
            let candidate = editors.registry().next_available_label(&base);
            if app.get_webview_window(&candidate).is_none() {
                break candidate;
            }
            sequence += 1;
        };
        let snapshot = {
            let mut registry = editors.registry();
            // A first save can adopt this ID after our initial lookup.
            if registry.editor_for_request(&workflow_id, kind).is_some() {
                continue;
            }
            registry.reserve(&label, &workflow_id, kind)?
        };
        break (label, snapshot);
    };
    workflow_editors::emit_snapshot(&app, &snapshot);

    let url = if workflow_id.is_empty() {
        if new_workflow {
            format!("index.html#/{route}?new=1")
        } else {
            format!("index.html#/{route}")
        }
    } else {
        let encoded_id: String = url::form_urlencoded::byte_serialize(workflow_id.as_bytes()).collect();
        format!("index.html#/{route}?workflowId={encoded_id}")
    };
    let result = WebviewWindowBuilder::new(&app, &label, WebviewUrl::App(url.into()))
        .title(title)
        .inner_size(1440.0, 900.0)
        .min_inner_size(1180.0, 720.0)
        .resizable(true)
        .minimizable(true)
        .maximizable(true)
        .closable(true)
        .decorations(cfg!(target_os = "macos"))
        .visible(false)
        .focused(true)
        .on_page_load(|window, payload| {
            if payload.event() == PageLoadEvent::Finished {
                let _ = window.show();
                let _ = window.set_focus();
            }
        })
        .build();
    if let Err(error) = result {
        let changed = editors.registry().release(&label);
        if let Some(snapshot) = changed {
            workflow_editors::emit_snapshot(&app, &snapshot);
        }
        return Err(AppError::Worker(error.to_string()));
    }
    // The page may have acquired a formal ID or already closed while build()
    // returned. Read its current ownership; do not restore the initial entry.
    let snapshot = editors.registry().snapshot();
    let current_workflow_id = snapshot.editors.iter()
        .find(|editor| editor.label == label)
        .map(|editor| editor.workflow_id.as_str())
        .unwrap_or(&workflow_id);
    Ok(serde_json::json!({
        "workflowId": current_workflow_id,
        "label": label,
        "kind": kind,
        "opened": true,
        "reused": false,
        "editorSnapshot": snapshot,
    }))
}

#[tauri::command]
pub async fn open_editor_window(
    app: AppHandle,
    editors: State<'_, WorkflowEditors>,
    payload: Value,
) -> AppResult<Value> {
    let project_id = payload
        .get("projectId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing projectId".into()))?
        .to_string();
    validate_editor_project_id(&project_id)?;
    let _application_access = crate::python::worker::acquire_application_operation()?;
    let _creation_guard = editors.creation_gate.lock().await;
    let label = format!("editor-{project_id}");
    if let Some(window) = app.get_webview_window(&label) {
        let _ = window.unminimize();
        let _ = window.set_focus();
        return Ok(
            serde_json::json!({ "projectId": project_id, "label": label, "opened": true, "reused": true }),
        );
    }

    let encoded_id: String = url::form_urlencoded::byte_serialize(project_id.as_bytes()).collect();
    let encoded_label: String = url::form_urlencoded::byte_serialize(label.as_bytes()).collect();
    let url = format!("index.html#/editor?projectId={encoded_id}&windowLabel={encoded_label}");
    WebviewWindowBuilder::new(&app, &label, WebviewUrl::App(url.into()))
        .title("Pymss Studio Editor")
        .inner_size(1440.0, 900.0)
        .min_inner_size(1180.0, 720.0)
        .resizable(true)
        .minimizable(true)
        .maximizable(true)
        .closable(true)
        .decorations(cfg!(target_os = "macos"))
        .visible(false)
        .focused(true)
        .on_page_load(|window, payload| {
            if payload.event() == PageLoadEvent::Finished {
                let _ = window.show();
                let _ = window.set_focus();
            }
        })
        .build()
        .map_err(|error| AppError::Worker(error.to_string()))?;
    Ok(
        serde_json::json!({ "projectId": project_id, "label": label, "opened": true, "reused": false }),
    )
}

#[tauri::command]
pub async fn create_editor_project_from_task(app: AppHandle, payload: Value) -> AppResult<Value> {
    let task_id = payload
        .get("taskId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing taskId".into()))?;

    let input = payload
        .get("input")
        .and_then(Value::as_str)
        .unwrap_or("Untitled");
    let output_dir = payload
        .get("outputDir")
        .and_then(Value::as_str)
        .unwrap_or("");
    let outputs = payload
        .get("outputs")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut paths: Vec<(String, String)> = outputs
        .iter()
        .filter_map(|item| {
            let path = item.get("path").and_then(Value::as_str)?;
            let stem = item
                .get("stem")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| stem_from_path(path));
            Some((stem, path.to_string()))
        })
        .collect();

    if paths.is_empty() && !output_dir.is_empty() {
        let scanned = scan_audio_paths(vec![output_dir.to_string()]).await?;
        paths = scanned
            .files
            .into_iter()
            .map(|path| (stem_from_path(&path), path))
            .collect();
    }
    paths.sort_by(|a, b| {
        stem_rank(&a.0)
            .cmp(&stem_rank(&b.0))
            .then_with(|| a.0.cmp(&b.0))
    });

    let project_id = editor_project_id_for_task_id(task_id);
    let timestamp = now_millis();
    let output_root = if output_dir.trim().is_empty() {
        None
    } else {
        Some(PathBuf::from(output_dir))
    };

    let linked_paths: Vec<(String, LinkedEditorAsset)> = paths
        .into_iter()
        .map(|(stem, path)| {
            let source_path = PathBuf::from(&path);
            let relative = output_root
                .as_ref()
                .and_then(|root| source_path.strip_prefix(root).ok());
            (
                stem,
                linked_asset_from_path(
                    &source_path,
                    "task-result",
                    output_root.as_deref(),
                    relative,
                ),
            )
        })
        .collect();

    let sources: Vec<Value> = linked_paths
        .iter()
        .enumerate()
        .map(|(index, (stem, asset))| {
            serde_json::json!({
                "id": format!("source_{}_{}", index, safe_file_name(stem)),
                "role": "stem",
                "stemKey": stem,
                "path": asset.path,
                "name": asset.name,
                "duration": 0,
                "sampleRate": 0,
                "channels": 0,
                "peaksPath": Value::Null,
                "peaks": [],
                "originKind": asset.origin_kind,
                "originRoot": asset.origin_root,
                "relativePath": asset.relative_path,
                "missing": asset.missing,
            })
        })
        .collect();
    let tracks: Vec<Value> = linked_paths
        .iter()
        .enumerate()
        .map(|(index, (stem, _))| {
            serde_json::json!({
                "id": format!("track_{}", index),
                "sourceId": format!("source_{}_{}", index, safe_file_name(stem)),
                "role": "stem",
                "name": stem,
                "autoName": true,
                "color": Value::Null,
                "volume": 1,
                "muted": false,
                "solo": false,
                "fadeIn": 0,
                "fadeOut": 0
            })
        })
        .collect();

    let project = serde_json::json!({
        "version": 2,
        "id": project_id,
        "name": file_name_from_path(input),
        "sourceTaskId": task_id,
        "sourceResultDir": output_dir,
        "masterVolume": 1,
        "sources": sources,
        "tracks": tracks,
        "createdAt": timestamp,
        "updatedAt": timestamp
    });
    write_editor_project(&app, &project)
}

#[tauri::command]
pub async fn list_editor_projects(app: AppHandle) -> AppResult<Vec<EditorProjectSummary>> {
    let root = editor_projects_root(&app)?;
    std::fs::create_dir_all(&root)?;

    let mut items = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }

        let fallback_id = entry.file_name().to_string_lossy().to_string();
        let project_path = entry.path().join("project.json");
        if !project_path.is_file() {
            continue;
        }

        let content = match std::fs::read_to_string(&project_path) {
            Ok(content) => content,
            Err(_) => continue,
        };
        let project: Value = match serde_json::from_str(&content) {
            Ok(project) => project,
            Err(_) => continue,
        };
        items.push(summary_from_project_value(&project, &fallback_id));
    }

    items.sort_by(|a, b| {
        b.updated_at
            .cmp(&a.updated_at)
            .then_with(|| b.created_at.cmp(&a.created_at))
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(items)
}

#[tauri::command]
pub async fn create_blank_editor_project(app: AppHandle, payload: Value) -> AppResult<Value> {
    let custom_name = payload
        .get("name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let locale = payload
        .get("locale")
        .and_then(Value::as_str)
        .unwrap_or("zh-CN");
    let default_name = if locale == "en" {
        "Untitled Blank Project"
    } else {
        "未命名空工程"
    };
    let timestamp = now_millis() as u64;
    let project_id = format!("edit_blank_{}", timestamp);
    let project = serde_json::json!({
        "version": 2,
        "id": project_id,
        "name": custom_name.unwrap_or(default_name),
        "masterVolume": 1,
        "sources": [],
        "tracks": [],
        "createdAt": timestamp,
        "updatedAt": timestamp
    });
    write_editor_project(&app, &project)
}

#[tauri::command]
pub async fn delete_editor_project(app: AppHandle, project_id: String) -> AppResult<bool> {
    let label = format!("editor-{}", safe_file_name(&project_id));
    if app.get_webview_window(&label).is_some() {
        return Err(AppError::Worker("请先关闭该工程窗口后再删除".into()));
    }

    let project_dir = editor_project_dir(&app, &project_id)?;
    if !project_dir.exists() {
        return Ok(false);
    }

    std::fs::remove_dir_all(project_dir)?;
    Ok(true)
}

#[tauri::command]
pub async fn delete_editor_projects_for_tasks(
    app: AppHandle,
    task_ids: Vec<String>,
) -> AppResult<EditorProjectCleanupResult> {
    let project_ids = editor_project_ids_for_task_ids(&task_ids);
    let blocked_project_ids = open_editor_project_ids(&app, &project_ids);
    if !blocked_project_ids.is_empty() {
        return Ok(EditorProjectCleanupResult {
            blocked_project_ids,
            ..EditorProjectCleanupResult::default()
        });
    }

    let root = editor_projects_root(&app)?;
    Ok(remove_editor_project_dirs(&root, &project_ids, &task_ids))
}

#[tauri::command]
pub async fn list_open_editor_projects_for_tasks(
    app: AppHandle,
    task_ids: Vec<String>,
) -> AppResult<Vec<String>> {
    let project_ids = editor_project_ids_for_task_ids(&task_ids);
    Ok(open_editor_project_ids(&app, &project_ids))
}

#[tauri::command]
pub async fn list_orphaned_editor_projects(
    app: AppHandle,
    active_task_ids: Vec<String>,
) -> AppResult<Vec<OrphanedEditorProject>> {
    let root = editor_projects_root(&app)?;
    orphaned_editor_projects(&root, &active_task_ids)
}

#[tauri::command]
pub async fn delete_orphaned_editor_projects(
    app: AppHandle,
    active_task_ids: Vec<String>,
) -> AppResult<EditorProjectCleanupResult> {
    let root = editor_projects_root(&app)?;
    let projects = orphaned_editor_projects(&root, &active_task_ids)?;
    let project_ids: Vec<String> = projects.iter().map(|project| project.project_id.clone()).collect();
    let task_ids: Vec<String> = projects.iter().map(|project| project.source_task_id.clone()).collect();
    let blocked_project_ids = open_editor_project_ids(&app, &project_ids);
    if !blocked_project_ids.is_empty() {
        return Ok(EditorProjectCleanupResult {
            blocked_project_ids,
            ..EditorProjectCleanupResult::default()
        });
    }
    Ok(remove_editor_project_dirs(&root, &project_ids, &task_ids))
}


#[tauri::command]
pub async fn import_editor_assets(
    _app: AppHandle,
    _project_id: String,
    paths: Vec<String>,
) -> AppResult<ImportEditorAssetsResult> {
    let mut imported = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = PathBuf::from(&raw);
        if target.is_file() {
            if is_audio_file(&target) {
                let origin_root = target.parent().map(PathBuf::from);
                imported.push(linked_asset_from_path(
                    &target,
                    "external",
                    origin_root.as_deref(),
                    target.file_name().map(Path::new),
                ));
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
            continue;
        }

        if target.is_dir() {
            let scanned = scan_audio_paths(vec![raw.clone()]).await?;
            warnings.extend(scanned.warnings);
            for file in scanned.files {
                let file_path = PathBuf::from(&file);
                let relative = file_path.strip_prefix(&target).ok();
                imported.push(linked_asset_from_path(
                    &file_path,
                    "external",
                    Some(target.as_path()),
                    relative,
                ));
            }
            continue;
        }

        warnings.push(format!("path not found: {}", raw));
    }
    imported.sort_by(|a, b| normalize_path_key(&a.path).cmp(&normalize_path_key(&b.path)));
    imported.dedup_by(|a, b| normalize_path_key(&a.path) == normalize_path_key(&b.path));
    Ok(ImportEditorAssetsResult { files: imported, warnings })
}

#[tauri::command]
pub async fn get_audio_metadata(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "audio_metadata", Some(payload))
}

const EDITOR_PREVIEW_CHUNK_SECONDS: u64 = 4;

pub(crate) fn decode_editor_preview_chunk(ffmpeg: &Path, path: &Path, index: u64, sample_rate: u32, channels: u32) -> AppResult<Vec<u8>> {
    if !(8_000..=192_000).contains(&sample_rate) || !(1..=2).contains(&channels) || index > 216_000 {
        return Err(AppError::Worker("Invalid preview chunk parameters".into()));
    }
    if !path.is_absolute() || !path.is_file() {
        return Err(AppError::Worker("Preview audio file is unavailable".into()));
    }
    let mut command = Command::new(ffmpeg);
    let start = index * EDITOR_PREVIEW_CHUNK_SECONDS;
    let warm_start = start.saturating_sub(1);
    // Warm compressed decoders before the boundary, preserve original stream
    // timestamps, and trim there. A direct input seek can discard AAC priming
    // samples twice or begin MP3 decoding without its previous-frame state.
    command.args(["-nostdin", "-v", "error", "-threads", "1", "-copyts", "-start_at_zero"]);
    // Even an input seek to zero can reapply MP4/AAC priming. The first chunk
    // must follow the normal decode path from the beginning.
    if start > 0 { command.arg("-ss").arg(warm_start.to_string()); }
    command.arg("-i").arg(path)
        .args(["-map", "0:a:0", "-vn", "-af"])
        // Reset the warmup window's timestamps before resampling, keeping its
        // phase on the same sample grid as a full decode (including MP4/AAC).
        // Trim AFTER resampling so both boundaries retain neighboring samples.
        .arg(format!("atrim=start={warm_start},asetpts=PTS-{warm_start}/TB,aresample={sample_rate}:first_pts=0,atrim=start={}:duration={EDITOR_PREVIEW_CHUNK_SECONDS},asetpts=PTS-STARTPTS", start - warm_start))
        .arg("-t").arg(EDITOR_PREVIEW_CHUNK_SECONDS.to_string())
        .arg("-ar").arg(sample_rate.to_string())
        .arg("-ac").arg(channels.to_string())
        .args(["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let output = command.output()?;
    if !output.status.success() {
        return Err(AppError::Worker(format!("Preview decode failed: {}", String::from_utf8_lossy(&output.stderr))));
    }
    let frame_bytes = channels as usize * 4;
    let limit = EDITOR_PREVIEW_CHUNK_SECONDS as usize * sample_rate as usize * frame_bytes;
    if output.stdout.len() > limit || output.stdout.len() % frame_bytes != 0 {
        return Err(AppError::Worker("Invalid decoded preview chunk".into()));
    }
    Ok(output.stdout)
}

#[tauri::command]
pub async fn editor_preview_chunk(app: AppHandle, path: String, index: u64, sample_rate: u32, channels: u32) -> AppResult<tauri::ipc::Response> {
    let ffmpeg_name = if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" };
    let ffmpeg = bundled_bin_dirs(&app)?.into_iter()
        .map(|dir| dir.join(ffmpeg_name)).find(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from(ffmpeg_name));
    let data = tauri::async_runtime::spawn_blocking(move || {
        decode_editor_preview_chunk(&ffmpeg, Path::new(&path), index, sample_rate, channels)
    }).await.map_err(|error| AppError::Worker(error.to_string()))??;
    Ok(tauri::ipc::Response::new(data))
}

#[tauri::command]
pub async fn generate_waveform_peaks(app: AppHandle, payload: Value) -> AppResult<Value> {
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        if !object.contains_key("cacheDir") {
            let project_id = object
                .get("projectId")
                .and_then(Value::as_str)
                .unwrap_or("shared");
            let cache_dir = editor_project_dir(&app, project_id)?.join("peaks");
            std::fs::create_dir_all(&cache_dir)?;
            object.insert(
                "cacheDir".into(),
                Value::String(cache_dir.to_string_lossy().to_string()),
            );
        }
    }
    run_worker_with_payload(&app, "waveform_peaks", Some(payload))
}

#[tauri::command]
pub async fn save_editor_project(app: AppHandle, project: Value) -> AppResult<Value> {
    let mut project = project;
    if let Some(object) = project.as_object_mut() {
        object.insert("updatedAt".into(), Value::from(now_millis() as u64));
    }
    write_editor_project(&app, &project)
}

#[tauri::command]
pub async fn load_editor_project(app: AppHandle, project_id: String) -> AppResult<Value> {
    let path = editor_project_path(&app, &project_id)?;
    let content = std::fs::read_to_string(path)?;
    let mut project: Value = serde_json::from_str(&content)?;
    if enrich_editor_project_sources(&mut project) {
        write_editor_project(&app, &project)?;
    }
    session_log::record_editor_project(&project);
    Ok(project)
}

#[tauri::command]
pub async fn relink_editor_sources(
    app: AppHandle,
    payload: Value,
) -> AppResult<RelinkEditorSourcesResult> {
    let project_id = payload
        .get("projectId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing projectId".into()))?;
    let source_id = payload
        .get("sourceId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing sourceId".into()))?;
    let picked_path = payload
        .get("pickedPath")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing pickedPath".into()))?;

    let path = editor_project_path(&app, project_id)?;
    let content = std::fs::read_to_string(&path)?;
    let mut project: Value = serde_json::from_str(&content)?;
    enrich_editor_project_sources(&mut project);

    let sources = project
        .get_mut("sources")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| AppError::Worker("editor project has no sources".into()))?;
    let anchor_source = sources
        .iter()
        .find(|source| source.get("id").and_then(Value::as_str) == Some(source_id))
        .cloned()
        .ok_or_else(|| AppError::Worker("source not found".into()))?;

    let anchor_relative = anchor_source.get("relativePath").and_then(Value::as_str);
    let picked_path_buf = PathBuf::from(picked_path);
    if !picked_path_buf.is_file() {
        return Err(AppError::Worker("picked relink file does not exist".into()));
    }

    let relink_root = derive_relink_root(&picked_path_buf, anchor_relative)
        .or_else(|| picked_path_buf.parent().map(PathBuf::from))
        .ok_or_else(|| AppError::Worker("failed to resolve relink root".into()))?;
    let file_name_index = build_file_name_index(&relink_root);

    let mut relinked = 0usize;
    let mut unresolved = Vec::new();

    for source in sources.iter_mut() {
        let Some(source_object) = source.as_object_mut() else {
            continue;
        };
        // Only attempt to repoint sources that are actually missing.
        // Healthy sources must keep their existing path/originRoot untouched,
        // otherwise a bulk relink could silently steal them into the relink root.
        if !source_object
            .get("missing")
            .and_then(Value::as_bool)
            .unwrap_or(false)
        {
            continue;
        }
        let relative_path = source_object.get("relativePath").and_then(Value::as_str);
        let fallback_name = source_object
            .get("path")
            .and_then(Value::as_str)
            .and_then(|value| Path::new(value).file_name().and_then(|name| name.to_str()));
        let candidate = build_relink_candidate(&relink_root, relative_path, &file_name_index)
            .or_else(|| {
                let file_name = fallback_name?.to_ascii_lowercase();
                let candidates = file_name_index.get(&file_name)?;
                if candidates.len() == 1 {
                    candidates.first().cloned()
                } else {
                    preferred_match_by_relative_path(candidates, relative_path).cloned()
                }
            });

        let Some(next_path) = candidate else {
            if source_object.get("missing").and_then(Value::as_bool).unwrap_or(false) {
                unresolved.push(
                    source_object
                        .get("id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                );
            }
            continue;
        };

        let next_path_string = path_to_string(&next_path);
        let current_path = source_object
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let current_missing = source_object
            .get("missing")
            .and_then(Value::as_bool)
            .unwrap_or(false);

        if current_path != next_path_string || current_missing {
            source_object.insert("path".into(), Value::String(next_path_string));
            source_object.insert("missing".into(), Value::Bool(false));
            relinked += 1;
        }

        source_object.insert(
            "originRoot".into(),
            Value::String(path_to_string(&relink_root)),
        );

        if source_object
            .get("originKind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            == "legacy"
        {
            source_object.insert("originKind".into(), Value::String("external".into()));
        }

    }

    enrich_editor_project_sources(&mut project);
    let saved = write_editor_project(&app, &project)?;
    Ok(RelinkEditorSourcesResult {
        project: saved,
        relinked,
        unresolved,
    })
}

#[tauri::command]
pub async fn editor_project_exists(app: AppHandle, project_id: String) -> AppResult<bool> {
    let path = editor_project_path(&app, &project_id)?;
    Ok(path.exists())
}

fn prepare_editor_export_payload(app: &AppHandle, mut payload: Value) -> AppResult<Value> {
    if let Some(object) = payload.as_object_mut() {
        let project_id = object
            .get("project")
            .and_then(|project| project.get("id"))
            .and_then(Value::as_str)
            .unwrap_or("shared");
        if !object.contains_key("exportDir") {
            let export_dir = editor_project_dir(&app, project_id)?.join("exports");
            std::fs::create_dir_all(&export_dir)?;
            object.insert(
                "exportDir".into(),
                Value::String(export_dir.to_string_lossy().to_string()),
            );
        }
    }
    Ok(payload)
}


#[tauri::command]
pub async fn start_editor_mix_export(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let payload = prepare_editor_export_payload(&app, payload)?;
    let task_id = payload
        .get("taskId")
        .or_else(|| payload.get("requestId"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| AppError::Worker("missing editor export task id".into()))?
        .to_string();
    spawn_worker_background(app, state, "export_editor_mix", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "started": true }))
}

#[tauri::command]
pub async fn cancel_editor_mix_export(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
    project_id: String,
    export_dir: Option<String>,
) -> AppResult<bool> {
    let directory = match export_dir
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        Some(value) => PathBuf::from(value),
        None => editor_project_dir(&app, &project_id)?.join("exports"),
    };
    if state
        .terminal_tasks
        .lock()
        .map(|terminal| terminal.contains(&task_id))
        .unwrap_or(false)
    {
        return Ok(false);
    }
    let running = state
        .tasks
        .lock()
        .map(|tasks| tasks.contains_key(&task_id))
        .unwrap_or(false);
    if !running {
        cleanup_editor_export_temp_files(&directory, &task_id);
        return Ok(false);
    }

    std::fs::create_dir_all(&directory)?;
    let cancel_path = directory.join(format!(
        ".pymss-export-{}.cancel",
        editor_export_task_token(&task_id)
    ));
    std::fs::write(&cancel_path, b"cancel")?;

    for _ in 0..200 {
        let still_running = state
            .tasks
            .lock()
            .map(|tasks| tasks.contains_key(&task_id))
            .unwrap_or(false);
        if !still_running {
            cleanup_editor_export_temp_files(&directory, &task_id);
            return Ok(true);
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }

    let cancelled = cancel_task(app.clone(), state, task_id.clone()).await;
    cleanup_editor_export_temp_files(&directory, &task_id);
    cancelled
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
