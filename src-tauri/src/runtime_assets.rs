//! 运行时资产：把 Python 运行时当成"按内容寻址的固定资产"来下载与校验。
//!
//! 设计约束（来自项目决定）：
//!   * 资产身份来自 `manifest/runtime.json`（可被 `versions.json` 覆盖成 desired），
//!     程序里不再写死 sha256——换运行时是换数据，不是重编译；
//!   * 资产落在共享 `runtime-envs/<assetId>`，并列存放，切指针即回滚；
//!   * 下载交给随包的 aria2c（4 连接、断点续传、自带重试），Rust 只负责编排与校验；
//!   * 解压先落临时目录、校验通过后原子改名，避免断电留下半个运行时。

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};

use crate::error::{AppError, AppResult};
use crate::storage;

mod transaction;
use transaction::{validate_descriptor, InstallLock};

pub(crate) fn validate_runtime_descriptor(descriptor: &RuntimeDescriptor) -> Result<(), String> {
    validate_descriptor(descriptor)
}

/// 清单里的运行时描述。字段名与 `manifest/runtime.json` 一致（camelCase）。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeDescriptor {
    pub asset_id: String,
    pub platform: String,
    pub arch: String,
    pub backend: String,
    pub python: String,
    pub sha256: String,
    pub size: u64,
    pub urls: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct RuntimeManifest {
    schema_version: u64,
    #[serde(default, rename = "_comment")]
    _comment: Vec<String>,
    fallback_asset_id: String,
    runtimes: Vec<Value>,
}

struct RuntimeCatalog {
    fallback_asset_id: String,
    runtimes: Vec<RuntimeDescriptor>,
}

#[derive(Clone, Copy)]
struct HostTarget<'a> {
    platform: &'a str,
    arch: &'a str,
}

const CURRENT_HOST: HostTarget<'static> = HostTarget {
    platform: std::env::consts::OS,
    arch: std::env::consts::ARCH,
};

/// 编译进去的基线清单：它决定"没有任何远程信息时用哪份运行时"。
const EMBEDDED_MANIFEST: &str = include_str!("../../manifest/runtime.json");

/// `versions.json` 接受后写入的期望运行时；存在时优先于基线清单。
const DESIRED_FILE: &str = "desired-runtime.json";
const PROGRESS_EVENT: &str = "mediaforge://runtime-asset";
const ASSET_MARKER: &str = ".asset-id";

fn expected_python() -> Result<String, String> {
    let manifest: Value = serde_json::from_str(include_str!("../../python/runtime-manifest.json"))
        .map_err(|error| error.to_string())?;
    let version = manifest
        .get("python")
        .and_then(Value::as_str)
        .ok_or("Python runtime manifest has no target version")?;
    if version != "3.12" {
        return Err("This release requires a Python 3.12 runtime contract".into());
    }
    Ok(version.to_string())
}

fn parse_desired_descriptor(text: &str) -> AppResult<RuntimeDescriptor> {
    let descriptor = serde_json::from_str::<RuntimeDescriptor>(text).map_err(|_| {
        AppError::Worker("期望运行时描述不兼容，需要明确的平台、架构、后端与 Python ABI".into())
    })?;
    validate_descriptor(&descriptor).map_err(AppError::Worker)?;
    Ok(descriptor)
}

fn parse_catalog(text: &str) -> AppResult<RuntimeCatalog> {
    let manifest: RuntimeManifest = serde_json::from_str(text)
        .map_err(|error| AppError::Worker(format!("manifest/runtime.json 解析失败：{error}")))?;
    if manifest.schema_version != 3 {
        return Err(AppError::Worker(
            "运行时清单必须使用 schemaVersion 3 与显式目标和 Python ABI".into(),
        ));
    }
    let mut runtimes = Vec::new();
    let mut ids = HashSet::new();
    let mut targets = HashSet::new();
    for item in manifest.runtimes {
        let Value::Object(mut fields) = item else {
            return Err(AppError::Worker("运行时清单条目必须是对象".into()));
        };
        for name in ["torch", "notes"] {
            if fields.remove(name).is_some_and(|value| !value.is_string()) {
                return Err(AppError::Worker(format!("运行时清单 {name} 必须是字符串")));
            }
        }
        if let Some(capabilities) = fields.remove("capabilities") {
            serde_json::from_value::<Vec<String>>(capabilities)?;
        }
        let descriptor = parse_desired_descriptor(&Value::Object(fields).to_string())?;
        if !ids.insert(descriptor.asset_id.to_ascii_lowercase())
            || !targets.insert((descriptor.platform.clone(), descriptor.arch.clone(), descriptor.backend.clone()))
        {
            return Err(AppError::Worker("运行时清单中的资产 ID 和目标必须唯一".into()));
        }
        runtimes.push(descriptor);
    }
    if !runtimes.iter().any(|item| item.asset_id == manifest.fallback_asset_id) {
        return Err(AppError::Worker(format!(
            "runtime.json 里找不到 fallbackAssetId={}",
            manifest.fallback_asset_id
        )));
    }
    Ok(RuntimeCatalog { fallback_asset_id: manifest.fallback_asset_id, runtimes })
}

fn unavailable(host: HostTarget<'_>, backend: Option<&str>) -> String {
    format!(
        "RUNTIME_ASSET_UNAVAILABLE: 没有适用于 {}/{}/{} 的托管运行时安装包",
        host.platform, host.arch, backend.unwrap_or("default")
    )
}

fn installer_supports(host: HostTarget<'_>, backend: Option<&str>) -> bool {
    // This installer validates Windows x64 Python/DLLs and launches aria2c.exe.
    host.platform == "windows" && host.arch == "x86_64" && backend.is_none_or(|value| value == "cuda")
}

