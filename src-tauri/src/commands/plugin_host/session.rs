//! 宿主状态与会话：能力协商、开关、测试环境配置。

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

#[derive(Default)]
pub(crate) struct Session {
    pub(crate) generation: u64,
    pub(crate) project_id: Option<String>,
    pub(crate) native_session_id: Option<String>,
    pub(crate) process: Option<HostProcess>,
}

#[derive(Clone, Default)]
pub struct PluginHostState {
    pub(crate) session: Arc<Mutex<Session>>,
    pub(crate) session_pid: Arc<ProcessLease>,
    pub(crate) renders: Arc<Mutex<HashMap<String, Arc<ProcessLease>>>>,
    pub(crate) scan_lock: Arc<Mutex<()>>,
    pub(crate) scan_process: Arc<ProcessLease>,
    pub(crate) scan_control: Arc<ScanControl>,
    pub(crate) scan_storage: Arc<Mutex<()>>,
    pub(crate) scan_sequence: Arc<AtomicU64>,
    pub(crate) generation: Arc<AtomicU64>,
    pub(crate) stopping: Arc<AtomicBool>,
}

impl PluginHostState {
    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        self.generation.fetch_add(1, Ordering::AcqRel);
        self.session_pid.cancel();
        self.scan_process.cancel();
        self.scan_control.shutdown();
        if let Ok(renders) = self.renders.lock() {
            for pid in renders.values() {
                pid.cancel();
            }
        }
        if let Ok(mut session) = self.session.lock() {
            session.process.take();
            session.project_id = None;
        }
    }
}

#[derive(Debug, PartialEq)]
pub(crate) struct HostCapabilities {
    pub(crate) enabled: bool,
    pub(crate) native_editor: bool,
}

pub(crate) fn platform_capabilities(host: bool, platform: &str, display: bool) -> HostCapabilities {
    let enabled = host && matches!(platform, "windows" | "macos" | "linux");
    HostCapabilities {
        enabled,
        native_editor: enabled && (platform != "linux" || display),
    }
}

pub(crate) fn host_capabilities() -> HostCapabilities {
    platform_capabilities(
        cfg!(feature = "native-plugins"),
        std::env::consts::OS,
        std::env::var_os("DISPLAY").is_some_and(|value| !value.is_empty()),
    )
}

pub(crate) fn enabled() -> bool {
    host_capabilities().enabled
}

pub(crate) fn require_enabled() -> Result<(), String> {
    if enabled() {
        Ok(())
    } else {
        Err("Native plugin hosting requires a build with native-plugins enabled on Windows, macOS, or Linux".into())
    }
}

#[tauri::command]
pub fn plugin_host_capabilities() -> Value {
    let capabilities = host_capabilities();
    #[cfg(feature = "native-plugins")]
    let (formats, native_editor_formats) = if capabilities.enabled {
        (
            pymss_audio_host::supported_formats(),
            if capabilities.native_editor {
                pymss_audio_host::native_editor_formats()
            } else {
                Vec::new()
            },
        )
    } else {
        (Vec::new(), Vec::new())
    };
    #[cfg(not(feature = "native-plugins"))]
    let (formats, native_editor_formats): (Vec<&str>, Vec<&str>) = (Vec::new(), Vec::new());
    #[cfg(feature = "native-plugins")]
    let default_directories = if capabilities.enabled {
        pymss_audio_host::default_search_paths().into_iter().filter(|path| path.is_absolute()).collect::<Vec<_>>()
    } else { Vec::new() };
    #[cfg(not(feature = "native-plugins"))]
    let default_directories: Vec<PathBuf> = Vec::new();
    json!({
        "enabled": capabilities.enabled, "platform": std::env::consts::OS,
        "formats": formats, "nativeEditorFormats": native_editor_formats,
        "recording": capabilities.enabled,
        "nativeEditor": capabilities.native_editor, "experimental": true,
        "labBuild": cfg!(feature = "vst-lab"),
        "scanControl": capabilities.enabled,
        "catalogCache": capabilities.enabled,
        "nativeTransport": capabilities.enabled && cfg!(any(windows, target_os = "macos")),
        "defaultDirectories": default_directories,
    })
}

/// Report build capabilities before desktop, storage, logging or singleton startup.
/// This also handles builds without the optional host, so packaging checks never open a GUI.
pub fn run_capabilities_cli() -> Option<i32> {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(std::ffi::OsStr::new("--plugin-host-capabilities")) {
        return None;
    }
    if args.next().is_some() {
        eprintln!("The plugin capability query does not accept additional arguments");
        return Some(2);
    }
    let mut output = std::io::stdout().lock();
    let result = serde_json::to_writer(&mut output, &plugin_host_capabilities())
        .map_err(std::io::Error::other)
        .and_then(|()| output.write_all(b"\n"))
        .and_then(|()| output.flush());
    Some(match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("Cannot write plugin host capabilities: {error}");
            1
        }
    })
}

/// Establish lab storage before logging, recovery, or Tauri initializes.
pub fn configure_lab_environment() {
    #[cfg(feature = "vst-lab")]
    if std::env::var_os("PYMSS_STUDIO_DATA_ROOT").is_none_or(|value| value.is_empty()) {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("the application manifest has a project parent")
            .join("data-vst-lab");
        std::env::set_var("PYMSS_STUDIO_DATA_ROOT", root);
    }
}
