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
        "task-history" => Ok("tasks.json"),
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
    Ok(value)
}

pub fn write_app_store(app: &AppHandle, name: &str, data: &Value) -> AppResult<()> {
    if name == "workflow-state" { validate_workflow_state(data)?; }
    ensure_app_directories(app)?;
    let path = app_store_path(app, name)?;
    write_json_file(&path, data)
}

pub(crate) fn write_json_file(path: &Path, data: &Value) -> AppResult<()> {
    let _write_guard = JSON_WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    write_json_file_locked(path, data)
}

pub(crate) fn write_editor_project_file(path: &Path, data: &Value) -> AppResult<()> {
    let _write_guard = JSON_WRITE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    validate_editor_project(data)?;
    if path.is_file() {
        let previous: Value = serde_json::from_slice(&std::fs::read(path)?)?;
        validate_editor_project(&previous)?;
    }
    write_json_file_locked(path, data)
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
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
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
        apply_workflow_mutation, resolve_data_root, store_file_name, write_json_file,
        JSON_WRITE_SEQUENCE,
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
        let root=tempfile::tempdir().unwrap(); let path=root.path().join("project.json");
        let current=json!({"id":"p","version":4,"sources":[],"tracks":[]});
        for version in [None,Some(2),Some(3),Some(5)] {
            let mut old=json!({"id":"p","sources":[],"tracks":[]});
            if let Some(version)=version { old["version"]=json!(version); }
            let raw=serde_json::to_vec(&old).unwrap(); std::fs::write(&path,&raw).unwrap();
            assert!(super::write_editor_project_file(&path,&current).is_err());
            assert_eq!(std::fs::read(&path).unwrap(),raw);
            assert!(!root.path().join("project.pre-plugins.json").exists());
        }
    }

    #[test]
    fn current_editor_writes_replace_only_the_current_project() {
        let root=tempfile::tempdir().unwrap(); let path=root.path().join("project.json");
        for revision in 1..=3 {
            super::write_editor_project_file(&path,&json!({"version":4,"sources":[],"tracks":[],"revision":revision})).unwrap();
        }
        let saved:Value=serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved["revision"],3);
        assert_eq!(std::fs::read_dir(root.path()).unwrap().count(),1);
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
