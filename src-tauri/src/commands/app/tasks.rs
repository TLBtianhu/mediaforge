//! 任务命令：分离、推理、媒体工具、operation 与 agent 任务。
//!
//! 从 app_cmd.rs 拆出；长任务统一走后台 worker + 任务台账。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_with_payload, spawn_worker_background};
use crate::python::{audio_jobs, task_journal};
use crate::session_log;
use crate::state::AppState;
use crate::storage;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager, State};
use crate::workflow_editors::{RegistrySnapshot, WorkflowEditors};

#[tauri::command]
pub async fn start_separation(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("jobId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing queued jobId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "worker.start_requested",
        vec![("kind", "separation".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}

#[tauri::command]
pub async fn start_workflow_inference(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let task_id = payload
        .get("jobId")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing queued jobId".into()))?
        .to_string();
    session_log::append(
        &app,
        "INFO",
        "worker.start_requested",
        vec![("kind", "workflow".to_string()), ("taskId", task_id.clone())],
    );
    spawn_worker_background(app, state, "infer_workflow", task_id.clone(), payload)?;
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
}


#[tauri::command]
pub fn get_workflow_editors(editors: State<'_, WorkflowEditors>) -> RegistrySnapshot {
    editors.registry().snapshot()
}


#[tauri::command]
pub async fn media_library(app: AppHandle, mut payload: Value) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    payload = scoped_library_payload(&root, payload)?;
    validate_library_import(&payload, |path| super::scan::is_selected_input(&app, path))?;
    // The worker's cross-process asset lease owns serialization. Do not hold
    // the editor/settings JSON guard while probing media or making thumbnails.
    let result = run_worker_with_payload(&app, "media_library", Some(payload))?;
    let assets = result["assets"].as_array().into_iter().flatten().chain(result.get("asset"));
    for asset in assets {
        if asset["id"].as_u64().is_some_and(|id| id > 0) && matches!(asset["kind"].as_str(), Some("image" | "video" | "audio")) {
            if let Some(source) = asset["path"].as_str() {
                crate::preview_assets::remember(&app, Path::new(source));
            }
            if let Some(thumbnail) = library_thumbnail(&root, asset) {
                crate::preview_assets::remember(&app, thumbnail);
            }
        }
    }
    Ok(result)
}

fn validate_library_import(payload: &Value, selected: impl Fn(&Path) -> bool) -> AppResult<()> {
    if !payload["operation"].as_str().is_some_and(|op| op.trim().eq_ignore_ascii_case("import")) {
        return Ok(());
    }
    let Some(paths) = payload.get("paths") else { return Ok(()); };
    if paths.as_array().is_some_and(|paths| paths.iter().all(|value| {
        value.as_str().is_some_and(|path| selected(Path::new(path)))
    })) {
        return Ok(());
    }
    Err(AppError::Worker("Select or drop the media files or folders before importing them".into()))
}

fn library_thumbnail<'a>(root: &Path, asset: &'a Value) -> Option<&'a Path> {
    let path = Path::new(asset["thumbnail"].as_str()?);
    let name = path.file_name()?.to_str()?.strip_suffix(".jpg")?;
    // The library worker checks the exact filename derived from the recorded source.
    (path.parent()? == root.join("thumbnails") && name.len() == 20
        && name.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)))
        .then_some(path)
}

fn scoped_library_payload(root: &Path, mut payload: Value) -> AppResult<Value> {
    payload.as_object_mut().ok_or_else(|| AppError::Worker("invalid library request".into()))?
        .insert("dataRoot".into(), serde_json::json!(root));
    Ok(payload)
}

#[cfg(test)]
mod library_scope_tests {
    use super::*;

    #[test]
    fn library_requests_cannot_override_the_application_profile() {
        let root = std::env::temp_dir().join("owned-library-profile");
        let payload = scoped_library_payload(&root, serde_json::json!({"operation":"list","dataRoot":"outside-profile"})).unwrap();
        assert_eq!(payload["dataRoot"], serde_json::json!(root));
        assert_eq!(payload["operation"], "list");
        for invalid in [Value::Null, serde_json::json!([]), serde_json::json!("outside-profile")] {
            assert!(scoped_library_payload(&root, invalid).is_err());
        }
    }

    #[test]
    fn library_import_rejects_any_unselected_path_before_worker_use() {
        let selected = |path: &Path| path == Path::new("selected");
        assert!(validate_library_import(&serde_json::json!({"operation":" import ","paths":["selected"]}), selected).is_ok());
        for paths in [serde_json::json!(["selected", "outside"]), serde_json::json!([5]), serde_json::json!("selected")] {
            assert!(validate_library_import(&serde_json::json!({"operation":"IMPORT","paths":paths}), selected).is_err());
        }
        assert!(validate_library_import(&serde_json::json!({"operation":"list"}), selected).is_ok());
    }

