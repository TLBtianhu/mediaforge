//! Isolated, opt-in native audio host. The UI process never loads plugin code.
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

mod blacklist;
mod dialogs;
mod process_exit;
pub(crate) mod process;
pub(crate) mod session;
pub(crate) mod state;
pub(crate) mod render;
pub(crate) mod request;
pub(crate) mod scan_engine;
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



fn close_session(session: &mut Session, pid: &ProcessLease) {
    pid.clear();
    session.process.take();
    session.project_id = None;
    session.native_session_id = None;
}

fn discard_session(session: &mut Session, pid: &ProcessLease, project: &str, generation: u64, native_session: &str) -> Value {
    // An old window may release its own unavailable session, but never a newer project's graph.
    let owned = session.project_id.as_deref() == Some(project) && session.generation == generation
        && session.native_session_id.as_deref() == Some(native_session);
    let absent = session.generation == generation && session.project_id.is_none()
        && session.native_session_id.is_none() && session.process.is_none();
    if owned {
        close_session(session, pid);
    }
    json!({ "closed": true, "superseded": !owned && !absent, "generation": generation })
}

fn begin_load_generation(state: &PluginHostState, expected: Option<u64>) -> Result<u64, String> {
    if let Some(expected) = expected {
        let next = expected.checked_add(1).ok_or("Audio host generation exhausted")?;
        state.generation.compare_exchange(expected, next, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| next).map_err(|_| "The audio session changed before recovery could reload it".into())
    } else {
        Ok(state.generation.fetch_add(1, Ordering::AcqRel) + 1)
    }
}

fn prepare_session_request(
    session: &mut Session,
    pid: &ProcessLease,
    loading: bool,
    prepare: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    let result = prepare();
    // Local update validation has not touched the child. Keep its controllers
    // available so recovery can capture edits before fixing the project.
    if loading && result.is_err() {
        close_session(session, pid);
    }
    result
}

fn request_session(
    session: &mut Session,
    pid: &ProcessLease,
    request: Value,
    timeout: Duration,
) -> Result<Value, HostRequestError> {
    let operation = request.get("op").and_then(Value::as_str).unwrap_or("").to_owned();
    let closing = matches!(
        request.get("op").and_then(Value::as_str),
        Some("shutdown" | "unload")
    );
    let mut result = match session.process.as_mut() {
        Some(process) => process.request_live(request, timeout),
        None => Err(HostRequestError::Transport(
            "The audio host session is closed".into(),
        )),
    };
    if let Err(HostRequestError::Transport(message)) = &mut result {
        if let Some(process) = session.process.as_ref() {
            let grace = if matches!(message.as_str(), "The audio host exited unexpectedly" | "The audio host response pipe closed") {
                Duration::from_millis(500)
            } else { Duration::ZERO };
            let observed = {
                // Observe without reaping. Cleanup still signals the owned process group before
                // releasing its PID, and cancellation uses this same identity guard.
                let active = process.control.active.lock().unwrap_or_else(|error| error.into_inner());
                if *active { process_exit::observe(&process.child, grace) } else { Ok(None) }
            };
            match observed {
                Ok(Some(status)) => {
                    message.push_str(&format!(" ({status})"));
                    crate::session_log::early("ERROR", "audio.plugin_host.exit", &format!("pid={} operation={operation} status={status}", process.control.pid));
                }
                other => {
                    let detail = other.err().map(|error| error.to_string()).unwrap_or_else(|| "not observed before cleanup".into());
                    crate::session_log::early("ERROR", "audio.plugin_host.transport", &format!("pid={} operation={operation} exitStatus=unavailable observation={detail} reason={message}", process.control.pid));
                }
            }
        }
    }
    reconcile_live_session(session, pid, result.as_ref().is_err_and(HostRequestError::is_fatal) || (result.is_ok() && closing));
    result
}

fn reconcile_live_session(session: &mut Session, pid: &ProcessLease, closing: bool) {
    let completed = session.process.as_mut().map(|process| (process.live_closed, process.completed_session_id.take()));
    if closing || completed.as_ref().is_some_and(|(closed, _)| *closed) {
        close_session(session, pid);
    } else if let Some((_, Some(id))) = completed {
        // Deferred load/update and latency-recovery replies carry the same authoritative ID.
        session.native_session_id = Some(id);
    }
}

fn prepare_live_load_with(session: &mut Session, pid: &ProcessLease, generation: u64, detector: &mut impl FnMut(u32) -> Option<String>) -> Result<(), HostRequestError> {
    // The caller holds the session lock and has checked this load's reservation. Claim it
    // before any fallible preparation so a retired or failed host remains recoverable.
    session.generation = generation;
    let mut result = match session.process.as_mut() {
        Some(process) => process.finish_pending_with(detector),
        None => Ok(()),
    };
    if result.is_ok() {
        if let Some(title) = session.process.as_ref().and_then(|process| detector(process.control.pid)) {
            // A dialog can also originate in a plugin window message, outside any RPC.
            result = Err(HostRequestError::DialogPending(dialogs::message(&title)));
        }
    }
    let fatal = result.as_ref().is_err_and(HostRequestError::is_fatal);
    reconcile_live_session(session, pid, fatal);
    result?;
    close_session(session, pid);
    Ok(())
}

fn require_generation(state: &PluginHostState, generation: u64) -> Result<(), String> {
    if !state.stopping.load(Ordering::Acquire)
        && state.generation.load(Ordering::Acquire) == generation
    {
        Ok(())
    } else {
        Err("The audio host request was superseded by a newer session".into())
    }
}

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
mod tests {
    use crate::commands::plugin_host::render::{cancel_render, cleanup_render, register_render, render_directory};
    use super::*;

    #[test]
    fn explicit_discard_releases_only_its_own_session_and_tolerates_an_already_closed_host() {
        let pid = ProcessLease::default();
        let mut session = Session { project_id: Some("old".into()), native_session_id: Some("instance-new".into()), generation: 4, ..Session::default() };
        discard_session(&mut session, &pid, "old", 3, "instance-new");
        assert_eq!(session.project_id.as_deref(), Some("old"));
        discard_session(&mut session, &pid, "other", 4, "instance-new");
        assert_eq!(session.project_id.as_deref(), Some("old"));
        discard_session(&mut session, &pid, "old", 4, "instance-old");
        assert_eq!(session.project_id.as_deref(), Some("old"));
        assert_eq!(discard_session(&mut session, &pid, "old", 4, "instance-new")["closed"], true);
        assert!(session.project_id.is_none());
        assert_eq!(discard_session(&mut session, &pid, "old", 4, "instance-new")["closed"], true);
    }

