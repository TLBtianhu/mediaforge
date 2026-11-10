use crate::error::{AppError, AppResult};
use crate::runtime_lease::RuntimeLease;
use crate::log_record::{legacy_stderr, LogRecord};
use crate::python::protocol::WorkerEnvelope;
use crate::python::task_journal::{self, JournalHandle};
use crate::session_log;
use crate::state::AppState;
use crate::storage;
use serde::Deserialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, State};

#[path = "audio_output.rs"]
mod audio_output;

pub(crate) fn recover_audio_output_temps(app: &AppHandle) {
    if let Ok(directory) = storage::temp_dir(app) {
        std::thread::spawn(move || audio_output::recover(&directory.join("audio-output-cleanup")));
    }
}

fn needs_audio_output_scope(command: &str, payload: &Value) -> bool {
    super::protocol::needs_audio_output_scope(
        command,
        payload.get("operation").and_then(Value::as_str),
    )
}

fn requires_background_result(command: &str) -> bool {
    super::protocol::requires_background_result(command)
}

fn operation_output_directory(payload: &Value, default: &Path) -> AppResult<PathBuf> {
    let raw = match payload.get("outputDir") {
        None | Some(Value::Null) => "",
        Some(Value::String(value)) => value.as_str(),
        _ => return Err(AppError::Worker("outputDir must be a string".into())),
    };
    let path = PathBuf::from(raw);
    let directory = if raw.is_empty() {
        default.to_path_buf()
    } else if path.is_absolute() {
        path
    } else {
        default.parent().unwrap_or(default).join(path)
    };
    Ok(std::path::absolute(directory)?)
}

pub(crate) fn kill_process_tree(pid: u32) {
    #[cfg(windows)]
    let _ = crate::process_command::hidden_command("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .status();
    #[cfg(not(windows))]
    let _ = crate::process_command::hidden_command("pkill").args(["-TERM", "-P", &pid.to_string()]).status();
}

const PYTHON_TERMINAL_LOG_PREFIX: &str = "__PYMSS_STUDIO_TERMINAL_LOG__";
static PROGRESS_LOG_TIMES: OnceLock<Mutex<HashMap<String, (Instant, u64)>>> = OnceLock::new();
const HOST_PYTHON_ENV_VARS: &[&str] = &[
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "__PYVENV_LAUNCHER__",
    "VIRTUAL_ENV",
    "PYENV_VERSION",
    "CONDA_PREFIX",
    "CONDA_PREFIX_1",
    "CONDA_DEFAULT_ENV",
    "CONDA_PROMPT_MODIFIER",
    "CONDA_SHLVL",
    "CONDA_EXE",
    "_CE_CONDA",
    "_CE_M",
];

static PAYLOAD_FILE_COUNTER: AtomicU64 = AtomicU64::new(0);

// Core packages are updated in the active environment. Hold a shared lease for every worker
// using that interpreter, and an exclusive lease for pip, including the process-start window.
static RUNTIME_ACCESS: Mutex<RuntimeAccess> = Mutex::new(RuntimeAccess {
    users: 0,
    updating: false,
});
// Application replacement also excludes bootstrap workers, which can otherwise keep
// importing modules from the old bundle while its directory is being replaced.
static APPLICATION_ACCESS: Mutex<RuntimeAccess> = Mutex::new(RuntimeAccess {
    users: 0,
    updating: false,
});

#[cfg(test)]
pub(crate) fn acquire_application_update() -> AppResult<impl Drop> {
    acquire_runtime_access(&APPLICATION_ACCESS, "update_runtime_core")
        .map_err(|error| match error {
            AppError::Worker(message) if message.starts_with("RUNTIME_BUSY:") => AppError::Worker(
                "Wait for running audio and runtime operations to finish before updating the application.".into(),
            ),
            error => error,
        })?
        .ok_or_else(|| AppError::Worker("Unable to reserve the application for updating".into()))
}

pub(crate) fn acquire_application_operation() -> AppResult<impl Drop + Send> {
    acquire_runtime_access(&APPLICATION_ACCESS, "application_operation")
        .map_err(|_| AppError::Worker("An application update is in progress. Wait for it to finish before starting another operation.".into()))?
        .ok_or_else(|| AppError::Worker("Unable to reserve the application for this operation".into()))
}

#[derive(Default)]
struct RuntimeAccess {
    users: usize,
    updating: bool,
}

struct RuntimeAccessGuard<'a> {
    access: &'a Mutex<RuntimeAccess>,
    updating: bool,
}

impl Drop for RuntimeAccessGuard<'_> {
    fn drop(&mut self) {
        if let Ok(mut access) = self.access.lock() {
            if self.updating {
                access.updating = false;
            } else {
                access.users -= 1;
            }
        }
    }
}

fn requires_bootstrap_python(command: &str) -> bool {
    super::protocol::is_bootstrap_command(command)
}

fn allows_bootstrap_without_runtime(command: &str) -> bool {
    super::protocol::bootstrap_allowed_without_runtime(command)
}

fn select_worker_python(
    command: &str,
    bootstrap_python: &str,
    active_runtime_python: Option<String>,
) -> AppResult<String> {
    if requires_bootstrap_python(command) {
        return Ok(bootstrap_python.to_string());
    }
    if let Some(python) = active_runtime_python {
        return Ok(python);
    }
    if command == "test_connection" || allows_bootstrap_without_runtime(command) {
        return Ok(bootstrap_python.to_string());
    }
    Err(AppError::Worker(
        "No active runtime environment is installed. Open Settings to install one before running this operation."
            .into(),
    ))
}

fn acquire_runtime_access<'a>(
    access: &'a Mutex<RuntimeAccess>,
    command: &str,
) -> AppResult<Option<RuntimeAccessGuard<'a>>> {
    let updating = super::protocol::command_updates_runtime(command);
    if !updating && requires_bootstrap_python(command) {
        return Ok(None);
    }
    let mut state = access
        .lock()
        .map_err(|_| AppError::Worker("runtime access lock poisoned".into()))?;
    if state.updating || (updating && state.users > 0) {
        return Err(AppError::Worker(
            "RUNTIME_BUSY: The runtime is in use. Wait for running operations to finish before updating core packages or starting another operation.".into(),
        ));
    }
    if updating {
        state.updating = true;
    } else {
        state.users += 1;
    }
    Ok(Some(RuntimeAccessGuard { access, updating }))
}

/// Cross-process companion to `RuntimeAccess`. Bootstrap-only commands do not
/// touch runtime-envs and therefore do not take a lease.
fn acquire_runtime_file_lease(app: &AppHandle, command: &str) -> AppResult<Option<RuntimeLease>> {
    let envs = match storage::runtime_envs_dir(app) {
        Ok(path) => path,
        Err(_) => return Ok(None),
    };
    if super::protocol::command_updates_runtime(command) {
        return RuntimeLease::exclusive(&envs).map(Some);
    }
    if requires_bootstrap_python(command) || allows_bootstrap_without_runtime(command) {
        return Ok(None);
    }
    RuntimeLease::shared(&envs).map(Some)
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ActiveRuntimeRecord {
    python_path: String,
    backend: Option<String>,
    /// 内容寻址的资产 id（`runtime-cuda-2026.10.2`）。并列目录用这个名字，
    /// 指针切到哪一份就运行哪一份。
    asset_id: Option<String>,
    source: Option<String>,
    overlay_path: Option<String>,
    #[serde(default)]
    debug_override: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ActiveRuntimeSelection {
    python: String,
    source: Option<String>,
    asset_id: Option<String>,
}

fn worker_path(app: &AppHandle) -> AppResult<PathBuf> {
    // Development builds must execute the workspace worker directly. Tauri's copied
    // resource directory is not refreshed while the dev process is running and can
    // otherwise leave Python modules out of sync with the frontend and Rust code.
    if storage::is_development_executable() {
        let path = dev_worker_path();
        if path.exists() {
            return Ok(path);
        }
    }

    if let Ok(resource) = app.path().resource_dir() {
        if let Some(path) = resource_worker_path(&resource) {
            return Ok(path);
        }
    }

    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let portable_worker = exe_dir.join("python").join("worker.py");
    if portable_worker.exists() {
        return Ok(portable_worker);
    }

    if storage::is_development_executable() {
        // Fallback: try cwd (for backward compatibility)
        let cwd = std::env::current_dir()?;
        Ok(cwd.join("python").join("worker.py"))
    } else {
        Ok(portable_worker)
    }
}

fn resource_worker_path(resource: &Path) -> Option<PathBuf> {
    storage::resource_roots(resource)
        .into_iter()
        .map(|root| root.join("python").join("worker.py"))
        .chain(std::iter::once(resource.join("worker.py")))
        .find(|path| path.exists())
}

fn dev_workspace_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is set at compile time to pymss-desktop/src-tauri.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn dev_worker_path() -> PathBuf {
    dev_workspace_root().join("python").join("worker.py")
}

fn embedded_python_path(app: &AppHandle) -> AppResult<Option<PathBuf>> {
    let resource = app.path().resource_dir().ok();
    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    Ok(embedded_python_from(
        resource.as_deref(),
        &exe_dir,
        cfg!(windows),
    ))
}

fn embedded_python_from(resource: Option<&Path>, exe_dir: &Path, windows: bool) -> Option<PathBuf> {
    let mut runtime_dirs: Vec<_> = resource
        .into_iter()
        .flat_map(storage::resource_roots)
        .map(|root| root.join("python-runtime"))
        .collect();
    runtime_dirs.push(exe_dir.join("python-runtime"));

    for runtime in runtime_dirs {
        let candidates = if windows {
            vec![
                runtime.join("python.exe"),
                runtime.join("Scripts").join("python.exe"),
            ]
        } else {
            vec![
                runtime.join("bin").join("python3"),
                runtime.join("bin").join("python"),
            ]
        };
        if let Some(path) = candidates.into_iter().find(|candidate| candidate.is_file()) {
            return Some(path);
        }
    }
    None
}

fn bootstrap_python_path(app: &AppHandle) -> AppResult<String> {
    if let Ok(value) = std::env::var("PYMSS_STUDIO_PYTHON") {
        let path = PathBuf::from(&value);
        if path.is_relative() {
            if let Ok(cwd) = std::env::current_dir() {
                let abs = cwd.join(&path);
                if abs.exists() {
                    return Ok(abs.to_string_lossy().to_string());
                }
            }
            let dev_root = dev_workspace_root();
            let abs_dev = dev_root.join(&path);
            if abs_dev.exists() {
                return Ok(abs_dev.to_string_lossy().to_string());
            }
        }
        return Ok(value);
    }
    if let Some(embedded) = embedded_python_path(app)? {
        #[cfg(target_os = "linux")]
        if !storage::is_development_executable()
            && std::env::var_os("APPIMAGE").is_some_and(|path| Path::new(&path).is_file())
        {
            let runtime = embedded.parent().and_then(Path::parent)
                .ok_or_else(|| AppError::Worker("Invalid bundled Python path".into()))?;
            let cached = super::bootstrap_cache::cached_python(
                runtime, &storage::data_root_dir(app)?, env!("CARGO_PKG_VERSION"),
            )?;
            return Ok(cached.to_string_lossy().into_owned());
        }
        return Ok(embedded.to_string_lossy().to_string());
    }
    if cfg!(windows) {
        Ok("python".to_string())
    } else {
        Ok("python3".to_string())
    }
}

fn runtime_python_command_path(python: &Path) -> Option<PathBuf> {
    if !python.is_file() {
        return None;
    }
    // Resolve directory aliases while keeping a POSIX venv's executable symlink.
    // Resolving the executable itself launches the base interpreter and loses pyvenv.cfg.
    Some(
        python
            .parent()?
            .canonicalize()
            .ok()?
            .join(python.file_name()?),
    )
}

/// Resolve `active-runtime.json`.
///
/// A missing file is a legitimate "no runtime activated". A file that exists but
/// cannot be read, parsed, or resolved is an error: silently falling back to
/// another interpreter would run work on a runtime the user did not activate.
fn resolve_active_runtime_record(file: &Path) -> AppResult<Option<ActiveRuntimeSelection>> {
    let content = match std::fs::read_to_string(file) {
        Ok(content) => content,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(AppError::Worker(format!(
                "active runtime pointer {} cannot be read: {error}",
                file.display()
            )))
        }
    };
    let record: ActiveRuntimeRecord = serde_json::from_str(&content).map_err(|error| {
        AppError::Worker(format!(
            "active runtime pointer {} is not a valid runtime record: {error}",
            file.display()
        ))
    })?;
    let python = PathBuf::from(&record.python_path);
    // Resolve the directory, but retain the interpreter filename. On Unix a venv
    // python is usually a symlink to the base interpreter; canonicalizing that
    // final component both loses the venv and breaks backend ownership checks.
    let resolved = if python.is_absolute() {
        python
    } else {
        file.parent()
            .ok_or_else(|| AppError::Worker(format!(
                "active runtime pointer {} has no parent directory",
                file.display()
            )))?
            .join(python)
    };
    let filename = resolved.file_name().ok_or_else(|| {
        AppError::Worker(format!(
            "active runtime pointer {} has an invalid python path: {}",
            file.display(),
            resolved.display()
        ))
    })?;
    let directory = resolved.parent().ok_or_else(|| {
        AppError::Worker(format!(
            "active runtime pointer {} has an invalid python path: {}",
            file.display(),
            resolved.display()
        ))
    })?;
    let executable = directory.canonicalize().map_err(|error| {
        AppError::Worker(format!(
            "active runtime pointer {} has an unresolvable python directory {}: {error}",
            file.display(),
            directory.display()
        ))
    })?.join(filename);
    if !executable.is_file() {
        return Err(AppError::Worker(format!(
            "active runtime pointer {} points to a missing interpreter: {}",
            file.display(),
            executable.display()
        )));
    }
    Ok(Some(ActiveRuntimeSelection {
        python: executable.to_string_lossy().to_string(),
        source: record.source,
        asset_id: record.asset_id.map(|value| value.trim().to_string()).filter(|value| !value.is_empty()),
    }))
}

fn bundled_runtime_envs_dir(app: &AppHandle) -> AppResult<Option<PathBuf>> {
    let user_runtime = storage::runtime_envs_dir(app)?.canonicalize().ok();
    Ok(distinct_bundled_runtime_env(
        user_runtime,
        storage::bundled_runtime_envs_dir(app)?,
    ))
}

fn distinct_bundled_runtime_env(
    user_runtime: Option<PathBuf>,
    bundled: Option<PathBuf>,
) -> Option<PathBuf> {
    bundled.filter(|path| path.canonicalize().ok() != user_runtime)
}

/// 候选的 `runtime-envs` 目录，按优先级排列。
///
/// 为什么不是"只有一处"：开发态的数据根与运行时根历史上不一致——
/// 数据在 `.dev-data`，而 exe 旁边会留下一个只有 `.lock` 的空壳
/// `target/debug/python-runtime/runtime-envs`。只看一处就会永远读到空壳。
fn runtime_envs_candidates(app: &AppHandle) -> Vec<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(envs) = storage::runtime_envs_dir(app) {
        candidates.push(envs);
    }
    if let Ok(root) = storage::data_root_dir(app) {
        candidates.push(root.join("runtime-envs"));
    }
    if storage::is_development_executable() {
        // 开发态兜底：项目根下的 `.dev-data/runtime-envs`
        if let Some(project) = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent() {
            candidates.push(project.join(".dev-data").join("runtime-envs"));
        }
    }
    if let Ok(Some(bundled)) = bundled_runtime_envs_dir(app) {
        candidates.push(bundled);
    }
    let mut seen: Vec<PathBuf> = Vec::new();
    let mut unique: Vec<PathBuf> = Vec::new();
    for candidate in candidates {
        let key = candidate.canonicalize().unwrap_or_else(|_| candidate.clone());
        if seen.iter().any(|existing| existing == &key) {
            continue;
        }
        seen.push(key);
        unique.push(candidate);
    }
    unique
}

fn python_under_envs_dir(envs_dir: &Path, python_path: &str) -> bool {
    let root = envs_dir.canonicalize().unwrap_or_else(|_| envs_dir.to_path_buf());
    PathBuf::from(python_path)
        .parent()
        .and_then(Path::parent)
        .and_then(|env| env.canonicalize().ok())
        .is_some_and(|env| env.starts_with(&root))
}

/// 从候选目录里挑出**第一个可用**的运行时记录。
///
/// 抽成纯函数是为了能测：这里的"空壳目录"曾经让开发态连续四轮报
/// "没有安装运行时"——单测比真机点四次便宜得多。
fn select_active_record(candidates: &[PathBuf], development: bool) -> AppResult<Option<ActiveRuntimeSelection>> {
    for envs_dir in candidates {
        let file = envs_dir.join("active-runtime.json");
        let Some(selection) = resolve_active_runtime_record(&file)? else {
            continue;
        };
        let managed = python_under_envs_dir(envs_dir, &selection.python);
        let debug_override = active_debug_override_path_allowed(&file, &selection.python, development);
        if (managed || debug_override) && active_path_backend_matches(&file, &selection.python) {
            return Ok(Some(selection));
        }
    }
    Ok(None)
}

fn active_runtime_selection(app: &AppHandle) -> AppResult<Option<ActiveRuntimeSelection>> {
    select_active_record(
        &runtime_envs_candidates(app),
        storage::is_development_executable(),
    )
}

fn runtime_asset_for_worker(active: Option<&ActiveRuntimeSelection>, desired: Option<String>) -> Option<String> {
    match active {
        Some(selection) => selection.asset_id.clone(),
        None => desired,
    }
}

#[cfg(test)]
mod runtime_record_tests {
    use super::*;
    use std::fs;

