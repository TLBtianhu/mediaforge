//! 音频宿主进程：租约、作业对象、请求编码与响应解码。

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

/// Cancellation retains a particular process identity, never a reusable PID.
#[derive(Default)]
pub(crate) struct ProcessLease(pub(crate) Mutex<LeaseState>);

#[derive(Default)]
pub(crate) struct LeaseState {
    pub(crate) cancelled: bool,
    pub(crate) process: Option<Arc<ProcessControl>>,
}

impl ProcessLease {
    pub(crate) fn id(&self) -> u32 {
        let state = self.0.lock().unwrap_or_else(|error| error.into_inner());
        if state.cancelled {
            u32::MAX
        } else {
            state.process.as_ref().map_or(0, |process| process.pid)
        }
    }

    pub(crate) fn publish(&self, process: &HostProcess) -> Result<(), String> {
        let mut state = self.0.lock().unwrap_or_else(|error| error.into_inner());
        if state.cancelled {
            return Err("The audio host operation was cancelled".into());
        }
        if state.process.is_some() {
            return Err("The audio host operation already owns a process".into());
        }
        state.process = Some(process.control.clone());
        Ok(())
    }

    pub(crate) fn cancel(&self) -> u32 {
        let mut state = self.0.lock().unwrap_or_else(|error| error.into_inner());
        let previous = if state.cancelled {
            u32::MAX
        } else {
            state.process.as_ref().map_or(0, |process| process.pid)
        };
        state.cancelled = true;
        if let Some(process) = state.process.take() {
            process.stop();
        }
        previous
    }

    pub(crate) fn clear(&self) {
        *self.0.lock().unwrap_or_else(|error| error.into_inner()) = LeaseState::default();
    }

    pub(crate) fn release(&self) {
        // Reuse the scanner lease without clearing a concurrent shutdown.
        self.0
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .process = None;
    }
}

pub(crate) struct ProcessControl {
    pub(crate) pid: u32,
    // Signalling and reaping must hold the same guard: a reaped PID may be reused.
    pub(crate) active: Mutex<bool>,
}

impl ProcessControl {
    pub(crate) fn stop(&self) {
        let active = self
            .active
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if *active {
            terminate_helper(self.pid);
        }
    }

    pub(crate) fn reap(&self, child: &mut Child) {
        let mut active = self
            .active
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        if *active {
            // Signal the group before waiting, including when its leader has
            // exited: decoder descendants may still keep its pipes open.
            terminate_helper(self.pid);
            let _ = child.kill();
            let _ = child.wait();
            *active = false;
        }
    }
}

#[cfg(any(unix, test))]
pub(crate) fn isolated_group_target(pid: u32, parent_group: i32) -> Option<i32> {
    i32::try_from(pid)
        .ok()
        .filter(|pid| *pid > 1 && *pid != parent_group)
        .map(|pid| -pid)
}

pub(crate) fn terminate_helper(pid: u32) {
    #[cfg(unix)]
    unsafe {
        // Every helper is spawned with process_group(0), so descendants inherit
        // a group whose leader is this unreaped child. Never signal our group.
        if let Some(group) = isolated_group_target(pid, libc::getpgrp()) {
            if libc::getpgid(pid as libc::pid_t) == pid as libc::pid_t {
                libc::kill(group, libc::SIGKILL);
            }
        }
    }
    #[cfg(windows)]
    if pid != 0 && pid != u32::MAX {
        crate::python::worker::kill_process_tree(pid);
    }
    #[cfg(not(any(unix, windows)))]
    let _ = pid;
}

#[cfg(windows)]
pub(crate) struct ProcessJob(isize);

#[cfg(windows)]
impl ProcessJob {
    pub(crate) fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        // The job outlives the child and owns all decoder descendants. Closing it
        // also terminates them if the UI exits without receiving a response.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err(std::io::Error::last_os_error().to_string());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const std::ffi::c_void,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
                || AssignProcessToJobObject(job, child.as_raw_handle()) == 0
            {
                let error = std::io::Error::last_os_error().to_string();
                CloseHandle(job);
                return Err(error);
            }
            Ok(Self(job as isize))
        }
    }
}

#[cfg(windows)]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

#[derive(Debug)]
pub(crate) enum HostRequestError {
    /// A valid native response rejected this operation; its process remains usable.
    Operation(String),
    /// Local validation rejected a request before it was written to the child.
    Request(String),
    /// A live helper awaits explicit user interaction; one response is still outstanding.
    DialogPending(String),
    /// The process or JSON-lines channel can no longer be trusted.
    Transport(String),
}

impl HostRequestError {
    pub(crate) fn is_fatal(&self) -> bool {
        matches!(self, Self::Transport(_))
    }
}