    #[test]
    fn recovery_ack_rejects_replacement_sessions_and_fenced_load_cannot_supersede_them() {
        let state = PluginHostState::default();
        state.generation.store(9, Ordering::Release);
        let mut session = Session { project_id: Some("new".into()), native_session_id: Some("instance-new".into()), generation: 9, ..Session::default() };
        let reply = discard_session(&mut session, &state.session_pid, "old", 9, "instance-old");
        assert_eq!(reply["superseded"], true);
        assert_eq!(session.project_id.as_deref(), Some("new"));
        close_session(&mut session, &state.session_pid);
        let reply = discard_session(&mut session, &state.session_pid, "old", 9, "instance-old");
        assert_eq!(reply["superseded"], false);
        assert_eq!(reply["generation"], 9);
        assert_eq!(begin_load_generation(&state, Some(9)).unwrap(), 10);
        assert!(begin_load_generation(&state, Some(9)).is_err());
        assert_eq!(state.generation.load(Ordering::Acquire), 10);
    }

    #[test]
    fn empty_session_cannot_authorize_recovery_over_a_pending_load() {
        let state = PluginHostState::default();
        state.generation.store(8, Ordering::Release);
        let mut session = Session { generation: 8, ..Session::default() };
        // A different window reserves a load before its blocking task obtains the session lock.
        let pending = begin_load_generation(&state, None).unwrap();
        assert_eq!(pending, 9);
        let reply = discard_session(&mut session, &state.session_pid, "old", pending, "old-native");
        assert_eq!(reply["superseded"], true);
        assert_eq!(session.generation, 8);
        assert!(require_generation(&state, pending).is_ok());
    }

    #[test]
    fn failed_load_preflight_retains_the_claimed_generation_for_recovery() {
        let state = PluginHostState::default();
        state.generation.store(8, Ordering::Release);
        let mut session = Session { generation: 8, ..Session::default() };
        let generation = begin_load_generation(&state, None).unwrap();
        assert!(require_generation(&state, generation).is_ok());
        prepare_live_load_with(&mut session, &state.session_pid, generation, &mut |_| None).unwrap();
        let root = tempfile::tempdir().unwrap();
        let mut request = json!({"project":{"pluginChain":[{
            "format":"vst3", "bypassed":false,
            "state":format!("sha256:{}", "a".repeat(64))
        }]}});
        assert!(prepare_session_request(&mut session, &state.session_pid, true, || {
            resolve_states(root.path(), &mut request)
        }).is_err());
        assert!(session.process.is_none());
        assert_eq!(session.generation, generation);
        let reply = discard_session(&mut session, &state.session_pid, "project", generation, "old-native");
        assert_eq!(reply["superseded"], false);
        assert_eq!(begin_load_generation(&state, Some(generation)).unwrap(), generation + 1);
    }

    #[test]
    fn editor_owner_comes_from_the_calling_window_and_never_from_frontend_json() {
        let mut request = json!({"op":"editor", "editorOwner":999});
        bind_editor_owner(&mut request, Some(12345)).unwrap();
        assert_eq!(request["editorOwner"], 12345);
        bind_editor_owner(&mut request, None).unwrap();
        assert!(request.get("editorOwner").is_none());
        request["editorOwner"] = json!(999);
        assert!(bind_editor_owner(&mut request, Some(0)).is_err());
        assert!(request.get("editorOwner").is_none());
    }

    #[test]
    fn build_capabilities_separate_native_host_support_from_lab_identity() {
        let capabilities = plugin_host_capabilities();
        let available = cfg!(feature = "native-plugins")
            && matches!(std::env::consts::OS, "windows" | "macos" | "linux");
        assert_eq!(capabilities["enabled"], available);
        assert_eq!(capabilities["labBuild"], cfg!(feature = "vst-lab"));
        assert_eq!(capabilities["experimental"], true);
        let formats = capabilities["formats"].as_array().unwrap();
        if available {
            assert!(formats.contains(&json!("vst2")));
            assert!(formats.contains(&json!("vst3")));
            assert_eq!(formats.contains(&json!("au")), cfg!(target_os = "macos"));
        } else {
            assert!(formats.is_empty());
        }
        assert!(!formats.contains(&json!("aax")));
        let directories = capabilities["defaultDirectories"].as_array().unwrap();
        #[cfg(feature = "native-plugins")]
        if available {
            let expected: Vec<PathBuf> = pymss_audio_host::default_search_paths().into_iter()
                .filter(|path| path.is_absolute()).collect();
            assert_eq!(capabilities["defaultDirectories"], json!(expected));
            assert!(!directories.is_empty());
            assert_eq!(directories.iter().map(|path| PathBuf::from(path.as_str().unwrap())).collect::<std::collections::HashSet<_>>().len(), directories.len());
        }
        if !available {
            assert!(directories.is_empty());
        }
        assert!(directories.iter().all(|path| Path::new(path.as_str().unwrap()).is_absolute()));
    }

    #[test]
    fn platform_support_keeps_headless_linux_audio_enabled_without_an_editor() {
        for platform in ["windows", "macos", "linux"] {
            assert_eq!(
                platform_capabilities(true, platform, true),
                HostCapabilities {
                    enabled: true,
                    native_editor: true,
                }
            );
            assert_eq!(
                platform_capabilities(false, platform, true),
                HostCapabilities {
                    enabled: false,
                    native_editor: false,
                }
            );
        }
        assert_eq!(
            platform_capabilities(true, "linux", false),
            HostCapabilities {
                enabled: true,
                native_editor: false,
            }
        );
        for platform in ["windows", "macos"] {
            assert!(platform_capabilities(true, platform, false).native_editor);
        }
        assert!(!platform_capabilities(true, "freebsd", true).enabled);
    }

    #[test]
    fn group_signals_reject_special_ids_parent_group_and_overflow() {
        for pid in [0, 1, 42, u32::MAX, i32::MAX as u32 + 1] {
            assert_eq!(isolated_group_target(pid, 42), None);
        }
        assert_eq!(isolated_group_target(43, 42), Some(-43));
        assert_eq!(isolated_group_target(i32::MAX as u32, 42), Some(-i32::MAX));
    }

    #[test]
    fn a_retired_process_identity_cannot_be_signalled_by_late_cancellation() {
        let control = Arc::new(ProcessControl {
            pid: std::process::id(),
            active: Mutex::new(false),
        });
        let lease = ProcessLease(Mutex::new(LeaseState {
            cancelled: false,
            process: Some(control.clone()),
        }));
        assert_eq!(lease.cancel(), std::process::id());
        assert_eq!(lease.id(), u32::MAX);
        assert_eq!(lease.cancel(), u32::MAX);
        assert!(!*control.active.lock().unwrap());
    }

    #[test]
    fn shutdown_rejects_new_work_and_cancels_queued_scan_and_render_leases() {
        let state = PluginHostState::default();
        let queued = register_render(&state, "queued-task".into()).unwrap();
        state.shutdown();
        assert!(queued.require_active().is_err());
        assert!(register_render(&state, "later-task".into()).is_err());
        assert_eq!(state.scan_process.id(), u32::MAX);
        assert!(require_generation(&state, state.generation.load(Ordering::Acquire)).is_err());
    }