    fn scratch(tag: &str) -> PathBuf {
        let mut root = std::env::temp_dir();
        root.push(format!("mediaforge-runtime-test-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        root
    }

    /// 造一个可用的 runtime-envs：`<envs>/<backend>/Scripts/python.exe` + 记录
    fn make_envs(root: &Path, backend: &str) -> PathBuf {
        let envs = root.join("runtime-envs");
        let python = envs.join(backend).join("Scripts").join("python.exe");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, b"stub").unwrap();
        let record = serde_json::json!({
            "pythonPath": python.to_string_lossy(),
            "backend": backend,
        });
        fs::write(envs.join("active-runtime.json"), record.to_string()).unwrap();
        envs
    }

    #[test]
    fn skips_empty_shell_directories() {
        let root = scratch("shell");
        // 空壳：只有锁文件，没有 active-runtime.json（真实开发态的 target/debug 就是这样）
        let shell = root.join("python-runtime").join("runtime-envs");
        fs::create_dir_all(&shell).unwrap();
        fs::write(shell.join(".runtime-management.lock"), b"").unwrap();

        let real = make_envs(&root.join("data"), "cuda");
        let picked = select_active_record(&[shell, real.clone()], true).unwrap();
        assert!(picked.is_some(), "空壳目录应被跳过，改用后面那个有记录的");
        assert!(picked.unwrap().python.contains("python.exe"));
    }

    #[test]
    fn rejects_a_record_whose_backend_disagrees_with_its_directory() {
        let root = scratch("mismatch");
        let envs = make_envs(&root, "cpu");
        // 把记录里的 backend 改成 cuda，但目录名仍是 cpu → 必须拒绝
        let record = serde_json::json!({
            "pythonPath": envs.join("cpu").join("Scripts").join("python.exe").to_string_lossy(),
            "backend": "cuda",
        });
        fs::write(envs.join("active-runtime.json"), record.to_string()).unwrap();
        assert_eq!(select_active_record(&[envs], false).unwrap(), None);
    }

    #[test]
    fn accepts_an_asset_directory_when_the_record_names_the_same_asset() {
        let root = scratch("asset-dir");
        let envs = root.join("runtime-envs");
        let asset = envs.join("runtime-cuda-2026.10.2");
        let python = asset.join("Scripts").join("python.exe");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, b"stub").unwrap();
        let record = serde_json::json!({
            "pythonPath": python.to_string_lossy(),
            "backend": "cuda",
            "assetId": "runtime-cuda-2026.10.2",
        });
        fs::write(envs.join("active-runtime.json"), record.to_string()).unwrap();
        let selected = select_active_record(&[envs.clone()], false).unwrap().unwrap();
        assert_eq!(selected.asset_id.as_deref(), Some("runtime-cuda-2026.10.2"));

        // 指针说 A、目录却是 B → 必须拒绝
        let record = serde_json::json!({
            "pythonPath": python.to_string_lossy(),
            "backend": "cuda",
            "assetId": "runtime-cuda-2026.10.1",
        });
        fs::write(envs.join("active-runtime.json"), record.to_string()).unwrap();
        assert_eq!(select_active_record(&[envs], false).unwrap(), None);
    }

    #[test]
    fn worker_asset_identity_comes_from_the_active_record_not_the_desired_update() {
        let active = ActiveRuntimeSelection {
            python: "active-python".into(), source: None, asset_id: Some("runtime-a".into()),
        };
        assert_eq!(runtime_asset_for_worker(Some(&active), Some("runtime-b".into())).as_deref(), Some("runtime-a"));
        let without_asset = ActiveRuntimeSelection { asset_id: None, ..active };
        assert_eq!(runtime_asset_for_worker(Some(&without_asset), Some("runtime-b".into())), None);
        assert_eq!(runtime_asset_for_worker(None, Some("runtime-b".into())).as_deref(), Some("runtime-b"));
    }

    #[test]
    fn returns_none_without_candidates() {
        assert_eq!(select_active_record(&[], true).unwrap(), None);
    }
}

fn resolve_runtime_overlay_for_record(
    file: &Path,
    active_python: &str,
    allowed_roots: &[PathBuf],
) -> Option<PathBuf> {
    let content = std::fs::read_to_string(file).ok()?;
    let record: ActiveRuntimeRecord = serde_json::from_str(&content).ok()?;
    let record_python = resolve_active_runtime_record(file).ok().flatten()?.python;
    let expected = runtime_python_command_path(Path::new(active_python))?;
    let recorded = runtime_python_command_path(Path::new(&record_python))?;
    if expected != recorded {
        return None;
    }
    let raw_overlay = record.overlay_path?.trim().to_string();
    if raw_overlay.is_empty() {
        return None;
    }
    let overlay = PathBuf::from(raw_overlay);
    let overlay = if overlay.is_absolute() {
        overlay
    } else {
        file.parent()?.join(overlay)
    };
    let overlay = overlay.canonicalize().ok()?;
    if !overlay.is_dir() {
        return None;
    }
    allowed_roots
        .iter()
        .filter_map(|root| root.canonicalize().ok())
        .any(|root| overlay.starts_with(root.join(".overlays")))
        .then_some(overlay)
}

fn active_runtime_overlay_path(app: &AppHandle, active_python: &str) -> AppResult<Option<PathBuf>> {
    let user_runtime = storage::runtime_envs_dir(app)?;
    let mut records = vec![storage::active_runtime_file(app)?];
    let mut allowed_roots = vec![user_runtime];
    if let Some(bundled) = bundled_runtime_envs_dir(app)? {
        records.push(bundled.join("active-runtime.json"));
        allowed_roots.push(bundled);
    }
    Ok(records
        .into_iter()
        .find_map(|file| resolve_runtime_overlay_for_record(&file, active_python, &allowed_roots)))
}

#[cfg(test)]
fn is_bundled_runtime_python_path(file: &Path, python_path: &str) -> AppResult<bool> {
    let content = std::fs::read_to_string(file)?;
    let record: ActiveRuntimeRecord = serde_json::from_str(&content)?;
    let backend = record
        .backend
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if !matches!(backend.as_str(), "cpu" | "cuda" | "rocm" | "mlx") {
        return Ok(false);
    }
    let Some(envs_dir) = file.parent() else {
        return Ok(false);
    };
    let envs_dir = envs_dir.canonicalize()?;
    let runtime_root = envs_dir
        .parent()
        .ok_or_else(|| AppError::Worker("bundled runtime root is missing".into()))?;
    let Some(python) = runtime_python_command_path(Path::new(python_path)) else {
        return Ok(false);
    };
    let bootstrap_matches = [
        runtime_root.join("python.exe"),
        runtime_root.join("bin").join("python3"),
        runtime_root.join("bin").join("python"),
    ]
    .into_iter()
    .filter_map(|candidate| runtime_python_command_path(&candidate))
    .any(|candidate| candidate == python);
    if bootstrap_matches {
        return Ok(backend == "mlx");
    }
    let Some(env_dir) = python.parent().and_then(Path::parent) else {
        return Ok(false);
    };
    Ok(env_dir.starts_with(&envs_dir)
        && env_dir
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.eq_ignore_ascii_case(&backend)))
}

fn active_path_backend_matches(file: &Path, python_path: &str) -> bool {
    let Ok(content) = std::fs::read_to_string(file) else {
        return false;
    };
    let Ok(record) = serde_json::from_str::<ActiveRuntimeRecord>(&content) else {
        return false;
    };
    let Some(backend) = record.backend.filter(|value| !value.trim().is_empty()) else {
        return false;
    };
    if !matches!(
        backend.trim().to_ascii_lowercase().as_str(),
        "cpu" | "cuda" | "rocm" | "mlx"
    ) {
        return false;
    }
    let path = PathBuf::from(python_path);
    let Some(directory) = path
        .parent()
        .and_then(Path::parent)
        .and_then(|env| env.file_name())
        .and_then(|name| name.to_str())
    else {
        return false;
    };
    if directory.eq_ignore_ascii_case(backend.trim()) {
        return true;
    }
    // 并列运行时：目录名是资产 id，记录里也必须写同一个 id，避免"指针写着 A、
    // 目录却是 B"的错配。
    record
        .asset_id
        .as_deref()
        .map(|asset| asset.trim().eq_ignore_ascii_case(directory))
        .unwrap_or(false)
}

fn active_debug_override_path_allowed(
    file: &Path,
    python_path: &str,
    development_executable: bool,
) -> bool {
    if !development_executable {
        return false;
    }
    let Ok(content) = std::fs::read_to_string(file) else {
        return false;
    };
    let Ok(record) = serde_json::from_str::<ActiveRuntimeRecord>(&content) else {
        return false;
    };
    record.debug_override && active_path_backend_matches(file, python_path)
}


pub(crate) fn bundled_bin_dirs(app: &AppHandle) -> AppResult<Vec<PathBuf>> {
    let resource = app.path().resource_dir().ok();
    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    Ok(bundled_bin_candidates(resource.as_deref(), &exe_dir)
        .into_iter()
        .filter(|dir| dir.is_dir())
        .collect())
}

fn bundled_bin_candidates(resource: Option<&Path>, exe_dir: &Path) -> Vec<PathBuf> {
    let mut dirs: Vec<_> = resource
        .into_iter()
        .flat_map(storage::resource_roots)
        .map(|root| root.join("bin"))
        .collect();
    dirs.push(exe_dir.join("bin"));
    dirs
}

#[cfg(windows)]
fn rocm_native_tool_dir(runtime_envs_dir: &Path) -> Option<PathBuf> {
    let site_packages = runtime_envs_dir
        .join("rocm")
        .join("Lib")
        .join("site-packages");
    let entries = std::fs::read_dir(site_packages).ok()?;
    entries
        .flatten()
        .map(|entry| entry.path())
        .find_map(|package_dir| {
            let name = package_dir.file_name()?.to_str()?;
            let tool_dir = package_dir.join("lib").join("llvm").join("bin");
            (name.starts_with("_rocm_sdk_core") && tool_dir.join("offload-arch.exe").is_file())
                .then_some(tool_dir)
        })
}

#[cfg(windows)]
fn rocm_native_tool_dirs(app: &AppHandle) -> AppResult<Vec<PathBuf>> {
    let mut runtime_envs_dirs = vec![storage::runtime_envs_dir(app)?];
    runtime_envs_dirs.extend(bundled_runtime_envs_dir(app)?);
    Ok(rocm_native_tool_dirs_from(runtime_envs_dirs))
}

#[cfg(windows)]
fn rocm_native_tool_dirs_from(runtime_envs_dirs: Vec<PathBuf>) -> Vec<PathBuf> {
    let mut result = Vec::new();
    for runtime_envs in runtime_envs_dirs {
        let Some(tool_dir) = rocm_native_tool_dir(&runtime_envs) else {
            continue;
        };
        let sdk_bin = tool_dir
            .parent()
            .and_then(|path| path.parent())
            .and_then(|path| path.parent())
            .map(|path| path.join("bin"));
        result.push(tool_dir);
        if let Some(sdk_bin) = sdk_bin.filter(|dir| dir.is_dir()) {
            result.push(sdk_bin);
        }
    }
    result
}

#[cfg(not(windows))]
fn rocm_native_tool_dirs(_app: &AppHandle) -> AppResult<Vec<PathBuf>> {
    Ok(Vec::new())
}

#[cfg(target_os = "macos")]
fn bundled_openssl_env(app: &AppHandle) -> AppResult<Option<(PathBuf, PathBuf)>> {
    for bin_dir in bundled_bin_dirs(app)? {
        let openssl_dir = bin_dir.join("openssl");
        let openssl_conf = openssl_dir.join("openssl.cnf");
        let openssl_modules = openssl_dir.join("ossl-modules");
        if openssl_conf.is_file() && openssl_modules.is_dir() {
            return Ok(Some((openssl_conf, openssl_modules)));
        }
    }
    Ok(None)
}

fn path_separator() -> &'static str {
    if cfg!(windows) {
        ";"
    } else {
        ":"
    }
}

fn prepend_path(existing: Option<String>, dirs: Vec<PathBuf>) -> Option<String> {
    if dirs.is_empty() {
        return existing;
    }

    let mut parts: Vec<String> = dirs
        .into_iter()
        .map(|dir| dir.to_string_lossy().to_string())
        .collect();

    if let Some(value) = existing {
        if !value.trim().is_empty() {
            parts.push(value);
        }
    }

    Some(parts.join(path_separator()))
}

fn isolate_python_environment(cmd: &mut Command, cache_root: &Path) {
    for key in HOST_PYTHON_ENV_VARS {
        cmd.env_remove(key);
    }
    cmd.env("PYTHONNOUSERSITE", "1")
        .env("NUMBA_CACHE_DIR", cache_root.join("numba-cache"))
        .env("PIP_CACHE_DIR", cache_root.join("pip-cache"));
}

fn default_output_dir(app: &AppHandle) -> AppResult<PathBuf> {
    storage::outputs_dir(app)
}

fn make_payload_file(command: &str, task_id: Option<&str>, payload: Value) -> AppResult<PathBuf> {
    let mut path = std::env::temp_dir();
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    let sequence = PAYLOAD_FILE_COUNTER.fetch_add(1, Ordering::Relaxed);
    path.push(format!(
        "pymss-studio-payload-{}-{}-{}-{}-{}.json",
        command,
        task_id.unwrap_or("once"),
        std::process::id(),
        stamp,
        sequence
    ));
    let mut file = std::fs::File::create(&path)?;
    file.write_all(serde_json::to_string(&payload)?.as_bytes())?;
    Ok(path)
}