impl std::fmt::Display for HostRequestError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let message = match self {
            Self::Operation(message) | Self::Request(message) | Self::DialogPending(message) | Self::Transport(message) => message,
        };
        formatter.write_str(message)
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct NativeRenderProgress {
    pub(crate) phase: String,
    pub(crate) completed: u64,
    pub(crate) total: u64,
}

pub(crate) fn receive_render_result(
    id: u64,
    started: std::time::Instant,
    timeout: Duration,
    mut receive: impl FnMut(Duration) -> Result<Value, HostRequestError>,
    mut progress: impl FnMut(NativeRenderProgress) -> Result<(), HostRequestError>,
) -> Result<Value, HostRequestError> {
    loop {
        let remaining = timeout.checked_sub(started.elapsed()).filter(|remaining| !remaining.is_zero())
            .ok_or_else(|| HostRequestError::Transport("The audio host timed out and was terminated".into()))?;
        let response = receive(remaining)?;
        if let Some(update) = decode_render_progress(&response, id)? {
            progress(update)?;
        } else {
            return decode_response(response, id);
        }
    }
}

pub(crate) fn decode_render_progress(response: &Value, id: u64) -> Result<Option<NativeRenderProgress>, HostRequestError> {
    if response.get("event").is_none() { return Ok(None); }
    if response["id"].as_u64() != Some(id) || response["event"].as_str() != Some("render_progress")
        || response.get("ok").is_some() || response.get("result").is_some() {
        return Err(HostRequestError::Transport("The audio host returned an invalid progress event".into()));
    }
    let progress: NativeRenderProgress = serde_json::from_value(response["payload"].clone())
        .map_err(|_| HostRequestError::Transport("The audio host returned invalid render progress".into()))?;
    if progress.total == 0 || progress.completed > progress.total
        || !matches!(progress.phase.as_str(), "preparing" | "rendering" | "finalizing" | "completed") {
        return Err(HostRequestError::Transport("The audio host returned invalid render progress".into()));
    }
    Ok(Some(progress))
}

pub(crate) struct HostProcess {
    pub(crate) child: Child,
    pub(crate) control: Arc<ProcessControl>,
    pub(crate) input: Option<mpsc::SyncSender<(Vec<u8>, mpsc::SyncSender<Result<(), String>>)>>,
    pub(crate) responses: mpsc::Receiver<Result<Value, String>>,
    pub(crate) sequence: u64,
    pub(crate) pending: Option<dialogs::Pending>,
    pub(crate) completed_session_id: Option<String>,
    pub(crate) live_closed: bool,
    #[cfg(windows)]
    pub(crate) _job: ProcessJob,
}

impl HostProcess {
    pub(crate) fn spawn(args: &[&std::ffi::OsStr]) -> Result<Self, String> {
        let mut command = Command::new(std::env::current_exe().map_err(|error| error.to_string())?);
        command.args(args);
        Self::spawn_command(command)
    }