fn ensure_target(
    descriptor: &RuntimeDescriptor,
    host: HostTarget<'_>,
    backend: Option<&str>,
) -> Result<(), String> {
    validate_descriptor(descriptor)?;
    if descriptor.platform != host.platform
        || descriptor.arch != host.arch
        || backend.is_some_and(|requested| descriptor.backend != requested)
        || !installer_supports(host, Some(&descriptor.backend))
    {
        return Err(unavailable(host, backend.or(Some(&descriptor.backend))));
    }
    Ok(())
}

pub(crate) fn validate_install_target(
    descriptor: &RuntimeDescriptor,
    backend: Option<&str>,
) -> Result<(), String> {
    ensure_target(descriptor, CURRENT_HOST, backend)?;
    validate_catalog_identity(&parse_catalog(EMBEDDED_MANIFEST).map_err(|error| error.to_string())?, descriptor)
}

fn validate_catalog_identity(catalog: &RuntimeCatalog, descriptor: &RuntimeDescriptor) -> Result<(), String> {
    if let Some(known) = catalog.runtimes.iter().find(|item| item.asset_id.eq_ignore_ascii_case(&descriptor.asset_id)) {
        // Reordering mirrors does not change the identity of the archive.
        if known.asset_id != descriptor.asset_id
            || known.platform != descriptor.platform
            || known.arch != descriptor.arch
            || known.backend != descriptor.backend
            || known.python != descriptor.python
            || !known.sha256.eq_ignore_ascii_case(&descriptor.sha256)
            || known.size != descriptor.size
        {
            return Err("运行时描述符不能改变现有资产 ID 的目标或内容，请使用新的资产 ID".into());
        }
    }
    Ok(())
}

fn select_embedded(
    catalog: &RuntimeCatalog,
    host: HostTarget<'_>,
    backend: Option<&str>,
) -> AppResult<RuntimeDescriptor> {
    let descriptor = catalog.runtimes.iter().find(|item| {
        item.platform == host.platform && item.arch == host.arch
            && match backend {
                Some(backend) => item.backend == backend,
                None => item.asset_id == catalog.fallback_asset_id,
            }
    })
    .ok_or_else(|| AppError::Worker(unavailable(host, backend)))?;
    ensure_target(descriptor, host, backend).map_err(AppError::Worker)?;
    Ok(descriptor.clone())
}

fn envs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    storage::runtime_envs_dir(app)
}