fn build_worker_command(
    app: &AppHandle,
    command: &str,
    payload_file: Option<&PathBuf>,
    task_id: Option<&str>,
    operation_id: &str,
) -> AppResult<Command> {
    let worker = worker_path(app)?;
    let bootstrap_python = bootstrap_python_path(app)?;
    // Runtime management must never run inside the environment it is managing: the bootstrap
    // interpreter is the only one guaranteed to exist while an environment is being built,
    // switched, or deleted.
    let active_runtime = if requires_bootstrap_python(command) {
        None
    } else {
        active_runtime_selection(app)?
    };
    let active_runtime_python = active_runtime.as_ref().map(|selection| selection.python.clone());
    let active_overlay = active_runtime_python
        .as_deref()
        .map(|python| active_runtime_overlay_path(app, python))
        .transpose()?
        .flatten();
    let python = select_worker_python(command, &bootstrap_python, active_runtime_python).map_err(|error| {
        // 这条错误信息要能自证：它找的是哪个目录、那里有没有 active-runtime.json，
        // 否则用户只能看到"没有运行时"，而不知道应用到底在哪儿找。
        let envs = storage::runtime_envs_dir(app)
            .map(|path| path.display().to_string())
            .unwrap_or_else(|_| "<unresolved>".into());
        let record = runtime_envs_candidates(app)
            .iter()
            .map(|dir| {
                let file = dir.join("active-runtime.json");
                format!("{} (exists={})", file.display(), file.is_file())
            })
            .collect::<Vec<_>>()
            .join(" | ");
        AppError::Worker(format!("{error} [runtime-envs={envs}; record={record}]"))
    })?;
    let python_for_log = python.clone();
    let worker_for_log = worker.clone();
    let mut cmd = crate::process_command::hidden_command(&python);
    #[cfg(unix)]
    if command == "manage_plugin" {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    isolate_python_environment(&mut cmd, &storage::temp_dir(app)?);
    cmd.arg(worker)
        .arg(command)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONUTF8", "1")
        .env("PYMSS_STUDIO_LOG_TRANSPORT", "protocol")
        .env(
            "PYMSS_STUDIO_DEBUG_LOG",
            if session_log::verbose_logging_enabled() {
                "1"
            } else {
                "0"
            },
        )
        .env("PYMSS_STUDIO_BOOTSTRAP_PYTHON", &bootstrap_python)
        .env(
            "PYMSS_STUDIO_DEFAULT_OUTPUT_DIR",
            default_output_dir(app)?.to_string_lossy().to_string(),
        );
    cmd.env_remove("MEDIAFORGE_RUNTIME_ASSET");
    let desired_asset = if active_runtime.is_none() {
        // Queries that intentionally run without an active runtime may describe the desired asset.
        crate::runtime_assets::desired_runtime(app).ok().map(|descriptor| descriptor.asset_id)
    } else {
        None
    };
    if let Some(asset_id) = runtime_asset_for_worker(active_runtime.as_ref(), desired_asset) {
        cmd.env("MEDIAFORGE_RUNTIME_ASSET", asset_id);
    }
    if let Some(overlay) = active_overlay {
        cmd.env("PYTHONPATH", overlay.to_string_lossy().to_string());
    }
    // The collector owns persistence and terminal output. Never inherit legacy file writers.
    cmd.env_remove("PYMSS_STUDIO_AUDIO_TEMP_TOKEN")
        .env_remove("PYMSS_STUDIO_AUDIO_TEMP_STATE")
        .env_remove("PYMSS_STUDIO_SESSION_LOG")
        .env_remove("PYMSS_STUDIO_PERSISTENT_LOG")
        .env_remove("PYMSS_STUDIO_TERMINAL_LOG");
    if let Ok(dir) = storage::runtime_envs_dir(app) {
        cmd.env(
            "MEDIAFORGE_RUNTIME_ENVS_DIR",
            dir.to_string_lossy().to_string(),
        );
    }
    if let Ok(file) = storage::active_runtime_file(app) {
        cmd.env(
            "MEDIAFORGE_ACTIVE_RUNTIME_FILE",
            file.to_string_lossy().to_string(),
        );
    }
    if storage::is_development_executable() {
        cmd.env("PYMSS_STUDIO_ALLOW_DEBUG_RUNTIME_OVERRIDE", "1");
    }
    if let Some(dir) = bundled_runtime_envs_dir(app)? {
        cmd.env(
            "PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR",
            dir.to_string_lossy().to_string(),
        );
    }
    apply_proxy_env(app, &mut cmd);
    let mut tool_dirs = rocm_native_tool_dirs(app)?;
    tool_dirs.extend(bundled_bin_dirs(app)?);
    if let Some(path) = prepend_path(std::env::var("PATH").ok(), tool_dirs) {
        cmd.env("PATH", path);
    }
    #[cfg(target_os = "macos")]
    if python == bootstrap_python {
        let embedded = embedded_python_path(app)?;
        if let Some(embedded) = embedded {
            if let Some(runtime_root) = embedded.parent().and_then(|path| path.parent()) {
                cmd.env("PYTHONHOME", runtime_root.to_string_lossy().to_string());
            }
        }
    }
    #[cfg(target_os = "macos")]
    if let Some((openssl_conf, openssl_modules)) = bundled_openssl_env(app)? {
        cmd.env(
            "PYMSS_STUDIO_OPENSSL_CONF",
            openssl_conf.to_string_lossy().to_string(),
        );
        cmd.env(
            "PYMSS_STUDIO_OPENSSL_MODULES",
            openssl_modules.to_string_lossy().to_string(),
        );
    }
    if let Ok(models_dir) = storage::models_dir(app) {
        cmd.env("MEDIAFORGE_MODEL_DIR", models_dir.to_string_lossy().to_string());
        cmd.env("PYMSS_MODEL_DIR", models_dir.to_string_lossy().to_string());
    }
    if let Ok(data_root) = storage::data_root_dir(app) {
        cmd.env("MEDIAFORGE_DATA_DIR", data_root.to_string_lossy().to_string());
    }
    // Keep the custom-model registry with the app's own state rather than in pymss's default
    // ~/.cache location, so a portable install carries its imported models with it.
    // pymss reads this at import time, which is why it has to be set before spawning.
    if let Ok(file) = storage::user_models_file(app) {
        cmd.env("PYMSS_USER_MODELS", file.to_string_lossy().to_string());
    }
    if let Ok(dir) = storage::data_root_dir(app).map(|root| root.join("debug")) {
        cmd.env("PYMSS_STUDIO_DEBUG_DIR", dir.to_string_lossy().to_string());
    }
    if let Some(path) = payload_file {
        cmd.arg("--payload").arg(path);
    }
    session_log::append(
        app,
        "DEBUG",
        "worker.command",
        vec![
            ("command", command.to_string()),
            ("taskId", task_id.unwrap_or_default().to_string()),
            ("operationId", operation_id.to_string()),
            ("python", python_for_log),
            ("worker", worker_for_log.to_string_lossy().to_string()),
            (
                "payloadFile",
                payload_file
                    .map(|path| path.to_string_lossy().to_string())
                    .unwrap_or_default(),
            ),
        ],
    );
    Ok(cmd)
}

fn normalize_proxy_url(url: &str) -> String {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    if trimmed.contains("://") {
        return trimmed.to_string();
    }
    format!("http://{}", trimmed)
}

fn apply_proxy_env(app: &AppHandle, cmd: &mut Command) {
    let Some(state) = app.try_state::<AppState>() else {
        return;
    };
    let Ok(proxy) = state.proxy_settings.lock() else {
        return;
    };
    let config = serde_json::json!({
        "mode": proxy.mode,
        "url": proxy.url,
        "bypass": proxy.bypass,
    });
    cmd.env("PYMSS_STUDIO_PROXY_CONFIG", config.to_string());
    match proxy.mode.as_str() {
        "none" => {
            cmd.env("PYMSS_STUDIO_PROXY_MODE", "none")
                .env("NO_PROXY", "*")
                .env("HTTP_PROXY", "")
                .env("HTTPS_PROXY", "")
                .env("http_proxy", "")
                .env("https_proxy", "");
        }
        "custom" => {
            let url = normalize_proxy_url(proxy.url.trim());
            if !url.is_empty() {
                cmd.env("HTTP_PROXY", &url)
                    .env("HTTPS_PROXY", &url)
                    .env("http_proxy", &url)
                    .env("https_proxy", &url)
                    .env("PYMSS_STUDIO_PROXY_MODE", "custom");
            }
            let bypass = proxy.bypass.trim();
            if !bypass.is_empty() {
                cmd.env("NO_PROXY", bypass).env("no_proxy", bypass);
            }
        }
        "system" => {
            cmd.env("PYMSS_STUDIO_PROXY_MODE", "system");
        }
        _ => {
            cmd.env("PYMSS_STUDIO_PROXY_MODE", "system");
        }
    }
}

fn emit_worker_stderr(app: &AppHandle, line: String, command: &str, operation_id: &str) {
    let mut record = legacy_stderr(&line);
    record.context.insert("command".into(), command.into());
    record.context.insert("operationId".into(), operation_id.into());
    let record = session_log::append_record(app, record);
    let _ = app.emit(
        "pymss://worker-event",
        serde_json::json!({
            "type": "worker_stderr",
            "payload": { "message": record.message, "level": record.level, "logRecord": record }
        }),
    );
}

fn forward_python_terminal_log(app: &AppHandle, line: &str) -> bool {
    let Some(line) = line.strip_prefix(PYTHON_TERMINAL_LOG_PREFIX) else {
        return false;
    };
    session_log::append_record(app, legacy_stderr(line));
    true
}

fn emit_batch_stderr(app: &AppHandle, task_ids: &[String], line: String, journal: &Option<JournalHandle>) {
    let mut record = legacy_stderr(&line);
    if task_ids.len() == 1 {
        record.task_id = task_ids.first().cloned();
    }
    if task_ids.len() > 1 {
        record
            .context
            .insert("taskIds".into(), serde_json::json!(task_ids));
    }
    let record = session_log::append_record(app, record);
    for task_id in task_ids {
        let envelope = WorkerEnvelope { event_type: "task.log".into(), task_id: Some(task_id.clone()), request_id: None,
            timestamp: None, payload: serde_json::json!({"logRecord": record}) };
        update_task_journal(app, journal, |journal| journal.observe(&envelope));
        let _ = app.emit(
            "pymss://worker-event",
            serde_json::json!({
                "type": "task.log", "taskId": task_id,
                "payload": { "level": record.level, "message": record.message, "logRecord": record }
            }),
        );
    }
}

fn emit_task_error_to_all(app: &AppHandle, task_ids: &[String], message: String) {
    let mut record = LogRecord::new(
        "studio",
        Some("error"),
        "studio.worker",
        "worker.failed",
        &message,
    );
    if task_ids.len() == 1 {
        record.task_id = task_ids.first().cloned();
    }
    if task_ids.len() > 1 {
        record
            .context
            .insert("taskIds".into(), serde_json::json!(task_ids));
    }
    let record = session_log::append_record(app, record);
    for task_id in task_ids {
        let _ = app.emit(
            "pymss://worker-event",
            serde_json::json!({
                "type": "error", "taskId": task_id,
                "payload": { "message": record.message, "logRecord": record }
            }),
        );
    }
}

fn broadcast_existing_task_error(
    app: &AppHandle,
    task_ids: &[String],
    message: String,
    record: Option<&Value>,
) {
    // A received error envelope/parse failure already has its collector record.
    for task_id in task_ids {
        let _ = app.emit(
            "pymss://worker-event",
            serde_json::json!({
            "type": "error", "taskId": task_id, "payload": { "message": message, "logRecord": record }
            }),
        );
    }
}
fn worker_error_message(envelope: &WorkerEnvelope) -> String {
    envelope
        .payload
        .get("message")
        .and_then(Value::as_str)
        .filter(|message| !message.trim().is_empty())
        .unwrap_or("Worker failed")
        .to_string()
}

fn is_log_envelope(envelope: &WorkerEnvelope) -> bool {
    matches!(
        envelope.event_type.as_str(),
        "worker_log"
            | "task.log"
            | "asset.log"
            | "optional_package_log"
            | "runtime_install_log"
            | "runtime_core_update_log"
    )
}

fn bind_operation_scope(record: &mut LogRecord, operation_id: Option<&str>, fallback_task_id: Option<&str>) {
    if let Some(id) = operation_id {
        record.context.insert("operationId".into(), id.into());
    }
    if record.task_id.is_none() && record.request_id.is_none()
        && record.context.get("taskScope").and_then(Value::as_str) != Some("operation") {
        record.task_id = fallback_task_id.map(str::to_string);
    }
}

fn bind_log_envelope_scope(record: &mut LogRecord, envelope: &WorkerEnvelope) {
    if record.task_id.is_none()
        && record.context.get("taskScope").and_then(Value::as_str) != Some("operation") {
        record.task_id = envelope.task_id.clone();
    }
    if record.request_id.is_none() {
        record.request_id = envelope.request_id.clone();
    }
}

pub(crate) fn log_worker_event(app: &AppHandle, command: &str, envelope: &mut WorkerEnvelope) {
    log_worker_event_with_scope(app, command, envelope, None, None);
}

fn log_worker_event_with_scope(
    app: &AppHandle,
    command: &str,
    envelope: &mut WorkerEnvelope,
    operation_id: Option<&str>,
    fallback_task_id: Option<&str>,
) {
    if envelope
        .payload
        .get("logRecord")
        .and_then(|record| record.get("schemaVersion"))
        .and_then(Value::as_u64)
        == Some(1)
    {
        return;
    }
    if envelope.event_type == "env_info" {
        session_log::record_torch_diagnostics(app, &envelope.payload);
    }
    if is_log_envelope(envelope) {
        let mut record = LogRecord::from_payload(&envelope.payload).unwrap_or_else(|| {
            LogRecord::new(
                "worker",
                envelope.payload.get("level").and_then(Value::as_str),
                "worker",
                "worker.log",
                envelope
                    .payload
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
            )
        });
        bind_log_envelope_scope(&mut record, envelope);
        record
            .context
            .entry("command".to_string())
            .or_insert_with(|| command.into());
        bind_operation_scope(&mut record, operation_id, fallback_task_id);
        let record = session_log::append_record(app, record);
        if let Some(payload) = envelope.payload.as_object_mut() {
            payload.insert("logRecord".into(), serde_json::json!(record));
        }
        return;
    }
    let execution = envelope
        .payload
        .get("event")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let level = if envelope.event_type == "error" || envelope.event_type.ends_with("_failed") {
        "ERROR"
    } else if envelope.event_type == "task.result"
        && envelope.payload.get("failed").and_then(Value::as_array).is_some_and(|items| !items.is_empty()) {
        "WARN"
    } else if envelope.event_type == "workflow_execution" && !execution.ends_with("progress") {
        // The terminal error event owns the exception; node lifecycle is context.
        "INFO"
    } else if is_noisy_worker_event(&envelope.event_type) {
        "DEBUG"
    } else if is_significant_worker_event(&envelope.event_type) {
        "INFO"
    } else {
        "DEBUG"
    };
    let mut record = record_worker_event(command, envelope, level);
    bind_operation_scope(&mut record, operation_id, fallback_task_id);
    if is_noisy_worker_event(&envelope.event_type) || execution.ends_with("progress") {
        if let Ok(mut samples) = PROGRESS_LOG_TIMES
            .get_or_init(|| Mutex::new(HashMap::new()))
            .lock()
        {
            let key = format!(
                "{command}:{}:{}",
                envelope.task_id.as_deref().or(fallback_task_id).or(operation_id).unwrap_or_default(),
                record.node_id.as_deref().unwrap_or_default()
            );
            let Some(skipped) = sample_progress_log(&mut samples, key, Instant::now()) else {
                return;
            };
            if skipped > 0 {
                record
                    .context
                    .insert("progressUpdatesSkipped".into(), skipped.into());
            }
        }
    }
    let record = session_log::append_record(app, record);
    if let Some(payload) = envelope.payload.as_object_mut() {
        payload.insert("logRecord".into(), serde_json::json!(record));
    }
}

fn sample_progress_log(
    samples: &mut HashMap<String, (Instant, u64)>,
    key: String,
    now: Instant,
) -> Option<u64> {
    if let Some((last, skipped)) = samples.get_mut(&key) {
        if now.saturating_duration_since(*last) < Duration::from_secs(1) {
            *skipped = skipped.saturating_add(1);
            return None;
        }
        let dropped = *skipped;
        *last = now;
        *skipped = 0;
        return Some(dropped);
    }
    // Keep diagnostic sampling bounded across many short-lived tasks.
    if samples.len() >= 256 {
        samples.clear();
    }
    samples.insert(key, (now, 0));
    Some(0)
}

fn record_worker_event(command: &str, envelope: &WorkerEnvelope, level: &str) -> LogRecord {
    let payload = &envelope.payload;
    let mut record = LogRecord::new(
        "worker",
        Some(level),
        "worker",
        &envelope.event_type,
        payload
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or(&envelope.event_type),
    );
    record.task_id = envelope.task_id.clone();
    record.request_id = envelope.request_id.clone();
    record.node_id = payload
        .get("nodeId")
        .and_then(Value::as_str)
        .map(str::to_string);
    record.job_id = payload
        .get("jobId")
        .and_then(Value::as_str)
        .map(str::to_string);
    record.detail = payload
        .get("detail")
        .and_then(Value::as_str)
        .map(str::to_string);
    record.context.insert("command".into(), command.into());
    for key in [
        "code", "stage", "phase", "logPath", "operation", "action", "event", "version",
        "nodeType", "nodeIndex", "nodeCount", "nodeProgress", "overallFraction", "errorType",
        "audioIndex", "audioCount", "progress", "done", "unit",
        "completed", "total", "succeeded", "backend", "model", "format", "outputFormat",
    ] {
        if let Some(value) = payload.get(key).filter(|value| !value.is_null()) {
            if value.is_string() || value.is_boolean() || value.is_number() {
                record.context.insert(key.into(), value.clone());
            }
        }
    }
    for (key, count_key) in [("failed", "failedCount"), ("skipped", "skippedCount"),
        ("warnings", "warningCount"), ("outputs", "outputCount"), ("items", "resultCount")] {
        if let Some(items) = payload.get(key).and_then(Value::as_array) {
            record.context.insert(count_key.into(), items.len().into());
        }
    }
    record
}

fn is_significant_worker_event(event_type: &str) -> bool {
    ["started", "finished", "done", "failed", "cancelled"]
        .iter()
        .any(|suffix| event_type.ends_with(suffix))
        || matches!(event_type, "task.result" | "editor_mix_exported")
}

fn worker_lifecycle_level(command: &str) -> &'static str {
    if matches!(
        command,
        "infer"
            | "separate"
            | "separate_batch"
            | "infer_workflow"
            | "infer_workflow_batch"
            | "audio_tools"
            | "export_editor_mix"
            | "install_runtime"
            | "update_runtime_core"
        ) {
        "INFO"
    } else {
        "DEBUG"
    }
}

fn is_noisy_worker_event(event_type: &str) -> bool {
    let lower = event_type.to_ascii_lowercase();
    lower.contains("progress") || lower.contains("chunk") || lower.contains("heartbeat")
}

fn log_worker_parse_error(
    app: &AppHandle,
    command: &str,
    task_id: Option<&str>,
    operation_id: &str,
    err: &serde_json::Error,
    line: &str,
) -> LogRecord {
    let mut record = LogRecord::new(
        "studio",
        Some("error"),
        "studio.worker",
        "worker.invalid_stdout",
        "Invalid worker event",
    );
    record.task_id = task_id.map(str::to_string);
    record.detail = Some(err.to_string());
    record.context.insert("command".into(), command.into());
    record.context.insert("operationId".into(), operation_id.into());
    record.context.insert("raw".into(), line.into());
    session_log::append_record(app, record)
}

fn is_background_terminal_event(command: &str, event_type: &str) -> bool {
    super::protocol::is_background_terminal_event(command, event_type)
}

fn is_supervisor_terminal_event(command: &str, envelope: &WorkerEnvelope) -> bool {
    if envelope.event_type == "error"
        && envelope.payload.get("recoverable").and_then(Value::as_bool) == Some(true)
    {
        return false;
    }
    is_background_terminal_event(command, &envelope.event_type)
}

fn mark_task_terminal(app: &AppHandle, task_id: &str) {
    if let Ok(mut terminal) = app.state::<AppState>().terminal_tasks.lock() {
        terminal.insert(task_id.to_string());
    }
}

pub(crate) fn editor_export_task_token(task_id: &str) -> String {
    let token = task_id
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .take(96)
        .collect::<String>();
    if token.is_empty() {
        "sync".to_string()
    } else {
        token
    }
}

pub(crate) fn cleanup_editor_export_temp_files(directory: &Path, task_id: &str) {
    let safe_task_id = editor_export_task_token(task_id);
    let prefix = format!(".pymss-export-{}-", &safe_task_id);
    let cancel_name = format!(".pymss-export-{safe_task_id}.cancel");
    let Ok(entries) = std::fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let matches_task = path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|name| {
                (name.starts_with(&prefix) && name.ends_with(".part")) || name == cancel_name
            });
        if matches_task {
            let _ = std::fs::remove_file(path);
        }
    }
}

fn read_lossy_lines<R: Read>(reader: R, mut on_line: impl FnMut(String)) {
    let mut reader = BufReader::new(reader);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) => break,
            Ok(_) => {
                while matches!(buf.last(), Some(b'\n' | b'\r')) {
                    buf.pop();
                }
                on_line(String::from_utf8_lossy(&buf).into_owned());
            }
            Err(_) => break,
        }
    }
}

fn emit_worker_stdout(app: &AppHandle, line: String) {
    let _ = app.emit(
        "pymss://worker-event",
        serde_json::json!({
            "type": "worker_stdout",
            "payload": { "message": line }
        }),
    );
}

