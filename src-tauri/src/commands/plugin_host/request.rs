//! 插件宿主请求命令：把编辑器/项目操作转发给隔离的音频宿主进程。

use base64::Engine as _;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter, State};

use super::*;

#[tauri::command]
pub async fn plugin_host_request(
    app: AppHandle,
    window: tauri::WebviewWindow,
    state: State<'_, PluginHostState>,
    mut request: Value,
) -> Result<Value, String> {
    require_enabled()?;
    let op = request
        .get("op")
        .and_then(Value::as_str)
        .ok_or("An audio host operation is required")?
        .to_string();
    if !supported_operation(&op) {
        return Err("Unsupported audio host operation".into());
    }
    let opening_editor = op == "editor" && request.get("open").and_then(Value::as_bool).unwrap_or(true);
    if opening_editor && !host_capabilities().native_editor {
        return Err("Native plugin editors require an X11 or XWayland display on Linux".into());
    }
    let id = project_id(&request)?.to_string();
    #[cfg(windows)]
    let owner = if opening_editor {
        Some(window.hwnd().map_err(|error| error.to_string())?.0 as usize)
    } else { None };
    #[cfg(not(windows))]
    let owner = None;
    bind_editor_owner(&mut request, owner)?;
    request["transportOwner"] = json!(window.label());
    let state = state.inner().clone();
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let recovery_load = op == "load" && request.get("expectedGeneration").is_some();
    let generation = if op == "load" {
        let expected = match request.get("expectedGeneration") {
            None => None,
            Some(value) => Some(value.as_u64().ok_or("Invalid expected audio session generation")?),
        };
        begin_load_generation(&state, expected)?
    } else {
        state.generation.load(Ordering::Acquire)
    };
    let result = tauri::async_runtime::spawn_blocking(move || {
        require_generation(&state, generation)?;
        let mut session = state
            .session
            .lock()
            .map_err(|_| "Audio host session lock failed")?;
        require_generation(&state, generation)?;
        if op == "unload" && request.get("discardUnsaved").and_then(Value::as_bool) == Some(true) {
            let expected = request.get("expectedSessionId").and_then(Value::as_str)
                .filter(|id| !id.is_empty()).ok_or("A native session identifier is required to discard changes")?;
            return Ok(discard_session(&mut session, &state.session_pid, &id, generation, expected));
        }
        if op != "load"
            && (session.project_id.as_deref() != Some(id.as_str())
                || session.generation != generation)
        {
            return Err("The audio host session belongs to another project or is closed".into());
        }
        if op == "load" {
            prepare_live_load_with(&mut session, &state.session_pid, generation, &mut dialogs::owned_dialog)
                .map_err(|error| error.to_string())?;
        }
        if op == "load" || op == "update" {
            prepare_session_request(&mut session, &state.session_pid, op == "load", || {
                request["ffmpeg"] = json!(ffmpeg_path(&app)?);
                resolve_states(&root, &mut request)
            })?;
        }
        require_generation(&state, generation)?;
        if session.process.is_none() {
            let process = HostProcess::spawn(&[std::ffi::OsStr::new("--audio-host")])?;
            state.session_pid.publish(&process)?;
            session.process = Some(process);
            session.project_id = Some(id.clone());
        }
        let timeout = if op == "load" || op == "update" {
            plugin_load_timeout(&request["project"])
        } else {
            REQUEST_TIMEOUT
        };
        let response = request_session(&mut session, &state.session_pid, request, timeout)
            .map_err(|error| error.to_string())?;
        require_generation(&state, generation)?;
        if op == "snapshot" {
            persist_snapshot(&root, response)
        } else {
            Ok(response)
        }
    })
    .await
    .map_err(|error| error.to_string())
    .and_then(|result| result);
    if recovery_load {
        result.map_err(|error| format!("PLUGIN_HOST_RECOVERY_LOAD_FAILED:{generation}:{error}"))
    } else { result }
}