    pub(crate) fn spawn_command(mut command: Command) -> Result<Self, String> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = command
            .spawn()
            .map_err(|error| format!("Unable to start the audio host: {error}"))?;
        let control = Arc::new(ProcessControl {
            pid: child.id(),
            active: Mutex::new(true),
        });
        #[cfg(windows)]
        let job = match ProcessJob::attach(&child) {
            Ok(job) => job,
            Err(error) => {
                control.reap(&mut child);
                return Err(format!("Unable to isolate the audio host process: {error}"));
            }
        };
        let Some(mut child_input) = child.stdin.take() else {
            control.reap(&mut child);
            return Err("Audio host input pipe is unavailable".into());
        };
        let Some(output) = child.stdout.take() else {
            drop(child_input);
            control.reap(&mut child);
            return Err("Audio host output pipe is unavailable".into());
        };
        if let Some(stderr) = child.stderr.take() {
            let child_pid = child.id();
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stderr);
                loop {
                    let mut line = Vec::new();
                    match (&mut reader).take(4097).read_until(b'\n', &mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => {
                            // Persist only our bounded, structured AU lifecycle records. Other
                            // vendor output retains its original stderr destination.
                            if line.len() <= 4096 && line.starts_with(b"[audio.au]") {
                                crate::session_log::early("INFO", "audio.au.lifecycle", &format!("pid={child_pid} {}", String::from_utf8_lossy(&line).trim_end()));
                            }
                            let _ = std::io::stderr().write_all(&line);
                        }
                    }
                }
            });
        }
        let (input, writes) =
            mpsc::sync_channel::<(Vec<u8>, mpsc::SyncSender<Result<(), String>>)>(1);
        std::thread::spawn(move || {
            while let Ok((bytes, completed)) = writes.recv() {
                let result = child_input
                    .write_all(&bytes)
                    .and_then(|_| child_input.flush())
                    .map_err(|error| format!("Unable to send an audio host request: {error}"));
                let failed = result.is_err();
                let _ = completed.send(result);
                if failed {
                    break;
                }
            }
        });
        let (sender, responses) = mpsc::sync_channel(1);
        std::thread::spawn(move || {
            let mut reader = BufReader::new(output);
            loop {
                let mut line = Vec::new();
                let read = (&mut reader)
                    .take(MAX_RESPONSE_BYTES as u64 + 1)
                    .read_until(b'\n', &mut line);
                let result = match read {
                    Ok(0) => Err("The audio host exited unexpectedly".to_string()),
                    Ok(_) if line.len() > MAX_RESPONSE_BYTES => {
                        Err("The audio host response exceeded the size limit".into())
                    }
                    Ok(_) => serde_json::from_slice(&line)
                        .map_err(|_| "The audio host returned an invalid response".into()),
                    Err(error) => Err(format!("Unable to read the audio host response: {error}")),
                };
                let failed = result.is_err();
                if sender.send(result).is_err() || failed {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            control,
            input: Some(input),
            responses,
            sequence: 0,
            pending: None,
            completed_session_id: None,
            live_closed: false,
            #[cfg(windows)]
            _job: job,
        })
    }

    pub(crate) fn request(
        &mut self,
        request: Value,
        timeout: Duration,
    ) -> Result<Value, HostRequestError> {
        let (id, started) = self.send_request(request, timeout)?;
        let response = self.receive(timeout.saturating_sub(started.elapsed()))?;
        decode_response(response, id)
    }

    pub(crate) fn request_with_progress(
        &mut self,
        mut request: Value,
        timeout: Duration,
        mut progress: impl FnMut(NativeRenderProgress) -> Result<(), HostRequestError>,
    ) -> Result<Value, HostRequestError> {
        request.as_object_mut().ok_or_else(|| HostRequestError::Request("Audio host request must be an object".into()))?
            .insert("progress".into(), json!(true));
        let (id, started) = self.send_request(request, timeout)?;
        receive_render_result(id, started, timeout, |remaining| self.receive(remaining), &mut progress)
    }

    pub(crate) fn send_request(&mut self, mut request: Value, timeout: Duration) -> Result<(u64, std::time::Instant), HostRequestError> {
        self.sequence = self.sequence.checked_add(1).ok_or_else(|| {
            HostRequestError::Transport("Audio host request sequence exhausted".into())
        })?;
        let id = self.sequence;
        request
            .as_object_mut()
            .ok_or_else(|| {
                HostRequestError::Request("Audio host request must be an object".into())
            })?
            .insert("id".into(), json!(id));
        let bytes = encode_request(&request)?;
        let started = std::time::Instant::now();
        let (completed, written) = mpsc::sync_channel(1);
        self.input
            .as_ref()
            .ok_or_else(|| HostRequestError::Transport("The audio host input pipe closed".into()))?
            .send((bytes, completed))
            .map_err(|_| HostRequestError::Transport("The audio host input pipe closed".into()))?;
        written
            .recv_timeout(timeout)
            .map_err(|_| {
                HostRequestError::Transport(
                    "The audio host input timed out and was terminated".into(),
                )
            })?
            .map_err(HostRequestError::Transport)?;
        Ok((id, started))
    }

    pub(crate) fn request_live(&mut self, request: Value, timeout: Duration) -> Result<Value, HostRequestError> {
        self.request_live_with(request, timeout, &mut dialogs::owned_dialog)
    }

    pub(crate) fn request_live_with(&mut self, request: Value, timeout: Duration, detector: &mut impl FnMut(u32) -> Option<String>) -> Result<Value, HostRequestError> {
        self.finish_pending_with(detector)?;
        let operation = request.get("op").and_then(Value::as_str).unwrap_or("").to_owned();
        if self.live_closed {
            return if matches!(operation.as_str(), "unload" | "shutdown") { Ok(json!({"closed":true})) }
                else { Err(HostRequestError::Operation("The previous request closed the audio session. Reload the project before retrying".into())) };
        }
        let (id, started) = self.send_request(request, timeout)?;
        self.pending = Some(dialogs::Pending::new(id, operation, started, timeout));
        self.receive_live_with(detector)
    }

    pub(crate) fn finish_pending_with(&mut self, detector: &mut impl FnMut(u32) -> Option<String>) -> Result<(), HostRequestError> {
        if self.pending.is_some() { self.receive_live_with(detector)?; }
        Ok(())
    }

    pub(crate) fn receive_live_with(&mut self, detector: &mut impl FnMut(u32) -> Option<String>) -> Result<Value, HostRequestError> {
        loop {
            // Always consume a completed response before inspecting windows. A dialog may
            // outlive its SDK call, and a dead helper must never be retained on that evidence.
            let response = match self.responses.try_recv() {
                Ok(value) => Some(value),
                Err(mpsc::TryRecvError::Disconnected) => return Err(HostRequestError::Transport("The audio host response pipe closed".into())),
                Err(mpsc::TryRecvError::Empty) => None,
            };
            if let Some(response) = response { return self.complete_live(response); }
            if self.pending.as_ref().is_some_and(dialogs::Pending::is_suspended) {
                if let Some(title) = detector(self.control.pid) {
                    return Err(HostRequestError::DialogPending(dialogs::message(&title)));
                }
            }
            let remaining = self.pending.as_mut().expect("a live request is outstanding").remaining();
            if remaining.is_zero() {
                if let Some(title) = detector(self.control.pid) {
                    self.pending.as_mut().expect("a live request is outstanding").suspend();
                    return Err(HostRequestError::DialogPending(dialogs::message(&title)));
                }
                return Err(HostRequestError::Transport("The audio host timed out and was terminated".into()));
            }
            // Give normal fast RPCs a chance to complete even if a plugin also owns a modeless
            // dialog. A real modal blocks the UI dispatch and leaves this response outstanding.
            match self.responses.recv_timeout(remaining.min(Duration::from_millis(50))) {
                Ok(value) => return self.complete_live(value),
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => return Err(HostRequestError::Transport("The audio host response pipe closed".into())),
            }
            if let Some(title) = detector(self.control.pid) {
                self.pending.as_mut().expect("a live request is outstanding").suspend();
                return Err(HostRequestError::DialogPending(dialogs::message(&title)));
            }
        }
    }

    pub(crate) fn complete_live(&mut self, response: Result<Value, String>) -> Result<Value, HostRequestError> {
        let pending = self.pending.take().expect("a live request is outstanding");
        let result = decode_response(response.map_err(HostRequestError::Transport)?, pending.id);
        if let Ok(value) = &result {
            if matches!(pending.operation.as_str(), "shutdown" | "unload") { self.live_closed = true; }
            if let Some(id) = value.get("sessionId").and_then(Value::as_str).filter(|id| !id.is_empty()) {
                self.completed_session_id = Some(id.to_owned());
            }
        }
        result
    }

    pub(crate) fn receive(&self, timeout: Duration) -> Result<Value, HostRequestError> {
        self.responses
            .recv_timeout(timeout)
            .map_err(|error| {
                HostRequestError::Transport(match error {
                    mpsc::RecvTimeoutError::Timeout => {
                        "The audio host timed out and was terminated".into()
                    }
                    mpsc::RecvTimeoutError::Disconnected => {
                        "The audio host response pipe closed".into()
                    }
                })
            })?
            .map_err(HostRequestError::Transport)
    }
}