fn worker_stdout_diagnostic(command: &str, operation_id: &str, line: &str) -> LogRecord {
    let mut record = LogRecord::new("third_party", None, "stdout", "process.stdout", line);
    record.context.insert("command".into(), command.into());
    record.context.insert("operationId".into(), operation_id.into());
    record
}

fn is_rocm_offload_arch_diagnostic(line: &str) -> bool {
    let message = line.trim();
    message.starts_with("Fatal error in launcher: Unable to create process using")
        || message.contains("offload-arch failed with return code")
        || message == "[stderr]"
        || message.starts_with("[rocm_sdk] offload-arch")
}

pub fn run_worker_once(app: &AppHandle, command: &str) -> AppResult<Value> {
    run_worker_with_payload(app, command, None)
}

pub fn run_worker_with_payload(
    app: &AppHandle,
    command: &str,
    payload: Option<Value>,
) -> AppResult<Value> {
    let operation_id = session_log::record_operation_request(app, command, payload.as_ref().unwrap_or(&Value::Null), None);
    let result = run_worker_with_payload_inner(app, command, payload, &operation_id);
    match &result {
        Ok(value) => session_log::record_environment_probe(command, value),
        Err(error) => session_log::record_operation_failure(app, command, &operation_id, &error.to_string()),
    }
    result
}

fn run_worker_with_payload_inner(
    app: &AppHandle,
    command: &str,
    payload: Option<Value>,
    operation_id: &str,
) -> AppResult<Value> {
    let mut reference_mutation = storage::model_reference_mutation(command);
    let _application_access = acquire_application_operation()?;
    let _runtime_access = acquire_runtime_access(&RUNTIME_ACCESS, command)?;
    let _runtime_file_lease = acquire_runtime_file_lease(app, command)?;
    let payload_file = match payload {
        Some(value) => Some(make_payload_file(command, None, value)?),
        None => None,
    };
    let mut cmd = match build_worker_command(app, command, payload_file.as_ref(), None, operation_id) {
        Ok(command) => command,
        Err(error) => {
            if let Some(path) = payload_file.as_ref() {
                let _ = std::fs::remove_file(path);
            }
            return Err(error);
        }
    };
    let started_at = std::time::Instant::now();
    session_log::append(
        app,
        "DEBUG",
        "worker.spawn",
        vec![("command", command.to_string())],
    );
    let mut child = match cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        Ok(child) => child,
        Err(error) => {
            if let Some(path) = payload_file.as_ref() {
                let _ = std::fs::remove_file(path);
            }
            return Err(error.into());
        }
    };

    if let Some(mutation) = reference_mutation.as_mut() { mutation.spawned(); }
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| AppError::Worker("missing worker stdout".into()))?;
    let stderr = child.stderr.take();
    let stderr_app = app.clone();
    let stderr_operation_id = operation_id.to_string();
    let stderr_command = command.to_string();
    let stderr_lines: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let stderr_lines_for_thread = Arc::clone(&stderr_lines);
    let stderr_handle = stderr.map(|stderr| {
        std::thread::spawn(move || {
            read_lossy_lines(stderr, |line| {
                if forward_python_terminal_log(&stderr_app, &line) {
                    return;
                }
                if let Ok(mut lines) = stderr_lines_for_thread.lock() {
                    lines.push(line.clone());
                    if lines.len() > 20 {
                        lines.remove(0);
                    }
                }
                emit_worker_stderr(&stderr_app, line, &stderr_command, &stderr_operation_id);
            });
        })
    });
    let mut last_payload = Value::Null;
    let mut worker_error: Option<AppError> = None;
    read_lossy_lines(stdout, |line| {
        if line.trim().is_empty() || worker_error.is_some() {
            return;
        }
        match serde_json::from_str::<WorkerEnvelope>(&line) {
            Ok(mut envelope) => {
                let is_log = is_log_envelope(&envelope);
                if !is_log {
                    last_payload = envelope.payload.clone();
                }
                log_worker_event_with_scope(app, command, &mut envelope, Some(operation_id), None);
                // Environment probes return through IPC or a correlated background event.
                // An untagged broadcast would bypass the caller's request ordering.
                if command != "env_info" || is_log {
                    let _ = app.emit("pymss://worker-event", &envelope);
                }
                if envelope.event_type == "error" {
                    worker_error = Some(AppError::Worker(
                        envelope
                            .payload
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("worker error")
                            .to_string(),
                    ));
                }
            }
            Err(err) => {
                if is_rocm_offload_arch_diagnostic(&line) {
                    session_log::append_record(app, worker_stdout_diagnostic(command, operation_id, &line));
                    emit_worker_stdout(app, line);
                } else {
                    log_worker_parse_error(app, command, None, operation_id, &err, &line);
                    worker_error = Some(AppError::Worker(format!(
                        "Invalid worker event: {err}; raw={line}"
                    )));
                }
            }
        }
    });
    let status = child.wait()?;
    if let Some(mutation) = reference_mutation.as_mut() { mutation.reaped(); }
    session_log::append(
        app,
        if status.success() {
            worker_lifecycle_level(command)
        } else {
            "INFO"
        },
        "worker.exit",
        vec![
            ("command", command.to_string()),
            ("status", status.to_string()),
            ("operationId", operation_id.to_string()),
            ("durationMs", started_at.elapsed().as_millis().to_string()),
        ],
    );
    if let Some(handle) = stderr_handle {
        let _ = handle.join();
    }
    if let Some(path) = payload_file.as_ref() {
        let _ = std::fs::remove_file(path);
    }
    if let Some(err) = worker_error {
        return Err(err);
    }
    if !status.success() {
        let detail = stderr_lines
            .lock()
            .ok()
            .map(|lines| lines.join("\n"))
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| status.to_string());
        return Err(AppError::Worker(format!("worker exited with {detail}")));
    }
    Ok(last_payload)
}

pub fn spawn_worker_background(
    app: AppHandle,
    state: State<'_, AppState>,
    command: &str,
    task_id: String,
    payload: Value,
) -> AppResult<()> {
    if super::execution_quota::applies(command)? {
        let root = storage::data_root_dir(&app)?;
        if matches!(command, "infer" | "infer_workflow") {
            if !payload.as_object().is_some_and(|map| map.len() == 1 && map.get("jobId").and_then(Value::as_str) == Some(&task_id)) {
                return Err(AppError::Worker("audio start requires only the persisted queued jobId".into()));
            }
        } else {
            task_journal::enqueue_manual(&root, command, &task_id, &payload)?;
        }
        let Some((key, created_at)) = task_journal::pending_identity(&root, command, &task_id)? else { return Ok(()); };
        register_pending_workers(&app, &state, vec![PendingWorker { root, command: command.into(), requested: key, created_at }])?;
        return Ok(());
    }
    let journal = None;
    let operation_id = session_log::record_operation_request(&app, command, &payload, Some(&task_id));
    let log_app = app.clone();
    let result = spawn_worker_background_inner(app, state, command, task_id, payload, operation_id.clone(), journal.clone());
    if let Err(error) = &result {
        update_task_journal(&log_app, &journal, |journal| journal.fail_all(&error.to_string()));
        session_log::record_operation_failure(&log_app, command, &operation_id, &error.to_string());
    }
    result
}

#[derive(Clone)]
pub(crate) struct PendingWorker {
    root: PathBuf,
    command: String,
    requested: String,
    created_at: f64,
}

fn select_pending_manual_workers(root: &Path, jobs: &[Value]) -> AppResult<Vec<PendingWorker>> {
    let mut candidates = Vec::new();
    for job in jobs {
        super::job_schema::validate(job)?;
        let command = job["command"].as_str().unwrap();
        if job["source"] == "manual" && job["status"] == "queued" && job["owner"].is_null()
            && job.get("audio").is_none() && !matches!(command, "infer" | "infer_workflow")
            && super::execution_quota::applies(command)?
        {
            candidates.push(PendingWorker { root: root.into(), command: command.into(),
                requested: job["jobId"].as_str().unwrap().into(), created_at: job["createdAt"].as_f64().unwrap() });
        }
    }
    Ok(candidates)
}

fn merge_pending_workers(pending: &mut HashMap<String, PendingWorker>, candidates: Vec<PendingWorker>) -> usize {
    let before = pending.len();
    for candidate in candidates { pending.entry(candidate.requested.clone()).or_insert(candidate); }
    pending.len() - before
}

fn register_pending_workers(app: &AppHandle, state: &AppState, candidates: Vec<PendingWorker>) -> AppResult<usize> {
    let mut pending = state.pending_jobs.lock().map_err(|_| AppError::Worker("pending task registry lock poisoned".into()))?;
    let added = merge_pending_workers(&mut pending, candidates);
    if !pending.is_empty() && !state.job_dispatcher_running.swap(true, Ordering::AcqRel) {
        let dispatcher_app = app.clone();
        if let Err(error) = std::thread::Builder::new().name("mediaforge-worker-dispatch".into()).spawn(move || dispatch_pending_workers(dispatcher_app)) {
            state.job_dispatcher_running.store(false, Ordering::Release);
            return Err(error.into());
        }
    }
    Ok(added)
}

/// Recover only generic manual waiters at application startup. Audio is handed
/// off after its frontend enqueue/cancel coordination; Agent jobs belong to Kernel.
fn load_pending_manual_workers(root: &Path) -> AppResult<Vec<PendingWorker>> {
    let _catalog = task_journal::catalog_lock(root)?;
    select_pending_manual_workers(root, &task_journal::read_all_unlocked(root)?)
}

pub(crate) fn restore_pending_manual_workers(app: &AppHandle) -> AppResult<usize> {
    let root = storage::data_root_dir(app)?;
    register_pending_workers(app, &app.state::<AppState>(), load_pending_manual_workers(&root)?)
}

struct DispatcherRunningGuard<'a> {
    running: &'a std::sync::atomic::AtomicBool,
    pending: &'a Mutex<HashMap<String, PendingWorker>>,
    armed: bool,
}

impl DispatcherRunningGuard<'_> {
    fn stopped_under_registry_lock(&mut self) {
        self.running.store(false, Ordering::Release);
        self.armed = false;
    }
}

impl Drop for DispatcherRunningGuard<'_> {
    fn drop(&mut self) {
        if self.armed {
            let _registry = self.pending.lock().unwrap_or_else(|error| error.into_inner());
            self.running.store(false, Ordering::Release);
        }
    }
}

fn remove_pending_after(
    pending: &Mutex<HashMap<String, PendingWorker>>,
    id: &str,
    persist: impl FnOnce() -> AppResult<()>,
) -> AppResult<()> {
    persist()?;
    pending.lock().unwrap_or_else(|error| error.into_inner()).remove(id);
    Ok(())
}

