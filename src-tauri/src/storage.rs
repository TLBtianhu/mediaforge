use crate::error::{AppError, AppResult};
use serde::Serialize;
use serde_json::Value;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Manager};

#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;

#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::ReplaceFileW;

const DATA_ROOT_ENV: &str = "PYMSS_STUDIO_DATA_ROOT";
const MEDIAFORGE_DATA_ROOT_ENV: &str = "MEDIAFORGE_DATA_ROOT";
const DATA_ROOT_DIR_NAME: &str = ".mediaforge";
const DEVELOPMENT_DATA_ROOT_DIR_NAME: &str = ".dev-data";
const LOCAL_DATA_ROOT_DIR_NAME: &str = "data";
static JSON_WRITE_SEQUENCE: AtomicU64 = AtomicU64::new(0);
// Multiple WebView windows can autosave the same store at once. Serialize the temporary-file
// replacement in the Rust process so a second writer never races the first ReplaceFileW call.
static JSON_WRITE_LOCK: Mutex<()> = Mutex::new(());
static MODEL_REFERENCE_MUTATIONS: Mutex<usize> = Mutex::new(0);
#[cfg(test)]
static MODEL_REFERENCE_TEST_LOCK: Mutex<()> = Mutex::new(());
#[cfg(test)]
pub(crate) fn model_reference_test_guard() -> std::sync::MutexGuard<'static, ()> {
    MODEL_REFERENCE_TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner())
}

pub(crate) struct ModelReferenceMutation { pending_reap: bool }
impl ModelReferenceMutation {
    pub(crate) fn spawned(&mut self) { self.pending_reap = true; }
    pub(crate) fn reaped(&mut self) { self.pending_reap = false; }
}
impl Drop for ModelReferenceMutation {
    fn drop(&mut self) {
        if self.pending_reap {
            // An uncertain child must never make result files appear unreferenced.
            // Keep deletion fail-closed for this process rather than guess it exited.
            eprintln!("model registry mutation exit is unconfirmed; result-file recycling remains blocked");
            return;
        }
        let mut count = MODEL_REFERENCE_MUTATIONS.lock().unwrap_or_else(|error| error.into_inner());
        *count -= 1;
    }
}
pub(crate) fn model_reference_mutation(command: &str) -> Option<ModelReferenceMutation> {
    if !matches!(command, "import_custom_model" | "relink_custom_model" | "remap_custom_model_paths" | "unregister_custom_model") { return None; }
    *MODEL_REFERENCE_MUTATIONS.lock().unwrap_or_else(|error| error.into_inner()) += 1;
    Some(ModelReferenceMutation { pending_reap: false })
}
pub(crate) fn model_reference_snapshot_guard() -> std::sync::MutexGuard<'static, usize> {
    MODEL_REFERENCE_MUTATIONS.lock().unwrap_or_else(|error| error.into_inner())
}
const PORTABLE_MARKER_FILE_NAME: &str = "mediaforge.portable";

#[derive(Debug, Clone, PartialEq, Eq)]
struct InstalledLayout {
    data: PathBuf,
    models: PathBuf,
    runtime_envs: PathBuf,
}

/// 安装根下的 app 子目录只放可替换程序，数据、模型和运行时与程序平级。
/// 旧扁平安装仍按旧数据根处理，不把任意 exe 目录误认成安装根。
fn installed_layout_for(exe: &Path) -> Option<InstalledLayout> {
    let app_dir = exe.parent()?;
    if app_dir.file_name()?.to_str()? != "app" {
        return None;
    }
    let root = app_dir.parent()?;
    Some(InstalledLayout {
        data: root.join("data"),
        models: root.join("models"),
        runtime_envs: root.join("runtime-envs"),
    })
}

fn portable_layout_for(exe: &Path) -> Option<InstalledLayout> {
    let root = exe.parent()?;
    if !root.join(PORTABLE_MARKER_FILE_NAME).is_file() {
        return None;
    }
    Some(InstalledLayout {
        data: root.join("data"),
        models: root.join("models"),
        runtime_envs: root.join("runtime-envs"),
    })
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppPathsPayload {
    pub data_root: String,
    pub settings_dir: String,
    pub models_dir: String,
    pub outputs_dir: String,
    pub editor_projects_dir: String,
    pub logs_dir: String,
    pub temp_dir: String,
}

pub fn home_dir(app: &AppHandle) -> AppResult<PathBuf> {
    app.path()
        .home_dir()
        .map_err(|error| AppError::Worker(error.to_string()))
}

fn legacy_data_root_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(home_dir(app)?.join(DATA_ROOT_DIR_NAME))
}

fn installed_data_root_dir(app: &AppHandle) -> AppResult<PathBuf> {
    #[cfg(windows)]
    if let Some(layout) = installed_layout_for(&std::env::current_exe()?) {
        return Ok(layout.data);
    }
    legacy_data_root_dir(app)
}

fn development_data_root_dir() -> AppResult<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let project_root = manifest_dir
        .parent()
        .ok_or_else(|| AppError::Worker("failed to resolve project root".into()))?;
    Ok(project_root.join(DEVELOPMENT_DATA_ROOT_DIR_NAME))
}

/// 便携版判定：程序同目录存在标记文件时，数据放在 `<exe 目录>\\data`。
/// 标记名必须与打包脚本和 CI 一致，否则便携版会静默落回安装版的数据根。
pub(crate) fn portable_data_root_for(exe: &Path) -> Option<PathBuf> {
    let exe_dir = exe.parent()?;
    exe_dir
        .join(PORTABLE_MARKER_FILE_NAME)
        .is_file()
        .then(|| exe_dir.join(LOCAL_DATA_ROOT_DIR_NAME))
}

fn portable_data_root_dir() -> AppResult<Option<PathBuf>> {
    #[cfg(windows)]
    {
        let exe = std::env::current_exe()?;
        return Ok(portable_data_root_for(&exe));
    }
    #[allow(unreachable_code)]
    Ok(None)
}

fn is_cargo_profile_executable(exe: &Path, target_dir: &Path) -> bool {
    let Some(profile_dir) = exe.parent() else {
        return false;
    };
    let profile = profile_dir.file_name().and_then(|value| value.to_str());
    if !matches!(profile, Some("debug" | "release")) {
        return false;
    }
    let Some(profile_parent) = profile_dir.parent() else {
        return false;
    };
    // Native builds live at target/{debug,release}; cross-compiled artifacts add exactly one
    // target-triple directory. Bundle contents are deeper and must use packaged semantics even
    // while CI is inspecting them in target/**/bundle.
    profile_parent == target_dir || profile_parent.parent() == Some(target_dir)
}

pub fn is_development_executable() -> bool {
    let target_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target");
    std::env::current_exe()
        .ok()
        .and_then(|path| std::fs::canonicalize(path).ok())
        .zip(std::fs::canonicalize(target_dir).ok())
        .map(|(exe, target)| is_cargo_profile_executable(&exe, &target))
        .unwrap_or(false)
}

fn resolve_data_root(
    env_root: Option<PathBuf>,
    development_root: PathBuf,
    portable_root: Option<PathBuf>,
    legacy_root: PathBuf,
    is_development: bool,
) -> PathBuf {
    if let Some(root) = env_root {
        return root;
    }
    if is_development {
        return development_root;
    }
    if let Some(root) = portable_root {
        return root;
    }
    legacy_root
}

pub fn data_root_dir(app: &AppHandle) -> AppResult<PathBuf> {
    let env_root = explicit_data_root();
    Ok(resolve_data_root(
        env_root,
        development_data_root_dir()?,
        portable_data_root_dir()?,
        installed_data_root_dir(app)?,
        is_development_executable(),
    ))
}

fn env_path(names: &[&str]) -> Option<PathBuf> {
    names.iter().find_map(|name| {
        std::env::var_os(name)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    })
}

fn explicit_data_root() -> Option<PathBuf> {
    env_path(&[MEDIAFORGE_DATA_ROOT_ENV, "MEDIAFORGE_DATA_DIR", DATA_ROOT_ENV])
}

