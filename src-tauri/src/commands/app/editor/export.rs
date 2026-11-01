//! 编辑器混音导出：后台任务启动、取消与临时文件清理。

use super::editor_project_dir;
use crate::commands::app::tasks::cancel_task;
use crate::error::{AppError, AppResult};
use crate::python::worker::{
    cleanup_editor_export_temp_files, editor_export_task_token, spawn_worker_background,
};
use crate::state::AppState;
use serde_json::Value;
use std::path::PathBuf;
use tauri::{AppHandle, State};

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
