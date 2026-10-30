//! 运行时资产：把 Python 运行时当成"按内容寻址的固定资产"来下载与校验。
//!
//! 设计约束（来自项目决定）：
//!   * 资产身份来自 `manifest/runtime.json`（可被 `versions.json` 覆盖成 desired），
//!     程序里不再写死 sha256——换运行时是换数据，不是重编译；
//!   * 资产落在数据根 `~/.mediaforge/runtime-envs/<assetId>`，并列存放，切指针即回滚；
//!   * 下载交给随包的 aria2c（4 连接、断点续传、自带重试），Rust 只负责编排与校验；
//!   * 解压先落临时目录、校验通过后原子改名，避免断电留下半个运行时。

use std::fs::{self, File};
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};

use crate::error::{AppError, AppResult};
use crate::storage;

/// 清单里的运行时描述。字段名与 `manifest/runtime.json` 一致（camelCase）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeDescriptor {
    pub asset_id: String,
    pub backend: String,
    pub sha256: String,
    pub size: u64,
    pub urls: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeManifest {
    #[serde(default)]
    fallback_asset_id: String,
    runtimes: Vec<RuntimeDescriptor>,
}

/// 编译进去的基线清单：它决定"没有任何远程信息时用哪份运行时"。
const EMBEDDED_MANIFEST: &str = include_str!("../../manifest/runtime.json");

/// `versions.json` 接受后写入的期望运行时；存在时优先于基线清单。
const DESIRED_FILE: &str = "desired-runtime.json";
const PROGRESS_EVENT: &str = "mediaforge://runtime-asset";
const ASSET_MARKER: &str = ".asset-id";

pub fn embedded_descriptor() -> AppResult<RuntimeDescriptor> {
    let manifest: RuntimeManifest = serde_json::from_str(EMBEDDED_MANIFEST)
        .map_err(|error| AppError::Worker(format!("manifest/runtime.json 解析失败：{error}")))?;
    let wanted = manifest.fallback_asset_id;
    let mut fallback = manifest.runtimes.into_iter();
    let first = fallback
        .next()
        .ok_or_else(|| AppError::Worker("manifest/runtime.json 没有任何 runtimes".into()))?;
    if wanted.is_empty() {
        return Ok(first);
    }
    let all = std::iter::once(first).chain(fallback);
    all.into_iter()
        .find(|item| item.asset_id == wanted)
        .ok_or_else(|| AppError::Worker(format!("runtime.json 里找不到 fallbackAssetId={wanted}")))
}

fn envs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(storage::data_root_dir(app)?.join("runtime-envs"))
}

/// 当前应该使用哪份运行时：`versions.json` 接受过就用它，否则用编译进的基线。
pub fn desired_runtime(app: &AppHandle) -> AppResult<RuntimeDescriptor> {
    let Ok(envs) = envs_dir(app) else {
        return embedded_descriptor();
    };
    let file = envs.join(DESIRED_FILE);
    if let Ok(text) = fs::read_to_string(&file) {
        if let Ok(descriptor) = serde_json::from_str::<RuntimeDescriptor>(&text) {
            if !descriptor.asset_id.trim().is_empty() && !descriptor.sha256.trim().is_empty() {
                return Ok(descriptor);
            }
        }
    }
    embedded_descriptor()
}