fn resolve_asset_root(
    explicit_asset: Option<PathBuf>,
    explicit_data: Option<PathBuf>,
    default_data: PathBuf,
    packaged_asset: Option<PathBuf>,
    name: &str,
) -> PathBuf {
    explicit_asset
        .or_else(|| explicit_data.map(|root| root.join(name)))
        .or(packaged_asset)
        .unwrap_or_else(|| default_data.join(name))
}

fn packaged_layout() -> AppResult<Option<InstalledLayout>> {
    if is_development_executable() {
        return Ok(None);
    }
    let exe = std::env::current_exe()?;
    #[cfg(windows)]
    if let Some(layout) = installed_layout_for(&exe) {
        return Ok(Some(layout));
    }
    Ok(portable_layout_for(&exe))
}

pub fn settings_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(data_root_dir(app)?.join("settings"))
}

pub fn models_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(resolve_asset_root(
        env_path(&["MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR"]),
        explicit_data_root(),
        data_root_dir(app)?,
        packaged_layout()?.map(|layout| layout.models),
        "models",
    ))
}

pub fn outputs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(data_root_dir(app)?.join("outputs"))
}

pub fn editor_projects_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(data_root_dir(app)?.join("editor-projects"))
}

pub fn logs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(data_root_dir(app)?.join("logs"))
}

pub fn temp_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(data_root_dir(app)?.join("temp"))
}

// Executable siblings and resource-specific fallbacks stay with their callers.
pub(crate) fn resource_roots(resource: &Path) -> [PathBuf; 3] {
    [
        resource.to_path_buf(),
        resource.join("_up_"),
        resource.join("resources"),
    ]
}

pub fn runtime_root_dir(app: &AppHandle) -> AppResult<PathBuf> {
    let resource = app.path().resource_dir().ok();
    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| AppError::Worker("failed to resolve executable directory".into()))?;
    Ok(runtime_root_from(resource.as_deref(), &exe_dir))
}

fn runtime_root_from(resource: Option<&Path>, exe_dir: &Path) -> PathBuf {
    let mut candidates: Vec<_> = resource
        .into_iter()
        .flat_map(resource_roots)
        .map(|root| root.join("python-runtime"))
        .collect();
    candidates.push(exe_dir.join("python-runtime"));
    candidates
        .into_iter()
        .find(|path| path.is_dir())
        .unwrap_or_else(|| exe_dir.join("python-runtime"))
}

pub fn bundled_runtime_envs_dir(app: &AppHandle) -> AppResult<Option<PathBuf>> {
    let resource = app.path().resource_dir().ok();
    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| AppError::Worker("failed to resolve executable directory".into()))?;
    Ok(bundled_runtime_envs_from(resource.as_deref(), &exe_dir))
}

fn bundled_runtime_envs_from(resource: Option<&Path>, exe_dir: &Path) -> Option<PathBuf> {
    let mut candidates: Vec<_> = resource
        .into_iter()
        .flat_map(resource_roots)
        .map(|root| root.join("python-runtime").join("runtime-envs"))
        .collect();
    candidates.push(exe_dir.join("python-runtime").join("runtime-envs"));
    candidates
        .into_iter()
        .find(|path| path.is_dir())
}

pub fn runtime_envs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(resolve_asset_root(
        env_path(&["MEDIAFORGE_RUNTIME_ENVS_DIR"]),
        explicit_data_root(),
        data_root_dir(app)?,
        packaged_layout()?.map(|layout| layout.runtime_envs),
        "runtime-envs",
    ))
}

pub fn active_runtime_file(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(runtime_envs_dir(app)?.join("active-runtime.json"))
}

/// pymss's registry of imported ("custom") models.
///
/// Kept under `settings/` rather than with the models themselves: the registry stores absolute
/// paths to files that usually live outside the model directory, so it is app state, not model
/// data, and must not be caught up in a model-directory migration.
pub fn user_models_file(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(settings_dir(app)?.join("user_models.json"))
}

pub fn ensure_app_directories(app: &AppHandle) -> AppResult<()> {
    for dir in [
        data_root_dir(app)?,
        settings_dir(app)?,
        models_dir(app)?,
        outputs_dir(app)?,
        editor_projects_dir(app)?,
        logs_dir(app)?,
        temp_dir(app)?,
    ] {
        std::fs::create_dir_all(dir)?;
    }
    Ok(())
}

pub fn app_paths_payload(app: &AppHandle) -> AppResult<AppPathsPayload> {
    ensure_app_directories(app)?;
    Ok(AppPathsPayload {
        data_root: data_root_dir(app)?.to_string_lossy().to_string(),
        settings_dir: settings_dir(app)?.to_string_lossy().to_string(),
        models_dir: models_dir(app)?.to_string_lossy().to_string(),
        outputs_dir: outputs_dir(app)?.to_string_lossy().to_string(),
        editor_projects_dir: editor_projects_dir(app)?.to_string_lossy().to_string(),
        logs_dir: logs_dir(app)?.to_string_lossy().to_string(),
        temp_dir: temp_dir(app)?.to_string_lossy().to_string(),
    })
}

fn store_file_name(name: &str) -> AppResult<&'static str> {
    match name {
        "app-settings" => Ok("app.json"),
        "task-history" => Err(AppError::Worker("task-history is unsupported; use the schema-2 job store; original file preserved".into())),
        "model-state" => Ok("model-cache.json"),
        "editor-ui" => Ok("editor-ui.json"),
        "audio-tools" => Ok("audio-tools.json"),
        "workflow-state" => Ok("workflows.json"),
        "media-workflows" => Ok("media-workflows.json"),
        "separate-state" => Ok("separate.json"),
        "update-state" => Ok("update.json"),
        _ => Err(AppError::Worker(format!("unknown app store: {name}"))),
    }
}

pub fn app_store_path(app: &AppHandle, name: &str) -> AppResult<PathBuf> {
    Ok(settings_dir(app)?.join(store_file_name(name)?))
}

fn validate_app_settings(data: &Value) -> AppResult<()> {
    let invalid = || AppError::Worker("unsupported app settings schema; original file preserved".into());
    let object = data.as_object().ok_or_else(invalid)?;
    // Every field is optional except the schema version; unknown fields are not.
    fn kind(value: &Value, expected: &str) -> bool {
        match expected {
            "string" => value.is_string(),
            "boolean" => value.is_boolean(),
            "number" => value.as_f64().is_some_and(f64::is_finite),
            "object" => value.is_object(),
            _ => false,
        }
    }
    fn one_of(value: &Value, choices: &[&str]) -> bool {
        value.as_str().is_some_and(|text| choices.contains(&text))
    }
    const THEME_MODES: &[&str] = &["system", "dark", "light"];
    const THEME_ACCENTS: &[&str] = &["blue", "pink", "sky", "teal"];
    const LOCALES: &[&str] = &["system", "zh-CN", "en"];
    const DOWNLOAD_METHODS: &[&str] = &["aria2c", "urllib"];
    const WAV_BITS: &[&str] = &["PCM_16", "PCM_24", "FLOAT"];
    const FLAC_BITS: &[&str] = &["PCM_16", "PCM_24"];
    const MP3_RATES: &[&str] = &["128k", "192k", "256k", "320k"];
    const M4A_CODECS: &[&str] = &["aac", "alac"];
    const PROXY_MODES: &[&str] = &["system", "none", "custom"];
    const UPDATE_CHANNELS: &[&str] = &["stable", "prerelease"];
    const UPDATE_SOURCES: &[&str] = &["auto", "github", "modelscope"];
    if object.get("version").and_then(Value::as_u64) != Some(1) {
        return Err(invalid());
    }
    const FIELDS: &[(&str, &str)] = &[
        ("themeMode", "string"), ("themeAccent", "string"), ("scaleFactor", "number"),
        ("locale", "string"), ("animationsEnabled", "boolean"), ("developerMode", "boolean"),
        ("modelDir", "string"), ("outputDir", "string"), ("defaultDevice", "string"),
        ("defaultFormat", "string"), ("downloadSource", "string"), ("downloadMethod", "string"),
        ("maxConcurrentSeparations", "number"), ("clearInputAfterSubmit", "boolean"),
        ("wavBitDepth", "string"), ("flacBitDepth", "string"), ("mp3BitRate", "string"),
        ("m4aBitRate", "string"), ("m4aCodec", "string"), ("startupOnboardingSeen", "boolean"),
        ("beginnerGuideInvitationSeen", "boolean"), ("beginnerGuideProgress", "object"),
        ("proxyMode", "string"), ("proxyUrl", "string"), ("proxyBypass", "string"),
        ("updateChannel", "string"), ("updateSource", "string"), ("updateEndpointOverride", "string"),
    ];
    let known: Vec<&str> = std::iter::once("version").chain(FIELDS.iter().map(|(name, _)| *name)).collect();
    if object.keys().any(|key| !known.contains(&key.as_str())) {
        return Err(invalid());
    }
    for (name, expected) in FIELDS {
        let Some(value) = object.get(*name) else { continue };
        if !kind(value, expected) {
            return Err(invalid());
        }
    }
    for (name, choices) in [
        ("themeMode", THEME_MODES), ("themeAccent", THEME_ACCENTS), ("locale", LOCALES),
        ("downloadMethod", DOWNLOAD_METHODS), ("wavBitDepth", WAV_BITS), ("flacBitDepth", FLAC_BITS),
        ("mp3BitRate", MP3_RATES), ("m4aCodec", M4A_CODECS), ("proxyMode", PROXY_MODES),
        ("updateChannel", UPDATE_CHANNELS), ("updateSource", UPDATE_SOURCES),
    ] {
        if let Some(value) = object.get(name) {
            if !one_of(value, choices) { return Err(invalid()); }
        }
    }
    if let Some(value) = object.get("maxConcurrentSeparations") {
        let limit = value.as_u64().ok_or_else(invalid)?;
        let maximum = crate::python::compute_worker_slots()? as u64;
        if !(1..=maximum).contains(&limit) {
            return Err(invalid());
        }
    }
    Ok(())
}

