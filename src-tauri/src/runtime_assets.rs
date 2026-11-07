//! 运行时资产：把 Python 运行时当成"按内容寻址的固定资产"来下载与校验。
//!
//! 设计约束（来自项目决定）：
//!   * 资产身份来自 `manifest/runtime.json`（可被 `versions.json` 覆盖成 desired），
//!     程序里不再写死 sha256——换运行时是换数据，不是重编译；
//!   * 资产落在共享 `runtime-envs/<assetId>`，并列存放，切指针即回滚；
//!   * 下载交给随包的 aria2c（4 连接、断点续传、自带重试），Rust 只负责编排与校验；
//!   * 解压先落临时目录、校验通过后原子改名，避免断电留下半个运行时。

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
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeDescriptor {
    pub asset_id: String,
    pub backend: String,
    pub python: String,
    pub sha256: String,
    pub size: u64,
    pub urls: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeManifest {
    schema_version: u64,
    #[serde(default)]
    fallback_asset_id: String,
    runtimes: Vec<Value>,
}

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
        AppError::Worker("期望运行时描述不兼容；请重新选择 Python 3.12 运行时".into())
    })?;
    validate_descriptor(&descriptor).map_err(AppError::Worker)?;
    Ok(descriptor)
}

pub fn embedded_descriptor() -> AppResult<RuntimeDescriptor> {
    let manifest: RuntimeManifest = serde_json::from_str(EMBEDDED_MANIFEST)
        .map_err(|error| AppError::Worker(format!("manifest/runtime.json 解析失败：{error}")))?;
    if manifest.schema_version != 2 {
        return Err(AppError::Worker(
            "运行时清单必须使用 schemaVersion 2 与显式 Python ABI".into(),
        ));
    }
    let wanted = manifest.fallback_asset_id;
    let selected = if wanted.is_empty() {
        manifest.runtimes.first()
    } else {
        manifest
            .runtimes
            .iter()
            .find(|item| item.get("assetId").and_then(Value::as_str) == Some(wanted.as_str()))
    }
    .ok_or_else(|| AppError::Worker(format!("runtime.json 里找不到 fallbackAssetId={wanted}")))?;
    // The catalogue also owns display/capability metadata. Desired/remote
    // descriptors themselves use the strict, required-field schema above.
    let fields = ["assetId", "backend", "python", "sha256", "size", "urls"];
    let descriptor = Value::Object(
        selected
            .as_object()
            .ok_or_else(|| AppError::Worker("运行时清单条目必须是对象".into()))?
            .iter()
            .filter(|(key, _)| fields.contains(&key.as_str()))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    parse_desired_descriptor(&descriptor.to_string())
}

fn envs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    storage::runtime_envs_dir(app)
}