fn read_desired(envs: &Path, catalog: &RuntimeCatalog) -> AppResult<Option<RuntimeDescriptor>> {
    let file = envs.join(DESIRED_FILE);
    transaction::ensure_plain(&file).map_err(AppError::Worker)?;
    match fs::read_to_string(file) {
        Ok(text) => {
            let descriptor = parse_desired_descriptor(&text)?;
            validate_catalog_identity(catalog, &descriptor).map_err(AppError::Worker)?;
            Ok(Some(descriptor))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

fn select_runtime(envs: &Path, host: HostTarget<'_>, backend: Option<&str>) -> AppResult<RuntimeDescriptor> {
    if !installer_supports(host, backend) {
        return Err(AppError::Worker(unavailable(host, backend)));
    }
    let catalog = parse_catalog(EMBEDDED_MANIFEST)?;
    if let Some(descriptor) = read_desired(envs, &catalog)? {
        if backend.is_none_or(|requested| descriptor.backend == requested) {
            ensure_target(&descriptor, host, backend).map_err(AppError::Worker)?;
            return Ok(descriptor);
        }
    }
    select_embedded(&catalog, host, backend)
}

pub(crate) fn desired_runtime(app: &AppHandle, backend: Option<&str>) -> AppResult<RuntimeDescriptor> {
    select_runtime(&envs_dir(app)?, CURRENT_HOST, backend)
}

pub(crate) fn accepted_runtime(app: &AppHandle, backend: &str) -> AppResult<Option<RuntimeDescriptor>> {
    let catalog = parse_catalog(EMBEDDED_MANIFEST)?;
    let descriptor = read_desired(&envs_dir(app)?, &catalog)?;
    Ok(descriptor.filter(|descriptor| ensure_target(descriptor, CURRENT_HOST, Some(backend)).is_ok()))
}

pub fn write_desired(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<()> {
    validate_install_target(descriptor, None).map_err(AppError::Worker)?;
    let envs = envs_dir(app)?;
    let catalog = parse_catalog(EMBEDDED_MANIFEST)?;
    read_desired(&envs, &catalog)?;
    let _lock = InstallLock::acquire(&envs).map_err(AppError::Worker)?;
    read_desired(&envs, &catalog)?;
    storage::write_json_file(&envs.join(DESIRED_FILE), &serde_json::to_value(descriptor)?)
}

fn read_marker(dir: &Path) -> Option<Value> {
    let file = dir.join(ASSET_MARKER);
    transaction::ensure_plain(&file).ok()?;
    let text = fs::read_to_string(file).ok()?;
    serde_json::from_str(&text).ok()
}

fn marker_matches(dir: &Path, descriptor: &RuntimeDescriptor) -> bool {
    let Some(value) = read_marker(dir) else {
        return false;
    };
    value.get("assetId").and_then(Value::as_str) == Some(descriptor.asset_id.as_str())
        && value.get("sha256").and_then(Value::as_str).is_some_and(|sha| sha.eq_ignore_ascii_case(&descriptor.sha256))
        && value.get("python").and_then(Value::as_str) == Some(descriptor.python.as_str())
        && value.get("schemaVersion").and_then(Value::as_u64) == Some(2)
}

fn aria2_path(app: &AppHandle) -> AppResult<PathBuf> {
    let exe_dir = std::env::current_exe()?
        .parent()
        .map(PathBuf::from)
        .ok_or_else(|| AppError::Worker("failed to resolve executable directory".into()))?;
    let bundled = exe_dir.join("bin").join("aria2c.exe");
    if bundled.is_file() {
        return Ok(bundled);
    }
    let resource = app
        .path()
        .resource_dir()
        .ok()
        .map(|dir| dir.join("bin").join("aria2c.exe"));
    match resource {
        Some(path) if path.is_file() => Ok(path),
        _ => Err(AppError::Worker(
            "bundled aria2c.exe is missing; the installer did not ship bin/".into(),
        )),
    }
}

fn emit(
    app: &AppHandle,
    descriptor: &RuntimeDescriptor,
    stage: &str,
    downloaded: u64,
    message: &str,
) {
    let percent = if descriptor.size > 0 {
        ((downloaded as f64 / descriptor.size as f64) * 100.0).clamp(0.0, 100.0)
    } else {
        0.0
    };
    let _ = app.emit(
        PROGRESS_EVENT,
        json!({
            "assetId": descriptor.asset_id,
            "backend": descriptor.backend,
            "stage": stage,
            "downloaded": downloaded,
            "total": descriptor.size,
            "percent": percent,
            "message": message,
        }),
    );
}

#[tauri::command]
pub fn runtime_asset_status(app: AppHandle, backend: Option<String>) -> AppResult<Value> {
    let envs = envs_dir(&app)?;
    let backend = backend.as_deref();
    let active = crate::python::worker::active_runtime_selection(&app);
    let mut status = json!({
        "available": false,
        "reason": null,
        "requestedBackend": backend,
        "platform": CURRENT_HOST.platform,
        "arch": CURRENT_HOST.arch,
        "activeUsable": active.as_ref().is_ok_and(|pointer| pointer.is_some()),
        "activeError": active.as_ref().err().map(ToString::to_string),
        "activeAssetId": active.ok().flatten().and_then(|pointer| matching_active_asset(pointer, CURRENT_HOST, backend)),
        "assetId": null,
        "sha256": null,
        "size": 0,
        "backend": null,
        "installed": false,
        "stale": false,
        "dir": null,
        "python": null,
    });
    let descriptor = match select_runtime(&envs, CURRENT_HOST, backend) {
        Ok(descriptor) => descriptor,
        Err(error) => {
            status["reason"] = json!(error.to_string());
            return Ok(status);
        }
    };
    let dir = envs.join(&descriptor.asset_id);
    let python = dir.join("Scripts").join("python.exe");
    let present = python.is_file();
    let ready = transaction::ready(&dir, &descriptor);
    status["available"] = json!(true);
    status["assetId"] = json!(descriptor.asset_id);
    status["sha256"] = json!(descriptor.sha256);
    status["size"] = json!(descriptor.size);
    status["backend"] = json!(descriptor.backend);
    status["installed"] = json!(ready);
    status["stale"] = json!(present && !ready);
    status["dir"] = json!(dir.display().to_string());
    status["python"] = json!(ready.then(|| python.display().to_string()));
    Ok(status)
}

fn matching_active_asset(
    pointer: crate::python::worker::ActiveRuntimeSelection,
    host: HostTarget<'_>,
    backend: Option<&str>,
) -> Option<String> {
    let actual_backend = pointer.backend.as_deref()?;
    if !installer_supports(host, Some(actual_backend))
        || backend.is_some_and(|requested| requested != actual_backend)
    {
        return None;
    }
    let id = pointer.asset_id?;
    let python = Path::new(&pointer.python);
    let scripts = python.parent()?;
    let root = scripts.parent()?;
    if !python.file_name()?.eq_ignore_ascii_case("python.exe")
        || !scripts.file_name()?.eq_ignore_ascii_case("Scripts")
        || root.file_name()?.to_str()? != id
    {
        return None;
    }
    let marker = read_marker(root)?;
    let hash = marker.get("sha256")?.as_str()?;
    if marker.get("schemaVersion")?.as_u64()? != 2
        || marker.get("assetId")?.as_str()? != id
        || marker.get("python")?.as_str()? != expected_python().ok()?
        || hash.len() != 64
        || !hash.bytes().all(|value| value.is_ascii_hexdigit())
    {
        return None;
    }
    Some(id)
}

pub(crate) fn active_managed_asset(app: &AppHandle, backend: &str) -> AppResult<Option<String>> {
    Ok(crate::python::worker::active_runtime_selection(app)?
        .and_then(|pointer| matching_active_asset(pointer, CURRENT_HOST, Some(backend))))
}

fn lock_install(
    envs: &Path,
    host: HostTarget<'_>,
    backend: Option<&str>,
) -> AppResult<(RuntimeDescriptor, InstallLock)> {
    // Reject incompatible requests before creating lock files or recovering pointers.
    select_runtime(envs, host, backend)?;
    let lock = InstallLock::acquire(envs).map_err(AppError::Worker)?;
    // A concurrent signed-version acceptance may have changed desired while locking.
    let descriptor = select_runtime(envs, host, backend)?;
    Ok((descriptor, lock))
}

/// 启动下载。立刻返回，进度通过 `mediaforge://runtime-asset` 事件推送。
#[tauri::command]
pub fn runtime_asset_install(app: AppHandle, backend: Option<String>) -> AppResult<Value> {
    let envs = envs_dir(&app)?;
    let (descriptor, lock) = lock_install(&envs, CURRENT_HOST, backend.as_deref())?;
    transaction::recover_pointer(&envs).map_err(AppError::Worker)?;
    let staging = envs.join(format!(".incoming-{}", descriptor.asset_id));
    transaction::recover_previous(&staging, &envs.join(&descriptor.asset_id))
        .map_err(AppError::Worker)?;
    if transaction::ready(&envs.join(&descriptor.asset_id), &descriptor) {
        transaction::activate_with_rollback(&envs, || write_active_pointer(&envs, &descriptor))
            .map_err(AppError::Worker)?;
        finish_install(&app, &descriptor, &staging);
        return Ok(install_response(&descriptor, true));
    }
    let response = install_response(&descriptor, false);
    std::thread::Builder::new()
        .name("runtime-asset-install".into())
        .spawn(move || {
            let _lock = lock;
            if let Err(error) = install_with_descriptor(&app, &envs, &descriptor) {
                emit(&app, &descriptor, "error", 0, &error);
            }
        })
        .map_err(|error| AppError::Worker(format!("无法启动运行时安装：{error}")))?;
    Ok(response)
}

fn install_response(descriptor: &RuntimeDescriptor, installed: bool) -> Value {
    json!({
        "started": !installed,
        "installed": installed,
        "assetId": descriptor.asset_id,
        "backend": descriptor.backend,
        "size": descriptor.size,
    })
}

fn apply_smoke_descriptor_override(app: &AppHandle) -> Result<(), String> {
    let Ok(raw) = std::env::var("MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR") else {
        return Ok(());
    };
    if raw.trim().is_empty() {
        return Ok(());
    }
    let descriptor: RuntimeDescriptor = serde_json::from_str(&raw)
        .map_err(|error| format!("MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR 不是合法描述符：{error}"))?;
    write_desired(app, &descriptor).map_err(|error| error.to_string())
}

/// dev-only 首装冒烟入口：在真实进程主线程里跑完整安装，返回进程退出码。
///
/// 为什么走 CLI 而不是 #[test]：Windows 的 Tao 事件循环要求主线程，测试线程起不了 App。
/// `MEDIAFORGE_SMOKE_RUNTIME_URL` 可把下载源换成本地 HTTP（不打外网、可复现）。
pub fn smoke_install(app: &AppHandle) -> i32 {
    if let Err(error) = apply_smoke_descriptor_override(app) {
        eprintln!("smoke-install-runtime: 写入本地描述符失败: {error}");
        return 1;
    }
    if let Ok(url) = std::env::var("MEDIAFORGE_SMOKE_RUNTIME_URL") {
        let url = url.trim().to_string();
        if !url.is_empty() {
            let fallback = parse_catalog(EMBEDDED_MANIFEST)
                .and_then(|catalog| select_embedded(&catalog, CURRENT_HOST, None));
            match fallback {
                Ok(mut descriptor) => {
                    descriptor.urls = vec![url];
                    if let Err(error) = write_desired(app, &descriptor) {
                        eprintln!("smoke-install-runtime: 写入本地描述符失败: {error}");
                        return 1;
                    }
                }
                Err(error) => {
                    eprintln!("smoke-install-runtime: 读取内嵌描述符失败: {error}");
                    return 1;
                }
            }
        }
    }
    match install_blocking(app) {
        Ok(()) => {
            println!("smoke-install-runtime: OK");
            0
        }
        Err(error) => {
            eprintln!("smoke-install-runtime: FAILED: {error}");
            1
        }
    }
}

/// dev-only 回滚/复用冒烟：走公开命令的 ready 分支，不重新下载资产。
pub fn smoke_activate(app: &AppHandle) -> i32 {
    if let Err(error) = apply_smoke_descriptor_override(app) {
        eprintln!("smoke-activate-runtime: 写入本地描述符失败: {error}");
        return 1;
    }
    match runtime_asset_install(app.clone(), None) {
        Ok(value)
            if value.get("started") == Some(&json!(false))
                && value.get("installed") == Some(&json!(true)) =>
        {
            println!("smoke-activate-runtime: OK");
            0
        }
        Ok(value) => {
            eprintln!("smoke-activate-runtime: 资产未就绪，不能走复用分支：{value}");
            1
        }
        Err(error) => {
            eprintln!("smoke-activate-runtime: FAILED: {error}");
            1
        }
    }
}

fn write_active_pointer(envs: &Path, descriptor: &RuntimeDescriptor) -> Result<(), String> {
    ensure_target(descriptor, CURRENT_HOST, None)?;
    let target = envs.join(&descriptor.asset_id);
    let record = json!({
        "pythonPath": target.join("Scripts").join("python.exe").to_string_lossy(),
        "backend": descriptor.backend,
        "assetId": descriptor.asset_id,
    });
    storage::write_json_file(&envs.join("active-runtime.json"), &record)
        .map_err(|error| error.to_string())
}

fn install_blocking(app: &AppHandle) -> Result<(), String> {
    let envs = envs_dir(app).map_err(|error| error.to_string())?;
    let (descriptor, _lock) =
        lock_install(&envs, CURRENT_HOST, None).map_err(|error| error.to_string())?;
    transaction::recover_pointer(&envs)?;
    install_with_descriptor(app, &envs, &descriptor)
}

fn finish_install(app: &AppHandle, descriptor: &RuntimeDescriptor, staging: &Path) {
    let message = match transaction::cleanup_staging(staging) {
        Ok(()) => "运行时已就绪".to_string(),
        Err(error) => format!("运行时已就绪，下载缓存未能清理：{error}"),
    };
    emit(app, descriptor, "done", descriptor.size, &message);
}

fn install_with_descriptor(
    app: &AppHandle,
    envs: &Path,
    descriptor: &RuntimeDescriptor,
) -> Result<(), String> {
    ensure_target(descriptor, CURRENT_HOST, None)?;
    let staging = envs.join(format!(".incoming-{}", descriptor.asset_id));
    let target = envs.join(&descriptor.asset_id);
    transaction::recover_previous(&staging, &target)?;
    if transaction::ready(&target, descriptor) {
        transaction::activate_with_rollback(envs, || write_active_pointer(envs, descriptor))?;
        finish_install(app, descriptor, &staging);
        return Ok(());
    }
    transaction::ensure_plain(&staging)?;
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    let archive = staging.join(format!("{}.zip", descriptor.asset_id));
    transaction::ensure_plain(&archive)?;

    if file_size(&archive) != descriptor.size {
        download(app, descriptor, &staging, &archive)?;
    }

    emit(
        app,
        descriptor,
        "verifying",
        descriptor.size,
        "校验运行时完整性",
    );
    let actual = sha256_of(&archive).map_err(|error| error.to_string())?;
    if !actual.eq_ignore_ascii_case(&descriptor.sha256) {
        let _ = fs::remove_file(&archive);
        return Err(format!(
            "运行时包校验失败：期望 {}，实际 {actual}（已删除，请重试）",
            descriptor.sha256
        ));
    }

    emit(app, descriptor, "extracting", descriptor.size, "解压运行时");
    transaction::publish_archive(
        envs,
        &staging,
        descriptor,
        &archive,
        |prepared| {
            if let Some(vcrt) = bundled_vcrt_dir(app) {
                let copied = copy_dlls(&vcrt, &prepared.join("Scripts"))
                    .map_err(|error| format!("复制 VC++ 运行库失败：{error}"))?;
                emit(
                    app,
                    descriptor,
                    "verifying",
                    descriptor.size,
                    &format!("已放置 {copied} 个 VC++ 运行库文件"),
                );
            }
            verify_interpreter(prepared, descriptor)
        },
        || write_active_pointer(envs, descriptor),
    )?;
    finish_install(app, descriptor, &staging);
    Ok(())
}

/// 安装包里的 VC++ 可再发行 DLL（`bin\vcrt`）。per-user 安装没有管理员权限，
/// 不能依赖 vc_redist，所以运行时装好后把它们复制进解释器目录。
fn bundled_vcrt_dir(app: &AppHandle) -> Option<PathBuf> {
    let exe_dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let bundled = exe_dir.join("bin").join("vcrt");
    if bundled.is_dir() {
        return Some(bundled);
    }
    let resource = app.path().resource_dir().ok()?.join("bin").join("vcrt");
    resource.is_dir().then_some(resource)
}

/// 把 DLL 复制到目标目录，返回复制数量。纯函数便于测试。
fn copy_dlls(from: &Path, to: &Path) -> std::io::Result<usize> {
    fs::create_dir_all(to)?;
    let mut copied = 0;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) == Some("dll") {
            let name = path.file_name().unwrap_or_default();
            fs::copy(&path, to.join(name))?;
            copied += 1;
        }
    }
    Ok(copied)
}

/// 运行时资产必须是自包含的嵌入式 Python，而不是依赖旧安装目录的 venv 启动器。
/// 资产原样验证；绝不从 bootstrap 借用解释器或删除 venv 配置。
fn validate_asset_layout(root: &Path, descriptor: &RuntimeDescriptor) -> Result<(), String> {
    let abi = descriptor.python.replace('.', "");
    for directory in [root.to_path_buf(), root.join("Scripts")] {
        if directory.join("pyvenv.cfg").exists() {
            return Err("运行时必须自包含，不能安装 venv 或删除配置后伪装为独立资产".into());
        }
        if directory.is_dir() {
            for entry in fs::read_dir(&directory).map_err(|error| error.to_string())? {
                let path = entry.map_err(|error| error.to_string())?.path();
                let name = path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase();
                if name.starts_with("python")
                    && (name.ends_with(".dll") || name.ends_with(".zip"))
                    && name != "python3.dll"
                    && name != format!("python{abi}.dll")
                    && name != format!("python{abi}.zip")
                {
                    return Err(format!("运行时包含不匹配的 Python ABI 文件：{name}"));
                }
            }
        }
    }
    if !root.join("Scripts/python.exe").is_file()
        || ![
            root.join(format!("Scripts/python{abi}.dll")),
            root.join(format!("python{abi}.dll")),
        ]
        .iter()
        .any(|path| path.is_file())
        || !root.join("Lib/site-packages").is_dir()
        || ![
            root.join("Lib/encodings/__init__.py"),
            root.join(format!("Scripts/python{abi}.zip")),
            root.join(format!("python{abi}.zip")),
        ]
        .iter()
        .any(|path| path.is_file())
    {
        return Err("运行时缺少匹配的自包含 Python 解释器、DLL、标准库或 site-packages".into());
    }
    Ok(())
}

fn validate_native_tree(directory: &Path, abi: &str) -> Result<(), String> {
    transaction::ensure_plain(directory)?;
    for entry in fs::read_dir(directory).map_err(|error| error.to_string())? {
        let path = entry.map_err(|error| error.to_string())?.path();
        transaction::ensure_plain(&path)?;
        if path.is_dir() {
            validate_native_tree(&path, abi)?;
        } else {
            let name = path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_lowercase();
            let foreign_extension = name.ends_with(".pyd")
                && name
                    .split(".cp")
                    .nth(1)
                    .map(|suffix| {
                        suffix
                            .chars()
                            .take_while(char::is_ascii_digit)
                            .collect::<String>()
                    })
                    .is_some_and(|tag| !tag.is_empty() && tag != abi);
            if name == "pyvenv.cfg" || foreign_extension {
                return Err(format!("运行时包含 venv 或不匹配的扩展 ABI：{name}"));
            }
        }
    }
    Ok(())
}

/// 装完立刻验证解释器能加载 C++ 运行库：失败时给出可操作的错误，
/// 而不是等用户点任务才看到 "DLL load failed"。
fn validate_probe(
    value: &Value,
    root: &Path,
    descriptor: &RuntimeDescriptor,
) -> Result<(), String> {
    if value.get("python").and_then(Value::as_str) != Some(descriptor.python.as_str())
        || value.get("platform").and_then(Value::as_str) != Some(descriptor.platform.as_str())
        || value.get("arch").and_then(Value::as_str) != Some(descriptor.arch.as_str())
        || value.get("imports").and_then(Value::as_bool) != Some(true)
        || value.get("torchBackend").and_then(Value::as_str) != Some(descriptor.backend.as_str())
    {
        return Err("运行时自检的平台、架构、Python ABI、依赖或 torch 后端与描述符不一致".into());
    }
    let root = root.canonicalize().map_err(|error| error.to_string())?;
    let resolve = |key| {
        value
            .get(key)
            .and_then(Value::as_str)
            .and_then(|path| Path::new(path).canonicalize().ok())
    };
    let prefix = resolve("prefix");
    if prefix.is_none()
        || prefix != resolve("basePrefix")
        || !prefix.as_ref().unwrap().starts_with(&root)
    {
        return Err("运行时引用了资产目录以外的解释器或 venv 基础环境".into());
    }
    Ok(())
}

fn verify_interpreter(root: &Path, descriptor: &RuntimeDescriptor) -> Result<(), String> {
    validate_descriptor(descriptor)?;
    validate_asset_layout(root, descriptor)?;
    validate_native_tree(root, &descriptor.python.replace('.', ""))?;
    let isolated = tempfile::Builder::new()
        .prefix("mediaforge-runtime-probe-")
        .tempdir()
        .map_err(|error| error.to_string())?;
    let mut command = crate::process_command::hidden_command(root.join("Scripts/python.exe"));
    command.args([
        "-I", "-B", "-c",
        concat!(
            "import sys,json,ctypes,sysconfig; ctypes.CDLL('msvcp140.dll'); ctypes.CDLL('vcruntime140.dll'); ",
            "import torch,pymss,pymss_core,pymss.graph; ",
            "backend='rocm' if getattr(torch.version,'hip',None) else 'cuda' if getattr(torch.version,'cuda',None) else 'cpu'; ",
            "target=sysconfig.get_platform(); arch={'win-amd64':'x86_64','win-arm64':'aarch64'}.get(target,target); ",
            "platform='windows' if sys.platform=='win32' else sys.platform; ",
            "print(json.dumps({'python':'.'.join(map(str,sys.version_info[:2])),'platform':platform,'arch':arch,",
            "'prefix':sys.prefix,'basePrefix':sys.base_prefix,'imports':True,'torchBackend':backend}))"
        ),
    ]);
    for name in [
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "MEDIAFORGE_DATA_ROOT",
        "MEDIAFORGE_DATA_DIR",
        "PYMSS_STUDIO_DATA_ROOT",
        "PYMSS_STUDIO_DATA_DIR",
        "MEDIAFORGE_MODEL_DIR",
        "PYMSS_MODEL_DIR",
        "PYMSS_STUDIO_MODEL_DIR",
        "MEDIAFORGE_RUNTIME_ENVS_DIR",
        "PYMSS_STUDIO_RUNTIME_ENVS_DIR",
        "WEBVIEW2_USER_DATA_FOLDER",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_ASSETS_CACHE",
        "HF_XET_CACHE",
        "TRANSFORMERS_CACHE",
        "MODELSCOPE_CACHE",
        "TORCH_HOME",
        "TORCH_EXTENSIONS_DIR",
        "PIP_CACHE_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "TEMP",
        "TMP",
        "TMPDIR",
    ] {
        command.env(name, isolated.path());
    }
    command
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("HF_HUB_OFFLINE", "1")
        .env("TRANSFORMERS_OFFLINE", "1")
        .env("MODELSCOPE_OFFLINE", "1");
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动解释器自检：{error}"))?;
    let mut stdout = child.stdout.take().ok_or("自检 stdout 缺失")?;
    let mut stderr = child.stderr.take().ok_or("自检 stderr 缺失")?;
    let out = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stdout.read_to_end(&mut bytes).map(|_| bytes)
    });
    let err = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        stderr.read_to_end(&mut bytes).map(|_| bytes)
    });
    let deadline = Instant::now() + Duration::from_secs(90);
    let status = loop {
        match child.try_wait().map_err(|error| error.to_string())? {
            Some(status) => break status,
            None if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = out.join();
                let _ = err.join();
                return Err("运行时 Python ABI 自检超时".into());
            }
            None => std::thread::sleep(Duration::from_millis(25)),
        }
    };
    let output = std::process::Output {
        status,
        stdout: out
            .join()
            .map_err(|_| "自检 stdout 读取失败")?
            .map_err(|error| error.to_string())?,
        stderr: err
            .join()
            .map_err(|_| "自检 stderr 读取失败")?
            .map_err(|error| error.to_string())?,
    };
    if output.status.success() {
        let probe = String::from_utf8_lossy(&output.stdout)
            .lines()
            .rev()
            .find_map(|line| serde_json::from_str::<Value>(line).ok())
            .ok_or("运行时自检未返回有效 JSON")?;
        return validate_probe(&probe, root, descriptor);
    }
    let detail = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "运行时自检失败（Python ABI 或依赖无法加载）：{}",
        detail.trim()
    ))
}