/// Reject a settings file we cannot interpret, without touching its bytes.
///
/// The reason is surfaced to the UI so the user can choose an explicit
/// recovery; no caller may silently replace the preserved file with defaults.
fn settings_bytes_rejection(bytes: &[u8]) -> Option<String> {
    let text = match std::str::from_utf8(bytes) {
        Ok(text) => text,
        Err(_) => return Some("settings file is not valid UTF-8".into()),
    };
    let value = match serde_json::from_str::<Value>(text) {
        Ok(value) => value,
        Err(error) => return Some(format!("settings file is not valid JSON: {error}")),
    };
    validate_app_settings(&value).err().map(|error| error.to_string())
}

/// `Some(reason)` means this path holds a file the current app cannot load.
pub(crate) fn app_settings_rejection(path: &Path) -> AppResult<Option<String>> {
    match std::fs::read(path) {
        Ok(bytes) => Ok(settings_bytes_rejection(&bytes)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// Explicit recovery: keep the exact original bytes in a new backup file, then
/// remove the rejected file so the next load starts from documented defaults.
///
/// Returns `None` when there is nothing to recover (missing or already valid).
pub(crate) fn recover_app_settings_file(path: &Path) -> AppResult<Option<PathBuf>> {
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if settings_bytes_rejection(&bytes).is_none() {
        return Ok(None);
    }
    let parent = path
        .parent()
        .ok_or_else(|| AppError::Worker("settings file has no parent directory".into()))?;
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("app");
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or(0);
    for attempt in 0..1000u32 {
        let name = if attempt == 0 {
            format!("{stem}.rejected-{stamp}.json")
        } else {
            format!("{stem}.rejected-{stamp}-{attempt}.json")
        };
        let backup = parent.join(name);
        let mut file = match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&backup)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        };
        let written = (|| -> AppResult<()> {
            use std::io::Write;
            file.write_all(&bytes)?;
            file.sync_all()?;
            Ok(())
        })();
        drop(file);
        if let Err(error) = written {
            let _ = std::fs::remove_file(&backup);
            return Err(error);
        }
        // The user's bytes are the point of the backup; never drop the original
        // unless the copy is byte-identical.
        if std::fs::read(&backup)? != bytes {
            let _ = std::fs::remove_file(&backup);
            return Err(AppError::Worker(
                "settings backup does not match the original bytes; original preserved".into(),
            ));
        }
        std::fs::remove_file(path)?;
        return Ok(Some(backup));
    }
    Err(AppError::Worker(
        "could not allocate a settings backup file name; original preserved".into(),
    ))
}

pub fn read_app_store(app: &AppHandle, name: &str) -> AppResult<Value> {
    ensure_app_directories(app)?;
    let path = app_store_path(app, name)?;
    if !path.is_file() {
        return Ok(Value::Null);
    }
    let content = std::fs::read_to_string(path)?;
    let value = serde_json::from_str::<Value>(&content)?;
    if name == "media-workflows" {
        if !value.is_object() { return Err(AppError::Worker("invalid media workflow store".into())); }
        return crate::media_workflows::normalize_state(value);
    }
    if name == "workflow-state" { validate_workflow_state(&value)?; }
    if name == "app-settings" { validate_app_settings(&value)?; }
    Ok(value)
}

/// A file the current schema cannot load must not be replaced by defaults.
fn ensure_app_settings_replaceable(path: &Path) -> AppResult<()> {
    match app_settings_rejection(path)? {
        None => Ok(()),
        Some(reason) => Err(AppError::Worker(format!(
            "app settings on disk cannot be replaced implicitly ({reason}); \
             keep the original file and use the explicit settings recovery"
        ))),
    }
}

pub fn write_app_store(app: &AppHandle, name: &str, data: &Value) -> AppResult<()> {
    if name == "workflow-state" { validate_workflow_state(data)?; }
    if name == "app-settings" { validate_app_settings(data)?; }
    ensure_app_directories(app)?;
    let path = app_store_path(app, name)?;
    if name == "app-settings" {
        // A file the current schema cannot load is evidence of an older/newer
        // install or damage. Replacing it with defaults would destroy the only
        // copy of the user's settings, so only explicit recovery may proceed.
        ensure_app_settings_replaceable(&path)?;
    }
    write_json_file(&path, data)
}

pub(crate) fn write_json_file(path: &Path, data: &Value) -> AppResult<()> {
    let _write_guard = JSON_WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    write_json_file_locked(path, data)
}

pub(crate) fn write_editor_project_file(data_root: &Path, path: &Path, data: &Value) -> AppResult<()> {
    write_editor_project_file_with_replace(data_root, path, data, replace_file)
}

fn write_editor_project_file_with_replace(
    data_root: &Path,
    path: &Path,
    data: &Value,
    replace: impl FnOnce(&Path, &Path) -> AppResult<()>,
) -> AppResult<()> {
    let _write_guard = JSON_WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    validate_editor_project(data)?;
    let id = data.get("id").and_then(Value::as_str)
        .ok_or_else(|| AppError::Worker("missing editor project id; original file preserved".into()))?;
    crate::commands::app::editor::validate_editor_project_id(id)?;
    let expected = data_root.join("editor-projects").join(id).join("project.json");
    if path != expected {
        return Err(AppError::Worker("editor project path does not match its identity; original file preserved".into()));
    }
    crate::python::result_jobs::plain_path(data_root).map_err(|reason| AppError::Worker(reason.into()))?;
    crate::python::result_jobs::plain_path(path).map_err(|reason| AppError::Worker(reason.into()))?;
    // Save and native deletion share JSON -> asset ordering. The OS lease also
    // excludes saves from another process until the atomic replacement finishes.
    let _asset = crate::python::library_references::AssetLease::acquire(data_root, Duration::from_secs(5))?;
    crate::python::result_jobs::plain_path(path).map_err(|reason| AppError::Worker(reason.into()))?;
    let previous = match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => {
            let previous = crate::python::result_jobs::parse_reference_json(&std::fs::read_to_string(path)?)?;
            validate_editor_project(&previous)?;
            if previous.get("id").and_then(Value::as_str) != Some(id) {
                return Err(AppError::Worker("stored editor project identity does not match; original file preserved".into()));
            }
            Some(previous)
        }
        Ok(_) => return Err(AppError::Worker("editor project must be a regular file; original file preserved".into())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error.into()),
    };
    crate::python::editor_references::validate_managed_reference_targets(data_root, data, previous.as_ref())?;
    write_json_file_with_replace(path, data, replace)
}

