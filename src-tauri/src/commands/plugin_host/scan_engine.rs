//! 扫描执行器：插件包指纹、路径判定、扫描调度与结果接收。

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

pub(crate) fn bundle_fingerprint(path: &Path) -> Result<String, String> {
    fn append(path: &Path, hash: &mut Sha256) -> Result<Option<fs::Metadata>, String> {
        hash.update(blacklist::path_key(&path.to_string_lossy()).as_bytes());
        let metadata = match fs::metadata(path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                hash.update([0]);
                return Ok(None);
            }
            Err(error) => return Err(error.to_string()),
        };
        hash.update([1]);
        hash.update(metadata.len().to_le_bytes());
        if let Ok(modified) = metadata.modified().and_then(|time| {
            time.duration_since(std::time::UNIX_EPOCH).map_err(std::io::Error::other)
        }) { hash.update(modified.as_nanos().to_le_bytes()); }
        Ok(Some(metadata))
    }
    let mut hash = Sha256::new();
    let metadata = append(path, &mut hash)?.ok_or("Plugin package is missing")?;
    if metadata.is_dir() {
        // Only executable and identity metadata affect identification. Do not walk skins,
        // presets, samples or other large resource trees on every incremental refresh.
        for relative in ["Contents", "Contents/Info.plist", "Contents/Resources",
            "Contents/Resources/moduleinfo.json", "Contents/moduleinfo.json"] {
            append(&path.join(relative), &mut hash)?;
        }
        let mut count = 0;
        for relative in ["Contents/MacOS", "Contents/x86_64-win", "Contents/aarch64-win",
            "Contents/arm64ec-win", "Contents/x86_64-linux", "Contents/aarch64-linux"] {
            let directory = path.join(relative);
            if append(&directory, &mut hash)?.is_some_and(|metadata| metadata.is_dir()) {
                let mut children = fs::read_dir(&directory).map_err(|error| error.to_string())?
                    .take(4097).map(|entry| entry.map(|entry| entry.path()))
                    .collect::<Result<Vec<_>, _>>().map_err(|error| error.to_string())?;
                children.sort();
                count += children.len();
                if count > 4096 { return Err("Plugin bundle contains too many executable files".into()); }
                for child in children { append(&child, &mut hash)?; }
            }
        }
    }
    Ok(format!("metadata-v1:{:x}", hash.finalize()))
}

// Upgrade existing cache fingerprints without forcing all installed plugins to be loaded again.
pub(crate) fn legacy_bundle_fingerprint(path: &Path) -> Result<String, String> {
    fn visit(path: &Path, hash: &mut Sha256, count: &mut usize) -> Result<(), String> {
        *count += 1;
        if *count > 4096 {
            return Err("Plugin bundle contains too many files".into());
        }
        let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
        if metadata.file_type().is_symlink() {
            return Ok(());
        }
        hash.update(path.to_string_lossy().as_bytes());
        hash.update(metadata.len().to_le_bytes());
        if let Ok(modified) = metadata.modified().and_then(|time| {
            time.duration_since(std::time::UNIX_EPOCH)
                .map_err(std::io::Error::other)
        }) {
            hash.update(modified.as_nanos().to_le_bytes());
        }
        if metadata.is_dir() {
            let mut children = fs::read_dir(path)
                .map_err(|error| error.to_string())?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| error.to_string())?;
            children.sort();
            for child in children {
                visit(&child, hash, count)?;
            }
        }
        Ok(())
    }
    let mut hash = Sha256::new();
    visit(path, &mut hash, &mut 0)?;
    Ok(format!("{:x}", hash.finalize()))
}

#[derive(Default, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ScanReport {
    pub(crate) plugins: Vec<Value>,
    pub(crate) failures: Vec<ScanFailure>,
    pub(crate) scanned: usize,
    pub(crate) cached: usize,
    pub(crate) blacklisted: Vec<BlacklistEntry>,
    pub(crate) cancelled: bool,
}

#[derive(serde::Serialize)]
pub(crate) struct ScanFailure {
    pub(crate) path: String,
    pub(crate) message: String,
}

pub(crate) fn is_plugin_path(path: &Path) -> bool {
    #[cfg(feature = "native-plugins")]
    {
        pymss_audio_host::is_plugin_path(path)
    }
    #[cfg(not(feature = "native-plugins"))]
    {
        path.extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| {
                extension.eq_ignore_ascii_case("vst3")
                    || extension.eq_ignore_ascii_case("aaxplugin")
                    || (cfg!(windows) && extension.eq_ignore_ascii_case("dll"))
                    || (cfg!(target_os = "linux") && extension.eq_ignore_ascii_case("so"))
                    || (cfg!(target_os = "macos") && extension.eq_ignore_ascii_case("vst"))
            })
    }
}