fn stderr_tail(bytes: &[u8]) -> String {
    const LIMIT: usize = 2000;
    let text = String::from_utf8_lossy(bytes);
    let trimmed = text.trim();
    if trimmed.len() <= LIMIT {
        return trimmed.to_string();
    }
    let start = trimmed
        .char_indices()
        .find(|(index, _)| *index >= trimmed.len() - LIMIT)
        .map(|(index, _)| index)
        .unwrap_or(0);
    trimmed[start..].to_string()
}

fn download(
    app: &AppHandle,
    descriptor: &RuntimeDescriptor,
    dir: &Path,
    archive: &Path,
) -> Result<(), String> {
    let aria2 = aria2_path(app).map_err(|error| error.to_string())?;
    emit(app, descriptor, "downloading", 0, "下载运行时");
    let mut command = crate::process_command::hidden_command(&aria2);
    command
        .args([
            "-x4",
            "-s4",
            "-c",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--file-allocation=none",
            "--summary-interval=0",
            "--console-log-level=warn",
            "-d",
        ])
        .arg(dir)
        .arg("-o")
        .arg(
            archive
                .file_name()
                .map(|name| name.to_string_lossy().to_string())
                .unwrap_or_else(|| format!("{}.zip", descriptor.asset_id)),
        )
        .args(&descriptor.urls)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动 aria2c：{error}"))?;
    #[cfg(windows)]
    let _download_job = match crate::process_job::ProcessJob::attach(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("无法管理下载进程生命周期：{error}"));
        }
    };

    let started = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(700));
        let size = file_size(archive);
        emit(app, descriptor, "downloading", size, "下载运行时");
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    let output = child
                        .wait_with_output()
                        .map_err(|error| error.to_string())?;
                    let detail = stderr_tail(&output.stderr);
                    let suffix = if detail.is_empty() {
                        String::new()
                    } else {
                        format!("：{detail}")
                    };
                    // 断点续传：下一轮从已有字节继续，所以先保留文件。
                    return Err(format!(
                        "下载中断（aria2c 退出码 {status:?}）{suffix}，可重试续传"
                    ));
                }
                break;
            }
            Ok(None) => {
                if started.elapsed() > Duration::from_secs(60 * 90) {
                    let _ = child.kill();
                    return Err("下载超时（90 分钟），可重试续传".into());
                }
            }
            Err(error) => return Err(format!("无法获取下载状态：{error}")),
        }
    }

    let size = file_size(archive);
    if size != descriptor.size {
        return Err(format!(
            "下载大小不符：期望 {}，实际 {size}",
            descriptor.size
        ));
    }
    Ok(())
}