/// Hold the same guard as editor saves while inspecting references and recycling
/// result files. The callback must not call another JSON-writing storage API.
pub(crate) fn with_editor_project_guard<T>(action: impl FnOnce() -> AppResult<T>) -> AppResult<T> {
    let _guard = JSON_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    action()
}

pub(crate) fn validate_editor_project(project: &Value) -> AppResult<()> {
    let valid = project.get("version").and_then(Value::as_u64) == Some(4)
        && project.get("sources").is_some_and(Value::is_array)
        && project.get("tracks").and_then(Value::as_array).is_some_and(|tracks| tracks.iter().all(|track| {
            track.is_object() && track.get("effects").is_none()
                && track.get("clips").is_some_and(Value::is_array)
                && track.get("autoName").is_some_and(Value::is_boolean)
        }));
    if !valid { return Err(AppError::Worker("unsupported editor project schema; original file preserved".into())); }
    Ok(())
}

fn write_json_file_locked(path: &Path, data: &Value) -> AppResult<()> {
    write_json_file_with_replace(path, data, replace_file)
}

fn write_json_file_with_replace(
    path: &Path,
    data: &Value,
    replace: impl FnOnce(&Path, &Path) -> AppResult<()>,
) -> AppResult<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = serde_json::to_vec_pretty(data)?;
    let sequence = JSON_WRITE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("store.json");
    let temporary = path.with_file_name(format!(
        ".{file_name}.{}.{}.tmp",
        std::process::id(),
        sequence,
    ));
    let result = (|| -> AppResult<()> {
        let mut file = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)?;
        file.write_all(&content)?;
        file.sync_all()?;
        drop(file);
        replace(&temporary, path)
    })();
    #[cfg(windows)]
    if let Err(AppError::Io(error)) = &result {
        // ReplaceFileW can remove/rename the old file before reporting these errors.
        // The complete, synced replacement is then the only copy of the latest save.
        if matches!(error.raw_os_error(), Some(1176 | 1177)) {
            return Err(AppError::Worker(format!(
                "Failed to save {}: {error}. Recovery file retained at {}",
                path.display(),
                temporary.display(),
            )));
        }
    }
    if result.is_ok() {
        bump_modified_time(path);
    }
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

/// ReplaceFileW may keep the replaced file's creation time, and two writes can
/// land in the same timestamp tick; a job cache keyed by (len, mtime, ctime)
/// would then miss the update. Give every successful replacement a strictly
/// increasing mtime so the stamp always changes.
fn bump_modified_time(path: &Path) {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    static LAST: AtomicU64 = AtomicU64::new(0);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos() as u64)
        .unwrap_or(0);
    let mut previous = LAST.load(Ordering::Relaxed);
    let stamp = loop {
        let next = now.max(previous.saturating_add(1));
        match LAST.compare_exchange_weak(previous, next, Ordering::Relaxed, Ordering::Relaxed) {
            Ok(_) => break next,
            Err(actual) => previous = actual,
        }
    };
    if let Ok(file) = std::fs::OpenOptions::new().write(true).open(path) {
        let _ = file.set_modified(UNIX_EPOCH + Duration::from_nanos(stamp));
    }
}

pub fn mutate_workflow_store(app: &AppHandle, mutation: &Value) -> AppResult<Value> {
    ensure_app_directories(app)?;
    mutate_workflow_file(&app_store_path(app, "workflow-state")?, mutation)
}

pub(crate) fn mutate_workflow_file(path: &Path, mutation: &Value) -> AppResult<Value> {
    let _write_guard = JSON_WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());

    let state = if path.is_file() {
        let content = std::fs::read_to_string(&path)?;
        serde_json::from_str::<Value>(&content)?
    } else {
        serde_json::json!({ "version": 1, "workflows": [], "selectedWorkflowId": "" })
    };
    let response = apply_workflow_mutation(state, mutation)?;
    if response.get("conflict").is_none() {
        let next_state = response
            .get("state")
            .ok_or_else(|| AppError::Worker("workflow mutation returned no state".into()))?;
        write_json_file_locked(&path, next_state)?;
    }
    Ok(response)
}