/// 当前应该使用哪份运行时：`versions.json` 接受过就用它，否则用编译进的基线。
pub fn desired_runtime(app: &AppHandle) -> AppResult<RuntimeDescriptor> {
    let envs = envs_dir(app)?;
    let file = envs.join(DESIRED_FILE);
    match fs::read_to_string(&file) {
        Ok(text) => parse_desired_descriptor(&text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => embedded_descriptor(),
        Err(error) => Err(error.into()),
    }
}

pub fn write_desired(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<()> {
    validate_descriptor(descriptor).map_err(AppError::Worker)?;
    let envs = envs_dir(app)?;
    let _lock = InstallLock::acquire(&envs).map_err(AppError::Worker)?;
    storage::write_json_file(&envs.join(DESIRED_FILE), &serde_json::to_value(descriptor)?)
}

fn asset_dir(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<PathBuf> {
    validate_descriptor(descriptor).map_err(AppError::Worker)?;
    Ok(envs_dir(app)?.join(&descriptor.asset_id))
}

fn python_path(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<PathBuf> {
    Ok(asset_dir(app, descriptor)?
        .join("Scripts")
        .join("python.exe"))
}

fn marker_matches(dir: &Path, descriptor: &RuntimeDescriptor) -> bool {
    let Ok(text) = fs::read_to_string(dir.join(ASSET_MARKER)) else {
        return false;
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    value.get("assetId").and_then(Value::as_str) == Some(descriptor.asset_id.as_str())
        && value.get("sha256").and_then(Value::as_str) == Some(descriptor.sha256.as_str())
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
            "stage": stage,
            "downloaded": downloaded,
            "total": descriptor.size,
            "percent": percent,
            "message": message,
        }),
    );
}

#[tauri::command]
pub fn runtime_asset_status(app: AppHandle) -> AppResult<Value> {
    let descriptor = desired_runtime(&app)?;
    let python = python_path(&app, &descriptor)?;
    let present = python.is_file();
    let ready = transaction::ready(
        python
            .parent()
            .and_then(Path::parent)
            .unwrap_or(Path::new("")),
        &descriptor,
    );
    Ok(json!({
        "assetId": descriptor.asset_id,
        "sha256": descriptor.sha256,
        "size": descriptor.size,
        "backend": descriptor.backend,
        "installed": ready,
        "stale": present && !ready,
        "dir": asset_dir(&app, &descriptor)?.display().to_string(),
        "python": if ready { Some(python.display().to_string()) } else { None },
    }))
}

/// 启动下载。立刻返回，进度通过 `mediaforge://runtime-asset` 事件推送。
#[tauri::command]
pub fn runtime_asset_install(app: AppHandle) -> AppResult<Value> {
    let envs = envs_dir(&app)?;
    let lock = InstallLock::acquire(&envs).map_err(AppError::Worker)?;
    transaction::recover_pointer(&envs).map_err(AppError::Worker)?;
    let descriptor = desired_runtime(&app)?;
    validate_descriptor(&descriptor).map_err(AppError::Worker)?;
    let staging = envs.join(format!(".incoming-{}", descriptor.asset_id));
    transaction::recover_previous(&staging, &envs.join(&descriptor.asset_id))
        .map_err(AppError::Worker)?;
    if transaction::ready(&envs.join(&descriptor.asset_id), &descriptor) {
        transaction::activate_with_rollback(&envs, || write_active_pointer(&envs, &descriptor))
            .map_err(AppError::Worker)?;
        finish_install(&app, &descriptor, &staging);
        return Ok(json!({ "started": false, "installed": true }));
    }
    std::thread::Builder::new()
        .name("runtime-asset-install".into())
        .spawn(move || {
            let _lock = lock;
            if let Err(error) = install_with_descriptor(&app, &envs, &descriptor) {
                emit(&app, &descriptor, "error", 0, &error);
            }
        })
        .map_err(|error| AppError::Worker(format!("无法启动运行时安装：{error}")))?;
    Ok(json!({ "started": true, "installed": false }))
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
            match embedded_descriptor() {
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
    match runtime_asset_install(app.clone()) {
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
    validate_descriptor(descriptor)?;
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
    let _lock = InstallLock::acquire(&envs)?;
    transaction::recover_pointer(&envs)?;
    let descriptor = desired_runtime(app).map_err(|error| error.to_string())?;
    validate_descriptor(&descriptor)?;
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
        || value.get("imports").and_then(Value::as_bool) != Some(true)
        || value.get("torchBackend").and_then(Value::as_str) != Some(descriptor.backend.as_str())
    {
        return Err("运行时自检的 Python ABI、依赖或 torch 后端与描述符不一致".into());
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
        "import sys,json,ctypes; ctypes.CDLL('msvcp140.dll'); ctypes.CDLL('vcruntime140.dll'); import torch,pymss,pymss_core,pymss.graph; backend='rocm' if getattr(torch.version,'hip',None) else 'cuda' if getattr(torch.version,'cuda',None) else 'cpu'; print(json.dumps({'python':'.'.join(map(str,sys.version_info[:2])),'prefix':sys.prefix,'basePrefix':sys.base_prefix,'imports':True,'torchBackend':backend}))",
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
        let valid = json!({"python":"3.12", "prefix":root.path(), "basePrefix":root.path(), "imports":true, "torchBackend":"cuda"});
        validate_probe(&valid, root.path(), &descriptor).unwrap();
        for (field, value) in [
            ("python", json!("3.11")),
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
        let mut missing = valid.clone();
        missing.as_object_mut().unwrap().remove("python");
        assert!(parse_desired_descriptor(&missing.to_string()).is_err());
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
        let descriptor =
            embedded_descriptor().expect("manifest must declare the current Python ABI");
        assert_eq!(descriptor.python, "3.12");
        validate_descriptor(&descriptor).unwrap();
    }
}