impl Drop for HostProcess {
    fn drop(&mut self) {
        // Release the writer's sender so an idle helper also receives EOF.
        self.input.take();
        self.control.reap(&mut self.child);
    }
}

pub(crate) fn encode_request(request: &Value) -> Result<Vec<u8>, HostRequestError> {
    let mut bytes = serde_json::to_vec(request)
        .map_err(|error| HostRequestError::Request(error.to_string()))?;
    validate_request_length(bytes.len())?;
    bytes.push(b'\n');
    Ok(bytes)
}

pub(crate) fn validate_request_length(bytes: usize) -> Result<(), HostRequestError> {
    // The native reader's bound includes the terminating newline.
    if bytes >= MAX_REQUEST_BYTES {
        Err(HostRequestError::Request(
            "Audio host request exceeded the size limit".into(),
        ))
    } else {
        Ok(())
    }
}

pub(crate) fn decode_response(response: Value, expected_id: u64) -> Result<Value, HostRequestError> {
    if response.get("id").and_then(Value::as_u64) != Some(expected_id) {
        return Err(HostRequestError::Transport(
            "The audio host returned a stale response".into(),
        ));
    }
    match response.get("ok").and_then(Value::as_bool) {
        Some(true) => response.get("result").cloned().ok_or_else(|| {
            HostRequestError::Transport("The audio host returned an invalid result".into())
        }),
        Some(false) => match response.get("error").and_then(Value::as_str) {
            Some(error) => Err(HostRequestError::Operation(error.into())),
            None => Err(HostRequestError::Transport(
                "The audio host returned an invalid error response".into(),
            )),
        },
        None => Err(HostRequestError::Transport(
            "The audio host returned an invalid response".into(),
        )),
    }
}