fn dispatch_pending_workers(app: AppHandle) {
    let dispatcher_state = app.state::<AppState>();
    let mut running_guard = DispatcherRunningGuard {
        running: &dispatcher_state.job_dispatcher_running, pending: &dispatcher_state.pending_jobs, armed: true,
    };
    let mut last_errors = HashMap::<String, String>::new();
    loop {
        let mut candidates = {
            let state = app.state::<AppState>();
            let mut pending = match state.pending_jobs.lock() {
                Ok(pending) => pending,
                Err(error) => error.into_inner(),
            };
            if pending.is_empty() {
                // Under the same registry lock as insertion: no lost wakeup.
                running_guard.stopped_under_registry_lock();
                return;
            }
            pending.values_mut().map(|candidate| candidate.clone()).collect::<Vec<_>>()
        };
        candidates.sort_by(|a, b| a.created_at.total_cmp(&b.created_at).then(a.requested.cmp(&b.requested)));
        for candidate in candidates {
            let attempt = task_journal::try_claim_pending(&candidate.root, &candidate.command, &candidate.requested);
            if matches!(&attempt, Ok(task_journal::ClaimAttempt::Waiting)) { break; }
            match attempt {
                Ok(task_journal::ClaimAttempt::Ready(journal, payload, task_id)) => {
                    let _ = remove_pending_after(&dispatcher_state.pending_jobs, &candidate.requested, || Ok(()));
                    last_errors.remove(&candidate.requested);
                    let operation_id = session_log::record_operation_request(&app, &candidate.command, &payload, Some(&task_id));
                    let result = spawn_worker_background_inner(app.clone(), app.state::<AppState>(), &candidate.command,
                        task_id, payload, operation_id.clone(), Some(journal.clone()));
                    if let Err(error) = result {
                        update_task_journal(&app, &Some(journal), |journal| journal.fail_all(&error.to_string()));
                        session_log::record_operation_failure(&app, &candidate.command, &operation_id, &error.to_string());
                    }
                }
                Ok(task_journal::ClaimAttempt::Finished) => {
                    let _ = remove_pending_after(&dispatcher_state.pending_jobs, &candidate.requested, || Ok(()));
                    last_errors.remove(&candidate.requested);
                }
                Ok(task_journal::ClaimAttempt::Waiting) => unreachable!(),
                Err(error) => {
                    let persisted = remove_pending_after(&dispatcher_state.pending_jobs, &candidate.requested, ||
                        task_journal::fail_unclaimed(&candidate.root, &candidate.command, &candidate.requested, &error.to_string()));
                    let message = match persisted {
                        Ok(()) => error.to_string(),
                        Err(persist_error) => format!("{error}; unable to persist admission failure, retrying: {persist_error}"),
                    };
                    if last_errors.get(&candidate.requested) != Some(&message) {
                        session_log::append(&app, "ERROR", "worker.admission_failed", vec![("taskId", candidate.requested.clone()), ("message", message.clone())]);
                        last_errors.insert(candidate.requested, message);
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(test)]
thread_local! { static THREAD_SPAWN_FAILURE: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) }; }

fn spawn_worker_thread<T: Send + 'static>(name: &str, run: impl FnOnce() -> T + Send + 'static) -> std::io::Result<std::thread::JoinHandle<T>> {
    #[cfg(test)]
    if THREAD_SPAWN_FAILURE.with(|failure| failure.borrow().as_deref() == Some(name)) {
        // Match Builder's failure semantics: the submitted closure is dropped
        // before spawn returns Err, including everything captured by it.
        drop(run);
        return Err(std::io::Error::other("injected worker thread creation failure"));
    }
    std::thread::Builder::new().name(name.into()).spawn(run)
}

fn update_task_journal(app: &AppHandle, journal: &Option<JournalHandle>, update: impl FnOnce(&mut task_journal::WorkerJournal) -> AppResult<()>) {
    let Some(journal) = journal else { return; };
    let result = journal.lock().map_err(|_| AppError::Worker("task journal lock poisoned".into())).and_then(|mut journal| update(&mut journal));
    if let Err(error) = result {
        session_log::append(app, "WARN", "worker.journal_failed", vec![("message", error.to_string())]);
    }
}

struct ManualCancelWatch {
    stop: Arc<std::sync::atomic::AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Drop for ManualCancelWatch {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Release);
        if let Some(thread) = self.thread.take() { let _ = thread.join(); }
    }
}

fn watch_manual_cancel(app: &AppHandle, child: &Arc<Mutex<Child>>, ids: &[String], root: PathBuf, enabled: bool) -> AppResult<ManualCancelWatch> {
    let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let thread = if enabled {
        let stopped = stop.clone(); let child = child.clone(); let ids = ids.to_vec(); let app = app.clone();
        Some(spawn_worker_thread("mediaforge-worker-cancel", move || {
            while !stopped.load(std::sync::atomic::Ordering::Acquire) {
                if ids.iter().any(|id| root.join("agent-jobs").join(format!("{id}.cancel")).is_file()) {
                    if let Ok(mut owned) = child.try_lock() {
                        if matches!(owned.try_wait(), Ok(None)) {
                            if let Ok(mut cancelled) = app.state::<AppState>().cancelled_tasks.lock() { cancelled.extend(ids.iter().cloned()); }
                            for id in &ids { let _ = task_journal::cancel(&root, id); }
                            kill_process_tree(owned.id()); let _ = owned.kill(); let _ = owned.wait();
                        }
                        for id in &ids { let _ = std::fs::remove_file(root.join("agent-jobs").join(format!("{id}.cancel"))); }
                        break;
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        })?)
    } else { None };
    Ok(ManualCancelWatch { stop, thread })
}

fn wait_reaped_child(child: &Arc<Mutex<Child>>, mut report: impl FnMut(&std::io::Error)) -> std::process::ExitStatus {
    let mut reported = false;
    loop {
        // Do not hold the child mutex in a blocking wait: cancellation must still
        // be able to terminate a worker that closed stdout before it exited.
        let result = match child.lock() {
            Ok(mut child) => child.try_wait(),
            Err(poisoned) => poisoned.into_inner().try_wait(),
        };
        match result {
            Ok(Some(status)) => return status,
            Ok(None) => {}
            Err(error) if !reported => { report(&error); reported = true; }
            Err(_) => {}
        }
        // A wait error is not proof of exit. Keep both owner and quota leases,
        // as well as the cancellation watcher, until the child is really reaped.
        std::thread::sleep(Duration::from_millis(25));
    }
}

struct WorkerStartResources {
    reference_mutation: Option<storage::ModelReferenceMutation>,
    audio_scope: Option<audio_output::AudioOutputScope>,
    runtime_access: Option<RuntimeAccessGuard<'static>>,
    runtime_file_lease: Option<RuntimeLease>,
    application_access: Option<Box<dyn Send>>,
    stderr: Option<std::thread::JoinHandle<()>>,
    cancel: Option<ManualCancelWatch>,
}

impl WorkerStartResources {
    fn finish(&mut self) {
        drop(self.cancel.take());
        if let Some(stderr) = self.stderr.take() { let _ = stderr.join(); }
        if let Some(mut scope) = self.audio_scope.take() { scope.finish(); }
        if let Some(mut mutation) = self.reference_mutation.take() { mutation.reaped(); }
        drop(self.runtime_access.take());
        drop(self.runtime_file_lease.take());
        drop(self.application_access.take());
    }
}

#[derive(Clone)]
struct WorkerStartCleanup {
    app: AppHandle,
    ids: Vec<String>,
    payload: PathBuf,
}

/// Both the spawning side and the submitted supervisor closure own a guard.
/// Builder drops its closure on failure before returning Err, so resources must
/// stay shared until whichever guard handles failure has killed and reaped Child.
struct WorkerStartGuard {
    child: Arc<Mutex<Child>>,
    resources: Arc<Mutex<WorkerStartResources>>,
    _journal: Option<JournalHandle>,
    cleanup: Option<WorkerStartCleanup>,
    reaped: bool,
    owns_cleanup: bool,
}

impl WorkerStartGuard {
    fn fork(&self) -> Self {
        Self { child: self.child.clone(), resources: self.resources.clone(), _journal: self._journal.clone(),
            cleanup: self.cleanup.clone(), reaped: false, owns_cleanup: true }
    }

    fn handoff(&mut self) { self.owns_cleanup = false; }

    fn finish_reaped(&mut self) {
        self.reaped = true;
        self.resources.lock().unwrap_or_else(|error| error.into_inner()).finish();
    }
}

impl Drop for WorkerStartGuard {
    fn drop(&mut self) {
        if !self.owns_cleanup { return; }
        if !self.reaped {
            {
                let mut child = self.child.lock().unwrap_or_else(|error| error.into_inner());
                if !matches!(child.try_wait(), Ok(Some(_))) {
                    kill_process_tree(child.id());
                    let _ = child.kill();
                }
            }
            wait_reaped_child(&self.child, |error| session_log::early("ERROR", "worker.startup_reap", &error.to_string()));
        }
        // These guards cannot leave scope before a real successful reap, even
        // when thread creation or the supervisor closure itself fails.
        self.resources.lock().unwrap_or_else(|error| error.into_inner()).finish();
        if let Some(cleanup) = &self.cleanup {
            let state = cleanup.app.state::<AppState>();
            let mut tasks = state.tasks.lock().unwrap_or_else(|error| error.into_inner());
            let owned = cleanup.ids.iter().filter(|id| tasks.get(*id).is_some_and(|child| Arc::ptr_eq(child, &self.child))).cloned().collect::<Vec<_>>();
            for id in &owned { tasks.remove(id); }
            drop(tasks);
            let mut terminal = state.terminal_tasks.lock().unwrap_or_else(|error| error.into_inner());
            for id in &owned { terminal.remove(id); }
            let _ = std::fs::remove_file(&cleanup.payload);
        }
    }
}

fn spawn_worker_background_inner(
    app: AppHandle,
    state: State<'_, AppState>,
    command: &str,
    task_id: String,
    mut payload: Value,
    operation_id: String,
    journal: Option<JournalHandle>,
) -> AppResult<()> {
    let mut reference_mutation = storage::model_reference_mutation(command);
    let mut application_access = Some(acquire_application_operation()?);
    let mut runtime_access = acquire_runtime_access(&RUNTIME_ACCESS, command)?;
    let runtime_file_lease = acquire_runtime_file_lease(&app, command)?;
    let registered_task_ids = task_journal::registered_ids(&task_id, &payload);
    {
        let tasks = state
            .tasks
            .lock()
            .map_err(|_| AppError::Worker("task registry lock poisoned".into()))?;
        if let Some(existing) = registered_task_ids
            .iter()
            .find(|id| tasks.contains_key(*id))
        {
            return Err(AppError::Worker(format!(
                "task already exists: {}",
                existing
            )));
        }
    }

    let command_name = command.to_string();
    if command == "run_operation" {
        let directory = operation_output_directory(&payload, &default_output_dir(&app)?)?;
        payload.as_object_mut()
            .ok_or_else(|| AppError::Worker("invalid operation request".into()))?
            .insert("outputDir".into(), Value::String(directory.to_string_lossy().into_owned()));
    }
    let mut audio_scope = if needs_audio_output_scope(command, &payload) {
        let raw = payload.get("outputDir").and_then(Value::as_str).unwrap_or("");
        let raw = if command == "run_operation" { raw } else { raw.trim() };
        if raw.is_empty() { None } else {
            let path = if raw == "~" { storage::home_dir(&app)? }
                else if raw.starts_with("~/") || raw.starts_with("~\\") { storage::home_dir(&app)?.join(&raw[2..]) }
                else { PathBuf::from(raw) };
            let directory = if path.is_absolute() { path } else { std::env::current_dir()?.join(path) };
            Some(audio_output::AudioOutputScope::create(&storage::temp_dir(&app)?.join("audio-output-cleanup"), directory)?)
        }
    } else { None };
    let editor_export_directory = (command == "export_editor_mix")
        .then(|| {
            payload
                .get("exportDir")
                .and_then(Value::as_str)
                .map(PathBuf::from)
        })
        .flatten();
    let payload_file = make_payload_file(command, Some(&task_id), payload)?;
    let mut cmd = match build_worker_command(&app, command, Some(&payload_file), Some(&task_id), &operation_id) {
        Ok(command) => command,
        Err(error) => {
            let _ = std::fs::remove_file(&payload_file);
            return Err(error);
        }
    };
    if let Some(scope) = &audio_scope { scope.configure(&mut cmd); }
    let started_at = std::time::Instant::now();
    session_log::append(
        &app,
        "DEBUG",
        "worker.spawn",
        vec![
            ("command", command.to_string()),
            ("taskId", task_id.clone()),
        ],
    );
    // Check the registry before spawning so a lock failure cannot strand a live
    // child after its owner and quota guards have already been released.
    let mut task_registry = state.tasks.lock()
        .map_err(|_| AppError::Worker("task registry lock poisoned".into()))?;
    for id in &registered_task_ids {
        if task_registry.contains_key(id) { return Err(AppError::Worker(format!("task already running: {id}"))); }
    }
    let child: Child = match cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn() {
        Ok(child) => child,
        Err(error) => {
            let _ = std::fs::remove_file(&payload_file);
            return Err(error.into());
        }
    };
    if let Some(mutation) = reference_mutation.as_mut() { mutation.spawned(); }
    let shared_child = Arc::new(Mutex::new(child));
    for registered_task_id in &registered_task_ids {
        task_registry.insert(registered_task_id.clone(), shared_child.clone());
    }
    drop(task_registry);
    let mut startup_guard = WorkerStartGuard {
        child: shared_child.clone(), _journal: journal.clone(), reaped: false, owns_cleanup: true,
        resources: Arc::new(Mutex::new(WorkerStartResources {
            reference_mutation,
            audio_scope: audio_scope.take(), runtime_access: runtime_access.take(), runtime_file_lease,
            application_access: application_access.take().map(|guard| Box::new(guard) as Box<dyn Send>), stderr: None, cancel: None,
        })),
        cleanup: Some(WorkerStartCleanup { app: app.clone(), ids: registered_task_ids.clone(), payload: payload_file.clone() }),
    };
    let worker_pid = shared_child.lock().unwrap_or_else(|error| error.into_inner()).id();
    {
        let mut resources = startup_guard.resources.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(scope) = resources.audio_scope.as_mut() { scope.attach(worker_pid)?; }
    }
    update_task_journal(&app, &journal, |journal| journal.running(worker_pid));
    let (stdout, stderr) = {
        let mut child = shared_child.lock().unwrap_or_else(|error| error.into_inner());
        (child.stdout.take(), child.stderr.take())
    };
    let stdout = stdout.ok_or_else(|| AppError::Worker("missing worker stdout".into()))?;
    let stderr_app = app.clone();
    let stderr_task_ids = registered_task_ids.clone();
    let stderr_journal = journal.clone();
    let stderr_handle = stderr.map(|stderr| {
        spawn_worker_thread("mediaforge-worker-stderr", move || {
            read_lossy_lines(stderr, |line| {
                if forward_python_terminal_log(&stderr_app, &line) {
                    return;
                }
                emit_batch_stderr(&stderr_app, &stderr_task_ids, line, &stderr_journal);
            });
        })
    }).transpose()?;
    startup_guard.resources.lock().unwrap_or_else(|error| error.into_inner()).stderr = stderr_handle;
    let cancel_root = journal.as_ref().and_then(|handle| handle.lock().ok().map(|journal| journal.data_root().to_path_buf()));
    let cancel_enabled = cancel_root.is_some();
    let cancel_watch = watch_manual_cancel(&app, &shared_child, &registered_task_ids, cancel_root.unwrap_or_default(), cancel_enabled)?;
    startup_guard.resources.lock().unwrap_or_else(|error| error.into_inner()).cancel = Some(cancel_watch);
    let mut supervisor_guard = startup_guard.fork();
    spawn_worker_thread("mediaforge-worker-supervisor", move || {
        let mut terminal_task_ids: HashSet<String> = HashSet::new();
        let mut core_terminal_events = Vec::new();
        let mut core_terminal_error = None;
        read_lossy_lines(stdout, |line| {
            if line.trim().is_empty() {
                return;
            }
            match serde_json::from_str::<WorkerEnvelope>(&line) {
                Ok(mut envelope) => {
                    update_task_journal(&app, &journal, |journal| journal.observe(&envelope));
                    if envelope.event_type == "error" {
                        log_worker_event_with_scope(&app, &command_name, &mut envelope, Some(&operation_id), Some(&task_id));
                    }
                    if is_supervisor_terminal_event(&command_name, &envelope) {
                        if let Some(envelope_task_id) = envelope.task_id.as_deref() {
                            if registered_task_ids.iter().any(|id| id == envelope_task_id) {
                                terminal_task_ids.insert(envelope_task_id.to_string());
                                mark_task_terminal(&app, envelope_task_id);
                            }
                        } else if envelope.event_type == "error" {
                            if let Some(request_id) = envelope.request_id.as_deref().filter(|id| {
                                registered_task_ids.iter().any(|task_id| task_id == id)
                            }) {
                                terminal_task_ids.insert(request_id.to_string());
                                mark_task_terminal(&app, request_id);
                            } else {
                                let message = worker_error_message(&envelope);
                                if matches!(command_name.as_str(), "update_runtime_core" | "manage_plugin") {
                                    core_terminal_error =
                                        Some((message, envelope.payload.get("logRecord").cloned()));
                                } else {
                                    broadcast_existing_task_error(
                                        &app,
                                        &registered_task_ids,
                                        message,
                                        envelope.payload.get("logRecord"),
                                    );
                                }
                                terminal_task_ids.extend(registered_task_ids.iter().cloned());
                                for registered_task_id in &registered_task_ids {
                                    mark_task_terminal(&app, registered_task_id);
                                }
                            }
                        } else {
                            terminal_task_ids.insert(task_id.clone());
                            mark_task_terminal(&app, &task_id);
                        }
                    }
                    if matches!(command_name.as_str(), "update_runtime_core" | "manage_plugin")
                        && is_supervisor_terminal_event(&command_name, &envelope)
                    {
                        core_terminal_events.push(envelope);
                        return;
                    }
                    log_worker_event_with_scope(&app, &command_name, &mut envelope, Some(&operation_id), Some(&task_id));
                    let _ = app.emit("pymss://worker-event", &envelope);
                }
                Err(err) => {
                    if is_rocm_offload_arch_diagnostic(&line) {
                        emit_batch_stderr(&app, &registered_task_ids, line.clone(), &journal);
                    } else {
                        update_task_journal(&app, &journal, |journal| journal.fail_all(&format!("Invalid worker event: {err}")));
                        let record = log_worker_parse_error(
                            &app,
                            &command_name,
                            Some(&task_id),
                            &operation_id,
                            &err,
                            &line,
                        );
                        let record = serde_json::to_value(record).ok();
                        broadcast_existing_task_error(
                            &app,
                            &registered_task_ids,
                            format!("Invalid worker event: {err}"),
                            record.as_ref(),
                        );
                        terminal_task_ids.extend(registered_task_ids.iter().cloned());
                    }
                }
            }
        });
        let exit_status = Some(wait_reaped_child(&shared_child, |error| {
            session_log::append(&app, "ERROR", "worker.reap_pending", vec![("taskId", task_id.clone()), ("message", error.to_string())]);
        }));
        if let Some(directory) = editor_export_directory.as_deref() {
            cleanup_editor_export_temp_files(directory, &task_id);
        }
        supervisor_guard.finish_reaped();
        // Finish Python cleanup before the terminal event starts the UI's environment refresh.
        if let Some((message, record)) = core_terminal_error {
            broadcast_existing_task_error(&app, &registered_task_ids, message, record.as_ref());
        }
        for mut envelope in core_terminal_events {
            log_worker_event_with_scope(&app, &command_name, &mut envelope, Some(&operation_id), Some(&task_id));
            let _ = app.emit("pymss://worker-event", &envelope);
        }
        let mut was_cancelled = false;
        if let Ok(mut cancelled) = app.state::<AppState>().cancelled_tasks.lock() {
            for registered_task_id in &registered_task_ids {
                if cancelled.remove(registered_task_id) {
                    was_cancelled = true;
                    terminal_task_ids.insert(registered_task_id.clone());
                }
            }
        }
        update_task_journal(&app, &journal, |journal| journal.exited(
            exit_status.as_ref().is_some_and(|status| status.success()), was_cancelled,
            &exit_status.as_ref().map(|status| format!("worker exited with {status}")).unwrap_or_else(|| "worker exited unexpectedly".into()),
        ));
        session_log::append(
            &app,
            match exit_status.as_ref() {
                _ if was_cancelled => "INFO",
                Some(status) if status.success() => worker_lifecycle_level(&command_name),
                _ => "INFO",
            },
            "worker.exit",
            vec![
                ("command", command_name.clone()),
                ("operationId", operation_id.clone()),
                ("taskId", task_id.clone()),
                ("cancelled", was_cancelled.to_string()),
                (
                    "status",
                    exit_status
                        .as_ref()
                        .map(|status| status.to_string())
                        .unwrap_or_else(|| "missing".to_string()),
                ),
                ("durationMs", started_at.elapsed().as_millis().to_string()),
            ],
        );
        if let Ok(mut cancelled) = app.state::<AppState>().cancelled_tasks.lock() {
            for registered_task_id in &registered_task_ids {
                if cancelled.remove(registered_task_id) {
                    terminal_task_ids.insert(registered_task_id.clone());
                    if command_name == "manage_plugin" {
                        let _ = app.emit("pymss://worker-event", serde_json::json!({
                            "type": "task.cancelled", "taskId": registered_task_id,
                            "payload": { "message": "Cancelled" }
                        }));
                    }
                }
            }
        }
        let missing_terminal_task_ids = registered_task_ids
            .iter()
            .filter(|task_id| !terminal_task_ids.contains(*task_id))
            .cloned()
            .collect::<Vec<_>>();
        if !missing_terminal_task_ids.is_empty() {
            match exit_status {
                Some(status) if !status.success() => {
                    emit_task_error_to_all(
                        &app,
                        &missing_terminal_task_ids,
                        format!("worker exited with {status}"),
                    );
                }
                None => {
                    emit_task_error_to_all(
                        &app,
                        &missing_terminal_task_ids,
                        "worker exited unexpectedly".to_string(),
                    );
                }
                Some(status)
                    if status.success()
                        && requires_background_result(&command_name) =>
                {
                    emit_task_error_to_all(
                        &app,
                        &missing_terminal_task_ids,
                        "worker exited without a result".to_string(),
                    );
                }
                _ => {}
            }
        }
    })?;
    startup_guard.handoff();

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::is_background_terminal_event;
    use serde_json::json;
    use std::fs;
    use std::path::Path;

    #[test]
    fn worker_restart_recovers_only_queued_generic_manual_compute_and_merges_idempotently() {
        let root = tempfile::tempdir().unwrap();
        let fixture: serde_json::Value = serde_json::from_str(include_str!("../../../tests/fixtures/jobs/current-contract.json")).unwrap();
        for row in fixture["records"].as_array().unwrap() {
            crate::storage::write_json_file(&root.path().join("agent-jobs").join(format!("{}.json", row["jobId"].as_str().unwrap())), row).unwrap();
        }
        let compute = ["audio_tools", "run_operation", "media_tool", "export_editor_mix"];
        for command in compute.into_iter().chain(["model_download", "health", "install_runtime", "infer", "infer_workflow"]) {
            super::task_journal::enqueue_manual(root.path(), command, command, &json!({"operation":"fixture"})).unwrap();
        }
        let base = super::task_journal::read_job(&root.path().join("agent-jobs/run_operation.json")).unwrap().unwrap();
        for (id, status, source, owner) in [
            ("external-agent", "queued", "agent", serde_json::Value::Null),
            ("running", "running", "manual", json!({"kind":"tauri-worker","pid":12345})),
            ("owned-queued", "queued", "manual", json!({"kind":"tauri-worker","pid":12345})),
            ("completed", "completed", "manual", serde_json::Value::Null),
            ("failed", "failed", "manual", serde_json::Value::Null),
            ("cancelled", "cancelled", "manual", serde_json::Value::Null),
            ("interrupted", "interrupted", "manual", serde_json::Value::Null),
        ] {
            let mut row = base.clone();
            row["jobId"] = json!(id); row["cancelTaskId"] = json!(id); row["status"] = json!(status); row["source"] = json!(source); row["owner"] = owner;
            if status == "running" { row["startedAt"] = json!(1.0); }
            if ["completed", "failed", "cancelled", "interrupted"].contains(&status) { row["finishedAt"] = json!(2.0); }
            crate::storage::write_json_file(&root.path().join("agent-jobs").join(format!("{id}.json")), &row).unwrap();
        }
        let bytes = || fs::read_dir(root.path().join("agent-jobs")).unwrap().map(|entry| entry.unwrap().path())
            .filter(|path| path.extension().is_some_and(|extension| extension == "json"))
            .map(|path| (path.clone(), fs::read(path).unwrap())).collect::<std::collections::BTreeMap<_, _>>();
        let before = bytes();
        let candidates = super::load_pending_manual_workers(root.path()).unwrap();
        let mut ids = candidates.iter().map(|candidate| candidate.requested.as_str()).collect::<Vec<_>>(); ids.sort();
        assert_eq!(ids, ["audio_tools", "export_editor_mix", "media_tool", "run_operation"]);
        assert!(candidates.iter().all(|candidate| candidate.root == root.path() && candidate.created_at > 0.0));
        let mut pending = std::collections::HashMap::new();
        assert_eq!(super::merge_pending_workers(&mut pending, candidates), 4);
        assert_eq!(super::merge_pending_workers(&mut pending, super::load_pending_manual_workers(root.path()).unwrap()), 0);
        assert_eq!(pending.len(), 4);
        assert_eq!(bytes(), before, "restoring waiters must not claim or rewrite queued records");
    }

    #[test]
    fn worker_restart_rejects_invalid_catalog_without_restoring_a_partial_default_queue() {
        let root = tempfile::tempdir().unwrap();
        super::task_journal::enqueue_manual(root.path(), "run_operation", "valid", &json!({"operation":"fixture"})).unwrap();
        let valid_path = root.path().join("agent-jobs/valid.json");
        let before = fs::read(&valid_path).unwrap();
        let bad = root.path().join("agent-jobs/bad.json");
        fs::write(&bad, b"{\"schemaVersion\":1}").unwrap();
        assert!(super::load_pending_manual_workers(root.path()).is_err());
        assert_eq!(fs::read(valid_path).unwrap(), before);
        assert_eq!(fs::read(bad).unwrap(), b"{\"schemaVersion\":1}");
    }

    struct StartupReleaseProbe {
        child: std::sync::Arc<std::sync::Mutex<std::process::Child>>,
        root: std::path::PathBuf,
        reaped: std::sync::Arc<std::sync::atomic::AtomicBool>,
        slot_held: std::sync::Arc<std::sync::atomic::AtomicBool>,
    }

    impl Drop for StartupReleaseProbe {
        fn drop(&mut self) {
            use std::sync::atomic::Ordering;
            self.reaped.store(matches!(self.child.lock().unwrap().try_wait(), Ok(Some(_))), Ordering::SeqCst);
            self.slot_held.store(matches!(super::task_journal::try_claim_pending(&self.root, "run_operation", "next"),
                Ok(super::task_journal::ClaimAttempt::Waiting)), Ordering::SeqCst);
        }
    }

    fn owned_startup_fixture(delay: f64) -> (tempfile::TempDir, super::WorkerStartGuard,
        std::sync::Arc<std::sync::atomic::AtomicBool>, std::sync::Arc<std::sync::atomic::AtomicBool>) {
        use std::io::{BufRead, BufReader};
        use std::sync::{Arc, Mutex};
        let root = tempfile::tempdir().unwrap();
        for id in ["owned", "next"] {
            super::task_journal::enqueue_manual(root.path(), "run_operation", id, &json!({"operation":"fixture"})).unwrap();
        }
        let super::task_journal::ClaimAttempt::Ready(journal, _, _) =
            super::task_journal::try_claim_pending(root.path(), "run_operation", "owned").unwrap() else { panic!("fixture not admitted") };
        let project = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let python = std::env::var_os("MEDIAFORGE_RUNTIME_PYTHON").map(std::path::PathBuf::from)
            .unwrap_or_else(|| project.join(if cfg!(windows) { "python-runtime/python.exe" } else { "python-runtime/bin/python3" }));
        assert!(python.is_absolute() && python.is_file(), "startup fixtures require explicit Python 3.12: {}", python.display());
        let script = format!("import sys,time; assert sys.version_info[:2]==(3,12), 'Python 3.12 required'; print('ready',flush=True); time.sleep({delay})");
        let mut command = crate::process_command::hidden_command(python);
        command.args(["-I", "-B", "-c", &script]).current_dir(root.path())
            .env("HOME", root.path()).env("USERPROFILE", root.path()).env("APPDATA", root.path())
            .env("LOCALAPPDATA", root.path()).env("TEMP", root.path()).env("TMP", root.path())
            .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null());
        let child = Arc::new(Mutex::new(command.spawn().unwrap()));
        let reaped = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let slot_held = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut scope = super::audio_output::AudioOutputScope::create(&root.path().join("receipts"), root.path().join("output")).unwrap();
        scope.attach(child.lock().unwrap().id()).unwrap();
        let mut reference_mutation = crate::storage::model_reference_mutation("import_custom_model").unwrap();
        reference_mutation.spawned();
        let guard = super::WorkerStartGuard { child: child.clone(), _journal: Some(journal), cleanup: None, reaped: false, owns_cleanup: true,
            resources: Arc::new(Mutex::new(super::WorkerStartResources { audio_scope: Some(scope), runtime_access: None, runtime_file_lease: None, stderr: None, cancel: None,
                reference_mutation: Some(reference_mutation),
                application_access: Some(Box::new(StartupReleaseProbe { child: child.clone(), root: root.path().into(), reaped: reaped.clone(), slot_held: slot_held.clone() })) })),
        };
        let stdout = child.lock().unwrap().stdout.take().unwrap();
        let (sender, receiver) = std::sync::mpsc::channel();
        let reader = super::spawn_worker_thread("mediaforge-startup-fixture-ready", move || {
            let mut line = String::new();
            let _ = sender.send(BufReader::new(stdout).read_line(&mut line).map(|_| line));
        }).unwrap();
        let ready = receiver.recv_timeout(std::time::Duration::from_secs(15));
        if !matches!(&ready, Ok(Ok(line)) if line.trim() == "ready") {
            drop(guard); let _ = reader.join(); panic!("owned Python 3.12 did not become ready: {ready:?}");
        }
        reader.join().unwrap();
        (root, guard, reaped, slot_held)
    }

    #[test]
    fn worker_startup_thread_failure_reaps_before_releasing_scope_or_quota() {
        let _serial = crate::storage::model_reference_test_guard();
        use std::sync::atomic::Ordering;
        for name in ["mediaforge-worker-stderr", "mediaforge-worker-cancel", "mediaforge-worker-supervisor"] {
            let (root, guard, reaped, slot_held) = owned_startup_fixture(60.0);
            let child = guard.child.clone();
            let submitted = guard.fork();
            super::THREAD_SPAWN_FAILURE.with(|failure| *failure.borrow_mut() = Some(name.into()));
            let spawned = super::spawn_worker_thread(name, move || drop(submitted));
            super::THREAD_SPAWN_FAILURE.with(|failure| *failure.borrow_mut() = None);
            assert!(spawned.is_err(), "{name}");
            assert!(child.lock().unwrap().try_wait().unwrap().is_some());
            assert!(reaped.load(Ordering::SeqCst), "resources released before reap: {name}");
            assert!(slot_held.load(Ordering::SeqCst), "quota released before startup cleanup: {name}");
            assert_eq!(*crate::storage::model_reference_snapshot_guard(), 0, "mutation lease retained after reap: {name}");
            assert_eq!(fs::read_dir(root.path().join("receipts")).unwrap().count(), 0);
            drop(guard);
            assert!(matches!(super::task_journal::try_claim_pending(root.path(), "run_operation", "next").unwrap(), super::task_journal::ClaimAttempt::Ready(_, _, _)));
        }
    }

    #[test]
    fn worker_startup_success_hands_off_without_killing_the_owned_child() {
        let _serial = crate::storage::model_reference_test_guard();
        use std::sync::atomic::Ordering;
        let (_root, mut guard, reaped, slot_held) = owned_startup_fixture(0.15);
        let mut supervisor = guard.fork();
        let thread = super::spawn_worker_thread("mediaforge-worker-supervisor", move || {
            let status = super::wait_reaped_child(&supervisor.child, |_| {});
            supervisor.finish_reaped();
            status
        }).unwrap();
        guard.handoff(); drop(guard);
        assert!(thread.join().unwrap().success());
        assert!(reaped.load(Ordering::SeqCst)); assert!(slot_held.load(Ordering::SeqCst));
        assert_eq!(*crate::storage::model_reference_snapshot_guard(), 0);
    }

    #[test]
    fn worker_startup_supervisor_unwind_still_reaps_before_releasing_resources() {
        let _serial = crate::storage::model_reference_test_guard();
        use std::sync::atomic::Ordering;
        let (_root, mut guard, reaped, slot_held) = owned_startup_fixture(60.0);
        let child = guard.child.clone();
        let supervisor = guard.fork();
        let thread = super::spawn_worker_thread("mediaforge-worker-supervisor", move || {
            let _supervisor = supervisor;
            panic!("injected supervisor failure");
        }).unwrap();
        guard.handoff(); drop(guard);
        assert!(thread.join().is_err());
        assert!(child.lock().unwrap().try_wait().unwrap().is_some());
        assert!(reaped.load(Ordering::SeqCst)); assert!(slot_held.load(Ordering::SeqCst));
        assert_eq!(*crate::storage::model_reference_snapshot_guard(), 0);
    }

    #[test]
    fn worker_startup_admission_keeps_pending_until_failure_is_durable() {
        let root = tempfile::tempdir().unwrap();
        super::task_journal::enqueue_manual(root.path(), "run_operation", "pending", &json!({"operation":"fixture"})).unwrap();
        let pending = std::sync::Mutex::new(std::collections::HashMap::from([("pending".into(), super::PendingWorker {
            root: root.path().into(), command: "run_operation".into(), requested: "pending".into(), created_at: 1.0,
        })]));
        assert!(super::remove_pending_after(&pending, "pending", || Err(crate::error::AppError::Worker("injected persistence failure".into()))).is_err());
        assert!(pending.lock().unwrap().contains_key("pending"));
        assert_eq!(super::task_journal::read_job(&root.path().join("agent-jobs/pending.json")).unwrap().unwrap()["status"], "queued");
        super::remove_pending_after(&pending, "pending", || super::task_journal::fail_unclaimed(root.path(), "run_operation", "pending", "admission failed")).unwrap();
        assert!(pending.lock().unwrap().is_empty());
        assert_eq!(super::task_journal::read_job(&root.path().join("agent-jobs/pending.json")).unwrap().unwrap()["status"], "failed");
    }

    #[test]
    fn worker_startup_dispatcher_unwind_resets_flag_without_clobbering_a_new_dispatcher() {
        use std::sync::atomic::{AtomicBool, Ordering};
        let running = AtomicBool::new(true);
        let pending = std::sync::Mutex::new(std::collections::HashMap::new());
        let outcome = std::panic::catch_unwind(|| {
            let _guard = super::DispatcherRunningGuard { running: &running, pending: &pending, armed: true };
            panic!("injected dispatcher failure");
        });
        assert!(outcome.is_err()); assert!(!running.load(Ordering::Acquire));
        let mut guard = super::DispatcherRunningGuard { running: &running, pending: &pending, armed: true };
        {
            let _registry = pending.lock().unwrap();
            guard.stopped_under_registry_lock();
            running.store(true, Ordering::Release);
        }
        drop(guard);
        assert!(running.load(Ordering::Acquire));
    }

    #[test]
    fn supervisor_terminal_decisions_preserve_recoverable_jobs() {
        let mut envelope = super::WorkerEnvelope {
            event_type: "error".into(), task_id: Some("fixture-task".into()), request_id: None,
            timestamp: None, payload: json!({"recoverable": true, "message": "continuing with next input"}),
        };
        for command in ["infer", "infer_workflow", "audio_tools", "run_operation", "model_download", "manage_plugin"] {
            // A recoverable error cannot populate the terminal registry that gates cancellation.
            assert!(!super::is_supervisor_terminal_event(command, &envelope), "{command}");
            envelope.payload = json!({"recoverable": false, "message": "fatal"});
            assert!(super::is_supervisor_terminal_event(command, &envelope), "{command}");
            envelope.payload = json!({"message": "fatal without an optional flag"});
            assert!(super::is_supervisor_terminal_event(command, &envelope), "{command}");
            envelope.payload = json!({"recoverable": true});
        }
        for (command, kind) in [("infer", "task.result"), ("infer_workflow", "task.cancelled"),
            ("model_download", "asset.done"), ("manage_plugin", "plugin_manage_result")] {
            envelope.event_type = kind.into();
            assert!(super::is_supervisor_terminal_event(command, &envelope), "{command}/{kind}");
        }
        envelope.event_type = "task.progress".into();
        assert!(!super::is_supervisor_terminal_event("infer", &envelope));
    }

    #[test]
    fn audio_output_scopes_match_python_operation_normalization() {
        for operation in ["convert", "Convert", " MERGE ", "\tsLiCeR\n"] {
            assert!(super::needs_audio_output_scope("audio_tools", &json!({"operation": operation})));
        }
        assert!(!super::needs_audio_output_scope("audio_tools", &json!({"operation": "scan"})));
        assert!(!super::needs_audio_output_scope("infer", &json!({"operation": "convert"})));
        assert!(!super::needs_audio_output_scope("audio_tools", &json!({})));
        assert!(super::needs_audio_output_scope("run_operation", &json!({})));
    }

    #[test]
    fn operation_output_scopes_use_the_same_directory_as_python() {
        let root = std::env::temp_dir().join("pymss-operation-scope");
        let default = root.join("outputs");
        for payload in [json!({}), json!({"outputDir": null}), json!({"outputDir": ""})] {
            assert_eq!(super::operation_output_directory(&payload, &default).unwrap(), std::path::absolute(&default).unwrap());
        }
        assert_eq!(super::operation_output_directory(&json!({"outputDir": "relative"}), &default).unwrap(), std::path::absolute(root.join("relative")).unwrap());
        assert_eq!(super::operation_output_directory(&json!({"outputDir": " spaced "}), &default).unwrap(), std::path::absolute(root.join(" spaced ")).unwrap());
        assert_eq!(super::operation_output_directory(&json!({"outputDir": &default}), &default).unwrap(), std::path::absolute(&default).unwrap());
        assert!(super::operation_output_directory(&json!({"outputDir": []}), &default).is_err());
    }

    #[test]
    fn operation_workers_must_emit_a_terminal_result_even_after_a_successful_exit() {
        assert!(super::requires_background_result("run_operation"));
        assert!(super::requires_background_result("manage_plugin"));
        assert!(!super::requires_background_result("list_operations"));
    }

    #[test]
    fn core_update_excludes_parallel_runtime_users_in_both_directions() {
        let access = std::sync::Mutex::new(super::RuntimeAccess::default());
        let inference = super::acquire_runtime_access(&access, "infer").unwrap();
        let workflow = super::acquire_runtime_access(&access, "infer_workflow").unwrap();
        assert!(super::acquire_runtime_access(&access, "update_runtime_core").is_err());
        drop(inference);
        assert!(super::acquire_runtime_access(&access, "update_runtime_core").is_err());
        drop(workflow);

        let update = super::acquire_runtime_access(&access, "update_runtime_core").unwrap();
        for command in [
            "infer",
            "infer_workflow",
            "env_info",
            "list_models",
            "audio_tools",
            "test_connection",
            "update_runtime_core",
        ] {
            let result = super::acquire_runtime_access(&access, command);
            assert!(result.is_err(), "{command} must wait for the core update");
            assert!(result.err().unwrap().to_string().contains("RUNTIME_BUSY"));
        }
        for command in [
            "health",
            "runtime_info",
            "runtime_core_versions",
            "runtime_env_sizes",
        ] {
            assert!(super::acquire_runtime_access(&access, command)
                .unwrap()
                .is_none());
        }
        drop(update);
        assert!(super::acquire_runtime_access(&access, "infer").is_ok());
    }

    #[test]
    fn plugin_management_excludes_execution_and_runtime_switching() {
        let access = std::sync::Mutex::new(super::RuntimeAccess::default());
        let operation = super::acquire_runtime_access(&access, "run_operation").unwrap();
        assert!(super::acquire_runtime_access(&access, "manage_plugin").is_err());
        drop(operation);
        let management = super::acquire_runtime_access(&access, "manage_plugin").unwrap();
        for command in ["infer", "list_operations", "list_nodes", "plugin_marketplace", "manage_plugin",
                        "activate_runtime", "delete_runtime", "update_runtime_core"] {
            assert!(super::acquire_runtime_access(&access, command).is_err(), "{command}");
        }
        drop(management);
        assert!(super::acquire_runtime_access(&access, "plugin_marketplace").is_ok());
    }

    #[test]
    fn plugin_management_result_is_terminal_but_logs_are_not() {
        assert!(is_background_terminal_event("manage_plugin", "plugin_manage_result"));
        assert!(is_background_terminal_event("manage_plugin", "error"));
        assert!(is_background_terminal_event("manage_plugin", "task.cancelled"));
        assert!(!is_background_terminal_event("manage_plugin", "plugin_stage"));
        assert!(!is_background_terminal_event("manage_plugin", "plugin_log"));
    }

    #[test]
    fn runtime_access_lease_can_follow_background_process_and_release_on_failure() {
        let access = std::sync::Mutex::new(super::RuntimeAccess::default());
        let lease = super::acquire_runtime_access(&access, "update_runtime_core").unwrap();
        std::thread::scope(|scope| {
            scope.spawn(move || drop(lease)).join().unwrap();
        });
        let fail_start = || -> crate::error::AppResult<()> {
            let _lease = super::acquire_runtime_access(&access, "infer")?;
            Err(crate::error::AppError::Worker(
                "process start failed".into(),
            ))
        };
        assert!(fail_start().is_err());
        assert!(super::acquire_runtime_access(&access, "update_runtime_core").is_ok());
    }

    #[test]
    fn application_update_excludes_native_operations_for_their_thread_lifetime() {
        let access = std::sync::Mutex::new(super::RuntimeAccess::default());
        let operation = super::acquire_runtime_access(&access, "application_operation").unwrap();
        assert!(super::acquire_runtime_access(&access, "update_runtime_core").is_err());
        std::thread::scope(|scope| scope.spawn(move || drop(operation)).join().unwrap());
        let update = super::acquire_runtime_access(&access, "update_runtime_core").unwrap();
        assert!(super::acquire_runtime_access(&access, "application_operation").is_err());
        drop(update);
        assert!(super::acquire_runtime_access(&access, "application_operation").is_ok());
    }

    #[test]
    fn editor_creation_and_update_reservations_exclude_each_other() {
        let update = super::acquire_application_update().unwrap();
        std::thread::scope(|scope| {
            scope.spawn(|| {
                // A delayed open request arriving after update reservation must fail.
                assert!(super::acquire_application_operation().is_err());
            }).join().unwrap();
        });
        drop(update);

        std::thread::scope(|scope| {
            let (started, pending) = std::sync::mpsc::channel();
            let (resume, wait) = std::sync::mpsc::channel();
            let opening = scope.spawn(move || {
                let _creation_access = super::acquire_application_operation().unwrap();
                started.send(()).unwrap();
                // Cover an open request awaiting the shared window-creation gate.
                wait.recv().unwrap();
            });
            pending.recv().unwrap();
            let blocked = super::acquire_application_update().is_err();
            resume.send(()).unwrap();
            opening.join().unwrap();
            assert!(blocked);
        });
        assert!(super::acquire_application_update().is_ok());
    }

    #[test]
    fn connection_test_prefers_active_runtime_and_falls_back_to_bootstrap() {
        assert_eq!(
            super::select_worker_python(
                "test_connection",
                "bootstrap-python",
                Some("active-python".into()),
            )
            .unwrap(),
            "active-python",
        );
        assert_eq!(
            super::select_worker_python("test_connection", "bootstrap-python", None).unwrap(),
            "bootstrap-python",
        );
    }

    #[test]
    fn runtime_management_ignores_active_runtime_but_inference_requires_it() {
        assert_eq!(
            super::select_worker_python(
                "install_runtime",
                "bootstrap-python",
                Some("active-python".into()),
            )
            .unwrap(),
            "bootstrap-python",
        );
        assert!(super::select_worker_python("infer", "bootstrap-python", None).is_err());
    }

    fn temp_root(label: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "pymss-worker-{label}-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    struct ResourceFixture(std::path::PathBuf);

    impl ResourceFixture {
        fn new(label: &str) -> Self {
            let root = temp_root(label);
            fs::create_dir(&root).unwrap();
            Self(root)
        }

        fn file(&self, path: &Path) {
            assert!(path.starts_with(&self.0));
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, b"resource").unwrap();
        }
    }

    impl Drop for ResourceFixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn worker_resource_layouts_keep_the_flat_fallback_last() {
        let fixture = ResourceFixture::new("worker-layout");
        let resource = fixture.0.join("resources");
        assert_eq!(super::resource_worker_path(&resource), None);
        let candidates = [
            resource.join("python/worker.py"),
            resource.join("_up_/python/worker.py"),
            resource.join("resources/python/worker.py"),
            resource.join("worker.py"),
        ];
        for candidate in candidates.iter().rev() {
            fixture.file(candidate);
            assert_eq!(
                super::resource_worker_path(&resource),
                Some(candidate.clone())
            );
        }
    }

    #[test]
    fn worker_lookup_retains_its_existing_exists_check() {
        let fixture = ResourceFixture::new("worker-exists");
        let worker = fixture.0.join("python/worker.py");
        fs::create_dir_all(&worker).unwrap();
        assert_eq!(super::resource_worker_path(&fixture.0), Some(worker));
    }

    #[test]
    fn embedded_python_preserves_root_and_platform_specific_filename_priority() {
        for windows in [true, false] {
            let fixture = ResourceFixture::new("embedded-layout");
            let resource = fixture.0.join("Pymss.app/Contents/Resources");
            let exe = fixture.0.join("Pymss.app/Contents/MacOS");
            let roots = [
                resource.join("python-runtime"),
                resource.join("_up_/python-runtime"),
                resource.join("resources/python-runtime"),
                exe.join("python-runtime"),
            ];
            let names = if windows {
                ["python.exe", "Scripts/python.exe"]
            } else {
                ["bin/python3", "bin/python"]
            };
            assert_eq!(
                super::embedded_python_from(Some(&resource), &exe, windows),
                None
            );
            for root in roots.iter().rev() {
                for name in names.iter().rev() {
                    let candidate = root.join(name);
                    fixture.file(&candidate);
                    assert_eq!(
                        super::embedded_python_from(Some(&resource), &exe, windows),
                        Some(candidate)
                    );
                }
            }
            assert_eq!(
                super::embedded_python_from(None, &exe, windows),
                Some(roots[3].join(names[0]))
            );
        }
    }

    #[test]
    fn embedded_python_skips_directories_named_like_interpreters() {
        let fixture = ResourceFixture::new("embedded-file-type");
        let resource = fixture.0.join("resources");
        let exe = fixture.0.join("portable");
        fs::create_dir_all(resource.join("python-runtime/python.exe")).unwrap();
        let python = exe.join("python-runtime/python.exe");
        fixture.file(&python);
        assert_eq!(
            super::embedded_python_from(Some(&resource), &exe, true),
            Some(python)
        );
    }

    #[test]
    fn bin_candidates_only_include_app_owned_directories() {
        let fixture = ResourceFixture::new("bin-layout");
        let resource = fixture.0.join("resources");
        let exe = fixture.0.join("portable");
        let expected = vec![
            resource.join("bin"),
            resource.join("_up_/bin"),
            resource.join("resources/bin"),
            exe.join("bin"),
        ];
        assert_eq!(
            super::bundled_bin_candidates(Some(&resource), &exe),
            expected
        );
        fs::create_dir_all(&expected[1]).unwrap();
        fs::create_dir_all(&expected[3]).unwrap();
        fixture.file(&expected[2]);
        let hits = super::bundled_bin_candidates(Some(&resource), &exe)
            .into_iter()
            .filter(|dir| dir.is_dir())
            .collect::<Vec<_>>();
        assert_eq!(hits, vec![expected[1].clone(), expected[3].clone()]);
        assert_eq!(
            super::bundled_bin_candidates(None, &exe),
            vec![exe.join("bin")]
        );
        let duplicate = super::bundled_bin_candidates(Some(&exe), &exe);
        assert_eq!(duplicate[0], duplicate[3]);
    }

    #[test]
    fn worker_python_environment_drops_host_python_and_conda_overrides() {
        let mut command = crate::process_command::hidden_command("python");
        for key in super::HOST_PYTHON_ENV_VARS {
            command.env(key, "host-runtime");
        }

        command.env("NUMBA_CACHE_DIR", "host-runtime");
        command.env("PIP_CACHE_DIR", "host-runtime");
        let cache_root = Path::new("user-data/temp");
        super::isolate_python_environment(&mut command, cache_root);

        for key in super::HOST_PYTHON_ENV_VARS {
            let (_, value) = command
                .get_envs()
                .find(|(name, _)| *name == std::ffi::OsStr::new(key))
                .unwrap_or_else(|| panic!("{key} was not explicitly removed"));
            assert!(value.is_none(), "{key} still leaks into the Worker");
        }
        let (_, no_user_site) = command
            .get_envs()
            .find(|(name, _)| *name == std::ffi::OsStr::new("PYTHONNOUSERSITE"))
            .expect("PYTHONNOUSERSITE was not configured");
        assert_eq!(no_user_site, Some(std::ffi::OsStr::new("1")));
        for (key, directory) in [
            ("NUMBA_CACHE_DIR", "numba-cache"),
            ("PIP_CACHE_DIR", "pip-cache"),
        ] {
            let (_, value) = command
                .get_envs()
                .find(|(name, _)| *name == std::ffi::OsStr::new(key))
                .unwrap();
            assert_eq!(value, Some(cache_root.join(directory).as_os_str()));
        }
    }

    #[test]
    fn bundled_env_selection_preserves_canonical_path_overlap_and_missing_path_behavior() {
        let fixture = ResourceFixture::new("env-overlap");
        let user = fixture.0.join("user");
        let bundled = fixture.0.join("bundled");
        fs::create_dir_all(&user).unwrap();
        fs::create_dir_all(&bundled).unwrap();
        let canonical_user = user.canonicalize().ok();
        assert_eq!(
            super::distinct_bundled_runtime_env(canonical_user.clone(), Some(user.join("."))),
            None
        );
        assert_eq!(
            super::distinct_bundled_runtime_env(canonical_user.clone(), Some(bundled.clone())),
            Some(bundled.clone())
        );
        assert_eq!(
            super::distinct_bundled_runtime_env(canonical_user.clone(), None),
            None
        );
        assert_eq!(
            super::distinct_bundled_runtime_env(None, Some(bundled.clone())),
            Some(bundled)
        );
        let missing = fixture.0.join("missing");
        assert_eq!(
            super::distinct_bundled_runtime_env(None, Some(missing.clone())),
            None
        );
        assert_eq!(
            super::distinct_bundled_runtime_env(canonical_user, Some(missing.clone())),
            Some(missing)
        );
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_venv_interpreter_remains_usable_by_business_commands() {
        use std::os::unix::fs::symlink;
        let unique = temp_root("venv-python-symlink");
        let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../.tmp").join(unique.file_name().unwrap());
        fs::create_dir_all(&root).unwrap();
        let fixture = ResourceFixture(root);
        let base = fixture.0.join("base/bin/python3");
        let python = fixture.0.join("runtime-envs/mlx/bin/python");
        fixture.file(&base);
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        symlink(&base, &python).unwrap();
        let pointer = fixture.0.join("runtime-envs/active-runtime.json");
        let expected = python.parent().unwrap().canonicalize().unwrap().join("python");
        for recorded in [python.to_string_lossy().to_string(), "mlx/bin/python".to_string()] {
            fs::write(&pointer, serde_json::to_vec(&json!({
                "pythonPath": recorded, "backend": "mlx"
            })).unwrap()).unwrap();
            let resolved = super::resolve_active_runtime_record(&pointer).unwrap().unwrap().python;
            let active = super::active_path_backend_matches(&pointer, &resolved).then_some(resolved);
            let selected = super::select_worker_python("plugin_marketplace", "bootstrap", active);
            assert!(selected.is_ok(), "an installed MLX venv was rejected: {selected:?}");
            assert_eq!(selected.unwrap(), expected.to_string_lossy());
        }
    }

    #[test]
    fn active_pointer_resolution_accepts_existing_relative_and_absolute_paths_without_rewriting() {
        let fixture = ResourceFixture::new("pointer-resolution");
        let python = fixture.0.join("cpu/bin/python");
        fixture.file(&python);
        let pointer = fixture.0.join("active-runtime.json");
        for path in [
            "cpu/bin/python".to_string(),
            python.to_string_lossy().to_string(),
        ] {
            let bytes = serde_json::to_vec(
                &json!({"pythonPath": path, "backend": "cpu", "source": "bundled", "custom": true}),
            )
            .unwrap();
            fs::write(&pointer, &bytes).unwrap();
            assert_eq!(
                super::resolve_active_runtime_record(&pointer).unwrap(),
                Some(super::ActiveRuntimeSelection {
                    python: python.canonicalize().unwrap().to_string_lossy().to_string(),
                    source: Some("bundled".into()),
                    asset_id: None,
                })
            );
            assert_eq!(fs::read(&pointer).unwrap(), bytes);
        }
        for bytes in [
            b"{}".as_slice(),
            b"not json",
            br#"{"pythonPath":"missing/python"}"#,
            br#"{"pythonPath":"cpu"}"#,
        ] {
            fs::write(&pointer, bytes).unwrap();
            assert!(
                super::resolve_active_runtime_record(&pointer).is_err(),
                "corrupt pointer must not be treated as absent"
            );
            assert_eq!(fs::read(&pointer).unwrap(), bytes);
        }
        assert_eq!(
            super::resolve_active_runtime_record(&fixture.0.join("missing.json")).unwrap(),
            None
        );
    }

    #[test]
    fn corrupt_pointer_errors_name_the_file_and_keep_it() {
        let fixture = ResourceFixture::new("pointer-error");
        let pointer = fixture.0.join("active-runtime.json");
        fs::write(&pointer, b"not json").unwrap();
        let error = super::resolve_active_runtime_record(&pointer).unwrap_err().to_string();
        assert!(error.contains("active-runtime.json"), "{error}");
        assert!(error.contains("not a valid runtime record"), "{error}");
        assert_eq!(fs::read(&pointer).unwrap(), b"not json");
    }

    #[cfg(unix)]
    #[test]
    fn active_pointer_keeps_the_posix_venv_executable_link() {
        let fixture = ResourceFixture::new("venv-pointer");
        let bootstrap = fixture.0.join("bundle/python-runtime/bin/python3");
        fixture.file(&bootstrap);
        let runtime_root = fixture.0.join("user/runtime-envs");
        let python = runtime_root.join("cpu/bin/python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink("../../../../bundle/python-runtime/bin/python3", &python)
            .unwrap();
        let pointer = runtime_root.join("active-runtime.json");
        let expected = python
            .parent()
            .unwrap()
            .canonicalize()
            .unwrap()
            .join("python");
        for path in [
            "cpu/bin/python".to_string(),
            python.to_string_lossy().to_string(),
        ] {
            fs::write(
                &pointer,
                serde_json::to_vec(&json!({"backend": "cpu", "pythonPath": path})).unwrap(),
            )
            .unwrap();
            let resolved = super::resolve_active_runtime_record(&pointer).unwrap().unwrap().python;
            assert_eq!(Path::new(&resolved), expected);
            assert_ne!(Path::new(&resolved), python.canonicalize().unwrap());
            assert!(super::active_path_backend_matches(&pointer, &resolved));
            assert_eq!(
                super::select_worker_python(
                    "infer",
                    &bootstrap.to_string_lossy(),
                    Some(resolved.clone())
                )
                .unwrap(),
                resolved,
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn venv_links_sharing_an_executable_do_not_share_runtime_overlays() {
        let fixture = ResourceFixture::new("venv-overlay");
        let bootstrap = fixture.0.join("python-runtime/bin/python3");
        fixture.file(&bootstrap);
        let runtime_root = fixture.0.join("python-runtime/runtime-envs");
        let cpu_python = runtime_root.join("cpu/bin/python");
        let mlx_python = runtime_root.join("mlx/bin/python");
        for python in [&cpu_python, &mlx_python] {
            fs::create_dir_all(python.parent().unwrap()).unwrap();
            std::os::unix::fs::symlink("../../../bin/python3", python).unwrap();
        }
        let overlay = runtime_root.join(".overlays/cpu/generation/site-packages");
        fs::create_dir_all(&overlay).unwrap();
        let pointer = runtime_root.join("active-runtime.json");
        fs::write(
            &pointer,
            serde_json::to_vec(
                &json!({"backend": "cpu", "pythonPath": "cpu/bin/python", "overlayPath": overlay}),
            )
            .unwrap(),
        )
        .unwrap();
        assert!(
            super::is_bundled_runtime_python_path(&pointer, &cpu_python.to_string_lossy()).unwrap()
        );
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &pointer,
                &cpu_python.to_string_lossy(),
                std::slice::from_ref(&runtime_root),
            ),
            Some(overlay.canonicalize().unwrap()),
        );
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &pointer,
                &mlx_python.to_string_lossy(),
                &[runtime_root],
            ),
            None,
        );
    }

    #[test]
    fn bundled_bootstrap_uses_user_overlay_without_changing_bundle() {
        let fixture = ResourceFixture::new("bundled-user-overlay");
        let bundled = fixture
            .0
            .join("Installed.app/Contents/Resources/python-runtime");
        let python = bundled.join("bin/python3");
        fixture.file(&python);
        let bundled_pointer = bundled.join("runtime-envs/active-runtime.json");
        fs::create_dir_all(bundled_pointer.parent().unwrap()).unwrap();
        let bundled_state = serde_json::to_vec(&json!({
            "backend": "mlx", "source": "bundled", "pythonPath": "../bin/python3",
        }))
        .unwrap();
        fs::write(&bundled_pointer, &bundled_state).unwrap();
        let user_root = fixture.0.join("user/runtime-envs");
        let overlay = user_root.join(".overlays/mlx/generation/site-packages");
        fs::create_dir_all(&overlay).unwrap();
        let user_pointer = user_root.join("active-runtime.json");
        fs::write(
            &user_pointer,
            serde_json::to_vec(&json!({
                "backend": "mlx", "source": "bundled", "pythonPath": python,
                "overlayPath": overlay,
            }))
            .unwrap(),
        )
        .unwrap();
        let active_python = super::resolve_active_runtime_record(&bundled_pointer)
            .unwrap()
            .unwrap()
            .python;
        assert!(super::is_bundled_runtime_python_path(&bundled_pointer, &active_python).unwrap());
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &user_pointer,
                &active_python,
                &[user_root.clone()],
            ),
            Some(overlay.canonicalize().unwrap())
        );
        fs::write(
            &user_pointer,
            serde_json::to_vec(&json!({
                "backend": "mlx", "source": "bundled", "pythonPath": python,
            }))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(
            super::resolve_runtime_overlay_for_record(&user_pointer, &active_python, &[user_root],),
            None
        );
        assert_eq!(fs::read(bundled_pointer).unwrap(), bundled_state);
    }

    #[test]
    fn runtime_overlay_must_match_the_active_python_and_runtime_root() {
        let fixture = ResourceFixture::new("runtime-overlay");
        let runtime_root = fixture.0.join("runtime-envs");
        let python = runtime_root.join("cpu/bin/python");
        let overlay = runtime_root.join(".overlays/cpu/generation/site-packages");
        let outside = fixture.0.join("outside/site-packages");
        fixture.file(&python);
        fs::create_dir_all(&overlay).unwrap();
        fs::create_dir_all(&outside).unwrap();
        let pointer = runtime_root.join("active-runtime.json");

        fs::write(
            &pointer,
            serde_json::to_vec(&json!({
                "pythonPath": python,
                "backend": "cpu",
                "overlayPath": overlay,
            }))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &pointer,
                &python.to_string_lossy(),
                std::slice::from_ref(&runtime_root),
            ),
            Some(overlay.canonicalize().unwrap()),
        );

        fs::write(
            &pointer,
            serde_json::to_vec(&json!({
                "pythonPath": python,
                "backend": "cpu",
                "overlayPath": outside,
            }))
            .unwrap(),
        )
        .unwrap();
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &pointer,
                &python.to_string_lossy(),
                std::slice::from_ref(&runtime_root),
            ),
            None,
        );
        assert_eq!(
            super::resolve_runtime_overlay_for_record(
                &pointer,
                &fixture.0.join("other-python").to_string_lossy(),
                &[runtime_root],
            ),
            None,
        );
    }

    #[cfg(windows)]
    #[test]
    fn rocm_tools_keep_user_before_bundled_then_regular_bins_and_existing_path() {
        let fixture = ResourceFixture::new("rocm-path-order");
        let user = fixture.0.join("user");
        let bundled = fixture.0.join("bundled");
        let mut expected = Vec::new();
        for root in [&user, &bundled] {
            let package = root
                .join("rocm")
                .join("Lib")
                .join("site-packages")
                .join("_rocm_sdk_core_1");
            let tool = package.join("lib").join("llvm").join("bin");
            fixture.file(&tool.join("offload-arch.exe"));
            let sdk_bin = package.join("bin");
            fs::create_dir_all(&sdk_bin).unwrap();
            expected.extend([tool, sdk_bin]);
            fixture
                .file(&root.join("rocm/Lib/site-packages/unrelated/lib/llvm/bin/offload-arch.exe"));
        }
        let mut dirs =
            super::rocm_native_tool_dirs_from(vec![user, fixture.0.join("absent"), bundled]);
        assert_eq!(dirs, expected);
        let bin = fixture.0.join("bin");
        fs::create_dir_all(&bin).unwrap();
        dirs.extend(super::bundled_bin_candidates(None, &fixture.0));
        expected.push(bin);
        let joined = expected
            .iter()
            .map(|p| p.to_string_lossy())
            .collect::<Vec<_>>()
            .join(";");
        assert_eq!(
            super::prepend_path(Some("existing-path".into()), dirs),
            Some(format!("{joined};existing-path"))
        );
    }

    #[test]
    fn model_download_result_and_cancellation_are_background_terminal_events() {
        assert!(is_background_terminal_event("model_download", "asset.done"));
        assert!(is_background_terminal_event("model_download", "error"));
        assert!(is_background_terminal_event("model_download", "task.cancelled"));
        assert!(!is_background_terminal_event("model_download", "asset.progress"));
    }

    #[test]
    fn audio_tool_result_is_a_background_terminal_event() {
        assert!(is_background_terminal_event(
            "audio_tools",
            "task.result"
        ));
        assert!(is_background_terminal_event("audio_tools", "error"));
        assert!(!is_background_terminal_event(
            "audio_tools",
            "task.progress"
        ));
    }

    #[test]
    fn operation_task_result_is_a_background_terminal_event() {
        assert!(is_background_terminal_event("run_operation", "task.result"));
        assert!(is_background_terminal_event("run_operation", "error"));
        assert!(is_background_terminal_event("run_operation", "task.cancelled"));
        assert!(!is_background_terminal_event(
            "run_operation",
            "task.progress"
        ));
    }

    #[test]
    fn editor_export_result_is_a_background_terminal_event() {
        assert!(is_background_terminal_event(
            "export_editor_mix",
            "editor_mix_exported"
        ));
        assert!(is_background_terminal_event("export_editor_mix", "error"));
        assert!(!is_background_terminal_event(
            "export_editor_mix",
            "editor_export_progress"
        ));
    }

    fn write_bundled_pointer(root: &Path, backend: &str, python_path: &str) -> std::path::PathBuf {
        let envs = root.join("python-runtime").join("runtime-envs");
        fs::create_dir_all(&envs).unwrap();
        let active = envs.join("active-runtime.json");
        fs::write(
            &active,
            serde_json::to_vec(&json!({
                "backend": backend,
                "pythonPath": python_path,
                "source": "bundled",
            }))
            .unwrap(),
        )
        .unwrap();
        active
    }

    #[test]
    fn accepts_bundled_mlx_bootstrap_pointer() {
        let root = temp_root("bundled-mlx-pointer");
        let runtime = root.join("python-runtime");
        let python = runtime.join("bin").join("python3");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "stub").unwrap();
        let active = write_bundled_pointer(&root, "mlx", "../bin/python3");

        assert!(super::is_bundled_runtime_python_path(
            &active,
            &fs::canonicalize(&python).unwrap().to_string_lossy(),
        )
        .unwrap());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn rejects_bundled_bootstrap_pointer_for_non_mlx_backend() {
        let root = temp_root("bundled-invalid-bootstrap");
        let runtime = root.join("python-runtime");
        let python = runtime.join("bin").join("python3");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "stub").unwrap();
        let active = write_bundled_pointer(&root, "cpu", "../bin/python3");

        assert!(!super::is_bundled_runtime_python_path(
            &active,
            &fs::canonicalize(&python).unwrap().to_string_lossy(),
        )
        .unwrap());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn rejects_bundled_pointer_that_escapes_the_package() {
        let root = temp_root("bundled-outside-pointer");
        let outside = root.join("outside").join("python3");
        fs::create_dir_all(outside.parent().unwrap()).unwrap();
        fs::write(&outside, "stub").unwrap();
        let active = write_bundled_pointer(&root, "mlx", "../../outside/python3");

        assert!(!super::is_bundled_runtime_python_path(
            &active,
            &fs::canonicalize(&outside).unwrap().to_string_lossy(),
        )
        .unwrap());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn rejects_bundled_environment_with_the_wrong_backend_directory() {
        let root = temp_root("bundled-wrong-backend");
        let envs = root.join("python-runtime").join("runtime-envs");
        let python = envs.join("cpu").join("bin").join("python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "stub").unwrap();
        let active = write_bundled_pointer(&root, "cuda", "cpu/bin/python");

        assert!(!super::is_bundled_runtime_python_path(
            &active,
            &fs::canonicalize(&python).unwrap().to_string_lossy(),
        )
        .unwrap());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn active_user_pointer_requires_a_matching_known_backend() {
        let root = temp_root("active-pointer-backend");
        let active = root.join("active-runtime.json");
        let python = root
            .join("runtime-envs")
            .join("cuda")
            .join("bin")
            .join("python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "stub").unwrap();
        let python = fs::canonicalize(python).unwrap();
        fs::write(
            &active,
            serde_json::to_vec(&json!({"backend": "cuda", "pythonPath": python.to_string_lossy()}))
                .unwrap(),
        )
        .unwrap();
        assert!(super::active_path_backend_matches(
            &active,
            &python.to_string_lossy()
        ));

        fs::write(
            &active,
            serde_json::to_vec(&json!({"backend": "mlx", "pythonPath": python.to_string_lossy()}))
                .unwrap(),
        )
        .unwrap();
        assert!(!super::active_path_backend_matches(
            &active,
            &python.to_string_lossy()
        ));

        fs::write(
            &active,
            serde_json::to_vec(
                &json!({"backend": "../outside", "pythonPath": python.to_string_lossy()}),
            )
            .unwrap(),
        )
        .unwrap();
        assert!(!super::active_path_backend_matches(
            &active,
            &python.to_string_lossy()
        ));

        fs::write(
            &active,
            serde_json::to_vec(&json!({"pythonPath": python.to_string_lossy()})).unwrap(),
        )
        .unwrap();
        assert!(!super::active_path_backend_matches(
            &active,
            &python.to_string_lossy()
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn external_runtime_pointer_requires_a_development_debug_override() {
        let root = temp_root("external-debug-runtime");
        let active = root.join("active-runtime.json");
        let python = root
            .join("external")
            .join("cuda")
            .join("bin")
            .join("python");
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, "stub").unwrap();
        let python = fs::canonicalize(python).unwrap();

        fs::write(
            &active,
            serde_json::to_vec(&json!({
                "backend": "cuda",
                "pythonPath": python.to_string_lossy(),
                "debugOverride": true,
            }))
            .unwrap(),
        )
        .unwrap();
        assert!(super::active_debug_override_path_allowed(
            &active,
            &python.to_string_lossy(),
            true,
        ));
        assert!(!super::active_debug_override_path_allowed(
            &active,
            &python.to_string_lossy(),
            false,
        ));

        fs::write(
            &active,
            serde_json::to_vec(&json!({
                "backend": "cuda",
                "pythonPath": python.to_string_lossy(),
            }))
            .unwrap(),
        )
        .unwrap();
        assert!(!super::active_debug_override_path_allowed(
            &active,
            &python.to_string_lossy(),
            true,
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(windows)]
    #[test]
    fn finds_rocm_native_offload_arch_tool() {
        let root = std::env::temp_dir().join(format!(
            "pymss-worker-rocm-tool-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let tool_dir = root
            .join("rocm")
            .join("Lib")
            .join("site-packages")
            .join("_rocm_sdk_core")
            .join("lib")
            .join("llvm")
            .join("bin");
        std::fs::create_dir_all(&tool_dir).unwrap();
        std::fs::write(tool_dir.join("offload-arch.exe"), "stub").unwrap();

        assert_eq!(super::rocm_native_tool_dir(&root), Some(tool_dir));

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn recognizes_only_known_rocm_offload_arch_diagnostics() {
        for line in [
            "Fatal error in launcher: Unable to create process using 'D:\\a\\python.exe'",
            "[WARNING] offload-arch failed with return code 1",
            "[stderr]",
            "[rocm_sdk] offload-arch not found",
        ] {
            assert!(super::is_rocm_offload_arch_diagnostic(line));
        }
        assert!(!super::is_rocm_offload_arch_diagnostic(
            "unrelated Python traceback"
        ));
    }

    #[test]
    fn logging_envelopes_never_replace_command_results() {
        for event_type in [
            "worker_log",
            "task.log",
            "asset.log",
            "optional_package_log",
            "runtime_install_log",
            "runtime_core_update_log",
        ] {
            let envelope: super::WorkerEnvelope = serde_json::from_value(
                serde_json::json!({"type": event_type, "payload": {"message": "line"}}),
            )
            .unwrap();
            assert!(super::is_log_envelope(&envelope));
            assert!(!super::is_background_terminal_event(
                "infer_workflow",
                event_type
            ));
        }
        let result: super::WorkerEnvelope = serde_json::from_value(
            serde_json::json!({"type": "models", "payload": {"schemaVersion": 1, "models": []}}),
        )
        .unwrap();
        assert!(!super::is_log_envelope(&result));
    }

    #[test]
    fn rocm_stdout_diagnostics_preserve_raw_message_and_operation_scope() {
        for line in [
            "Fatal error in launcher: Unable to create process using 'D:/runtime/python.exe offload-arch'",
            "offload-arch failed with return code 1",
            "[rocm_sdk] offload-arch probe failed",
            "[stderr]",
        ] {
            assert!(super::is_rocm_offload_arch_diagnostic(line));
            let record = super::worker_stdout_diagnostic("env_info", "operation-a", line);
            assert_eq!(record.source, "third_party");
            assert!(record.level.is_none());
            assert_eq!(record.event, "process.stdout");
            assert_eq!(record.logger, "stdout");
            assert_eq!(record.message, line);
            assert_eq!(record.context["command"], "env_info");
            assert_eq!(record.context["operationId"], "operation-a");
            assert!(record.detail.is_none());
        }
    }

    #[test]
    fn progress_sampling_preserves_task_boundaries_and_reports_suppressed_updates() {
        let mut samples = std::collections::HashMap::new();
        let start = std::time::Instant::now();
        assert_eq!(
            super::sample_progress_log(&mut samples, "task-a".into(), start),
            Some(0)
        );
        for _ in 0..50 {
            assert_eq!(
                super::sample_progress_log(&mut samples, "task-a".into(), start),
                None
            );
        }
        assert_eq!(
            super::sample_progress_log(&mut samples, "task-b".into(), start),
            Some(0)
        );
        assert_eq!(
            super::sample_progress_log(
                &mut samples,
                "task-a".into(),
                start + std::time::Duration::from_secs(1)
            ),
            Some(50)
        );
        for index in 0..1000 {
            super::sample_progress_log(&mut samples, format!("task-{index}"), start);
        }
        assert!(samples.len() <= 256);
    }

    #[test]
    fn worker_events_keep_error_context_outside_message() {
        let envelope: super::WorkerEnvelope = serde_json::from_value(serde_json::json!({
            "type": "error", "taskId": "task-a", "requestId": "request-a",
            "payload": {"message": "Allocation failed", "detail": "Traceback\nMemoryError", "nodeId": "node-a", "jobId": "job-a", "code": "OOM"}
        })).unwrap();
        let record = super::record_worker_event("infer_workflow", &envelope, "ERROR");
        assert_eq!(record.message, "Allocation failed");
        assert_eq!(record.source, "worker");
        assert_eq!(record.level.as_deref(), Some("error"));
        assert_eq!(record.detail.as_deref(), Some("Traceback\nMemoryError"));
        assert_eq!(record.node_id.as_deref(), Some("node-a"));
        assert_eq!(record.context["code"], "OOM");
    }

    #[test]
    fn report_events_retain_workflow_lifecycle_and_tool_counts_without_result_contents() {
        let envelope: super::WorkerEnvelope = serde_json::from_value(serde_json::json!({
            "type": "workflow_execution", "taskId": "task-a",
            "payload": {"event": "node_started", "nodeId": "separate-1", "nodeType": "mss_separate",
                "nodeIndex": 2, "nodeCount": 5, "nodeProgress": 0.25, "overallFraction": 0.1}
        })).unwrap();
        let record = super::record_worker_event("infer_workflow", &envelope, "INFO");
        assert_eq!(record.context["event"], "node_started");
        assert_eq!(record.context["nodeCount"], 5);
        assert_eq!(record.context["nodeProgress"], 0.25);
        let tool: super::WorkerEnvelope = serde_json::from_value(serde_json::json!({
            "type": "task.result", "requestId": "tool-1",
            "payload": {"operation": "asr", "action": "transcribe", "succeeded": 1,
                "items": [{"text": "private transcript", "path": "private-input.wav"}],
                "failed": [{"path": "other-private-input.wav"}], "warnings": ["warning"]}
        })).unwrap();
        let record = super::record_worker_event("audio_tools", &tool, "WARN");
        assert_eq!(record.context["operation"], "asr");
        assert_eq!(record.context["resultCount"], 1);
        assert_eq!(record.context["failedCount"], 1);
        assert_eq!(record.context["warningCount"], 1);
        let json = serde_json::to_string(&record).unwrap();
        assert!(!json.contains("private transcript"));
        assert!(!json.contains("private-input.wav"));
    }

    #[test]
    fn untagged_worker_failures_bind_to_the_request_without_changing_business_envelopes() {
        let envelope: super::WorkerEnvelope = serde_json::from_value(serde_json::json!({
            "type": "error", "payload": {"code": "UNKNOWN_ERROR", "message": "Worker initialization failed"}
        })).unwrap();
        let mut record = super::record_worker_event("infer_workflow", &envelope, "ERROR");
        super::bind_operation_scope(&mut record, Some("operation-a"), Some("batch-parent"));
        assert_eq!(record.context["operationId"], "operation-a");
        assert_eq!(record.task_id.as_deref(), Some("batch-parent"));
        assert!(envelope.task_id.is_none());
        assert!(envelope.request_id.is_none());
    }

    #[test]
    fn operation_scoped_batch_logs_never_inherit_the_first_file_task() {
        let mut record = super::LogRecord::new("pymss", Some("info"), "pymss", "log", "Preparing shared model");
        record.context.insert("taskScope".into(), "operation".into());
        let mut envelope: super::WorkerEnvelope = serde_json::from_value(serde_json::json!({
            "type": "task.log", "taskId": "first-file", "payload": {}
        })).unwrap();
        super::bind_log_envelope_scope(&mut record, &envelope);
        super::bind_operation_scope(&mut record, Some("batch-operation"), Some("first-file"));
        assert!(record.task_id.is_none());
        assert_eq!(record.context["operationId"], "batch-operation");
        envelope.request_id = Some("batch-request".into());
        super::bind_log_envelope_scope(&mut record, &envelope);
        assert!(record.task_id.is_none());
        assert_eq!(record.request_id.as_deref(), Some("batch-request"));

        let mut file_record = super::LogRecord::new("pymss", Some("info"), "pymss", "log", "Separating file");
        super::bind_log_envelope_scope(&mut file_record, &envelope);
        super::bind_operation_scope(&mut file_record, Some("batch-operation"), Some("first-file"));
        assert_eq!(file_record.task_id.as_deref(), Some("first-file"));
        assert_eq!(file_record.context["operationId"], "batch-operation");
    }
}