pub(crate) fn is_virtual_scan_target(path: &Path) -> bool {
    path == Path::new("au:system")
}

pub(crate) fn unsupported_scan_reason(path: &Path) -> Option<&'static str> {
    #[cfg(feature = "native-plugins")]
    {
        pymss_audio_host::unsupported_plugin_reason(path)
    }
    #[cfg(not(feature = "native-plugins"))]
    {
        path.ancestors()
            .any(|path| {
                path.extension()
                    .is_some_and(|extension| extension.eq_ignore_ascii_case("aaxplugin"))
            })
            .then_some("AAX plugin hosting is not supported in this build")
    }
}

pub(crate) fn audio_unit_version(id: &str) -> Option<u8> {
    fn field(value: &str) -> Option<(u32, &str)> {
        let bytes = value.as_bytes();
        if bytes.first() == Some(&b'#') {
            if let Some(hex) = value.get(1..9) {
                if hex.bytes().all(|byte| byte.is_ascii_hexdigit())
                    && matches!(bytes.get(9), None | Some(b':'))
                {
                    return Some((u32::from_str_radix(hex, 16).ok()?, value.get(9..)?));
                }
            }
        }
        let code: [u8; 4] = bytes.get(..4)?.try_into().ok()?;
        code.iter()
            .all(|byte| (0x20..=0x7e).contains(byte))
            .then_some((u32::from_be_bytes(code), value.get(4..)?))
    }
    let (version, value) = if let Some(value) = id.strip_prefix("au3:") {
        (3, value)
    } else {
        (2, id.strip_prefix("au:")?)
    };
    let (kind, value) = field(value)?;
    let (_, value) = field(value.strip_prefix(':')?)?;
    let (_, remainder) = field(value.strip_prefix(':')?)?;
    (remainder.is_empty()
        && [*b"aufx", *b"aumf", *b"aumu"]
            .map(u32::from_be_bytes)
            .contains(&kind))
    .then_some(version)
}

pub(crate) fn normalize_descriptor(
    descriptor: &mut Value,
    path: &str,
    virtual_target: bool,
) -> Result<(), String> {
    let object = descriptor
        .as_object_mut()
        .ok_or("The plugin scanner returned an invalid result")?;
    let id = object.get("id").and_then(Value::as_str).unwrap_or("");
    let au_version = audio_unit_version(id);
    let inferred = if id.starts_with("vst2:") {
        "vst2"
    } else if au_version.is_some() {
        "au"
    } else {
        "vst3"
    };
    let format = object
        .get("format")
        .and_then(Value::as_str)
        .unwrap_or(inferred)
        .to_string();
    let descriptor_path = if virtual_target || format == "au" {
        if format != "au" || au_version.is_none() {
            return Err("The Audio Unit scanner returned an invalid component identity".into());
        }
        id.to_string()
    } else {
        path.to_string()
    };
    if format == "au" {
        object.insert("auVersion".into(), json!(au_version));
        object.insert("classId".into(), json!(descriptor_path));
    }
    object.insert("format".into(), json!(format));
    object.insert("path".into(), json!(descriptor_path));
    Ok(())
}

pub(crate) fn scan_paths(paths: Vec<String>) -> Result<(Vec<PathBuf>, Vec<ScanFailure>), String> {
    let paths: Vec<_> = paths.into_iter().map(PathBuf::from).collect();
    if paths
        .iter()
        .any(|path| !is_virtual_scan_target(path) && !path.is_absolute())
    {
        return Err("Plugin scan directories must be absolute".into());
    }
    let mut valid = Vec::new();
    let mut failures = Vec::new();
    for path in paths {
        let result = (|| {
            if is_virtual_scan_target(&path) {
                return if cfg!(all(feature = "native-plugins", target_os = "macos")) {
                    Ok(())
                } else {
                    Err("Audio Unit discovery is only available on macOS".into())
                };
            }
            let metadata = fs::metadata(&path).map_err(|error| error.to_string())?;
            let bundle = is_plugin_path(&path);
            if !bundle && !metadata.is_dir() {
                return Err("The selected path is not a directory or a recognized plugin".into());
            }
            // Validate access here: the recursive bundle finder skips unreadable roots.
            if metadata.is_dir() && !bundle {
                fs::read_dir(&path).map_err(|error| error.to_string())?;
            }
            Ok(())
        })();
        match result {
            Ok(()) => valid.push(path),
            Err(message) => failures.push(ScanFailure {
                path: path.to_string_lossy().into_owned(),
                message,
            }),
        }
    }
    Ok((valid, failures))
}

