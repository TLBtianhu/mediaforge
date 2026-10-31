//! 项目状态持久化：状态文件路径、编码/解码、快照写入与旧格式兼容。

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

pub(crate) fn project_id(request: &Value) -> Result<&str, String> {
    request
        .get("projectId")
        .and_then(Value::as_str)
        .filter(|id| !id.trim().is_empty() && id.len() <= 256)
        .ok_or_else(|| "A project identifier is required for the audio host session".into())
}

pub(crate) fn state_path(root: &Path, token: &str) -> Result<PathBuf, String> {
    let digest = token
        .strip_prefix("sha256:")
        .filter(|value| {
            value.len() == 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or("Invalid plugin state reference")?;
    Ok(root.join("plugin-states").join(format!("{digest}.bin")))
}

pub(crate) fn persist_state(root: &Path, encoded: &str) -> Result<String, String> {
    if encoded.len() > MAX_STATE_BYTES.div_ceil(3) * 4 {
        return Err("Plugin state exceeded the size limit".into());
    }
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|_| "The plugin returned invalid state data")?;
    if bytes.len() > MAX_STATE_BYTES {
        return Err("Plugin state exceeded the size limit".into());
    }
    let token = format!("sha256:{:x}", Sha256::digest(&bytes));
    let path = state_path(root, &token)?;
    let directory = path.parent().unwrap();
    fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    if !path.exists() {
        let mut file =
            tempfile::NamedTempFile::new_in(directory).map_err(|error| error.to_string())?;
        file.write_all(&bytes)
            .and_then(|_| file.as_file().sync_all())
            .map_err(|error| error.to_string())?;
        if let Err(error) = file.persist_noclobber(&path) {
            if error.error.kind() != std::io::ErrorKind::AlreadyExists {
                return Err(error.error.to_string());
            }
        }
    }
    Ok(token)
}

pub(crate) fn resolve_state(root: &Path, state: &str) -> Result<String, String> {
    if !state.starts_with("sha256:") {
        return Ok(state.to_string());
    }
    let path = state_path(root, state)?;
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|error| format!("Unable to restore plugin state: {error}"))?
        .take(MAX_STATE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > MAX_STATE_BYTES {
        return Err("Stored plugin state exceeded the size limit".into());
    }
    if format!("sha256:{:x}", Sha256::digest(&bytes)) != state {
        return Err("Stored plugin state failed its integrity check".into());
    }
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

pub(crate) fn plugin_warning(message: &str) -> String {
    message
        .chars()
        .filter(|character| !character.is_control())
        .take(512)
        .collect()
}

pub(crate) fn supported_state_format(format: &str) -> bool {
    matches!(format, "vst3" | "vst2") || (cfg!(target_os = "macos") && format == "au")
}

pub(crate) fn omit_bypassed_render_plugins(project: &mut Value) {
    fn retain_active(chain: Option<&mut Value>) {
        if let Some(Value::Array(slots)) = chain {
            slots.retain(|slot| slot.get("bypassed").and_then(Value::as_bool) != Some(true));
        }
    }
    retain_active(project.get_mut("pluginChain"));
    if let Some(Value::Array(tracks)) = project.get_mut("tracks") {
        for track in tracks {
            retain_active(track.get_mut("pluginChain"));
        }
    }
}

pub(crate) fn resolve_states(root: &Path, value: &mut Value) -> Result<(), String> {
    match value {
        Value::Object(object) => {
            let bypassed = object.get("bypassed").and_then(Value::as_bool) == Some(true);
            if bypassed && object.get("skipLoad").and_then(Value::as_bool) == Some(true) {
                return Ok(());
            }
            let unsupported = object
                .get("format")
                .and_then(Value::as_str)
                .is_some_and(|format| !supported_state_format(format));
            if bypassed && unsupported {
                // This is a child-process copy: retain the original reference in
                // the editor project and never interpret another format's blob.
                if object.remove("state").is_some() {
                    object.insert("stateUnavailable".into(), json!(true));
                    object.insert(
                        "bypassWarning".into(),
                        json!("The bypassed plugin format is unavailable in this host"),
                    );
                }
            } else if let Some(Value::String(state)) = object.get("state") {
                match resolve_state(root, state) {
                    Ok(encoded) => {
                        object.insert("state".into(), json!(encoded));
                        object.remove("stateUnavailable");
                        object.remove("bypassWarning");
                    }
                    Err(error) if bypassed => {
                        object.remove("state");
                        object.insert("stateUnavailable".into(), json!(true));
                        object.insert("bypassWarning".into(), json!(plugin_warning(&error)));
                    }
                    Err(error) => return Err(error),
                }
            }
            for child in object.values_mut() {
                resolve_states(root, child)?;
            }
        }
        Value::Array(values) => {
            for child in values {
                resolve_states(root, child)?;
            }
        }
        _ => (),
    }
    Ok(())
}

pub(crate) fn persist_snapshot(root: &Path, mut snapshot: Value) -> Result<Value, String> {
    let states = snapshot
        .get_mut("states")
        .and_then(Value::as_object_mut)
        .ok_or("The audio host returned an invalid state snapshot")?;
    for state in states.values_mut() {
        let encoded = state
            .as_str()
            .ok_or("The audio host returned invalid plugin state")?;
        *state = Value::String(persist_state(root, encoded)?);
    }
    Ok(snapshot)
}

