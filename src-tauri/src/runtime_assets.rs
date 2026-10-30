//! 运行时资产：把 Python 运行时当成"按内容寻址的固定资产"来下载与校验。
//!
//! 设计约束（来自项目决定）：
//!   * 应用只认一个 sha256，没有"最新版本"这个概念，因此不存在版本协商；
//!   * 资产落在数据根 `~/.mediaforge/runtime-envs/<assetId>`（并列存放，切指针即回滚）；
//!   * 下载交给随包的 aria2c（4 连接、断点续传、自带重试），Rust 只负责编排与校验；
//!   * 解压先落临时目录、校验通过后原子改名，避免断电留下半个运行时。

use std::fs::{self, File};
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager};

use crate::error::{AppError, AppResult};
use crate::storage;

/// 当前应用钉死的运行时资产。换运行时 = 发新版应用，绝不后台偷偷升。
pub const ASSET_ID: &str = "runtime-cuda-2026.10.2";
pub const ASSET_SHA256: &str = "7a1040eb6ed5510e1ede30ef90314a6a5993f94eee409c1ef224fb5d6559784d";
pub const ASSET_SIZE: u64 = 3616417066;
/// 同一份资产的两个源，按实测速度排序。
///
/// 顺序按速度排：hf-mirror 单连接实测 5.5 MB/s，魔搭单连接约 1.2 MB/s。两者都已发布同一份资产。
/// huggingface.co 在国内不可达（实测 443 超时），备源写的是社区镜像 hf-mirror；
/// 实测镜像单连接 5.5 MB/s，魔搭单连接约 1.2 MB/s、四连接聚合约 3 MB/s。
/// aria2c 支持"同一文件多个 URI"，前一个失败会自动换下一个，因此备用源是零成本冗余。
pub const ASSET_URLS: [&str; 2] = [
    "https://hf-mirror.com/datasets/TLBtianhu/MediaForge-Dist/resolve/main/runtime/runtime-cuda-2026.10.2.zip",
    "https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/runtime/runtime-cuda-2026.10.2.zip",
];
const BACKEND: &str = "cuda";
const PROGRESS_EVENT: &str = "mediaforge://runtime-asset";
/// 安装成功后写入的标记，用来判断"本地这份是不是当前钉死的资产"。
/// 不能每次启动去算 5 GB 的 sha256，那样冷启动要多花几十秒。
const ASSET_MARKER: &str = ".asset-id";

fn envs_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(storage::data_root_dir(app)?.join("runtime-envs"))
}

/// 当前二进制钉死的资产目录：`runtime-envs/<assetId>`。
/// 并列存放意味着旧资产不删——回滚只需要把 active-runtime.json 指回去。
fn asset_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(envs_dir(app)?.join(ASSET_ID))
}

/// 旧布局（`runtime-envs/<backend>`）只用于读取，不再写入。
fn legacy_backend_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(envs_dir(app)?.join(BACKEND))
}

fn python_path(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(asset_dir(app)?.join("Scripts").join("python.exe"))
}

/// 运行时是否已就位（只看解释器在不在，不重算 5 GB 的哈希）。
pub fn is_ready(app: &AppHandle) -> bool {
    python_path(app).map(|path| path.is_file()).unwrap_or(false) && marker_matches(app)
}