pub(crate) fn mutate_media_workflow_file(path: &Path, mutation: &Value) -> AppResult<Value> {
    let _guard = JSON_WRITE_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let state = match std::fs::read_to_string(path) {
        Ok(content) => {
            let value = serde_json::from_str::<Value>(&content)?;
            if !value.is_object() { return Err(AppError::Worker("invalid media workflow store".into())); }
            value
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Value::Null,
        Err(error) => return Err(error.into()),
    };
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis().min(u64::MAX as u128) as u64;
    let response = crate::media_workflows::apply_mutation(state, mutation, now)?;
    if response.get("conflict").is_none() { write_json_file_locked(path, &response["state"])?; }
    Ok(response)
}

pub fn mutate_media_workflows(app: &AppHandle, mutation: &Value) -> AppResult<Value> {
    mutate_media_workflow_file(&app_store_path(app, "media-workflows")?, mutation)
}

fn validate_workflow_state(state: &Value) -> AppResult<()> {
    let invalid = || AppError::Worker("unsupported workflow-state schema; original file preserved".into());
    if state.get("version").and_then(Value::as_u64) != Some(1) { return Err(invalid()); }
    let entries = state.get("workflows").and_then(Value::as_array).ok_or_else(invalid)?;
    let selected = state.get("selectedWorkflowId").and_then(Value::as_str).ok_or_else(invalid)?;
    let mut ids = std::collections::HashSet::new();
    for entry in entries {
        let id = entry.get("id").and_then(Value::as_str).filter(|id| !id.trim().is_empty()).ok_or_else(invalid)?;
        if !ids.insert(id) || entry.get("formatVersion").and_then(Value::as_u64) != Some(1) {
            return Err(invalid());
        }
    }
    if !selected.is_empty() && !ids.contains(selected) { return Err(invalid()); }
    Ok(())
}

fn apply_workflow_mutation(mut state: Value, mutation: &Value) -> AppResult<Value> {
    validate_workflow_state(&state)?;
    if mutation.get("legacyIndex").is_some() || mutation.get("legacyEntry").is_some() {
        return Err(AppError::Worker("unsupported workflow mutation schema".into()));
    }
    let state_object = state
        .as_object_mut()
        .ok_or_else(|| AppError::Worker("workflow store must be an object".into()))?;
    let selected_before = state_object
        .get("selectedWorkflowId")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let workflows = state_object.get_mut("workflows").ok_or_else(|| AppError::Worker("workflow list is missing".into()))?;
    let workflow_list = workflows
        .as_array_mut()
        .ok_or_else(|| AppError::Worker("workflow list must be an array".into()))?;
    let action = mutation.get("action").and_then(Value::as_str).unwrap_or("");
    let mut conflict = None;

    match action {
        "upsert" => {
            let mut entry = mutation
                .get("entry")
                .cloned()
                .filter(Value::is_object)
                .ok_or_else(|| AppError::Worker("workflow entry is required".into()))?;
            if entry.get("formatVersion").and_then(Value::as_u64) != Some(1) {
                return Err(AppError::Worker("unsupported workflow record schema".into()));
            }
            let workflow_id = entry
                .get("id")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| AppError::Worker("workflow id is required".into()))?
                .to_string();
            let existing_index = workflow_list.iter().position(|item| {
                item.get("id").and_then(Value::as_str) == Some(workflow_id.as_str())
            });
            let actual_updated_at = existing_index
                .and_then(|index| workflow_list[index].get("updatedAt"))
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let expected_updated_at = mutation.get("expectedUpdatedAt").and_then(Value::as_u64);
            let force = mutation
                .get("force")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            if !force
                && expected_updated_at.is_some()
                && expected_updated_at.unwrap_or(0) != actual_updated_at
            {
                conflict = Some(serde_json::json!({
                    "workflowId": workflow_id,
                    "expectedUpdatedAt": expected_updated_at.unwrap_or(0),
                    "actualUpdatedAt": actual_updated_at,
                }));
            } else {
                if let Some(index) = existing_index {
                    let created_at = workflow_list[index]
                        .get("createdAt")
                        .and_then(Value::as_u64)
                        .unwrap_or(0);
                    if created_at > 0 {
                        entry["createdAt"] = Value::from(created_at);
                    }
                }
                let requested_updated_at =
                    entry.get("updatedAt").and_then(Value::as_u64).unwrap_or(0);
                entry["updatedAt"] =
                    Value::from(requested_updated_at.max(actual_updated_at.saturating_add(1)));
                if let Some(index) = existing_index {
                    workflow_list[index] = entry;
                } else {
                    workflow_list.push(entry);
                }
                workflow_list.sort_by(|left, right| {
                    let left_value = left.get("updatedAt").and_then(Value::as_u64).unwrap_or(0);
                    let right_value = right.get("updatedAt").and_then(Value::as_u64).unwrap_or(0);
                    right_value.cmp(&left_value)
                });
                state_object.insert("selectedWorkflowId".into(), Value::String(workflow_id));
            }
        }
        "delete" => {
            let workflow_id = mutation
                .get("workflowId")
                .and_then(Value::as_str)
                .unwrap_or("");
            let index = workflow_list
                .iter()
                .position(|item| item.get("id").and_then(Value::as_str) == Some(workflow_id));
            let actual_updated_at = index
                .and_then(|index| workflow_list[index].get("updatedAt"))
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let expected_updated_at = mutation.get("expectedUpdatedAt").and_then(Value::as_u64);
            if expected_updated_at.is_some() && expected_updated_at.unwrap_or(0) != actual_updated_at
            {
                conflict = Some(serde_json::json!({
                    "workflowId": workflow_id,
                    "expectedUpdatedAt": expected_updated_at.unwrap_or(0),
                    "actualUpdatedAt": actual_updated_at,
                }));
            } else {
                let removed_selected = index
                    .map(|index| workflow_list.remove(index))
                    .and_then(|item| {
                        item.get("id")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .is_some_and(|removed_id| removed_id == selected_before);
                if selected_before == workflow_id || removed_selected {
                    let replacement = workflow_list
                        .first()
                        .and_then(|item| item.get("id"))
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    state_object.insert("selectedWorkflowId".into(), Value::String(replacement));
                }
            }
        }
        "select" => {
            let workflow_id = mutation
                .get("workflowId")
                .and_then(Value::as_str)
                .unwrap_or("");
            let existing_index = workflow_list
                .iter()
                .position(|item| item.get("id").and_then(Value::as_str) == Some(workflow_id));
            if !workflow_id.is_empty() && existing_index.is_none() {
                return Err(AppError::Worker("workflow id is missing".into()));
            }
            state_object.insert("selectedWorkflowId".into(), Value::String(workflow_id.to_string()));
        }

        _ => {
            return Err(AppError::Worker(format!(
                "unknown workflow mutation: {action}"
            )))
        }
    }

    if let Some(conflict) = conflict {
        return Ok(serde_json::json!({ "state": state, "conflict": conflict }));
    }
    Ok(serde_json::json!({ "state": state }))
}

#[cfg(windows)]
fn replace_file(temporary: &Path, destination: &Path) -> AppResult<()> {
    if !destination.exists() {
        std::fs::rename(temporary, destination)?;
        return Ok(());
    }
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let temporary = temporary
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    retry_file_replacement(|| {
        let replaced = unsafe {
            ReplaceFileW(
                destination.as_ptr(),
                temporary.as_ptr(),
                std::ptr::null(),
                0,
                std::ptr::null(),
                std::ptr::null(),
            )
        };
        if replaced != 0 {
            Ok(())
        } else {
            Err(std::io::Error::last_os_error())
        }
    })?;
    Ok(())
}

#[cfg(windows)]
fn retry_file_replacement(mut replace: impl FnMut() -> std::io::Result<()>) -> std::io::Result<()> {
    for attempt in 0..6 {
        match replace() {
            Ok(()) => return Ok(()),
            Err(error) => {
                // 1175 (ERROR_UNABLE_TO_REMOVE_REPLACED) leaves both names intact, so it
                // is safe to retry just like sharing violations. Never retry 1176/1177:
                // those indicate a partially completed replacement.
                let retryable = matches!(error.raw_os_error(), Some(5 | 32 | 33 | 1175));
                if !retryable || attempt == 5 {
                    return Err(error);
                }
            }
        }
        std::thread::sleep(Duration::from_millis(40 * (attempt + 1) as u64));
    }
    unreachable!("ReplaceFileW retry loop must return")
}

#[cfg(not(windows))]
fn replace_file(temporary: &Path, destination: &Path) -> AppResult<()> {
    std::fs::rename(temporary, destination)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        app_settings_rejection, apply_workflow_mutation, ensure_app_settings_replaceable,
        recover_app_settings_file, resolve_data_root, settings_bytes_rejection, store_file_name,
        validate_app_settings, write_json_file, JSON_WRITE_SEQUENCE,
    };
    use serde_json::{json, Value};
    use std::path::PathBuf;
    use std::sync::atomic::Ordering;

    fn path(name: &str) -> PathBuf {
        PathBuf::from(name)
    }

    fn current_workflow_state(mut state: Value) -> Value {
        state["version"]=json!(1);
        if let Some(entries)=state["workflows"].as_array_mut() { for entry in entries { entry["formatVersion"]=json!(1); } }
        state
    }

    fn current_workflow_mutation(state: Value, mutation: &Value) -> crate::error::AppResult<Value> {
        let state = current_workflow_state(state);
        let mut mutation=mutation.clone();
        if mutation.get("entry").is_some() { mutation["entry"]["formatVersion"]=json!(1); }
        apply_workflow_mutation(state,&mutation)
    }

    #[test]
    fn app_settings_require_the_current_version_and_reject_unknown_fields() {
        validate_app_settings(&json!({"version": 1, "themeMode": "dark"})).unwrap();
        for value in [
            json!(null), json!([]), json!({}), json!({"version": 0}), json!({"version": 2}),
            json!({"version": 1, "futureSetting": true}),
        ] {
            assert!(validate_app_settings(&value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn rejected_settings_are_preserved_and_only_explicit_recovery_moves_them() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("app.json");
        // Every rejection reason the current schema defines.
        for (name, content) in [
            ("missing version", br#"{"themeMode":"dark"}"#.as_slice()),
            ("zero version", br#"{"version":0}"#.as_slice()),
            ("future version", br#"{"version":2}"#.as_slice()),
            ("unknown field", br#"{"version":1,"futureSetting":true}"#.as_slice()),
            ("bad enum", br#"{"version":1,"themeMode":"neon"}"#.as_slice()),
            ("broken json", br#"{"version":1,"#.as_slice()),
        ] {
            assert!(
                settings_bytes_rejection(content).is_some(),
                "{name} should be rejected"
            );
        }
        assert!(settings_bytes_rejection(br#"{"version":1,"themeMode":"dark"}"#).is_none());

        let invalid = br#"{"version":0,"themeMode":"light"}"#.to_vec();
        std::fs::write(&file, &invalid).unwrap();
        assert!(app_settings_rejection(&file).unwrap().is_some());
        // Reading a rejected file never rewrites it.
        assert_eq!(std::fs::read(&file).unwrap(), invalid);
        // A normal save must refuse before touching the preserved file.
        let guard = ensure_app_settings_replaceable(&file);
        assert!(guard.is_err(), "a rejected file must block implicit replacement");
        assert_eq!(std::fs::read(&file).unwrap(), invalid);

        let backup = recover_app_settings_file(&file).unwrap().expect("a backup is created");
        assert_eq!(std::fs::read(&backup).unwrap(), invalid, "the backup keeps the original bytes");
        assert!(!file.exists(), "the rejected file is removed only after the backup is verified");
        assert_eq!(backup.parent(), Some(root.path()));
        assert!(backup.file_name().unwrap().to_string_lossy().starts_with("app.rejected-"));

        // Nothing is left to recover, and a valid file is never moved.
        assert!(recover_app_settings_file(&file).unwrap().is_none());
        std::fs::write(&file, br#"{"version":1}"#).unwrap();
        assert!(recover_app_settings_file(&file).unwrap().is_none());
        assert!(file.exists());

        // Repeated recoveries never overwrite an earlier backup.
        std::fs::write(&file, &invalid).unwrap();
        let second = recover_app_settings_file(&file).unwrap().unwrap();
        assert_ne!(second, backup);
        assert_eq!(std::fs::read(&second).unwrap(), invalid);
    }

    #[test]
    fn media_workflow_file_transactions_merge_parallel_windows() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("media-workflows.json");
        let handles = (0..8).map(|index| {
            let path = path.clone();
            std::thread::spawn(move || super::mutate_media_workflow_file(&path, &json!({
                "action":"upsert","id":format!("preset_{index}"),"name":format!("Preset {index}"),
                "definition":{"version":1,"modality":"image","steps":[{"capability":"upscale","bindings":{"upscaler":"media:real-esrgan-x2"},"params":{"scale":2}}]}
            })).unwrap())
        }).collect::<Vec<_>>();
        for handle in handles { handle.join().unwrap(); }
        let stored: serde_json::Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored["presets"].as_array().unwrap().len(), 8);
        let revision = stored["presets"][0]["updatedAt"].as_u64().unwrap();
        let id = stored["presets"][0]["id"].as_str().unwrap();
        let original = std::fs::read(&path).unwrap();
        let conflict = super::mutate_media_workflow_file(&path, &json!({"action":"delete","id":id,"expectedUpdatedAt":revision - 1})).unwrap();
        assert!(conflict.get("conflict").is_some());
        assert_eq!(std::fs::read(&path).unwrap(), original);
    }

    #[test]
    fn media_workflow_file_corruption_never_gets_overwritten() {
        let root = tempfile::tempdir().unwrap(); let path = root.path().join("media-workflows.json");
        std::fs::write(&path, b"broken-json").unwrap();
        assert!(super::mutate_media_workflow_file(&path, &json!({"action":"delete","id":"preset","expectedUpdatedAt":1})).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"broken-json");
        std::fs::write(&path, b"null").unwrap();
        assert!(super::mutate_media_workflow_file(&path, &json!({"action":"delete","id":"preset","expectedUpdatedAt":1})).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"null");
        assert_eq!(store_file_name("media-workflows").unwrap(), "media-workflows.json");
    }

    #[test]
    fn workflow_mutations_merge_different_editor_saves_into_the_latest_state() {
        let initial = json!({
            "workflows": [
                { "id": "first", "name": "First", "createdAt": 1, "updatedAt": 10 },
                { "id": "second", "name": "Second", "createdAt": 2, "updatedAt": 10 },
            ],
            "selectedWorkflowId": "first",
        });
        let first = current_workflow_mutation(
            initial,
            &json!({
                "action": "upsert",
                "entry": { "id": "first", "name": "First changed", "createdAt": 1, "updatedAt": 20 },
                "expectedUpdatedAt": 10,
            }),
        )
        .unwrap();
        let second = current_workflow_mutation(
            first["state"].clone(),
            &json!({
                "action": "upsert",
                "entry": { "id": "second", "name": "Second changed", "createdAt": 2, "updatedAt": 21 },
                "expectedUpdatedAt": 10,
            }),
        )
        .unwrap();
        let workflows = second["state"]["workflows"].as_array().unwrap();
        assert_eq!(workflows.len(), 2);
        assert!(workflows
            .iter()
            .any(|item| item["id"] == "first" && item["name"] == "First changed"));
        assert!(workflows
            .iter()
            .any(|item| item["id"] == "second" && item["name"] == "Second changed"));
    }

    #[test]
    fn workflow_mutation_reports_a_real_persisted_revision_conflict() {
        let state = json!({
            "workflows": [{ "id": "first", "name": "Remote", "createdAt": 1, "updatedAt": 20 }],
            "selectedWorkflowId": "first",
        });
        let response = current_workflow_mutation(
            state.clone(),
            &json!({
                "action": "upsert",
                "entry": { "id": "first", "name": "Stale", "createdAt": 1, "updatedAt": 21 },
                "expectedUpdatedAt": 10,
            }),
        )
        .unwrap();
        assert_eq!(response["conflict"]["actualUpdatedAt"], 20);
        assert_eq!(response["state"], current_workflow_state(state));
    }

    #[test]
    fn workflow_mutation_can_clear_the_selected_workflow() {
        let state = json!({
            "workflows": [{ "id": "first", "name": "First", "createdAt": 1, "updatedAt": 10 }],
            "selectedWorkflowId": "first",
        });
        let response = current_workflow_mutation(
            state,
            &json!({ "action": "select", "workflowId": "" }),
        )
        .unwrap();
        assert_eq!(response["state"]["selectedWorkflowId"], "");
        assert_eq!(response["state"]["workflows"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn incompatible_workflow_files_are_never_migrated_or_overwritten() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("workflows.json");
        for state in [
            json!({"workflows":[],"selectedWorkflowId":""}),
            json!({"version":2,"workflows":[],"selectedWorkflowId":""}),
            json!({"version":1,"workflows":[{"name":"missing id"}],"selectedWorkflowId":""}),
        ] {
            let raw = serde_json::to_vec(&state).unwrap();
            std::fs::write(&path,&raw).unwrap();
            assert!(super::mutate_workflow_file(&path,&json!({"action":"select","workflowId":""})).is_err());
            assert_eq!(std::fs::read(&path).unwrap(),raw);
        }
        let state = json!({"version":1,"workflows":[],"selectedWorkflowId":""});
        assert!(apply_workflow_mutation(state,&json!({"action":"select","legacyIndex":0})).is_err());
    }

    #[test]
    fn stale_delete_does_not_remove_a_newer_workflow_revision() {
        let state = json!({
            "workflows": [{
                "id": "workflow",
                "name": "Updated elsewhere",
                "definition": {},
                "createdAt": 1,
                "updatedAt": 20,
            }],
            "selectedWorkflowId": "workflow",
        });
        let response = current_workflow_mutation(
            state.clone(),
            &json!({
                "action": "delete",
                "workflowId": "workflow",
                "expectedUpdatedAt": 10,
            }),
        )
        .unwrap();
        assert_eq!(response["conflict"]["actualUpdatedAt"], 20);
        assert_eq!(response["state"], current_workflow_state(state));
    }

    struct ResourceFixture(PathBuf);

    impl ResourceFixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "pymss-resource-layout-{}-{}",
                std::process::id(),
                JSON_WRITE_SEQUENCE.fetch_add(1, Ordering::Relaxed),
            ));
            std::fs::create_dir(&root).unwrap();
            Self(root)
        }
    }

    impl Drop for ResourceFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn runtime_layouts_keep_resource_up_resources_and_exe_priority() {
        let fixture = ResourceFixture::new();
        let resource = fixture.0.join("Pymss.app/Contents/Resources");
        let exe = fixture.0.join("Pymss.app/Contents/MacOS");
        let candidates = [
            resource.join("python-runtime"),
            resource.join("_up_/python-runtime"),
            resource.join("resources/python-runtime"),
            exe.join("python-runtime"),
        ];
        for candidate in candidates.iter().rev() {
            std::fs::create_dir_all(candidate).unwrap();
            assert_eq!(super::runtime_root_from(Some(&resource), &exe), *candidate);
            let envs = candidate.join("runtime-envs");
            std::fs::create_dir_all(&envs).unwrap();
            assert_eq!(super::bundled_runtime_envs_from(Some(&resource), &exe), Some(envs));
        }
    }

    #[test]
    fn bundled_env_discovery_does_not_assume_the_first_runtime_root_contains_envs() {
        let fixture = ResourceFixture::new();
        let resource = fixture.0.join("resources");
        let exe = fixture.0.join("portable");
        std::fs::create_dir_all(resource.join("python-runtime")).unwrap();
        let envs = exe.join("python-runtime/runtime-envs");
        std::fs::create_dir_all(&envs).unwrap();
        assert_eq!(super::runtime_root_from(Some(&resource), &exe), resource.join("python-runtime"));
        assert_eq!(super::bundled_runtime_envs_from(Some(&resource), &exe), Some(envs));
    }

    #[test]
    fn missing_runtime_root_has_a_fixed_fallback_but_bundled_envs_are_optional() {
        let fixture = ResourceFixture::new();
        let resource = fixture.0.join("resources");
        let exe = fixture.0.join("installer");
        for resource in [Some(resource.as_path()), None] {
            assert_eq!(super::runtime_root_from(resource, &exe), exe.join("python-runtime"));
            assert_eq!(super::bundled_runtime_envs_from(resource, &exe), None);
        }
        assert!(!exe.exists());
    }

    #[test]
    fn runtime_directory_selection_ignores_files_and_accepts_missing_resource_api() {
        let fixture = ResourceFixture::new();
        let resource = fixture.0.join("resources");
        let exe = fixture.0.join("portable");
        std::fs::create_dir_all(&resource).unwrap();
        std::fs::write(resource.join("python-runtime"), b"not a directory").unwrap();
        let envs = exe.join("python-runtime/runtime-envs");
        std::fs::create_dir_all(&envs).unwrap();
        for resource in [Some(resource.as_path()), None, Some(exe.as_path())] {
            assert_eq!(super::runtime_root_from(resource, &exe), exe.join("python-runtime"));
            assert_eq!(super::bundled_runtime_envs_from(resource, &exe), Some(envs.clone()));
        }
    }

    #[test]
    fn development_detection_does_not_treat_packaged_bundle_paths_as_a_checkout() {
        let target = path("workspace/src-tauri/target");
        for relative in ["debug/pymss.exe", "release/pymss.exe", "aarch64-apple-darwin/release/pymss"] {
            assert!(super::is_cargo_profile_executable(&target.join(relative), &target));
        }
        for relative in [
            "release/bundle/Pymss.app/Contents/MacOS/pymss",
            "release/bundle/portable/pymss.exe",
            "debug/deps/pymss.exe",
            "unrelated/pymss.exe",
        ] {
            assert!(!super::is_cargo_profile_executable(&target.join(relative), &target));
        }
    }

    #[test]
    fn separate_state_has_a_store_file() {
        assert_eq!(store_file_name("separate-state").unwrap(), "separate.json");
    }

    #[test]
    fn audio_tools_has_a_store_file() {
        assert_eq!(store_file_name("audio-tools").unwrap(), "audio-tools.json");
    }

    #[test]
    fn update_state_has_a_store_file() {
        assert_eq!(store_file_name("update-state").unwrap(), "update.json");
    }

    #[test]
    fn json_writes_replace_existing_content_without_leaving_temporary_files() {
        let root = std::env::temp_dir().join(format!(
            "pymss-storage-test-{}-{}",
            std::process::id(),
            JSON_WRITE_SEQUENCE.fetch_add(1, Ordering::Relaxed),
        ));
        let path = root.join("settings.json");
        write_json_file(&path, &json!({ "value": 1 })).unwrap();
        write_json_file(&path, &json!({ "value": 2 })).unwrap();

        let stored: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored, json!({ "value": 2 }));
        assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unsupported_editor_files_are_preserved_without_upgrade_backups() {
        let root=tempfile::tempdir().unwrap(); let path=root.path().join("editor-projects/p/project.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        for version in [None,Some(2),Some(3),Some(5)] {
            let mut old=json!({"id":"p","sources":[],"tracks":[]});
            if let Some(version)=version { old["version"]=json!(version); }
            let raw=serde_json::to_vec(&old).unwrap(); std::fs::write(&path,&raw).unwrap();
            assert!(super::write_editor_project_file(root.path(),&path,&current).is_err());
            assert_eq!(std::fs::read(&path).unwrap(),raw);
            assert!(!path.parent().unwrap().join("project.pre-plugins.json").exists());
        }
    }

    #[test]
    fn current_editor_writes_replace_only_the_current_project() {
        let root=tempfile::tempdir().unwrap(); let path=root.path().join("editor-projects/p/project.json");
        for revision in 1..=3 {
            super::write_editor_project_file(root.path(),&path,&json!({"id":"p","version":4,"sources":[],"tracks":[],"revision":revision})).unwrap();
        }
        let saved:Value=serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved["revision"],3);
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(),1);
    }

    #[test]
    fn editor_save_rejects_duplicate_or_mismatched_previous_identity_without_rewriting() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/p/project.json");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        for raw in [
            r#"{"id":"other","id":"p","version":4,"sources":[],"tracks":[]}"#,
            r#"{"id":"p","version":4,"sources":[{"path":"keep.wav","path":"hide.wav"}],"tracks":[]}"#,
            r#"{"id":"other","version":4,"sources":[],"tracks":[]}"#,
        ] {
            std::fs::write(&path,raw).unwrap();
            assert!(super::write_editor_project_file(root.path(),&path,&current).is_err());
            assert_eq!(std::fs::read_to_string(&path).unwrap(),raw);
            assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(),1);
        }
    }

    #[test]
    fn editor_save_requires_the_exact_profile_project_and_identity() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/p/project.json");
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        super::write_editor_project_file(root.path(),&path,&current).unwrap();
        let before=std::fs::read(&path).unwrap();
        for invalid in [json!({"version":4,"sources":[],"tracks":[]}),
            json!({"id":"../outside","version":4,"sources":[],"tracks":[]}),
            json!({"id":"other","version":4,"sources":[],"tracks":[]})] {
            assert!(super::write_editor_project_file(root.path(),&path,&invalid).is_err());
            assert_eq!(std::fs::read(&path).unwrap(),before);
        }
        for invalid_path in [root.path().join("project.json"),root.path().join("editor-projects/p/other.json"),
            root.path().join("editor-projects/p/../p/project.json")] {
            assert!(super::write_editor_project_file(root.path(),&invalid_path,&current).is_err());
        }
        let relative_root=std::path::Path::new("relative-editor-save-root");
        assert!(super::write_editor_project_file(relative_root,&relative_root.join("editor-projects/p/project.json"),&current).is_err());
        assert_eq!(std::fs::read(&path).unwrap(),before);
    }

    #[test]
    fn editor_save_holds_asset_lease_through_atomic_replace_and_releases_it_on_failure() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/p/project.json");
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        super::write_editor_project_file(root.path(),&path,&current).unwrap();
        let before=std::fs::read(&path).unwrap();
        let mut replacing=false;
        let result=super::write_editor_project_file_with_replace(root.path(),&path,&current,|temporary,destination| {
            replacing=true;
            assert!(crate::python::library_references::AssetLease::acquire(root.path(),std::time::Duration::ZERO).is_err());
            assert_eq!(std::fs::read(destination).unwrap(),before);
            assert!(temporary.is_file());
            Err(crate::error::AppError::Worker("injected replacement failure".into()))
        });
        assert!(replacing && result.is_err());
        assert_eq!(std::fs::read(&path).unwrap(),before);
        assert_eq!(std::fs::read_dir(path.parent().unwrap()).unwrap().count(),1);
        let _released=crate::python::library_references::AssetLease::acquire(root.path(),std::time::Duration::ZERO).unwrap();
    }

    #[test]
    fn editor_save_rejects_new_missing_managed_assets_but_preserves_relinkable_missing_sources() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/p/project.json");
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        super::write_editor_project_file(root.path(),&path,&current).unwrap();
        let before=std::fs::read(&path).unwrap();
        let mut changed=current.clone();
        changed["sources"]=json!([{"id":"source","path":root.path().join("editor-projects/deleted/recordings/take.wav"),"missing":true}]);
        assert!(super::write_editor_project_file(root.path(),&path,&changed).is_err());
        assert_eq!(std::fs::read(&path).unwrap(),before);
        // A previously saved missing reference must remain editable for relinking.
        std::fs::write(&path,serde_json::to_vec(&changed).unwrap()).unwrap();
        changed["name"]=json!("Editing while relinking");
        super::write_editor_project_file(root.path(),&path,&changed).unwrap();
        let outside=tempfile::tempdir().unwrap();
        changed["sources"][0]["path"]=json!(outside.path().join("missing.wav"));
        super::write_editor_project_file(root.path(),&path,&changed).unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&std::fs::read(&path).unwrap()).unwrap()["sources"][0]["path"],changed["sources"][0]["path"]);
    }

    #[cfg(unix)]
    #[test]
    fn editor_save_rejects_linked_project_paths_without_touching_the_target() {
        let root=tempfile::tempdir().unwrap();let outside=tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("editor-projects")).unwrap();
        std::os::unix::fs::symlink(outside.path(),root.path().join("editor-projects/p")).unwrap();
        let path=root.path().join("editor-projects/p/project.json");
        let raw=r#"{"id":"p","version":4,"sources":[],"tracks":[]}"#;
        std::fs::write(outside.path().join("project.json"),raw).unwrap();
        assert!(super::write_editor_project_file(root.path(),&path,&json!({"id":"p","version":4,"sources":[],"tracks":[]})).is_err());
        assert_eq!(std::fs::read_to_string(outside.path().join("project.json")).unwrap(),raw);
    }

    #[cfg(windows)]
    #[test]
    fn json_save_retries_unable_to_remove_replaced_without_touching_the_old_file() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("project.json");
        let old = br#"{"revision":1}"#;
        std::fs::write(&path, old).unwrap();
        let mut attempts = 0;
        super::write_json_file_with_replace(&path, &json!({ "revision": 2 }), |temp, dest| {
            super::retry_file_replacement(|| {
                attempts += 1;
                assert_eq!(std::fs::read(dest).unwrap(), old);
                let pending: serde_json::Value =
                    serde_json::from_slice(&std::fs::read(temp).unwrap()).unwrap();
                assert_eq!(pending, json!({ "revision": 2 }));
                if attempts < 3 {
                    Err(std::io::Error::from_raw_os_error(1175))
                } else {
                    std::fs::rename(temp, dest)
                }
            })?;
            Ok(())
        })
        .unwrap();
        assert_eq!(attempts, 3);
        let stored: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored, json!({ "revision": 2 }));
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn persistent_replacement_failure_is_bounded_and_keeps_the_previous_save() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("project.json");
        let old = br#"{"revision":1}"#;
        std::fs::write(&path, old).unwrap();
        let mut attempts = 0;
        let error = super::write_json_file_with_replace(&path, &json!({ "revision": 2 }), |_, _| {
            super::retry_file_replacement(|| {
                attempts += 1;
                Err(std::io::Error::from_raw_os_error(1175))
            })?;
            Ok(())
        })
        .unwrap_err();
        assert_eq!(attempts, 6);
        assert!(matches!(error,
            crate::error::AppError::Io(error) if error.raw_os_error() == Some(1175)));
        assert_eq!(std::fs::read(&path).unwrap(), old);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }

    #[cfg(windows)]
    #[test]
    fn partial_replacements_are_not_retried_and_keep_a_complete_recovery_file() {
        for code in [1176, 1177] {
            let root = tempfile::tempdir().unwrap();
            let path = root.path().join("project.json");
            std::fs::write(&path, br#"{"revision":1}"#).unwrap();
            let mut recovery = PathBuf::new();
            let mut attempts = 0;
            let error = super::write_json_file_with_replace(
                &path,
                &json!({ "revision": 2 }),
                |temp, dest| {
                    recovery = temp.to_path_buf();
                    super::retry_file_replacement(|| {
                        attempts += 1;
                        if code == 1176 {
                            std::fs::remove_file(dest)?;
                        } else {
                            std::fs::rename(dest, root.path().join("original.json"))?;
                        }
                        Err(std::io::Error::from_raw_os_error(code))
                    })?;
                    Ok(())
                },
            )
            .unwrap_err();
            assert_eq!(attempts, 1);
            assert!(!path.exists());
            assert!(error.to_string().contains(&recovery.display().to_string()));
            let pending: serde_json::Value =
                serde_json::from_slice(&std::fs::read(recovery).unwrap()).unwrap();
            assert_eq!(pending, json!({ "revision": 2 }));
        }
    }

    #[cfg(windows)]
    #[test]
    fn non_transient_file_errors_fail_without_retrying() {
        for code in [2, 87, 112] {
            let mut attempts = 0;
            let error = super::retry_file_replacement(|| {
                attempts += 1;
                Err(std::io::Error::from_raw_os_error(code))
            })
            .unwrap_err();
            assert_eq!(attempts, 1);
            assert_eq!(error.raw_os_error(), Some(code));
        }
    }

    #[cfg(windows)]
    #[test]
    fn json_save_succeeds_after_a_windows_reader_releases_its_handle() {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{FILE_SHARE_READ, FILE_SHARE_WRITE};

        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("project.json");
        write_json_file(&path, &json!({ "revision": 1 })).unwrap();
        let reader = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .open(&path)
            .unwrap();
        let release = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(100));
            drop(reader);
        });
        let result = write_json_file(&path, &json!({ "revision": 2 }));
        release.join().unwrap();
        result.unwrap();
        let stored: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(stored, json!({ "revision": 2 }));
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 1);
    }

    #[test]
    fn env_root_has_highest_priority() {
        let root = resolve_data_root(
            Some(path("env-data")),
            path("dev-data"),
            Some(path("portable-data")),
            path("legacy-data"),
            true,
        );

        assert_eq!(root, path("env-data"));
    }

    #[test]
    fn development_root_matches_the_dev_script() {
        assert!(super::development_data_root_dir()
            .unwrap()
            .ends_with(".dev-data"));
    }

    #[test]
    fn development_uses_project_local_data() {
        let root = resolve_data_root(
            None,
            path("dev-data"),
            Some(path("portable-data")),
            path("legacy-data"),
            true,
        );

        assert_eq!(root, path("dev-data"));
    }

    #[test]
    fn release_portable_uses_portable_data() {
        let root = resolve_data_root(
            None,
            path("dev-data"),
            Some(path("portable-data")),
            path("legacy-data"),
            false,
        );

        assert_eq!(root, path("portable-data"));
    }

    #[test]
    fn portable_marker_next_to_the_executable_selects_data_beside_it() {
        let fixture = ResourceFixture::new();
        let exe_dir = fixture.0.join("MediaForge-Portable");
        std::fs::create_dir_all(&exe_dir).unwrap();
        let exe = exe_dir.join("MediaForge.exe");
        std::fs::write(&exe, b"").unwrap();
        assert_eq!(super::portable_data_root_for(&exe), None);
        std::fs::write(exe_dir.join(super::PORTABLE_MARKER_FILE_NAME), b"portable").unwrap();
        assert_eq!(super::portable_data_root_for(&exe), Some(exe_dir.join("data")));
    }

    #[test]
    fn portable_marker_name_must_match_the_packaging_scripts() {
        assert_eq!(super::PORTABLE_MARKER_FILE_NAME, "mediaforge.portable");
    }

    #[test]
    fn portable_layout_keeps_models_and_runtime_beside_data() {
        let fixture = ResourceFixture::new();
        let exe = fixture.0.join("MediaForge.exe");
        std::fs::write(&exe, b"").unwrap();
        assert!(super::portable_layout_for(&exe).is_none());
        std::fs::write(fixture.0.join(super::PORTABLE_MARKER_FILE_NAME), b"portable").unwrap();
        let layout = super::portable_layout_for(&exe).expect("portable layout");
        assert_eq!(layout.data, fixture.0.join("data"));
        assert_eq!(layout.models, fixture.0.join("models"));
        assert_eq!(layout.runtime_envs, fixture.0.join("runtime-envs"));
    }

    #[test]
    fn asset_roots_keep_explicit_data_inside_its_boundary() {
        for name in ["models", "runtime-envs"] {
            let data = path("isolated").join("data");
            let result = super::resolve_asset_root(
                None, Some(data.clone()), path("unused-data"), Some(path("installed").join(name)), name,
            );
            assert_eq!(result, data.join(name));
        }
    }

    #[test]
    fn asset_roots_preserve_explicit_asset_overrides() {
        for name in ["models", "runtime-envs"] {
            let explicit = path("separate-assets").join(name);
            assert_eq!(super::resolve_asset_root(
                Some(explicit.clone()), Some(path("isolated-data")), path("unused-data"), Some(path("installed").join(name)), name,
            ), explicit);
        }
    }

    #[test]
    fn asset_roots_use_sibling_layout_only_without_data_override() {
        for name in ["models", "runtime-envs"] {
            let installed = path("installed").join(name);
            assert_eq!(super::resolve_asset_root(None, None, path("installed").join("data"), Some(installed.clone()), name), installed);
            assert_eq!(super::resolve_asset_root(None, None, path("dev-data"), None, name), path("dev-data").join(name));
        }
    }
}