    #[cfg(any(unix, windows))]
    #[test]
    fn active_render_cancellation_holds_its_child_identity_until_reaping() {
        let state = PluginHostState::default();
        let task = register_render(&state, "running-task".into()).unwrap();
        #[cfg(unix)]
        let mut command = Command::new("/bin/sh");
        #[cfg(unix)]
        command.args(["-c", "exec sleep 30"]);
        #[cfg(windows)]
        let mut command = Command::new("cmd.exe");
        #[cfg(windows)]
        command.args(["/D", "/C", "ping -n 30 127.0.0.1 >nul"]);
        let process = HostProcess::spawn_command(command).unwrap();
        let pid = process.child.id();
        let control = process.control.clone();
        task.pid.publish(&process).unwrap();
        assert!(task.pid.publish(&process).is_err());
        assert_eq!(cancel_render(&state, "running-task").unwrap(), Some(pid));
        assert!(task.require_active().is_err());
        assert!(task.pid.publish(&process).is_err());
        drop(process);
        assert!(!*control.active.lock().unwrap());
        assert_eq!(
            cancel_render(&state, "running-task").unwrap(),
            Some(u32::MAX)
        );
        drop(task);
        assert_eq!(cancel_render(&state, "running-task").unwrap(), None);
    }

    #[cfg(unix)]
    #[test]
    fn unix_helpers_have_an_owned_process_group_and_reaped_identity() {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "exec sleep 30"]);
        let process = HostProcess::spawn_command(command).unwrap();
        let lease = ProcessLease::default();
        lease.publish(&process).unwrap();
        let control = process.control.clone();
        let pid = process.child.id();
        unsafe {
            assert_eq!(libc::getpgid(pid as libc::pid_t), pid as libc::pid_t);
            assert_ne!(pid as libc::pid_t, libc::getpgrp());
        }
        drop(process);
        assert!(!*control.active.lock().unwrap());
        assert_eq!(lease.cancel(), pid);
        unsafe {
            assert_eq!(
                libc::waitpid(pid as libc::pid_t, std::ptr::null_mut(), libc::WNOHANG),
                -1
            );
        }
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ECHILD)
        );
    }

    #[test]
    fn response_ids_reject_late_or_malformed_responses() {
        assert!(decode_response(json!({"id":1,"ok":true,"result":7}), 2).is_err());
        assert!(
            decode_response(json!({"id":2,"ok":false,"error":"Plugin failed"}), 2)
                .unwrap_err()
                .to_string()
                .contains("Plugin failed")
        );
        assert_eq!(
            decode_response(json!({"id":2,"ok":true,"result":7}), 2).unwrap(),
            json!(7)
        );
    }

    #[test]
    fn state_references_round_trip_and_reject_corruption_and_traversal() {
        let root = tempfile::tempdir().unwrap();
        let encoded = base64::engine::general_purpose::STANDARD.encode(b"plugin-state");
        let token = persist_state(root.path(), &encoded).unwrap();
        assert_eq!(persist_state(root.path(), &encoded).unwrap(), token);
        let mut project =
            json!({"tracks":[{"pluginChain":[{"state":token,"unknown":"preserved"}]}]});
        resolve_states(root.path(), &mut project).unwrap();
        assert_eq!(project["tracks"][0]["pluginChain"][0]["state"], encoded);
        assert_eq!(
            project["tracks"][0]["pluginChain"][0]["unknown"],
            "preserved"
        );
        assert!(state_path(root.path(), "sha256:../../secret").is_err());
        fs::write(state_path(root.path(), &token).unwrap(), b"tampered").unwrap();
        assert!(resolve_states(root.path(), &mut json!({"state":token})).is_err());
    }

    #[test]
    fn quarantined_bypass_states_remain_opaque_until_explicitly_enabled() {
        let root = tempfile::tempdir().unwrap();
        let mut slot = json!({"format":"au", "bypassed":true, "skipLoad":true, "state":"sha256:../../unresolved"});
        resolve_states(root.path(), &mut slot).unwrap();
        assert_eq!(slot["state"], "sha256:../../unresolved");
        slot["bypassed"] = json!(false);
        assert!(resolve_states(root.path(), &mut slot).is_err());
    }

    #[test]
    fn render_copy_omits_disabled_plugins_before_resolving_states_and_preserves_the_project() {
        let root = tempfile::tempdir().unwrap();
        let project = json!({"id":"render", "pluginChain":[
            {"id":"off", "bypassed":true, "state":"sha256:../../invalid"},
            {"id":"on", "bypassed":false},
            {"id":"default"}
        ], "tracks":[{"id":"track", "pluginChain":[
            {"id":"track-off", "bypassed":true, "state":"sha256:missing"},
            {"id":"track-on", "bypassed":false}
        ]}, {"id":"plain"}], "metadata":{"bypassed":true,"keep":1}});
        let mut render = project.clone();
        omit_bypassed_render_plugins(&mut render);
        resolve_states(root.path(), &mut render).unwrap();
        assert_eq!(render["pluginChain"], json!([{"id":"on","bypassed":false},{"id":"default"}]));
        assert_eq!(render["tracks"][0]["pluginChain"], json!([{"id":"track-on","bypassed":false}]));
        assert_eq!(render["tracks"][1], project["tracks"][1]);
        assert_eq!(render["metadata"], project["metadata"]);
        assert_eq!(project["pluginChain"][0]["state"], "sha256:../../invalid");
        assert_eq!(project["tracks"][0]["pluginChain"][0]["state"], "sha256:missing");
    }

    #[test]
    fn bypassed_missing_or_corrupt_states_are_only_removed_from_the_child_copy() {
        let root = tempfile::tempdir().unwrap();
        let encoded = base64::engine::general_purpose::STANDARD.encode(b"saved-state");
        let token = persist_state(root.path(), &encoded).unwrap();
        for corrupt in [false, true] {
            let path = state_path(root.path(), &token).unwrap();
            if corrupt {
                fs::write(&path, b"corrupt").unwrap();
            } else {
                fs::remove_file(&path).unwrap();
            }
            let original = json!({"pluginChain":[{
                "id":"bypassed", "format":"vst3", "bypassed":true,
                "state":token, "vendorMetadata":{"preserved":true}
            }]});
            let mut child = original.clone();
            resolve_states(root.path(), &mut child).unwrap();
            assert_eq!(original["pluginChain"][0]["state"], token);
            let slot = &child["pluginChain"][0];
            assert!(slot.get("state").is_none());
            assert_eq!(slot["stateUnavailable"], true);
            assert!(slot["bypassWarning"]
                .as_str()
                .is_some_and(|value| !value.is_empty()));
            assert_eq!(
                slot["vendorMetadata"],
                original["pluginChain"][0]["vendorMetadata"]
            );
        }
    }

    #[test]
    fn enabled_missing_states_and_invalid_references_remain_strict() {
        let root = tempfile::tempdir().unwrap();
        for bypassed in [false, true] {
            let mut slot =
                json!({"format":"vst3", "bypassed":bypassed, "state":"sha256:../../invalid"});
            let result = resolve_states(root.path(), &mut slot);
            assert_eq!(result.is_ok(), bypassed);
            if bypassed {
                assert_eq!(slot["stateUnavailable"], true);
                assert!(slot.get("state").is_none());
            }
        }
        let missing = format!("sha256:{}", "a".repeat(64));
        assert!(
            resolve_states(root.path(), &mut json!({"bypassed":false, "state":missing})).is_err()
        );
    }

    #[test]
    fn bypassed_unknown_formats_never_resolve_their_opaque_state() {
        let root = tempfile::tempdir().unwrap();
        let original =
            json!({"format":"aax", "bypassed":true, "state":"sha256:opaque-other-format"});
        let mut child = original.clone();
        resolve_states(root.path(), &mut child).unwrap();
        assert_eq!(original["state"], "sha256:opaque-other-format");
        assert!(child.get("state").is_none());
        assert_eq!(child["stateUnavailable"], true);
        assert!(!root.path().join("plugin-states").exists());
    }

    #[test]
    fn valid_bypassed_state_is_restored_and_clears_stale_unavailable_metadata() {
        let root = tempfile::tempdir().unwrap();
        let token = persist_state(root.path(), "YQ==").unwrap();
        let mut child = json!({"format":"vst3", "bypassed":true, "state":token,
            "stateUnavailable":true,"bypassWarning":"old failure"});
        resolve_states(root.path(), &mut child).unwrap();
        assert_eq!(child["state"], "YQ==");
        assert!(child.get("stateUnavailable").is_none());
        assert!(child.get("bypassWarning").is_none());
        assert_eq!(plugin_warning("first\nsecond\0"), "firstsecond");
    }

    #[test]
    fn every_supported_format_resolves_persisted_state_without_losing_metadata() {
        let root = tempfile::tempdir().unwrap();
        let encoded = base64::engine::general_purpose::STANDARD.encode(b"opaque-plugin-state");
        let token = persist_state(root.path(), &encoded).unwrap();
        for format in ["vst3", "vst2", "au"]
            .into_iter()
            .filter(|format| supported_state_format(format))
        {
            let mut slot =
                json!({"format":format,"bypassed":true,"state":token,"metadata":{"keep":true}});
            resolve_states(root.path(), &mut slot).unwrap();
            assert_eq!(slot["state"], encoded);
            assert_eq!(slot["metadata"]["keep"], true);
            assert!(slot.get("bypassWarning").is_none());
        }
    }

    #[test]
    fn virtual_audio_unit_catalog_is_saved_for_display_but_reenumerated_on_refresh() {
        let root = tempfile::tempdir().unwrap();
        let target = PathBuf::from("au:system");
        for count in [1, 2] {
            let report = scan_bundles(
                root.path(),
                vec![target.clone()],
                Vec::new(),
                false,
                |path| {
                    assert_eq!(path, Path::new("au:system"));
                    Ok((0..count)
                        .map(|index| json!({"id":format!("au:aufx:{index:04}:test"), "name":"Effect"}))
                        .collect())
                },
            )
            .unwrap();
            assert_eq!(report.scanned, 1);
            assert_eq!(report.cached, 0);
            assert_eq!(report.plugins.len(), count);
            assert_eq!(report.plugins[0]["format"], "au");
            assert_eq!(report.plugins[0]["path"], report.plugins[0]["id"]);
            let cache: Value =
                serde_json::from_slice(&fs::read(root.path().join("plugin-cache.json")).unwrap())
                    .unwrap();
            assert_eq!(cache["entries"][0]["path"], "au:system");
            let cached = scan_pipeline::cached_report(root.path(), &["au:system".into()]).unwrap();
            assert_eq!(cached["plugins"].as_array().unwrap().len(), count);
            assert_eq!(cached["scanned"], 0);
        }
        let empty = scan_bundles(root.path(), vec![target], Vec::new(), false, |_| {
            Ok(Vec::new())
        })
        .unwrap();
        assert!(empty.failures.is_empty());
    }

    #[test]
    fn scan_descriptors_keep_explicit_formats_and_infer_legacy_prefixes() {
        for (id, expected) in [
            ("vst2:gain", "vst2"),
            ("au:aufx:gain:test", "au"),
            ("au3:aufx:gain:test", "au"),
            ("legacy-class", "vst3"),
        ] {
            let mut descriptor = json!({"id":id});
            normalize_descriptor(&mut descriptor, "plugin.path", false).unwrap();
            assert_eq!(descriptor["format"], expected);
        }
        let mut explicit = json!({"id":"gain", "format":"vst2"});
        normalize_descriptor(&mut explicit, "plugin.path", false).unwrap();
        assert_eq!(explicit["format"], "vst2");
        assert!(normalize_descriptor(&mut explicit, "au:system", true).is_err());
    }

    #[test]
    fn audio_unit_identity_parser_accepts_sdk_fourccs_and_rejects_partial_prefixes() {
        for version in ["au", "au3"] {
            assert_eq!(
                audio_unit_version(&format!("{version}:aufx:A:B::Test")),
                Some(if version == "au" { 2 } else { 3 })
            );
            assert!(audio_unit_version(&format!("{version}:#61756678:#00000001:test")).is_some());
            for suffix in [
                "system",
                "aufx:x:test",
                "aufx:gain:test:extra",
                "xxxx:gain:test",
                "aufx:éabc:test",
                "aufx:#0000000Z:test",
            ] {
                let id = format!("{version}:{suffix}");
                assert!(audio_unit_version(&id).is_none(), "{id}");
                assert!(normalize_descriptor(
                    &mut json!({"id":id,"format":"au"}),
                    "au:system",
                    true
                )
                .is_err());
            }
        }
    }

    #[test]
    fn audio_unit_catalog_preserves_v2_v3_and_nullable_shell_metadata() {
        let root = tempfile::tempdir().unwrap();
        let report = scan_bundles(root.path(), vec![PathBuf::from("au:system")], Vec::new(), false, |_| Ok(vec![
            json!({"id":"au:aufx:gain:test","name":"Gain","isInstrument":false,"vendorMetadata":{"keep":true}}),
            json!({"id":"au3:aufx:gain:test","name":"Gain","isInstrument":false,"vendorMetadata":{"keep":true}}),
        ])).unwrap();
        assert_eq!(report.plugins.len(), 2);
        for (index, version) in [2, 3].into_iter().enumerate() {
            assert_eq!(report.plugins[index]["auVersion"], version);
            assert_eq!(report.plugins[index]["path"], report.plugins[index]["id"]);
            assert_eq!(
                report.plugins[index]["classId"],
                report.plugins[index]["id"]
            );
            assert_eq!(report.plugins[index]["vendorMetadata"]["keep"], true);
        }
        assert_ne!(report.plugins[0]["id"], report.plugins[1]["id"]);
        let mut child = json!({"id":"vst2:12345678","isInstrument":null,"isShellChild":true,"unknown":{"keep":true}});
        normalize_descriptor(&mut child, "shell.dll", false).unwrap();
        assert!(child["isInstrument"].is_null());
        assert_eq!(child["isShellChild"], true);
        assert_eq!(child["unknown"]["keep"], true);
    }

    #[test]
    fn asynchronous_load_budget_includes_bypassed_master_and_track_units_and_is_bounded() {
        let extension = json!({"format":"au", "pluginId":"au3:aufx:gain:test", "bypassed":false});
        let project = json!({"pluginChain":[extension], "tracks":[{"pluginChain":[
            extension,
            {"format":"au", "pluginId":"au3:aufx:gain:test", "bypassed":true},
            {"format":"au", "pluginId":"au:aufx:gain:test"},
            {"format":"vst3", "pluginId":"au3:aufx:gain:test"}
        ]}]});
        assert_eq!(plugin_load_timeout(&project), Duration::from_secs(60));
        assert_eq!(plugin_load_timeout(&json!({})), REQUEST_TIMEOUT);
        assert_eq!(
            plugin_load_timeout(&json!({"pluginChain":vec![extension; 100]})),
            Duration::from_secs(120)
        );
    }

    #[test]
    fn legacy_unsupported_shell_cache_is_reprobed_with_the_current_contract() {
        let root = tempfile::tempdir().unwrap();
        let binary = root.path().join("shell.dll");
        fs::write(&binary, "shell-binary").unwrap();
        let path = binary
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let fingerprint = bundle_fingerprint(&binary).unwrap();
        fs::write(root.path().join("plugin-cache.json"), serde_json::to_vec(&json!({"version":1,"entries":[{
            "path":path,"fingerprint":fingerprint,"plugins":[],"error":"VST2 shell containers are not supported"
        }]})).unwrap()).unwrap();
        let report = scan_bundles(root.path(), vec![binary], Vec::new(), false, |_| {
            Ok(vec![
                json!({"id":"vst2:12345678","isInstrument":null,"isShellChild":true}),
            ])
        })
        .unwrap();
        assert_eq!(report.scanned, 1);
        assert_eq!(report.cached, 0);
        assert!(report.failures.is_empty());
        assert_eq!(report.plugins[0]["isShellChild"], true);
    }

    #[test]
    fn old_scan_cache_is_reprobed_and_current_category_metadata_survives_reuse() {
        let root = tempfile::tempdir().unwrap();
        let binary = root.path().join("effect.vst3");
        fs::write(&binary, "plugin-binary").unwrap();
        let path = binary
            .canonicalize()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        let fingerprint = bundle_fingerprint(&binary).unwrap();
        fs::write(
            root.path().join("plugin-cache.json"),
            serde_json::to_vec(&json!({
                "version":2,"entries":[{"path":path,"fingerprint":fingerprint,
                    "plugins":[{"id":"gain","isInstrument":false}],"error":null}]
            }))
            .unwrap(),
        )
        .unwrap();
        let report = scan_bundles(root.path(), vec![binary.clone()], Vec::new(), false, |_| {
            Ok(vec![
                json!({"id":"gain","format":"vst3","isInstrument":false,
                "category":"Eq","subCategories":["Fx","EQ"]}),
            ])
        })
        .unwrap();
        assert_eq!(report.scanned, 1);
        assert_eq!(report.cached, 0);
        let cached = scan_bundles(root.path(), vec![binary], Vec::new(), false, |_| {
            panic!("current metadata should be reused without loading the plugin")
        })
        .unwrap();
        assert_eq!(cached.cached, 1);
        assert_eq!(cached.plugins[0]["category"], "Eq");
        assert_eq!(cached.plugins[0]["subCategories"], json!(["Fx", "EQ"]));
    }

    #[test]
    fn scan_paths_only_validates_explicit_sources() {
        let root = tempfile::tempdir().unwrap();
        let missing = root.path().join("missing");
        let file = root.path().join("readme.txt");
        fs::write(&file, "readme").unwrap();
        let valid = root.path().join("plugins");
        fs::create_dir(&valid).unwrap();
        let (paths, failures) = scan_paths(
            [&missing, &file, &valid]
                .map(|path| path.to_string_lossy().into_owned())
                .to_vec(),
        )
        .unwrap();
        assert_eq!(paths, vec![valid]);
        assert_eq!(failures.len(), 2);
        let (paths, failures) = scan_paths(Vec::new()).unwrap();
        assert!(paths.is_empty());
        assert!(failures.is_empty());
        assert!(scan_paths(vec!["relative/plugins".into()]).is_err());
    }

    #[test]
    fn empty_sources_never_probe_or_reuse_an_old_catalog() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("plugin-cache.json"),
            serde_json::to_vec(&json!({"version":SCAN_CACHE_VERSION,"entries":[{
                "path":"old-fixture.vst3","plugins":[{"id":"old-fixture"}]
            }]}))
            .unwrap(),
        )
        .unwrap();
        let (paths, failures) = scan_paths(Vec::new()).unwrap();
        let bundles = selected_scan_targets(paths);
        assert!(bundles.is_empty());
        let report = scan_bundles(root.path(), bundles, failures, false, |_| {
            panic!("empty sources must never load a plugin")
        })
        .unwrap();
        assert!(report.plugins.is_empty());
        assert!(report.failures.is_empty());
        assert_eq!(report.scanned, 0);
        assert_eq!(report.cached, 0);
    }

    #[cfg(feature = "native-plugins")]
    #[test]
    fn selected_directory_does_not_include_other_sources_or_audio_units() {
        let root = tempfile::tempdir().unwrap();
        let selected = root.path().join("selected");
        let other = root.path().join("other");
        fs::create_dir(&selected).unwrap();
        fs::create_dir(&other).unwrap();
        let plugin = selected.join("Gain.vst3");
        fs::create_dir(&plugin).unwrap();
        fs::create_dir(other.join("Unselected.vst3")).unwrap();
        let (paths, failures) = scan_paths(vec![selected.to_string_lossy().into_owned()]).unwrap();
        assert!(failures.is_empty());
        assert_eq!(selected_scan_targets(paths), vec![plugin]);
    }

    #[test]
    fn audio_unit_catalog_requires_an_explicit_supported_source() {
        let (paths, failures) = scan_paths(vec!["au:system".into(), "au:system".into()]).unwrap();
        if cfg!(all(feature = "native-plugins", target_os = "macos")) {
            assert!(failures.is_empty());
            assert_eq!(
                selected_scan_targets(paths),
                vec![PathBuf::from("au:system")]
            );
        } else {
            assert!(paths.is_empty());
            assert_eq!(failures.len(), 2);
            assert!(failures[0].message.contains("only available on macOS"));
        }
        assert!(scan_paths(vec!["au:unknown".into()]).is_err());
    }

    #[test]
    fn scan_paths_accepts_the_platform_vst2_plugin_file() {
        let root = tempfile::tempdir().unwrap();
        let extension = if cfg!(windows) {
            "dll"
        } else if cfg!(target_os = "macos") {
            "vst"
        } else {
            "so"
        };
        let plugin = root.path().join(format!("effect.{extension}"));
        fs::write(&plugin, "plugin-binary").unwrap();
        let (paths, failures) = scan_paths(vec![plugin.to_string_lossy().into_owned()]).unwrap();
        assert_eq!(paths, vec![plugin]);
        assert!(failures.is_empty());
    }

    #[test]
    fn aax_scan_reports_the_format_boundary_without_loading_or_traversing_contents() {
        let directory = tempfile::tempdir().unwrap();
        let bundle = directory.path().join("Effect.aaxplugin");
        fs::create_dir(&bundle).unwrap();
        let (paths, failures) = scan_paths(vec![bundle.to_string_lossy().into_owned()]).unwrap();
        assert!(failures.is_empty());
        let report = scan_bundles(directory.path(), paths, Vec::new(), true, |_| {
            panic!("an unsupported format must never reach the VST loader")
        })
        .unwrap();
        assert!(report.plugins.is_empty());
        assert_eq!(report.scanned, 0);
        assert_eq!(report.failures.len(), 1);
        assert!(report.failures[0]
            .message
            .contains("AAX plugin hosting is not supported"));
    }

    #[test]
    fn scan_report_retains_successes_and_reuses_success_and_error_fingerprints() {
        let root = tempfile::tempdir().unwrap();
        let good = root.path().join("good.vst3");
        let bad = root.path().join("bad.vst3");
        fs::write(&good, "good").unwrap();
        fs::write(&bad, "bad").unwrap();
        let bundles = vec![good.clone(), bad.clone()];
        let mut calls = 0;
        let first = scan_bundles(root.path(), bundles.clone(), Vec::new(), false, |path| {
            calls += 1;
            if path.file_name().unwrap() == "bad.vst3" {
                Err("Plugin process exited".into())
            } else {
                Ok(vec![
                    json!({"id":"gain","vendorMetadata":{"longValue":"keep"}}),
                ])
            }
        })
        .unwrap();
        assert_eq!(calls, 2);
        assert_eq!(first.scanned, 2);
        assert_eq!(first.cached, 0);
        assert_eq!(first.plugins.len(), 1);
        assert_eq!(first.failures.len(), 1);
        assert_eq!(first.plugins[0]["vendorMetadata"]["longValue"], "keep");
        assert_eq!(
            first.plugins[0]["path"],
            good.canonicalize().unwrap().to_string_lossy().as_ref()
        );
        let second = scan_bundles(root.path(), bundles.clone(), Vec::new(), false, |_| {
            panic!("unchanged successes and errors must both be cached")
        })
        .unwrap();
        assert_eq!(second.scanned, 0);
        assert_eq!(second.cached, 2);
        assert_eq!(second.failures[0].message, "Plugin process exited");
        let forced = scan_bundles(root.path(), bundles.clone(), Vec::new(), true, |_| {
            Ok(vec![json!({"id":"recovered"})])
        })
        .unwrap();
        assert_eq!(forced.scanned, 2);
        assert_eq!(forced.cached, 0);
        assert_eq!(forced.plugins.len(), 2);
        assert!(forced.failures.is_empty());
        fs::write(&bad, "updated plugin bytes").unwrap();
        let changed = scan_bundles(root.path(), bundles, Vec::new(), false, |_| {
            Ok(vec![json!({"id":"updated"})])
        })
        .unwrap();
        assert_eq!(changed.scanned, 1);
        assert_eq!(changed.cached, 1);
    }

    #[test]
    fn scan_report_exposes_invalid_or_empty_bundle_results_and_fingerprint_errors() {
        let root = tempfile::tempdir().unwrap();
        let bad = root.path().join("bad.vst3");
        fs::write(&bad, "bad").unwrap();
        for invalid in [vec![], vec![json!("invalid descriptor")]] {
            let report = scan_bundles(root.path(), vec![bad.clone()], Vec::new(), true, |_| {
                Ok(invalid.clone())
            })
            .unwrap();
            assert!(report.plugins.is_empty());
            assert_eq!(report.failures.len(), 1);
            assert_eq!(report.scanned, 1);
        }
        fs::remove_file(&bad).unwrap();
        let report = scan_bundles(root.path(), vec![bad], Vec::new(), false, |_| {
            panic!("a missing bundle must never be probed")
        })
        .unwrap();
        assert_eq!(report.failures.len(), 1);
        assert_eq!(report.scanned, 0);
        assert_eq!(report.cached, 0);
    }

    #[test]
    fn scan_bundle_limit_and_cache_size_remain_bounded() {
        let root = tempfile::tempdir().unwrap();
        assert!(scan_bundles(
            root.path(),
            vec![root.path().join("bad.vst3"); 4097],
            Vec::new(),
            false,
            |_| { panic!("bundle limit must be checked before probing") }
        )
        .is_err());
        let bundle = root.path().join("large.vst3");
        fs::write(&bundle, "binary").unwrap();
        let error = scan_bundles(root.path(), vec![bundle], Vec::new(), false, |_| {
            Ok(vec![
                json!({"id":"large", "data":"x".repeat(MAX_CACHE_BYTES as usize)}),
            ])
        })
        .err()
        .unwrap();
        assert!(error.contains("size limit"));
        assert!(!root.path().join("plugin-cache.json").exists());
    }

    #[test]
    fn scan_cancellation_preserves_the_previous_cache_and_stops_remaining_probes() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("plugin-cache.json");
        let previous = br#"{"version":1,"entries":[]}"#;
        fs::write(&cache, previous).unwrap();
        let bundle = root.path().join("effect.vst3");
        fs::write(&bundle, "binary").unwrap();
        let lease = ProcessLease::default();
        let mut calls = 0;
        let result = scan_bundles(
            root.path(),
            vec![bundle.clone(), bundle],
            Vec::new(),
            true,
            |_| {
                calls += 1;
                lease.cancel();
                lease.release();
                Err(ScanProbeError::Cancelled)
            },
        );
        assert!(result.err().unwrap().contains("cancelled"));
        assert_eq!(calls, 1);
        assert_eq!(lease.id(), u32::MAX);
        assert_eq!(fs::read(cache).unwrap(), previous);
    }

    #[test]
    fn snapshot_references_are_content_addressed_without_losing_unknown_slots() {
        let root = tempfile::tempdir().unwrap();
        let saved = persist_snapshot(
            root.path(),
            json!({"states":{"missing-slot":"","known-slot":"YQ=="},"projectId":"one","sessionId":"native-session","stateRevision":7}),
        )
        .unwrap();
        assert!(saved["states"]["missing-slot"]
            .as_str()
            .unwrap()
            .starts_with("sha256:"));
        assert_eq!(saved["projectId"], "one");
        assert_eq!(saved["sessionId"], "native-session");
        assert_eq!(saved["stateRevision"], 7);
    }

    #[test]
    fn render_paths_are_limited_to_a_single_task_directory() {
        let root = tempfile::tempdir().unwrap();
        for invalid in ["", "..", "../other", "a/b", r"a\b", "C:other"] {
            assert!(render_directory(root.path(), invalid).is_err());
        }
        assert_eq!(
            render_directory(root.path(), "task-01_2").unwrap(),
            root.path().join("temp/vst-renders/task-01_2")
        );
    }

    #[cfg(windows)]
    #[test]
    fn crashed_child_reports_failure_without_waiting_for_the_request_timeout() {
        let mut command = Command::new("cmd.exe");
        command.args(["/D", "/C", "exit /b 23"]);
        let process = HostProcess::spawn_command(command).unwrap();
        assert!(process.receive(Duration::from_secs(2)).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn a_child_that_stops_reading_cannot_block_the_input_timeout() {
        let mut command = Command::new("cmd.exe");
        command.args(["/D", "/C", "ping -n 30 127.0.0.1 >nul"]);
        let started = std::time::Instant::now();
        let mut process = HostProcess::spawn_command(command).unwrap();
        let error = process
            .request(
                json!({"op":"load", "data":"x".repeat(1024 * 1024)}),
                Duration::from_millis(80),
            )
            .unwrap_err();
        assert!(error.to_string().contains("timed out"));
        drop(process);
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn a_new_load_invalidates_old_requests_even_when_the_project_id_is_reused() {
        let state = PluginHostState::default();
        let first = state.generation.fetch_add(1, Ordering::AcqRel) + 1;
        assert!(require_generation(&state, first).is_ok());
        let second = state.generation.fetch_add(1, Ordering::AcqRel) + 1;
        assert!(require_generation(&state, first).is_err());
        assert!(require_generation(&state, second).is_ok());
    }

    #[test]
    fn project_identifiers_are_required() {
        assert!(project_id(&json!({})).is_err());
        assert!(project_id(&json!({"projectId":" "})).is_err());
        assert_eq!(project_id(&json!({"projectId":"one"})).unwrap(), "one");
    }
    #[cfg(windows)]
    fn scripted_session(responses: &[&str]) -> (tempfile::TempDir, Session, ProcessLease) {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("host.cmd");
        let mut script = String::from("@echo off");
        script.push(char::from(10));
        for response in responses {
            script.push_str("set /p fixture_input=");
            script.push(char::from(10));
            script.push_str("echo ");
            script.push_str(response);
            script.push(char::from(10));
        }
        script.push_str("set /p fixture_input=");
        script.push(char::from(10));
        fs::write(&path, script).unwrap();
        let mut command = Command::new("cmd.exe");
        command.args(["/D", "/Q", "/C"]).arg(&path);
        let process = HostProcess::spawn_command(command).unwrap();
        let pid = ProcessLease::default();
        pid.publish(&process).unwrap();
        (
            root,
            Session {
                generation: 9,
                project_id: Some("fixture".into()),
                native_session_id: Some("fixture-session".into()),
                process: Some(process),
            },
            pid,
        )
    }

    #[cfg(windows)]
    #[test]
    fn status_after_native_recovery_updates_the_identity_required_for_discard() {
        let (_root, mut session, pid) = scripted_session(&[
            r#"{"id":1,"ok":true,"result":{"sessionId":"recovered-session","stateRevision":8}}"#,
        ]);
        request_session(&mut session, &pid, json!({"op":"status"}), Duration::from_secs(2)).unwrap();
        assert_eq!(session.native_session_id.as_deref(), Some("recovered-session"));
        discard_session(&mut session, &pid, "fixture", 9, "fixture-session");
        assert!(session.process.is_some());
        discard_session(&mut session, &pid, "fixture", 9, "recovered-session");
        assert!(session.process.is_none());
        assert!(session.native_session_id.is_none());
        assert_eq!(pid.id(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn operation_failure_preserves_session_and_allows_snapshot_retry_in_the_same_process() {
        let (_root, mut session, pid) = scripted_session(&[
            r#"{"id":1,"ok":false,"error":"Snapshot temporarily unavailable"}"#,
            r#"{"id":2,"ok":true,"result":{"states":{},"sessionId":"fixture-session","stateRevision":7}}"#,
        ]);
        let original_pid = pid.id();
        let error = request_session(
            &mut session,
            &pid,
            json!({"op":"snapshot"}),
            Duration::from_secs(2),
        )
        .unwrap_err();
        assert!(matches!(error, HostRequestError::Operation(_)));
        assert_eq!(session.project_id.as_deref(), Some("fixture"));
        assert_eq!(session.generation, 9);
        assert_eq!(pid.id(), original_pid);
        assert!(session
            .process
            .as_mut()
            .unwrap()
            .child
            .try_wait()
            .unwrap()
            .is_none());
        let retried = request_session(
            &mut session,
            &pid,
            json!({"op":"snapshot"}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(retried["stateRevision"], 7);
        assert_eq!(retried["sessionId"], "fixture-session");
        assert_eq!(session.process.as_ref().unwrap().child.id(), original_pid);
    }

    #[cfg(windows)]
    #[test]
    fn local_update_preparation_failure_keeps_the_old_graph_for_recovery() {
        let (root, mut session, pid) = scripted_session(&[
            r#"{"id":1,"ok":true,"result":{"states":{"healthy":"YQ=="}}}"#,
            r#"{"id":2,"ok":true,"result":{"playing":false,"unavailablePlugins":[{"slotId":"missing","message":"Saved state is unavailable"}]}}"#,
        ]);
        let original_pid = pid.id();
        let state = PluginHostState::default();
        state.generation.store(9, Ordering::Release);
        let mut update = json!({"projectId":"fixture","project":{"pluginChain":[{
            "id":"missing", "format":"vst3", "bypassed":false,
            "state":format!("sha256:{}", "a".repeat(64))
        }]}});
        assert!(prepare_session_request(&mut session, &pid, false, || {
            resolve_states(root.path(), &mut update)
        })
        .is_err());
        assert_eq!(session.project_id.as_deref(), Some("fixture"));
        assert_eq!(session.generation, 9);
        assert!(require_generation(&state, 9).is_ok());
        assert_eq!(pid.id(), original_pid);
        assert_eq!(session.process.as_ref().unwrap().child.id(), original_pid);
        let snapshot = request_session(
            &mut session,
            &pid,
            json!({"op":"snapshot","projectId":"fixture"}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(snapshot["states"]["healthy"], "YQ==");
        update["project"]["pluginChain"][0]["bypassed"] = json!(true);
        prepare_session_request(&mut session, &pid, false, || {
            resolve_states(root.path(), &mut update)
        })
        .unwrap();
        assert_eq!(
            update["project"]["pluginChain"][0]["stateUnavailable"],
            true
        );
        update["op"] = json!("update");
        let response = request_session(&mut session, &pid, update, Duration::from_secs(2)).unwrap();
        assert_eq!(response["unavailablePlugins"][0]["slotId"], "missing");
        assert_eq!(session.process.as_ref().unwrap().child.id(), original_pid);
    }

    #[cfg(windows)]
    #[test]
    fn local_load_preparation_failure_still_closes_the_previous_session() {
        let (_root, mut session, pid) = scripted_session(&[]);
        let error = prepare_session_request(&mut session, &pid, true, || {
            Err("Stored plugin state failed its integrity check".into())
        })
        .unwrap_err();
        assert!(error.contains("integrity check"));
        assert!(session.process.is_none());
        assert!(session.project_id.is_none());
        assert_eq!(pid.id(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn local_request_rejection_keeps_the_native_process_usable() {
        let (_root, mut session, pid) =
            scripted_session(&[r#"{"id":2,"ok":true,"result":{"states":{}}}"#]);
        let original_pid = pid.id();
        let error = request_session(&mut session, &pid, json!("invalid"), Duration::from_secs(2))
            .unwrap_err();
        assert!(matches!(error, HostRequestError::Request(_)));
        assert_eq!(pid.id(), original_pid);
        request_session(
            &mut session,
            &pid,
            json!({"op":"snapshot"}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(session.process.as_ref().unwrap().child.id(), original_pid);
    }

    #[cfg(windows)]
    #[test]
    fn malformed_json_and_response_ids_clear_the_session_and_reap_the_child() {
        for response in ["invalid-json", r#"{"id":99,"ok":true,"result":{}}"#] {
            let (_root, mut session, pid) = scripted_session(&[response]);
            let error = request_session(
                &mut session,
                &pid,
                json!({"op":"snapshot"}),
                Duration::from_secs(2),
            )
            .unwrap_err();
            assert!(error.is_fatal());
            assert!(session.process.is_none());
            assert!(session.project_id.is_none());
            assert_eq!(pid.id(), 0);
        }
    }

    #[cfg(windows)]
    #[test]
    fn blocked_or_crashed_child_clears_the_preview_session() {
        for action in ["ping -n 30 127.0.0.1 >nul", "exit /b 23"] {
            let mut command = Command::new("cmd.exe");
            command.args(["/D", "/C", action]);
            let process = HostProcess::spawn_command(command).unwrap();
            let pid = ProcessLease::default();
            pid.publish(&process).unwrap();
            let mut session = Session {
                generation: 1,
                project_id: Some("fixture".into()),
                native_session_id: Some("fixture-session".into()),
                process: Some(process),
            };
            let error = request_session(
                &mut session,
                &pid,
                json!({"op":"snapshot"}),
                Duration::from_millis(80),
            )
            .unwrap_err();
            assert!(error.is_fatal());
            assert!(session.process.is_none());
            assert!(session.project_id.is_none());
            assert_eq!(pid.id(), 0);
        }
    }

    #[test]
    fn request_limit_matches_the_native_reader_and_is_nonfatal() {
        assert!(validate_request_length(MAX_REQUEST_BYTES - 1).is_ok());
        let error = validate_request_length(MAX_REQUEST_BYTES).unwrap_err();
        assert!(matches!(error, HostRequestError::Request(_)));
        assert!(!error.is_fatal());
        assert!(validate_request_length(MAX_RESPONSE_BYTES).is_err());
    }

    #[test]
    fn queued_render_cancellation_prevents_launch_and_cleanup_waits_for_its_lease() {
        let state = PluginHostState::default();
        let root = tempfile::tempdir().unwrap();
        let task = register_render(&state, "queued-task".into()).unwrap();
        assert_eq!(cancel_render(&state, "queued-task").unwrap(), Some(0));
        assert!(task.require_active().is_err());
        assert!(cleanup_render(&state, root.path(), "queued-task").is_err());
        assert!(!render_directory(root.path(), "queued-task")
            .unwrap()
            .exists());
        drop(task);
        assert_eq!(cancel_render(&state, "queued-task").unwrap(), None);
        assert!(cleanup_render(&state, root.path(), "queued-task").is_ok());
    }

    #[test]
    fn duplicate_registration_and_old_leases_cannot_remove_another_render() {
        let state = PluginHostState::default();
        let first = register_render(&state, "same-task".into()).unwrap();
        assert!(register_render(&state, "same-task".into()).is_err());
        assert!(Arc::ptr_eq(
            state.renders.lock().unwrap().get("same-task").unwrap(),
            &first.pid
        ));
        // A stale lease must not remove a replacement registration after an identifier is reused.
        state.renders.lock().unwrap().remove("same-task");
        let replacement = register_render(&state, "same-task".into()).unwrap();
        drop(first);
        assert!(Arc::ptr_eq(
            state.renders.lock().unwrap().get("same-task").unwrap(),
            &replacement.pid
        ));
        drop(replacement);
        assert!(state.renders.lock().unwrap().is_empty());
    }
    #[test]
    fn public_operations_include_parameter_control_without_bypassing_render_isolation() {
        assert!(supported_operation("param"));
        assert!(supported_operation("parameters"));
        // Rendering must retain its dedicated task directory and cancellation lifetime.
        for private_operation in ["render", "--audio-host", "--plugin-scan", "unknown"] {
            assert!(!supported_operation(private_operation));
        }
    }

    #[cfg(windows)]
    #[test]
    fn invalid_parameter_preserves_session_for_parameter_reads_and_subsequent_edits() {
        let (_root, mut session, pid) = scripted_session(&[
            r#"{"id":1,"ok":false,"error":"Parameter value is outside the supported range"}"#,
            r#"{"id":2,"ok":true,"result":{"parameters":[{"id":"7","value":0.5}]}}"#,
            r#"{"id":3,"ok":true,"result":{"stateRevision":8}}"#,
        ]);
        let original_pid = pid.id();
        let error = request_session(
            &mut session,
            &pid,
            json!({"op":"param","slotId":"gain","paramId":"7","value":-1}),
            Duration::from_secs(2),
        )
        .unwrap_err();
        assert!(matches!(error, HostRequestError::Operation(_)));
        assert_eq!(session.project_id.as_deref(), Some("fixture"));
        assert_eq!(session.generation, 9);
        assert_eq!(pid.id(), original_pid);
        let parameters = request_session(
            &mut session,
            &pid,
            json!({"op":"parameters","slotId":"gain"}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(parameters["parameters"][0]["id"], "7");
        let edited = request_session(
            &mut session,
            &pid,
            json!({"op":"param","slotId":"gain","paramId":"7","value":0.75}),
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(edited["stateRevision"], 8);
        assert_eq!(session.process.as_ref().unwrap().child.id(), original_pid);
    }
}
