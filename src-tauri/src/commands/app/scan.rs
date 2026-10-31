//! 媒体扫描：音频/视频/图片路径收集、类型判定、去重与定位。
//!
//! 从 app_cmd.rs 拆出；都是本地文件系统操作，不经过 worker。

use crate::error::{AppError, AppResult};
use crate::storage;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::AppHandle;

const AUDIO_EXTENSIONS: &[&str] = &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"];
const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mkv", "mov", "avi", "webm", "flv"];

#[derive(Debug, Serialize)]
pub struct ScanAudioPathsResult {
    pub files: Vec<String>,
    pub warnings: Vec<String>,
}

pub(crate) fn is_audio_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| AUDIO_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn is_media_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| {
            let ext = ext.to_lowercase();
            AUDIO_EXTENSIONS.contains(&ext.as_str()) || VIDEO_EXTENSIONS.contains(&ext.as_str())
        })
        .unwrap_or(false)
}

pub(crate) fn collect_audio_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_audio_files(&path, results, warnings);
                } else if path.is_file() && is_audio_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn collect_media_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_media_files(&path, results, warnings);
                } else if path.is_file() && is_media_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn collect_audio_files_shallow(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_file() && is_audio_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn stable_dedup(paths: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut deduped = Vec::with_capacity(paths.len());
    for path in paths {
        if seen.insert(path.clone()) {
            deduped.push(path);
        }
    }
    deduped
}

/// 图片扩展名：与 `scan_media_paths` 的音视频集合分开，
/// 否则把一张 png 拖进图片页会被判成"不支持的文件"。
const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"];

fn is_image_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| IMAGE_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn collect_image_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(error) => {
            warnings.push(format!("{}: {}", dir.display(), error));
            return;
        }
    };
    for entry in entries {
        match entry {
            Ok(entry) => {
                let path = entry.path();
                if path.is_dir() {
                    collect_image_files(&path, results, warnings);
                } else if path.is_file() && is_image_file(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

/// 判断拖进来的路径是文件还是目录。
///
/// 拖放事件只给路径字符串，"这是文件夹"与否必须查文件系统——用它来决定
/// 是"引用这个目录"还是"把文件加进来"。
#[tauri::command]
pub async fn path_kinds(paths: Vec<String>) -> AppResult<Vec<(String, String)>> {
    let mut result = Vec::new();
    for raw in paths {
        let path = Path::new(&raw);
        let kind = if path.is_dir() {
            "directory"
        } else if path.is_file() {
            "file"
        } else {
            "missing"
        };
        result.push((raw, kind.to_string()));
    }
    Ok(result)
}

/// 拖放/选择到的路径 → 图片文件列表（目录会递归展开）。
#[tauri::command]
pub async fn scan_image_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_image_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported image file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_image_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_audio_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_audio_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths_with_options(
    paths: Vec<String>,
    recursive: Option<bool>,
    sort_files: Option<bool>,
) -> AppResult<ScanAudioPathsResult> {
    let recursive = recursive.unwrap_or(true);
    let sort_files = sort_files.unwrap_or(true);
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_audio_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            if recursive {
                collect_audio_files(target, &mut files, &mut warnings);
            } else {
                collect_audio_files_shallow(target, &mut files, &mut warnings);
            }
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    if sort_files {
        files.sort();
        files.dedup();
    } else {
        files = stable_dedup(files);
    }
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_media_paths(paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    for raw in paths {
        let target = Path::new(&raw);
        if target.is_file() {
            if is_media_file(target) {
                files.push(target.to_string_lossy().to_string());
            } else {
                warnings.push(format!("unsupported file: {}", target.display()));
            }
        } else if target.is_dir() {
            collect_media_files(target, &mut files, &mut warnings);
        } else {
            warnings.push(format!("path not found: {}", raw));
        }
    }
    files.sort();
    files.dedup();
    Ok(ScanAudioPathsResult { files, warnings })
}


fn resolve_reveal_path(app: &AppHandle, path: &str) -> AppResult<PathBuf> {
    let target = PathBuf::from(path);
    if target.is_absolute() {
        return Ok(target);
    }

    // Worker output paths may be entered as a relative directory. Keep this
    // resolution identical to the worker's PYMSS_STUDIO_DEFAULT_OUTPUT_DIR
    // handling so Explorer opens the directory where inference wrote files.
    let output_root = storage::outputs_dir(app)?;
    Ok(output_root
        .parent()
        .unwrap_or(output_root.as_path())
        .join(target))
}

#[tauri::command]
pub async fn reveal_path(app: AppHandle, path: String) -> AppResult<()> {
    let resolved = resolve_reveal_path(&app, &path)?;
    let target = resolved.as_path();
    let reveal_target = reveal_target_path(target);
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer").arg(reveal_target).spawn()?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open").arg(reveal_target).spawn()?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open").arg(reveal_target).spawn()?;
    }
    Ok(())
}

pub(crate) fn reveal_target_path(path: &Path) -> &Path {
    if path.is_file() {
        return path.parent().unwrap_or(path);
    }
    if path.is_dir() {
        return path;
    }
    path.ancestors()
        .skip(1)
        .find(|ancestor| ancestor.is_dir())
        .unwrap_or_else(|| path.parent().unwrap_or(path))
}

