//! Isolated, opt-in native audio host. The UI process never loads plugin code.
use base64::Engine as _;
use serde_json::{json, Value};
use sha2::Digest;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
#[cfg(test)]
use std::process::Command;
use std::sync::atomic::Ordering;
#[cfg(test)]
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

mod blacklist;
mod dialogs;
mod process_exit;
pub(crate) mod live;
pub(crate) mod process;
pub(crate) mod session;
pub(crate) mod state;
pub(crate) mod render;
pub(crate) mod request;
pub(crate) mod scan_engine;
pub(crate) use live::*;
pub(crate) use process::*;
pub(crate) use session::*;
pub(crate) use state::*;
pub(crate) use scan_engine::*;
pub(crate) mod scan;
#[cfg(test)]
mod dialog_tests;
mod scan_control;
mod scan_pipeline;
#[cfg(test)]
mod scan_tests;
#[cfg(test)]
mod cache_tests;
#[cfg(test)]
mod progress_tests;
use blacklist::{BlacklistEntry, BlacklistReason};
use scan_control::{Decision as ScanDecision, ScanControl, ScanRun, Ticket as ScanTicket};
#[cfg(test)]
use scan_pipeline::scan_bundles;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const SCAN_TIMEOUT: Duration = Duration::from_secs(10);
const RENDER_TIMEOUT: Duration = Duration::from_secs(60 * 30);
const MAX_STATE_BYTES: usize = 32 * 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 128 * 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 64 * 1024 * 1024;
const MAX_CACHE_BYTES: u64 = 8 * 1024 * 1024;
const SCAN_CACHE_VERSION: u32 = 3;

fn ffmpeg_path(app: &AppHandle) -> Result<PathBuf, String> {
    let name = if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    let bundled =
        crate::python::worker::bundled_bin_dirs(app).map_err(|error| error.to_string())?;
    bundled
        .into_iter()
        .chain(
            std::env::var_os("PATH")
                .map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
                .unwrap_or_default(),
        )
        .map(|directory| directory.join(name))
        .find(|path| path.is_file())
        .ok_or_else(|| "FFmpeg is required for the native audio host".to_string())?
        .canonicalize()
        .map_err(|error| error.to_string())
}

fn supported_operation(operation: &str) -> bool {
    matches!(
        operation,
        "load"
            | "update"
            | "play"
            | "pause"
            | "seek"
            | "editor"
            | "snapshot"
            | "status"
            | "unload"
            | "shutdown"
            | "param"
            | "parameters"
            | "monitor"
    )
}

fn plugin_load_timeout(project: &Value) -> Duration {
    // AUv3 extensions and some legacy components instantiate asynchronously,
    // with a bounded ten-second wait per component. The registry-only async
    // flag is unavailable in projects, so reserve a budget for each AU. Bypassed
    // slots are also initialized to retain state/controller access. Keep the
    // overall wait bounded even for projects with many components.
    let units = std::iter::once(project)
        .chain(
            project
                .get("tracks")
                .and_then(Value::as_array)
                .into_iter()
                .flatten(),
        )
        .flat_map(|owner| {
            owner
                .get("pluginChain")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter(|slot| {
            slot.get("format").and_then(Value::as_str) == Some("au")
                && slot
                    .get("pluginId")
                    .and_then(Value::as_str)
                    .and_then(audio_unit_version)
                    .is_some()
        })
        .take(10)
        .count();
    REQUEST_TIMEOUT + Duration::from_secs(units as u64 * 10)
}

fn bind_editor_owner(request: &mut Value, owner: Option<usize>) -> Result<(), String> {
    let object = request.as_object_mut().ok_or("Audio host request must be an object")?;
    object.remove("editorOwner");
    if let Some(owner) = owner {
        if owner == 0 { return Err("The editor window is unavailable".into()); }
        object.insert("editorOwner".into(), json!(owner));
    }
    Ok(())
}


#[cfg(test)]
mod tests;
