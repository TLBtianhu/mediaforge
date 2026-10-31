//! 会话生命周期：关闭、丢弃、代数推进与活动会话重建。

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



pub(crate) fn close_session(session: &mut Session, pid: &ProcessLease) {
    pid.clear();
    session.process.take();
    session.project_id = None;
    session.native_session_id = None;
}

pub(crate) fn discard_session(session: &mut Session, pid: &ProcessLease, project: &str, generation: u64, native_session: &str) -> Value {
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

pub(crate) fn begin_load_generation(state: &PluginHostState, expected: Option<u64>) -> Result<u64, String> {
    if let Some(expected) = expected {
        let next = expected.checked_add(1).ok_or("Audio host generation exhausted")?;
        state.generation.compare_exchange(expected, next, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| next).map_err(|_| "The audio session changed before recovery could reload it".into())
    } else {
        Ok(state.generation.fetch_add(1, Ordering::AcqRel) + 1)
    }
}

pub(crate) fn prepare_session_request(
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

pub(crate) fn request_session(
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

pub(crate) fn reconcile_live_session(session: &mut Session, pid: &ProcessLease, closing: bool) {
    let completed = session.process.as_mut().map(|process| (process.live_closed, process.completed_session_id.take()));
    if closing || completed.as_ref().is_some_and(|(closed, _)| *closed) {
        close_session(session, pid);
    } else if let Some((_, Some(id))) = completed {
        // Deferred load/update and latency-recovery replies carry the same authoritative ID.
        session.native_session_id = Some(id);
    }
}

pub(crate) fn prepare_live_load_with(session: &mut Session, pid: &ProcessLease, generation: u64, detector: &mut impl FnMut(u32) -> Option<String>) -> Result<(), HostRequestError> {
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

pub(crate) fn require_generation(state: &PluginHostState, generation: u64) -> Result<(), String> {
    if !state.stopping.load(Ordering::Acquire)
        && state.generation.load(Ordering::Acquire) == generation
    {
        Ok(())
    } else {
        Err("The audio host request was superseded by a newer session".into())
    }
}