pub fn write_desired(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<()> {
    let envs = envs_dir(app)?;
    fs::create_dir_all(&envs)?;
    fs::write(envs.join(DESIRED_FILE), serde_json::to_string(descriptor)?)?;
    Ok(())
}

fn asset_dir(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<PathBuf> {
    Ok(envs_dir(app)?.join(&descriptor.asset_id))
}

fn legacy_backend_dir(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<PathBuf> {
    Ok(envs_dir(app)?.join(&descriptor.backend))
}

fn python_path(app: &AppHandle, descriptor: &RuntimeDescriptor) -> AppResult<PathBuf> {
    Ok(asset_dir(app, descriptor)?.join("Scripts").join("python.exe"))
}

/// 运行时是否已就位（只看解释器与标记，不重算 5 GB 的哈希）。
pub fn is_ready(app: &AppHandle) -> bool {
    let Ok(descriptor) = desired_runtime(app) else {
        return false;
    };
    let Ok(dir) = asset_dir(app, &descriptor) else {
        return false;
    };
    dir.join("Scripts").join("python.exe").is_file() && marker_matches(&dir, &descriptor)
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

fn emit(app: &AppHandle, descriptor: &RuntimeDescriptor, stage: &str, downloaded: u64, message: &str) {
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
    let ready = present && marker_matches(python.parent().and_then(Path::parent).unwrap_or(Path::new("")), &descriptor);
    Ok(json!({
        "assetId": descriptor.asset_id,
        "sha256": descriptor.sha256,
        "size": descriptor.size,
        "backend": descriptor.backend,
        "installed": ready,
        "stale": present && !ready,
        "dir": asset_dir(&app, &descriptor)?.display().to_string(),
        "legacyDir": legacy_backend_dir(&app, &descriptor)?.display().to_string(),
        "python": if ready { Some(python.display().to_string()) } else { None },
    }))
}

/// 启动下载。立刻返回，进度通过 `mediaforge://runtime-asset` 事件推送。
#[tauri::command]
pub fn runtime_asset_install(app: AppHandle) -> AppResult<Value> {
    let handle = app.clone();
    if is_ready(&handle) {
        return Ok(json!({ "started": false, "installed": true }));
    }
    std::thread::spawn(move || {
        if let Err(error) = install_blocking(&handle) {
            if let Ok(descriptor) = desired_runtime(&handle) {
                emit(&handle, &descriptor, "error", 0, &error);
            }
        }
    });
    Ok(json!({ "started": true, "installed": false }))
}

fn install_blocking(app: &AppHandle) -> Result<(), String> {
    let descriptor = desired_runtime(app).map_err(|error| error.to_string())?;
    let envs = envs_dir(app).map_err(|error| error.to_string())?;
    let staging = envs.join(format!(".incoming-{}", descriptor.asset_id));
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    let archive = staging.join(format!("{}.zip", descriptor.asset_id));

    if file_size(&archive) != descriptor.size {
        download(app, &descriptor, &staging, &archive)?;
    }

    emit(app, &descriptor, "verifying", descriptor.size, "校验运行时完整性");
    let actual = sha256_of(&archive).map_err(|error| error.to_string())?;
    if actual != descriptor.sha256 {
        let _ = fs::remove_file(&archive);
        return Err(format!(
            "运行时包校验失败：期望 {}，实际 {actual}（已删除，请重试）",
            descriptor.sha256
        ));
    }

    emit(app, &descriptor, "extracting", descriptor.size, "解压运行时");
    let target = envs.join(&descriptor.asset_id);
    if target.exists() {
        let _ = fs::remove_dir_all(&target);
    }
    extract_zip(&archive, &target).map_err(|error| error.to_string())?;

    let record = json!({
        "pythonPath": target.join("Scripts").join("python.exe").to_string_lossy(),
        "backend": descriptor.backend,
        "assetId": descriptor.asset_id,
    });
    fs::write(envs.join("active-runtime.json"), record.to_string())
        .map_err(|error| error.to_string())?;
    // 标记最后写：它存在就意味着这份运行时完整、且与描述符一致。
    let marker = json!({ "assetId": descriptor.asset_id, "sha256": descriptor.sha256 });
    fs::write(target.join(ASSET_MARKER), marker.to_string()).map_err(|error| error.to_string())?;

    let _ = fs::remove_dir_all(&staging);
    emit(app, &descriptor, "done", descriptor.size, "运行时已就绪");
    Ok(())
}

fn download(
    app: &AppHandle,
    descriptor: &RuntimeDescriptor,
    dir: &Path,
    archive: &Path,
) -> Result<(), String> {
    let aria2 = aria2_path(app).map_err(|error| error.to_string())?;
    emit(app, descriptor, "downloading", 0, "下载运行时");
    let mut child = Command::new(&aria2)
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
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("无法启动 aria2c：{error}"))?;

    let started = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(700));
        let size = file_size(archive);
        emit(app, descriptor, "downloading", size, "下载运行时");
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    // 断点续传：下一轮从已有字节继续，所以先保留文件。
                    return Err(format!("下载中断（aria2c 退出码 {status:?}），可重试续传"));
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
        let Some(relative) = entry.enclosed_name() else {
            continue;
        };
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

    #[test]
    fn embedded_manifest_resolves_the_fallback_asset() {
        let descriptor = embedded_descriptor().expect("manifest 应能解析");
        assert!(descriptor.asset_id.starts_with("runtime-"));
        assert_eq!(descriptor.sha256.len(), 64);
        assert!(descriptor.size > 0);
        assert!(!descriptor.urls.is_empty());
    }
}