pub(crate) fn selected_scan_targets(paths: Vec<PathBuf>) -> Vec<PathBuf> {
    let audio_units = cfg!(all(feature = "native-plugins", target_os = "macos"))
        && paths.iter().any(|path| is_virtual_scan_target(path));
    let roots: Vec<_> = paths
        .into_iter()
        .filter(|path| !is_virtual_scan_target(path))
        .collect();
    // Never let an empty source list reach the native finder's default fallback.
    #[cfg(feature = "native-plugins")]
    let mut bundles = if roots.is_empty() {
        Vec::new()
    } else {
        pymss_audio_host::find_bundles(&roots)
    };
    #[cfg(not(feature = "native-plugins"))]
    let mut bundles: Vec<PathBuf> = {
        let _ = roots;
        Vec::new()
    };
    if audio_units {
        bundles.push(PathBuf::from("au:system"));
    }
    bundles
}

#[derive(Debug)]
pub(crate) enum ScanProbeError {
    Failure(String),
    Skipped(BlacklistReason),
    Cancelled,
}

impl From<String> for ScanProbeError {
    fn from(message: String) -> Self {
        Self::Failure(message)
    }
}

impl From<&str> for ScanProbeError {
    fn from(message: &str) -> Self {
        Self::Failure(message.into())
    }
}

pub(crate) async fn scan_plugins(
    app: AppHandle,
    state: PluginHostState,
    paths: Vec<String>,
    force: bool,
    scan_id: Option<String>,
) -> Result<ScanReport, String> {
    require_enabled()?;
    if paths.is_empty() {
        return Ok(ScanReport::default());
    }
    let root = crate::storage::data_root_dir(&app).map_err(|error| error.to_string())?;
    let scan_id = scan_id.unwrap_or_else(|| format!("legacy-{}", state.scan_sequence.fetch_add(1, Ordering::Relaxed)));
    // Register before waiting for the worker/scan lock, so disposing a queued request can cancel it.
    let run = state.scan_control.start(scan_id)?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = state
            .scan_lock
            .lock()
            .map_err(|_| "Plugin scan lock failed")?;
        if state.scan_process.id() == u32::MAX {
            return Err("Plugin scanning was cancelled".into());
        }
        let (paths, failures) = scan_paths(paths)?;
        let bundles = selected_scan_targets(paths);
        scan_pipeline::scan(&root, bundles, failures, force,
          scan_pipeline::Context { run: Some(&run), storage: &state.scan_storage }, |bundle, ticket| {
            if state.scan_process.id() == u32::MAX {
                return Err(ScanProbeError::Cancelled);
            }
            let ticket = ticket.expect("a controlled scan owns each package ticket");
            let process =
                HostProcess::spawn(&[std::ffi::OsStr::new("--plugin-scan"), bundle.as_os_str()])?;
            if let Err(message) = state.scan_process.publish(&process) {
                return Err(if state.scan_process.id() == u32::MAX {
                    ScanProbeError::Cancelled
                } else {
                    ScanProbeError::Failure(message)
                });
            }
            run.attach(ticket, process.control.clone());
            let result = receive_scan(&process, &run, ticket, SCAN_TIMEOUT)
                .and_then(|result| {
                    result
                        .get("plugins")
                        .and_then(Value::as_array)
                        .cloned()
                        .ok_or_else(|| ScanProbeError::Failure("The plugin scanner returned an invalid result".into()))
                });
            drop(process);
            state.scan_process.release();
            if state.scan_process.id() == u32::MAX {
                Err(ScanProbeError::Cancelled)
            } else {
                result
            }
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

pub(crate) fn receive_scan(process: &HostProcess, run: &ScanRun, ticket: ScanTicket, timeout: Duration) -> Result<Value, ScanProbeError> {
    loop {
        match run.decision(Some(ticket)) {
            ScanDecision::Skip => return Err(ScanProbeError::Skipped(BlacklistReason::Manual)),
            ScanDecision::Cancel => return Err(ScanProbeError::Cancelled),
            ScanDecision::Continue => {}
        }
        let remaining = timeout.saturating_sub(ticket.started.elapsed());
        if remaining.is_zero() { return Err(ScanProbeError::Skipped(BlacklistReason::Timeout)); }
        match process.responses.recv_timeout(remaining.min(Duration::from_millis(50))) {
            Ok(value) => return value.map_err(ScanProbeError::Failure),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => return Err(ScanProbeError::Failure("The plugin scanner response pipe closed".into())),
        }
    }
}
