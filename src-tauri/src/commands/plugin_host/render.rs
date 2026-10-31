//! 插件渲染命令与渲染目录管理。

use crate::commands::plugin_host::{require_enabled, PluginHostState};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, State};
use super::*;

pub(crate) fn render_directory(root: &Path, task_id: &str) -> Result<PathBuf, String> {
    if task_id.is_empty()
        || task_id.len() > 128
        || !task_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    {
        return Err("Invalid plugin render task identifier".into());
    }
    let temp = root.join("temp");
    if fs::symlink_metadata(&temp).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("The audio temporary directory cannot be a symbolic link".into());
    }
    let parent = temp.join("vst-renders");
    if fs::symlink_metadata(&parent).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("The plugin render directory cannot be a symbolic link".into());
    }
    let path = parent.join(task_id);
    if fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("The plugin render task directory cannot be a symbolic link".into());
    }
    Ok(path)
}

pub(crate) struct RenderTask {
    pub(crate) task_id: String,
    pub(crate) pid: Arc<ProcessLease>,
    pub(crate) renders: Arc<Mutex<HashMap<String, Arc<ProcessLease>>>>,
}

impl RenderTask {
    pub(crate) fn require_active(&self) -> Result<(), String> {
        if self.pid.id() == u32::MAX {
            Err("Plugin rendering was cancelled".into())
        } else {
            Ok(())
        }
    }
}

impl Drop for RenderTask {
    fn drop(&mut self) {
        self.pid.clear();
        if let Ok(mut renders) = self.renders.lock() {
            // A lease may only remove its own registration, even if an identifier is reused.
            if renders
                .get(&self.task_id)
                .is_some_and(|pid| Arc::ptr_eq(pid, &self.pid))
            {
                renders.remove(&self.task_id);
            }
        }
    }
}

pub(crate) fn register_render(state: &PluginHostState, task_id: String) -> Result<RenderTask, String> {
    let pid = Arc::new(ProcessLease::default());
    {
        let mut renders = state
            .renders
            .lock()
            .map_err(|_| "Plugin render lock failed")?;
        if state.stopping.load(Ordering::Acquire) {
            return Err("The audio host is shutting down".into());
        }
        if renders.contains_key(&task_id) {
            return Err("This plugin render task is already running".into());
        }
        renders.insert(task_id.clone(), pid.clone());
    }
    Ok(RenderTask {
        task_id,
        pid,
        renders: state.renders.clone(),
    })
}

pub(crate) fn cancel_render(state: &PluginHostState, task_id: &str) -> Result<Option<u32>, String> {
    let renders = state
        .renders
        .lock()
        .map_err(|_| "Plugin render lock failed")?;
    Ok(renders.get(task_id).map(|pid| pid.cancel()))
}

pub(crate) fn cleanup_render(state: &PluginHostState, root: &Path, task_id: &str) -> Result<(), String> {
    let directory = render_directory(root, task_id)?;
    let renders = state
        .renders
        .lock()
        .map_err(|_| "Plugin render lock failed")?;
    if renders.contains_key(task_id) {
        return Err("The plugin render task is still running".into());
    }
    match fs::remove_dir_all(directory) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.to_string()),
        _ => Ok(()),
    }
}

#[tauri::command]
pub async fn plugin_host_render(
    app: AppHandle,
    window: tauri::WebviewWindow,
    state: State<'_, PluginHostState>,
    mut project: Value,
    task_id: String,
    sample_rate: Option<u32>,
) -> Result<Value, String> {
    require_enabled()?;
    if sample_rate.is_some_and(|rate| !(8000..=48000).contains(&rate)) {
        return Err("The render sample rate must be between 8000 and 48000 Hz".into());
    }
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let directory = render_directory(&root, &task_id)?;
    let state = state.inner().clone();
    // Register before queueing blocking work so immediate cancellation is remembered.
    let task = register_render(&state, task_id)?;
    let ffmpeg = ffmpeg_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let mut created_directory = false;
        let result = (|| {
            task.require_active()?;
            let mut emit_progress = |progress: NativeRenderProgress| -> Result<(), HostRequestError> {
                task.require_active().map_err(HostRequestError::Operation)?;
                let _ = app.emit_to(window.label(), "pymss://worker-event", json!({
                    "type":"editor_plugin_render_progress", "taskId":task.task_id.as_str(), "payload":progress,
                }));
                Ok(())
            };
            emit_progress(NativeRenderProgress { phase: "preparing".into(), completed: 0, total: 1 })
                .map_err(|error| error.to_string())?;
            omit_bypassed_render_plugins(&mut project);
            resolve_states(&root, &mut project)?;
            task.require_active()?;
            fs::create_dir_all(directory.parent().unwrap()).map_err(|error| error.to_string())?;
            fs::create_dir(&directory).map_err(|error| error.to_string())?;
            created_directory = true;
            let output = directory.join("mix.wav");
            task.require_active()?;
            let mut process = HostProcess::spawn(&[std::ffi::OsStr::new("--audio-host")])?;
            if task.pid.publish(&process).is_err() {
                return Err("Plugin rendering was cancelled".into());
            }
            let project_id = project.get("id").and_then(Value::as_str).unwrap_or("render").to_string();
            let load_timeout = plugin_load_timeout(&project);
            let mut load = json!({"op":"load","projectId":project_id,"project":project,"ffmpeg":ffmpeg,"offline":true});
            if let Some(rate) = sample_rate { load["sampleRate"] = json!(rate); }
            process.request(load, load_timeout)
                .map_err(|error| error.to_string())?;
            let mut result = process.request_with_progress(json!({"op":"render","projectId":project_id,"path":output}), RENDER_TIMEOUT, &mut emit_progress)
                .map_err(|error| error.to_string())?;
            task.require_active()?;
            if !output.is_file() { return Err("The audio host did not produce a rendered mix".into()); }
            result["path"] = json!(output);
            Ok(result)
        })();
        if result.is_err() && created_directory { let _ = fs::remove_dir_all(&directory); }
        // Retain the registration until the child and failed output have both been cleaned.
        drop(task);
        result
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn plugin_host_cancel_render(
    state: State<'_, PluginHostState>,
    task_id: String,
) -> Result<bool, String> {
    require_enabled()?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        cancel_render(&state, &task_id).map(|pid| pid.is_some())
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn plugin_host_cleanup_render(
    app: AppHandle,
    state: State<'_, PluginHostState>,
    task_id: String,
) -> Result<(), String> {
    require_enabled()?;
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || cleanup_render(&state, &root, &task_id))
        .await
        .map_err(|error| error.to_string())?
}