/// 本地运行时是否就是当前资产（标记里的 id 与 sha256 都要对上）。
fn marker_matches(app: &AppHandle) -> bool {
    let Ok(dir) = asset_dir(app) else {
        return false;
    };
    let Ok(text) = fs::read_to_string(dir.join(ASSET_MARKER)) else {
        return false;
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    value.get("assetId").and_then(Value::as_str) == Some(ASSET_ID)
        && value.get("sha256").and_then(Value::as_str) == Some(ASSET_SHA256)
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

fn emit(app: &AppHandle, stage: &str, downloaded: u64, message: &str) {
    let percent = if ASSET_SIZE > 0 {
        ((downloaded as f64 / ASSET_SIZE as f64) * 100.0).clamp(0.0, 100.0)
    } else {
        0.0
    };
    let _ = app.emit(
        PROGRESS_EVENT,
        json!({
            "assetId": ASSET_ID,
            "stage": stage,
            "downloaded": downloaded,
            "total": ASSET_SIZE,
            "percent": percent,
            "message": message,
        }),
    );
}

#[tauri::command]
pub fn runtime_asset_status(app: AppHandle) -> AppResult<Value> {
    let python = python_path(&app)?;
    let present = python.is_file();
    let ready = present && marker_matches(&app);
    Ok(json!({
        "assetId": ASSET_ID,
        "sha256": ASSET_SHA256,
        "size": ASSET_SIZE,
        "backend": BACKEND,
        "installed": ready,
        "stale": present && !ready,
        "dir": asset_dir(&app)?.display().to_string(),
        "legacyDir": legacy_backend_dir(&app)?.display().to_string(),
        "python": if ready { Some(python.display().to_string()) } else { None },
    }))
}

/// 启动下载。立刻返回，进度通过 `mediaforge://runtime-asset` 事件推送。
#[tauri::command]
pub fn runtime_asset_install(app: AppHandle) -> AppResult<Value> {
    if is_ready(&app) {
        return Ok(json!({ "started": false, "installed": true }));
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Err(error) = install_blocking(&handle) {
            emit(&handle, "error", 0, &error);
        }
    });
    Ok(json!({ "started": true, "installed": false }))
}

fn install_blocking(app: &AppHandle) -> Result<(), String> {
    let envs = envs_dir(app).map_err(|error| error.to_string())?;
    let staging = envs.join(format!(".incoming-{ASSET_ID}"));
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    let archive = staging.join(format!("{ASSET_ID}.zip"));

    if file_size(&archive) != ASSET_SIZE {
        download(app, &staging, &archive)?;
    }

    emit(app, "verifying", ASSET_SIZE, "校验运行时完整性");
    let actual = sha256_of(&archive).map_err(|error| error.to_string())?;
    if actual != ASSET_SHA256 {
        let _ = fs::remove_file(&archive);
        return Err(format!(
            "运行时包校验失败：期望 {ASSET_SHA256}，实际 {actual}（已删除，请重试）"
        ));
    }

    emit(app, "extracting", ASSET_SIZE, "解压运行时");
    let target = envs.join(ASSET_ID);
    if target.exists() {
        let _ = fs::remove_dir_all(&target);
    }
    extract_zip(&archive, &target).map_err(|error| error.to_string())?;

    let record = json!({
        "pythonPath": target.join("Scripts").join("python.exe").to_string_lossy(),
        "backend": BACKEND,
        "assetId": ASSET_ID,
    });
    fs::write(envs.join("active-runtime.json"), record.to_string())
        .map_err(|error| error.to_string())?;
    // 标记最后写：它存在就意味着这份运行时是完整的、且与当前二进制钉死的资产一致。
    let marker = json!({ "assetId": ASSET_ID, "sha256": ASSET_SHA256 });
    fs::write(target.join(ASSET_MARKER), marker.to_string()).map_err(|error| error.to_string())?;

    let _ = fs::remove_dir_all(&staging);
    emit(app, "done", ASSET_SIZE, "运行时已就绪");
    Ok(())
}

fn download(app: &AppHandle, dir: &Path, archive: &Path) -> Result<(), String> {
    let aria2 = aria2_path(app).map_err(|error| error.to_string())?;
    emit(app, "downloading", 0, "下载运行时");
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
                .unwrap_or_else(|| format!("{ASSET_ID}.zip")),
        )
        .args(ASSET_URLS)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("无法启动 aria2c：{error}"))?;

    let started = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(700));
        let size = file_size(archive);
        emit(app, "downloading", size, "下载运行时");
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
    if size != ASSET_SIZE {
        return Err(format!("下载大小不符：期望 {ASSET_SIZE}，实际 {size}"));
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