fn file_size(path: &Path) -> u64 {
    fs::metadata(path).map(|meta| meta.len()).unwrap_or(0)
}

fn sha256_of(path: &Path) -> std::io::Result<String> {
    let mut reader = BufReader::new(File::open(path)?);
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn extract_zip(archive: &Path, target: &Path) -> std::io::Result<()> {
    let file = File::open(archive)?;
    let mut zip = zip::ZipArchive::new(file)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string()))?;
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index)?;
        let relative = entry.enclosed_name().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "runtime archive path escapes its directory",
            )
        })?;
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "runtime archive cannot contain symbolic links",
            ));
        }
        let destination = target.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&destination)?;
            continue;
        }
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut out = File::create(&destination)?;
        std::io::copy(&mut entry, &mut out)?;
        out.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod descriptor_tests {
    use super::*;

    fn descriptor() -> RuntimeDescriptor {
        RuntimeDescriptor {
            asset_id: "runtime-test312".into(),
            platform: "windows".into(),
            arch: "x86_64".into(),
            backend: "cuda".into(),
            python: "3.12".into(),
            sha256: "0".repeat(64),
            size: 1,
            urls: vec!["http://127.0.0.1/test.zip".into()],
        }
    }

    fn layout(root: &Path) {
        fs::create_dir_all(root.join("Scripts")).unwrap();
        fs::create_dir_all(root.join("Lib/site-packages")).unwrap();
        fs::create_dir_all(root.join("Lib/encodings")).unwrap();
        for name in [
            "Scripts/python.exe",
            "Scripts/python312.dll",
            "Lib/encodings/__init__.py",
        ] {
            fs::write(root.join(name), b"tiny layout fixture").unwrap();
        }
    }

    #[test]
    fn self_contained_layout_rejects_venv_and_foreign_dll_without_modifying_files() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        layout(root.path());
        validate_asset_layout(root.path(), &descriptor).unwrap();
        let original = fs::read(root.path().join("Scripts/python.exe")).unwrap();
        for name in ["pyvenv.cfg", "Scripts/pyvenv.cfg", "Scripts/python311.dll"] {
            fs::write(root.path().join(name), b"foreign ABI").unwrap();
            assert!(validate_asset_layout(root.path(), &descriptor).is_err());
            assert_eq!(
                fs::read(root.path().join("Scripts/python.exe")).unwrap(),
                original
            );
            assert!(root.path().join(name).is_file());
            fs::remove_file(root.path().join(name)).unwrap();
        }
        fs::remove_file(root.path().join("Scripts/python312.dll")).unwrap();
        assert!(validate_asset_layout(root.path(), &descriptor).is_err());
    }

    #[test]
    fn asset_tree_rejects_foreign_native_extension_and_preserves_venv_config() {
        let root = tempfile::tempdir().unwrap();
        layout(root.path());
        let matching = root
            .path()
            .join("Lib/site-packages/matching.cp312-win_amd64.pyd");
        fs::write(&matching, b"fixture").unwrap();
        validate_native_tree(root.path(), "312").unwrap();
        let foreign = root
            .path()
            .join("Lib/site-packages/foreign.cp311-win_amd64.pyd");
        fs::write(&foreign, b"fixture").unwrap();
        assert!(validate_native_tree(root.path(), "312").is_err());
        fs::remove_file(foreign).unwrap();
        fs::write(
            root.path().join("Lib/site-packages/pyvenv.cfg"),
            b"external home",
        )
        .unwrap();
        assert!(validate_native_tree(root.path(), "312").is_err());
        assert!(root.path().join("Lib/site-packages/pyvenv.cfg").is_file());
    }

    #[test]
    fn probe_requires_matching_python_backend_imports_and_owned_base_prefix() {
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let valid = json!({
            "python": "3.12", "platform": "windows", "arch": "x86_64", "prefix": root.path(),
            "basePrefix": root.path(), "imports": true, "torchBackend": "cuda",
        });
        validate_probe(&valid, root.path(), &descriptor).unwrap();
        for (field, value) in [
            ("python", json!("3.11")),
            ("platform", json!("linux")),
            ("arch", json!("aarch64")),
            ("imports", json!(false)),
            ("torchBackend", json!("cpu")),
            ("basePrefix", json!(outside.path())),
            ("prefix", json!(outside.path())),
        ] {
            let mut invalid = valid.clone();
            invalid[field] = value;
            assert!(
                validate_probe(&invalid, root.path(), &descriptor).is_err(),
                "{field}"
            );
        }
    }

    #[test]
    fn desired_descriptor_rejects_old_missing_wrong_or_unknown_contract_fields() {
        let valid = serde_json::to_value(descriptor()).unwrap();
        parse_desired_descriptor(&valid.to_string()).unwrap();
        for field in ["python", "platform", "arch", "backend"] {
            let mut missing = valid.clone();
            missing.as_object_mut().unwrap().remove(field);
            assert!(parse_desired_descriptor(&missing.to_string()).is_err(), "{field}");
        }
        let mut wrong = valid.clone();
        wrong["python"] = json!("3.11");
        assert!(parse_desired_descriptor(&wrong.to_string()).is_err());
        let mut extra = valid;
        extra["legacyField"] = json!(true);
        assert!(parse_desired_descriptor(&extra.to_string()).is_err());
    }

    #[test]
    fn copy_dlls_only_takes_dlls_and_creates_the_target() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("vcrt");
        let target = root.path().join("env/Scripts");
        fs::create_dir_all(&source).unwrap();
        fs::write(source.join("msvcp140.dll"), b"stub").unwrap();
        fs::write(source.join("SOURCE.json"), b"{}").unwrap();
        assert_eq!(copy_dlls(&source, &target).unwrap(), 1);
        assert!(!target.join("SOURCE.json").exists());
    }

    #[test]
    fn stderr_tail_keeps_the_actual_download_error() {
        assert_eq!(
            stderr_tail(b"  TLS certificate verification failed\n"),
            "TLS certificate verification failed"
        );
        let long = "下载失败".repeat(1000);
        let tail = stderr_tail(long.as_bytes());
        assert!(tail.len() <= 2000);
        assert!(long.ends_with(&tail));
    }

    #[test]
    fn embedded_manifest_resolves_the_fallback_asset() {
        let host = HostTarget { platform: "windows", arch: "x86_64" };
        let catalog = parse_catalog(EMBEDDED_MANIFEST).unwrap();
        let descriptor = select_embedded(&catalog, host, None).unwrap();
        assert_eq!(descriptor.python, "3.12");
        assert_eq!(descriptor.platform, "windows");
        assert_eq!(descriptor.arch, "x86_64");
        assert_eq!(descriptor.backend, "cuda");
        validate_descriptor(&descriptor).unwrap();
    }

    #[test]
    fn unsupported_runtime_matrix_stops_before_creating_the_install_root() {
        let temporary = tempfile::tempdir().unwrap();
        let envs = temporary.path().join("runtime-envs");
        for (platform, arch, backend) in [
            ("linux", "x86_64", Some("cuda")),
            ("linux", "x86_64", None),
            ("macos", "aarch64", Some("mlx")),
            ("windows", "aarch64", Some("cuda")),
            ("windows", "x86_64", Some("cpu")),
            ("windows", "x86_64", Some("rocm")),
            ("windows", "x86_64", Some("unknown")),
        ] {
            let host = HostTarget { platform, arch };
            let error = lock_install(&envs, host, backend).err().unwrap().to_string();
            assert!(error.contains("RUNTIME_ASSET_UNAVAILABLE"), "{error}");
            assert!(!envs.exists(), "{platform}/{arch}/{backend:?}");
        }
    }

    #[test]
    fn desired_runtime_checks_target_and_preserves_incompatible_or_invalid_files() {
        let temporary = tempfile::tempdir().unwrap();
        let envs = temporary.path();
        let host = HostTarget { platform: "windows", arch: "x86_64" };
        let mut desired = descriptor();
        let file = envs.join(DESIRED_FILE);
        let recovery = envs.join("active-runtime.rollback.json");
        fs::write(&recovery, b"leave this checkpoint alone").unwrap();

        for (platform, arch) in [("linux", "x86_64"), ("windows", "aarch64")] {
            desired.platform = platform.into();
            desired.arch = arch.into();
            let bytes = serde_json::to_vec(&desired).unwrap();
            fs::write(&file, &bytes).unwrap();
            assert!(lock_install(envs, host, Some("cuda")).is_err());
            assert_eq!(fs::read(&file).unwrap(), bytes);
            assert_eq!(fs::read(&recovery).unwrap(), b"leave this checkpoint alone");
            assert!(!envs.join(".runtime-asset-install.lock").exists());
            assert!(!envs.join("active-runtime.json").exists());
        }

        fs::write(&file, br#"{"assetId":"old-descriptor"}"#).unwrap();
        assert!(lock_install(envs, host, Some("cuda")).is_err());
        assert_eq!(fs::read(&file).unwrap(), br#"{"assetId":"old-descriptor"}"#);
        assert!(!envs.join(".runtime-asset-install.lock").exists());

        desired.platform = "windows".into();
        desired.arch = "x86_64".into();
        fs::write(&file, serde_json::to_vec(&desired).unwrap()).unwrap();
        assert_eq!(select_runtime(envs, host, Some("cuda")).unwrap(), desired);
    }

    #[test]
    fn catalogue_rejects_missing_fallback_ambiguous_targets_and_reused_asset_identity() {
        let raw: Value = serde_json::from_str(EMBEDDED_MANIFEST).unwrap();
        let mut malformed = raw.clone();
        malformed["fallbackAssetId"] = json!("missing");
        assert!(parse_catalog(&malformed.to_string()).is_err());

        let mut duplicate = raw.clone();
        let mut same_target = raw["runtimes"][0].clone();
        same_target["assetId"] = json!("another-asset");
        duplicate["runtimes"].as_array_mut().unwrap().push(same_target);
        assert!(parse_catalog(&duplicate.to_string()).is_err());

        let mut duplicate = raw;
        let mut same_id = duplicate["runtimes"][0].clone();
        same_id["arch"] = json!("aarch64");
        same_id["assetId"] = json!(same_id["assetId"].as_str().unwrap().to_ascii_uppercase());
        duplicate["runtimes"].as_array_mut().unwrap().push(same_id);
        assert!(parse_catalog(&duplicate.to_string()).is_err());

        let catalog = parse_catalog(EMBEDDED_MANIFEST).unwrap();
        let mut known = catalog.runtimes[0].clone();
        known.urls.reverse();
        assert!(validate_catalog_identity(&catalog, &known).is_ok());
        known.sha256 = "0".repeat(64);
        assert!(validate_catalog_identity(&catalog, &known).is_err());
    }
}
