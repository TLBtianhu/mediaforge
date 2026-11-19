//! 编辑器域：项目 CRUD、素材导入与重链、音频预览、混音导出。
//!
//! 从 app_cmd.rs 拆出；命令形状与路径不变，main.rs 只改注册路径。

pub(crate) mod export;
mod cleanup;
#[cfg(test)]
use cleanup::with_current_orphaned_projects;
#[cfg(test)]
use cleanup::delete_current_orphaned_projects;
pub(crate) mod preview;
pub(crate) mod recording;

use crate::error::{AppError, AppResult};
use crate::session_log;
use crate::storage;
use crate::workflow_editors::{self, WorkflowEditorKind, WorkflowEditors};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindowBuilder};


use crate::commands::app::dialogs::path_to_string;
use crate::commands::app::scan::{collect_audio_files, is_audio_file, scan_audio_paths};

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

static TASK_PROJECT_CREATION_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub(crate) fn editor_project_id_for_task_id(task_id: &str) -> String {
    format!("edit_{}", safe_file_name(task_id))
}

fn existing_task_project(path: &Path, task_id: &str, project_id: &str) -> AppResult<Option<Value>> {
    crate::python::result_jobs::plain_path(path).map_err(|reason| AppError::Worker(reason.into()))?;
    let content = match std::fs::read_to_string(path) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let project = crate::python::result_jobs::parse_reference_json(&content)?;
    storage::validate_editor_project(&project)?;
    if project.get("id").and_then(Value::as_str) != Some(project_id)
        || project.get("sourceTaskId").and_then(Value::as_str) != Some(task_id)
    {
        return Err(AppError::Worker(
            "editor project identity collides with another task; original file preserved".into(),
        ));
    }
    Ok(Some(project))
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
    storage::write_editor_project_file(&storage::data_root_dir(app)?, &dir.join("project.json"), project)?;
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
    let project_id = editor_project_id_for_task_id(task_id);
    let _creation = TASK_PROJECT_CREATION_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    if let Some(project) = existing_task_project(
        &editor_project_path(&app, &project_id)?,
        task_id,
        &project_id,
    )? {
        remember_editor_previews(&app, &project);
        return Ok(project);
    }

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
    let requested = outputs
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
        .collect::<Vec<_>>();
    let root = storage::data_root_dir(&app)?;
    let mut paths = crate::python::task_journal::with_catalog_records(&root, |rows| {
        let row = rows.iter().find(|row| row["jobId"].as_str() == Some(task_id))
            .ok_or_else(|| AppError::Worker("editor source task was not found".into()))?;
        let recorded = crate::python::result_jobs::preview_result_paths(row)
            .filter(|path| is_audio_file(path))
            .map(path_to_string)
            .collect::<Vec<_>>();
        select_task_editor_paths(requested, &recorded)
    })?;
    paths.sort_by(|a, b| {
        stem_rank(&a.0)
            .cmp(&stem_rank(&b.0))
            .then_with(|| a.0.cmp(&b.0))
    });

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
                "fadeOut": 0,
                "pan": 0,
                "clips": [],
                "pluginChain": []
            })
        })
        .collect();

    let project = serde_json::json!({
        "version": 4,
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
    let saved = write_editor_project(&app, &project)?;
    remember_editor_previews(&app, &saved);
    Ok(saved)
}

fn select_task_editor_paths(
    requested: Vec<(String, String)>, recorded: &[String],
) -> AppResult<Vec<(String, String)>> {
    if requested.is_empty() {
        return Ok(recorded.iter().map(|path| (stem_from_path(path), path.clone())).collect());
    }
    let known = recorded.iter().map(Path::new).collect::<std::collections::HashSet<_>>();
    if requested.iter().any(|(_, path)| !known.contains(&Path::new(path))) {
        return Err(AppError::Worker("editor source is not a recorded task output".into()));
    }
    Ok(requested)
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
        "version": 4,
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

fn open_editor_blocks_deletion(app: &AppHandle, project_ids: &[String]) -> Vec<String> {
    if app.webview_windows().keys().any(|label| label.starts_with("editor-")) {
        project_ids.to_vec()
    } else {
        Vec::new()
    }
}

fn editor_project_protection_from(
    data_root: &Path,
    mut asset_roots: Vec<PathBuf>,
    executable: &Path,
    program_roots: &[PathBuf],
) -> crate::python::result_jobs::Protection {
    // Model/runtime overrides remain protected even when they contain data_root.
    // Only broad program/resource ancestors permit their managed project area.
    asset_roots.push(executable.to_path_buf());
    let data = crate::python::result_jobs::PathReferences::from_paths(&[data_root.to_path_buf()]);
    asset_roots.extend(program_roots.iter().filter(|root| !data.includes_directory(root)).cloned());
    crate::python::result_jobs::Protection { roots: asset_roots }
}

fn editor_project_protection(app: &AppHandle) -> AppResult<crate::python::result_jobs::Protection> {
    let data_root = storage::data_root_dir(app)?;
    let asset_roots = vec![storage::models_dir(app)?, storage::runtime_envs_dir(app)?, storage::runtime_root_dir(app)?];
    let executable = std::env::current_exe()?;
    let mut program_roots = executable.parent().map(PathBuf::from).into_iter().collect::<Vec<_>>();
    if let Ok(resource) = app.path().resource_dir() { program_roots.extend(storage::resource_roots(&resource)); }
    Ok(editor_project_protection_from(&data_root, asset_roots, &executable, &program_roots))
}

#[tauri::command]
pub async fn delete_editor_project(app: AppHandle, project_id: String) -> AppResult<bool> {
    let policy = editor_project_protection(&app)?;
    let result = cleanup::delete_projects(&storage::data_root_dir(&app)?, &[project_id.clone()],
        cleanup::Selection::Explicit, &policy.roots, |ids| open_editor_blocks_deletion(&app, ids))?;
    if !result.blocked_project_ids.is_empty() {
        return Err(AppError::Worker("工程仍被任务、模型、素材库或其它工程引用，或仍有编辑器窗口打开，无法删除".into()));
    }
    if !result.failed_project_ids.is_empty() {
        return Err(AppError::Worker("工程目录检查或删除失败；请检查目录权限和链接".into()));
    }
    Ok(result.deleted_project_ids.contains(&project_id))
}

#[tauri::command]
pub async fn delete_editor_projects_for_tasks(
    app: AppHandle,
    task_ids: Vec<String>,
) -> AppResult<EditorProjectCleanupResult> {
    let project_ids = editor_project_ids_for_task_ids(&task_ids);
    let policy = editor_project_protection(&app)?;
    cleanup::delete_projects(&storage::data_root_dir(&app)?, &project_ids,
        cleanup::Selection::Tasks(&task_ids), &policy.roots, |ids| open_editor_blocks_deletion(&app, ids))
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
) -> AppResult<Vec<OrphanedEditorProject>> {
    let policy = editor_project_protection(&app)?;
    cleanup::list_orphaned_projects(&storage::data_root_dir(&app)?, &policy.roots)
}

#[tauri::command]
pub async fn delete_orphaned_editor_projects(
    app: AppHandle,
    project_ids: Vec<String>,
) -> AppResult<EditorProjectCleanupResult> {
    let policy = editor_project_protection(&app)?;
    cleanup::delete_projects(&storage::data_root_dir(&app)?, &project_ids,
        cleanup::Selection::Orphans, &policy.roots, |ids| open_editor_blocks_deletion(&app, ids))
}


#[tauri::command]
pub async fn import_editor_assets(
    app: AppHandle,
    project_id: String,
    paths: Vec<String>,
) -> AppResult<ImportEditorAssetsResult> {
    read_current_editor_project(&app, &project_id)?;
    let mut imported = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = PathBuf::from(&raw);
        if !super::scan::is_selected_input(&app, &target) {
            warnings.push(format!("unselected editor input: {}", target.display()));
            continue;
        }
        if target.is_file() {
            if is_audio_file(&target) {
                crate::preview_assets::remember(&app, &target);
                if !crate::preview_assets::is_allowed(&app, &target) {
                    warnings.push(format!("unavailable editor input: {}", target.display()));
                    continue;
                }
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
            let scanned = scan_audio_paths(app.clone(), vec![raw.clone()]).await?;
            warnings.extend(scanned.warnings);
            for file in scanned.files {
                let file_path = PathBuf::from(&file);
                if !crate::preview_assets::is_allowed(&app, &file_path) { continue; }
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
pub async fn save_editor_project(app: AppHandle, project: Value) -> AppResult<Value> {
    let mut project = project;
    let project_id = project["id"].as_str().ok_or_else(|| AppError::Worker("missing editor project id".into()))?;
    let previous = match read_current_editor_project(&app, project_id) {
        Ok(previous) => Some(previous),
        Err(AppError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    validate_editor_source_provenance(&project, previous.as_ref(), |path| {
        crate::preview_assets::is_allowed(&app, path)
    })?;
    if let Some(object) = project.as_object_mut() {
        object.insert("updatedAt".into(), Value::from(now_millis() as u64));
    }
    write_editor_project(&app, &project)
}

fn editor_source_paths(project: &Value) -> impl Iterator<Item = &str> {
    project["sources"].as_array().into_iter().flatten().filter_map(|source| source["path"].as_str())
}

fn validate_editor_source_provenance(
    project: &Value, previous: Option<&Value>, allowed: impl Fn(&Path) -> bool,
) -> AppResult<()> {
    let known = previous.into_iter().flat_map(editor_source_paths).map(Path::new)
        .collect::<std::collections::HashSet<_>>();
    if editor_source_paths(project).any(|path| !known.contains(&Path::new(path))
        && (!is_audio_file(Path::new(path)) || !allowed(Path::new(path)))) {
        return Err(AppError::Worker("editor source was not selected, recorded or previously linked".into()));
    }
    Ok(())
}

fn read_current_editor_project(app: &AppHandle, project_id: &str) -> AppResult<Value> {
    let path = editor_project_path(app, project_id)?;
    crate::python::result_jobs::plain_path(&path).map_err(|reason| AppError::Worker(reason.into()))?;
    let project = crate::python::result_jobs::parse_reference_json(&std::fs::read_to_string(path)?)?;
    storage::validate_editor_project(&project)?;
    if project["id"].as_str() != Some(project_id) {
        return Err(AppError::Worker("editor project identity does not match".into()));
    }
    Ok(project)
}

fn remember_editor_previews(app: &AppHandle, project: &Value) {
    for path in editor_source_paths(project).map(Path::new).filter(|path| is_audio_file(path)) {
        crate::preview_assets::remember(app, path);
    }
    let Some(project_id) = project["id"].as_str() else { return; };
    for source in project["sources"].as_array().into_iter().flatten() {
        if let (Some(path), Some(source_path)) = (source["peaksPath"].as_str(), source["path"].as_str()) {
            preview::remember_managed_waveform(app, project_id, Path::new(path), Path::new(source_path));
        }
    }
}

#[tauri::command]
pub async fn load_editor_project(app: AppHandle, project_id: String) -> AppResult<Value> {
    let mut project = read_current_editor_project(&app, &project_id)?;
    if enrich_editor_project_sources(&mut project) {
        write_editor_project(&app, &project)?;
    }
    remember_editor_previews(&app, &project);
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

    let mut project = read_current_editor_project(&app, project_id)?;
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
    if !picked_path_buf.is_file() || !is_audio_file(&picked_path_buf)
        || !crate::preview_assets::is_allowed(&app, &picked_path_buf) {
        return Err(AppError::Worker("relink requires a selected audio file".into()));
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

        let Some(next_path) = candidate.filter(|candidate| relink_file_is_contained(&relink_root, candidate)) else {
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
    remember_editor_previews(&app, &saved);
    Ok(RelinkEditorSourcesResult {
        project: saved,
        relinked,
        unresolved,
    })
}

#[tauri::command]
pub fn editor_project_ids_for_tasks(task_ids: Vec<String>) -> Vec<String> {
    editor_project_ids_for_task_ids(&task_ids)
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
        if relink_file_is_contained(relink_root, &direct) {
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

fn relink_file_is_contained(root: &Path, candidate: &Path) -> bool {
    if !candidate.is_file() || !is_audio_file(candidate)
        || crate::python::result_jobs::plain_path(candidate).is_err() {
        return false;
    }
    match (root.canonicalize(), candidate.canonicalize()) {
        (Ok(root), Ok(candidate)) => candidate.starts_with(root),
        _ => false,
    }
}




#[cfg(test)]
mod tests {
    use crate::commands::app::editor::recording::{pcm_wav_header, validate_recording_id};
    use crate::python::worker::cleanup_editor_export_temp_files;
    use crate::commands::app::scan::reveal_target_path;
    use crate::commands::app::editor::preview::decode_editor_preview_chunk;
    use crate::commands::app::editor::{editor_project_id_for_task_id, editor_project_ids_for_task_ids, safe_file_name, validate_editor_project_id};
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
    fn editor_source_provenance_preserves_existing_missing_paths_and_rejects_new_raw_paths() {
        let temp = tempfile::tempdir().unwrap();
        let missing = temp.path().join("previous.wav");
        let selected = temp.path().join("chosen.wav");
        let unselected = temp.path().join("unselected.wav");
        let previous = serde_json::json!({"sources":[{"path":missing,"missing":true}]});
        let allowed = |path: &Path| path == selected.as_path();
        let mut project = serde_json::json!({"sources":[{"path":missing,"missing":true},{"path":selected}]});
        assert!(validate_editor_source_provenance(&project, Some(&previous), allowed).is_ok());
        project["sources"][1]["path"] = serde_json::json!(unselected);
        assert!(validate_editor_source_provenance(&project, Some(&previous), allowed).is_err());
        assert!(validate_editor_source_provenance(&previous, None, allowed).is_err());
        let alias = serde_json::json!({"sources":[{"path":temp.path().join("Previous.wav")}]});
        assert!(validate_editor_source_provenance(&alias, Some(&previous), allowed).is_err());
    }

    #[test]
    fn task_project_uses_only_recorded_output_paths_including_missing_files() {
        let temp = tempfile::tempdir().unwrap();
        let missing = temp.path().join("vocals.wav").to_string_lossy().into_owned();
        let recorded = [missing.clone()];
        let paths = select_task_editor_paths(Vec::new(), &recorded).unwrap();
        assert_eq!(paths, [("vocals".to_string(), missing.clone())]);
        assert!(select_task_editor_paths(vec![("requested-label".into(), missing)], &recorded).is_ok());
        assert!(select_task_editor_paths(vec![("unowned".into(), "arbitrary.wav".into())], &recorded).is_err());
    }

    #[test]
    fn relink_relative_hint_cannot_escape_the_selected_root() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("chosen");
        fs::create_dir(&root).unwrap();
        fs::write(temp.path().join("outside.wav"), b"unselected").unwrap();
        assert!(build_relink_candidate(&root, Some("../outside.wav"), &Default::default()).is_none());
    }

    fn current_orphan_project(root: &Path, project_id: &str, source: Option<&str>) -> PathBuf {
        let directory=root.join("editor-projects").join(project_id);fs::create_dir_all(&directory).unwrap();
        let mut project=serde_json::json!({"version":4,"id":project_id,"name":project_id,"sources":[],"tracks":[]});
        if let Some(id)=source {project["sourceTaskId"]=serde_json::json!(id);}
        fs::write(directory.join("project.json"),project.to_string()).unwrap();directory
    }

    fn current_orphan_job(root: &Path, id: &str, source: &str, status: &str, input: &Path) {
        let row=serde_json::json!({"schemaVersion":2,"jobId":id,"command":"audio_tools","source":source,"status":status,"fraction":0,
            "message":"fixture","error":null,"result":null,"createdAt":1,"updatedAt":2,"startedAt":null,"finishedAt":2,
            "owner":null,"runtime":{"stage":null,"progress":null,"nodes":{},"order":[]},"logs":[],"payload":{"inputPath":input}});
        let jobs=root.join("agent-jobs");fs::create_dir_all(&jobs).unwrap();fs::write(jobs.join(format!("{id}.json")),row.to_string()).unwrap();
    }

    #[test]
    fn canonical_orphan_scan_preserves_jobs_without_outputs_failed_jobs_and_agent_projects() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let source=root.path().join("source.wav");fs::write(&source,"source").unwrap();
        let mut projects=vec![];
        for (id,origin,status) in [("no_output","manual","completed"),("failed","manual","failed"),("agent","agent","failed")] {
            current_orphan_job(root.path(),id,origin,status,&source);
            projects.push(current_orphan_project(root.path(),&editor_project_id_for_task_id(id),Some(id)));
        }
        assert!(with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).unwrap().is_empty());
        let ids=projects.iter().map(|project|project.file_name().unwrap().to_string_lossy().into_owned()).collect::<Vec<_>>();
        let result=delete_current_orphaned_projects(root.path(),&ids,|_|vec![]).unwrap();assert!(result.deleted_project_ids.is_empty());assert_eq!(result.blocked_project_ids.len(),3);
        assert!(projects.iter().all(|project|project.join("project.json").is_file()));assert!(source.exists());
    }

    #[test]
    fn canonical_orphan_cleanup_removes_only_closed_owned_projects_with_no_job_record() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let owned=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let independent=current_orphan_project(root.path(),"independent",Some("gone"));let blank=current_orphan_project(root.path(),"edit_blank",None);
        let projects=with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).unwrap();
        assert_eq!(projects.len(),1);assert_eq!(projects[0].project_id,"edit_gone");
        let selected=vec!["edit_gone".to_string()];
        let blocked=delete_current_orphaned_projects(root.path(),&selected,|ids|ids.to_vec()).unwrap();assert_eq!(blocked.blocked_project_ids,vec!["edit_gone"]);assert!(owned.exists());
        let result=delete_current_orphaned_projects(root.path(),&selected,|_|vec![]).unwrap();assert_eq!(result.deleted_project_ids,vec!["edit_gone"]);
        assert!(!owned.exists());assert!(independent.exists());assert!(blank.exists());
    }

    #[test]
    fn canonical_orphan_cleanup_rejects_a_broken_catalog_before_touching_any_project() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let before=fs::read(project.join("project.json")).unwrap();fs::create_dir_all(root.path().join("agent-jobs")).unwrap();
        fs::write(root.path().join("agent-jobs/bad.json"),"{}").unwrap();
        assert!(with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).is_err());
        assert!(delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).is_err());assert_eq!(fs::read(project.join("project.json")).unwrap(),before);
    }

    #[test]
    fn canonical_orphan_snapshot_keeps_catalog_locked_through_the_cleanup_callback() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let (entered,enter)=std::sync::mpsc::channel();let mut writer=None;
        with_current_orphaned_projects(root.path(),|_,projects| {
            assert_eq!(projects.len(),1);let data=root.path().to_path_buf();
            assert!(crate::python::library_references::AssetLease::acquire(root.path(), std::time::Duration::ZERO).is_err());
            writer=Some(std::thread::spawn(move ||crate::python::task_journal::with_catalog_records(&data,|_|{entered.send(()).unwrap();Ok(())}).unwrap()));
            assert!(enter.recv_timeout(std::time::Duration::from_millis(30)).is_err());Ok(())
        }).unwrap();
        enter.recv_timeout(std::time::Duration::from_secs(2)).unwrap();writer.unwrap().join().unwrap();
    }

    #[test]
    fn canonical_orphan_cleanup_revalidates_prefetched_selection_against_new_job_records() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let selected=with_current_orphaned_projects(root.path(),|_,projects|Ok(projects.into_iter().map(|project|project.project_id).collect::<Vec<_>>())).unwrap();
        let source=root.path().join("source.wav");fs::write(&source,"source").unwrap();current_orphan_job(root.path(),"gone","agent","failed",&source);
        let result=delete_current_orphaned_projects(root.path(),&selected,|_|vec![]).unwrap();
        assert_eq!(result.blocked_project_ids,vec!["edit_gone"]);assert!(result.deleted_project_ids.is_empty());assert!(project.exists());
    }

    #[test]
    fn canonical_orphan_cleanup_never_expands_the_explicit_project_selection() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let selected_project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let selected=with_current_orphaned_projects(root.path(),|_,projects|Ok(projects.into_iter().map(|project|project.project_id).collect::<Vec<_>>())).unwrap();
        let appeared_later=current_orphan_project(root.path(),"edit_new",Some("new"));
        let result=delete_current_orphaned_projects(root.path(),&selected,|_|vec![]).unwrap();
        assert_eq!(result.deleted_project_ids,vec!["edit_gone"]);assert!(!selected_project.exists());assert!(appeared_later.exists());
    }

    #[test]
    fn canonical_orphan_scan_preserves_project_referenced_by_an_export_after_its_source_job_is_removed() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let outside=root.path().join("source.wav");fs::write(&outside,"source").unwrap();current_orphan_job(root.path(),"export","manual","completed",&outside);
        let job=root.path().join("agent-jobs/export.json");let mut row:Value=serde_json::from_str(&fs::read_to_string(&job).unwrap()).unwrap();
        row["command"]=serde_json::json!("export_editor_mix");row["payload"]=serde_json::json!({"project":{"id":"edit_gone","version":4,"assets":[],"tracks":[]}});
        fs::write(job,row.to_string()).unwrap();
        assert!(with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).unwrap().is_empty());
        let result=delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).unwrap();
        assert_eq!(result.blocked_project_ids,vec!["edit_gone"]);assert!(project.exists());
    }

    #[test]
    fn canonical_orphan_scan_preserves_any_current_job_input_or_result_inside_the_project_directory() {
        let _model = crate::storage::model_reference_test_guard();
        for result_reference in [false,true] {
            let root=tempfile::tempdir().unwrap();let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
            let output=project.join("exports/mix.wav");fs::create_dir_all(output.parent().unwrap()).unwrap();fs::write(&output,"mix").unwrap();
            current_orphan_job(root.path(),"consumer","agent","failed",&output);
            if result_reference {let job=root.path().join("agent-jobs/consumer.json");let mut row:Value=serde_json::from_str(&fs::read_to_string(&job).unwrap()).unwrap();
                row["payload"]=serde_json::json!({});row["result"]=serde_json::json!({"outputs":[{"path":output}]});fs::write(job,row.to_string()).unwrap();}
            assert!(with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).unwrap().is_empty());
            let result=delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).unwrap();
            assert_eq!(result.blocked_project_ids,vec!["edit_gone"]);assert!(output.exists());
        }
    }

    #[test]
    fn canonical_orphan_cleanup_rejects_duplicate_project_identity_or_source_keys_and_preserves_bytes() {
        let _model = crate::storage::model_reference_test_guard();
        for duplicate in ["id","sourceTaskId"] {
            let root=tempfile::tempdir().unwrap();let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
            let raw=if duplicate=="id" {r#"{"version":4,"id":"edit_live","id":"edit_gone","sourceTaskId":"gone","sources":[],"tracks":[]}"#}
                else {r#"{"version":4,"id":"edit_gone","sourceTaskId":"live","sourceTaskId":"gone","sources":[],"tracks":[]}"#};
            let path=project.join("project.json");fs::write(&path,raw).unwrap();
            assert!(with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).is_err());
            assert!(delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).is_err());assert_eq!(fs::read(path).unwrap(),raw.as_bytes());
        }
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

    #[cfg(windows)]
    #[test]
    fn canonical_orphan_cleanup_preserves_library_sources_inside_an_unlinked_project() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();
        let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        let source=project.join("library-source.wav");fs::write(&source,b"referenced source").unwrap();
        crate::python::library_references::create_fixture(root.path(),&[(source.clone(),std::path::PathBuf::new())]).unwrap();
        let before=fs::read(root.path().join("library.db")).unwrap();
        let projects=with_current_orphaned_projects(root.path(),|_,projects|Ok(projects)).unwrap();
        assert!(projects.is_empty());
        let result=delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).unwrap();
        assert_eq!(result.blocked_project_ids,vec!["edit_gone"]);assert!(result.deleted_project_ids.is_empty());
        assert_eq!(fs::read(source).unwrap(),b"referenced source");
        assert_eq!(fs::read(root.path().join("library.db")).unwrap(),before);
    }

    #[cfg(windows)]
    #[test]
    fn canonical_orphan_cleanup_allows_a_valid_library_without_a_project_reference() {
        let _model = crate::storage::model_reference_test_guard();
        let root=tempfile::tempdir().unwrap();
        let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
        crate::python::library_references::create_fixture(root.path(),&[]).unwrap();
        let before=fs::read(root.path().join("library.db")).unwrap();
        let result=delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).unwrap();
        assert_eq!(result.deleted_project_ids,vec!["edit_gone"]);assert!(!project.exists());
        assert_eq!(fs::read(root.path().join("library.db")).unwrap(),before);
    }

    #[cfg(windows)]
    #[test]
    fn canonical_orphan_cleanup_rejects_old_or_broken_libraries_before_project_deletion() {
        let _model = crate::storage::model_reference_test_guard();
        for broken in [false,true] {
            let root=tempfile::tempdir().unwrap();
            let project=current_orphan_project(root.path(),"edit_gone",Some("gone"));
            if broken {fs::write(root.path().join("library.db"),b"not a sqlite database").unwrap();}
            else {
                crate::python::library_references::create_fixture(root.path(),&[]).unwrap();
                crate::python::library_references::fixture_execute(root.path(),"PRAGMA user_version=0").unwrap();
            }
            let before=fs::read(root.path().join("library.db")).unwrap();
            assert!(delete_current_orphaned_projects(root.path(),&["edit_gone".into()],|_|vec![]).is_err());
            assert!(project.join("project.json").is_file());
            assert_eq!(fs::read(root.path().join("library.db")).unwrap(),before);
        }
    }

    #[test]
    fn existing_task_project_is_reused_without_overwrite_and_collisions_are_rejected() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("edit_task/project.json");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let project = serde_json::json!({
            "id": "edit_task", "sourceTaskId": "task", "version": 4,
            "sources": [], "tracks": [], "name": "Edited project"
        });
        fs::write(&path, serde_json::to_vec_pretty(&project).unwrap()).unwrap();
        let before = fs::read(&path).unwrap();
        assert_eq!(existing_task_project(&path, "task", "edit_task").unwrap(), Some(project));
        assert!(existing_task_project(&path, "other", "edit_task").is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        assert!(existing_task_project(&root.path().join("missing.json"), "task", "edit_task").unwrap().is_none());
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
        let _model = crate::storage::model_reference_test_guard();
        let root = tempfile::tempdir().unwrap();
        let existing_id = "edit_existing".to_string();
        let missing_id = "edit_missing".to_string();
        let project = current_orphan_project(root.path(), &existing_id, Some("existing"));
        fs::create_dir_all(project.join("recordings")).unwrap();
        fs::write(project.join("recordings/take.wav"), b"audio").unwrap();
        let result = cleanup::delete_projects(root.path(), &[existing_id.clone(), missing_id.clone()],
            cleanup::Selection::Tasks(&["existing".to_string()]), &[], |_| vec![]).unwrap();
        assert_eq!(result.deleted_project_ids, vec![existing_id]);
        assert_eq!(result.missing_project_ids, vec![missing_id]);
        assert!(result.blocked_project_ids.is_empty());
        assert!(result.failed_project_ids.is_empty());
        assert!(!project.exists());
    }

    #[test]
    fn editor_project_cleanup_rejects_lossy_task_id_collisions_and_unknown_ownership() {
        let _model = crate::storage::model_reference_test_guard();
        let root = tempfile::tempdir().unwrap();
        let project_id = editor_project_id_for_task_id("a:b");
        assert_eq!(project_id, editor_project_id_for_task_id("a/b"));
        let project = current_orphan_project(root.path(), &project_id, Some("a:b"));
        let result = cleanup::delete_projects(root.path(), &[project_id.clone()],
            cleanup::Selection::Tasks(&["a/b".into()]), &[], |_| vec![]).unwrap();
        assert_eq!(result.blocked_project_ids, vec![project_id.clone()]);
        assert!(project.exists());
        let project_path = project.join("project.json");
        for content in [r#"{"version":4,"id":"edit_a_b","sources":[],"tracks":[]}"#,
            r#"{"version":4,"id":"other","sourceTaskId":"a:b","sources":[],"tracks":[]}"#, "invalid",
            r#"{"id":"edit_a_b","sourceTaskId":"a:b"}"#] {
            fs::write(&project_path, content).unwrap();
            let result = cleanup::delete_projects(root.path(), &[project_id.clone()],
                cleanup::Selection::Tasks(&["a:b".into()]), &[], |_| vec![]);
            assert!(result.is_err() || !result.unwrap().blocked_project_ids.is_empty());
            assert_eq!(fs::read(&project_path).unwrap(), content.as_bytes());
        }
        // A valid current task's exact identity still permits explicit task cleanup.
        fs::remove_dir_all(&project).unwrap();
        let current = current_orphan_project(root.path(), "edit_current", Some("current"));
        let result = cleanup::delete_projects(root.path(), &["edit_current".into()],
            cleanup::Selection::Tasks(&[" current ".into()]), &[], |_| vec![]).unwrap();
        assert_eq!(result.deleted_project_ids, vec!["edit_current"]);
        assert!(!current.exists());
    }
    #[test]
    fn native_path_keys_preserve_posix_case_and_literal_backslashes() {
        if cfg!(windows) {
            assert_eq!(normalize_path_key("C:\\Audio\\Song.wav"), normalize_path_key("c:/audio/song.wav"));
        } else {
            assert_ne!(normalize_path_key("/audio/Foo.wav"), normalize_path_key("/audio/foo.wav"));
            assert_ne!(normalize_path_key("/audio/a\\b.wav"), normalize_path_key("/audio/a/b.wav"));
        }
    }

    #[test]
    fn editor_project_cleanup_rejects_directory_cycles() {
        let _model = crate::storage::model_reference_test_guard();
        let data_root = tempfile::tempdir().unwrap();
        let root = data_root.path().join("editor-projects");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join(".DS_Store"), b"metadata").unwrap();
        let link = root.join("loop");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&root, &link).unwrap();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let status = crate::process_command::hidden_command("cmd.exe")
                .raw_arg(format!("/c mklink /J \"{}\" \"{}\"", link.display(), root.display()))
                .output().expect("create directory junction");
            assert!(status.status.success());
        }
        let result = cleanup::delete_projects(data_root.path(), &["loop".into()],
            cleanup::Selection::Explicit, &[], |_| vec![]);
        assert!(result.is_err());
        assert_eq!(fs::read(root.join(".DS_Store")).unwrap(), b"metadata");
        #[cfg(unix)]
        fs::remove_file(&link).unwrap();
        #[cfg(windows)]
        fs::remove_dir(&link).unwrap();
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
        if crate::process_command::hidden_command(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping native codec check");
            return;
        }
        for source_rate in [44_100, 48_000, 96_000] {
            let path = preview_wave_fixture(source_rate);
            for rate in [44_100, 48_000] {
                let full = crate::process_command::hidden_command(ffmpeg).args(["-v", "error", "-i"]).arg(&path)
                    .arg("-ar").arg(rate.to_string())
                    .args(["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]).output().unwrap();
                let pieces: Vec<_> = (0..3).map(|index| decode_editor_preview_chunk(ffmpeg, &path, index, rate, 2).unwrap()).collect();
                assert!(full.status.success(), "full decode failed: {}", String::from_utf8_lossy(&full.stderr));
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
        if crate::process_command::hidden_command(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping compressed codec check");
            return;
        }
        let wave = preview_wave_fixture(44_100);
        for (codec, extension) in [("libmp3lame", "mp3"), ("aac", "m4a"), ("aac", "aac"), ("libopus", "opus")] {
            let encoded = wave.with_extension(extension);
            let mut encoder = crate::process_command::hidden_command(ffmpeg);
            encoder.args(["-v", "error", "-y", "-i"]).arg(&wave).arg("-c:a").arg(codec);
            // Make the AAC fixture deterministic, without random perceptual noise.
            if codec == "aac" { encoder.args(["-aac_pns", "0"]); }
            let encoded_result = encoder.arg(&encoded).output().unwrap();
            if !encoded_result.status.success() {
                eprintln!("Encoder {codec} unavailable; skipping its fixture");
                continue;
            }
            for rate in [44_100, 48_000] {
                let full = crate::process_command::hidden_command(ffmpeg).args(["-v", "error", "-threads", "1", "-i"]).arg(&encoded)
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