    #[test]
    fn library_thumbnail_must_be_a_single_owned_cache_file() {
        let root = std::env::temp_dir().join("owned-library-profile");
        let name = "0123456789abcdef0123.jpg";
        let candidate = root.join("thumbnails").join(name);
        let asset = serde_json::json!({"thumbnail": candidate});
        assert_eq!(library_thumbnail(&root, &asset), Some(candidate.as_path()));
        for path in [root.join(name), root.join("thumbnails/other").join(name), root.join("thumbnails").join("unowned.jpg")] {
            assert!(library_thumbnail(&root, &serde_json::json!({"thumbnail":path})).is_none());
        }
    }
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

/// Journal snapshot for the UI.
///
/// `known` is the caller's `{jobId: updatedAt}` map. With it the backend returns
/// only changed records plus the ids that disappeared; without it the caller
/// gets the authoritative full snapshot (first load, recovery, diagnostics).
#[tauri::command]
pub async fn agent_jobs(app: AppHandle, known: Option<Value>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let known = parse_known_jobs(known)?;
    let delta = task_journal::read_delta(&root, known.as_ref())?;
    for row in &delta.jobs {
        crate::python::result_jobs::remember_previews(&app, row);
    }
    Ok(serde_json::json!({
        "schemaVersion": 3,
        "full": delta.full,
        "dataRoot": root,
        "jobsDir": root.join("agent-jobs"),
        "jobs": delta.jobs,
        "removed": delta.removed,
    }))
}

fn parse_known_jobs(known: Option<Value>) -> AppResult<Option<HashMap<String, f64>>> {
    let Some(known) = known.filter(|value| !value.is_null()) else { return Ok(None) };
    let object = known
        .as_object()
        .ok_or_else(|| AppError::Worker("known journal state must be an object".into()))?;
    let mut map = HashMap::with_capacity(object.len());
    for (id, value) in object {
        if !task_journal::valid_id(id) {
            return Err(AppError::Worker("invalid job id in known journal state".into()));
        }
        let updated = value
            .as_f64()
            .filter(|updated| updated.is_finite())
            .ok_or_else(|| AppError::Worker("invalid updatedAt in known journal state".into()))?;
        map.insert(id.clone(), updated);
    }
    Ok(Some(map))
}

#[tauri::command]
pub async fn enqueue_audio_jobs(app: AppHandle, payload: Value) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let jobs = audio_jobs::enqueue(&root, payload)?;
    Ok(serde_json::json!({
        "schemaVersion": 3,
        "full": false,
        "dataRoot": root,
        "jobsDir": root.join("agent-jobs"),
        "jobs": jobs,
        "removed": [],
    }))
}

#[tauri::command]
pub async fn remove_audio_jobs(app: AppHandle, job_ids: Vec<String>, delete_files: Option<bool>, check_only: Option<bool>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let policy = result_protection(&app)?;
    let result = crate::python::result_jobs::remove_audio(&root, &job_ids, delete_files == Some(true), check_only == Some(true), &policy,
        || app.webview_windows().keys().any(|label| label.starts_with("editor-")))?;
    Ok(serde_json::to_value(result)?)
}

fn result_protection(app: &AppHandle) -> AppResult<crate::python::result_jobs::Protection> {
    let mut roots = vec![storage::models_dir(&app)?, storage::runtime_envs_dir(&app)?, storage::runtime_root_dir(&app)?];
    let executable = std::env::current_exe()?;
    roots.push(executable.clone());
    let mut program_roots = executable.parent().map(PathBuf::from).into_iter().collect::<Vec<_>>();
    if let Ok(resource) = app.path().resource_dir() { program_roots.extend(storage::resource_roots(&resource)); }
    roots.extend(program_roots);
    Ok(crate::python::result_jobs::Protection { roots })
}

#[tauri::command]
pub async fn remove_result_jobs(app: AppHandle, job_ids: Vec<String>, delete_files: Option<bool>, check_only: Option<bool>) -> AppResult<Value> {
    let root = storage::data_root_dir(&app)?;
    let policy = result_protection(&app)?;
    let result = crate::python::result_jobs::remove(&root, &job_ids, delete_files == Some(true), check_only == Some(true), &policy,
        || app.webview_windows().keys().any(|label| label.starts_with("editor-")))?;
    Ok(serde_json::to_value(result)?)
}

#[tauri::command]
pub async fn cancel_agent_job(app: AppHandle, job_id: String) -> AppResult<Value> {
    if !task_journal::valid_id(&job_id) {
        return Err(AppError::Worker("invalid agent job id".into()));
    }
    let root = storage::data_root_dir(&app)?;
    let requested = task_journal::request_cancel(&root, &job_id)?;
    Ok(serde_json::json!({ "ok": requested, "jobId": job_id }))
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
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
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
    Ok(serde_json::json!({ "taskId": task_id, "queued": true, "started": false }))
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
    let running = {
        let tasks = state.tasks.lock().map_err(|_| AppError::Worker("task registry lock poisoned".into()))?;
        tasks.get(&task_id).map(|process| {
            let linked = tasks.iter()
                .filter(|(_, candidate)| std::sync::Arc::ptr_eq(candidate, process))
                .map(|(id, _)| id.clone()).collect::<Vec<_>>();
            (process.clone(), linked)
        })
    };
    if let Some((process, linked)) = running {
        return Ok(process.cancel(&state, &linked));
    }
    let root = storage::data_root_dir(&app)?;
    task_journal::request_cancel(&root, &task_id)
}
