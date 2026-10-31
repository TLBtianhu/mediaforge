//! 回收站：把文件或空目录移到系统回收站。
//!
//! 从 app_cmd.rs 拆出；使用 trash crate，失败项逐个返回，不中断整批。

use crate::error::{AppError, AppResult};
use serde::Serialize;
use std::fs;
use std::path::Path;
use std::process::Command;
use tauri::AppHandle;

use crate::commands::app_cmd::normalize_path_key;

#[derive(Serialize)]
pub struct TrashResult {
    pub trashed: Vec<String>,
    pub failed: Vec<String>,
}


fn is_ignored_empty_dir_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name == ".DS_Store")
}

pub(crate) fn is_link_metadata(metadata: &std::fs::Metadata) -> bool {
    metadata.file_type().is_symlink()
}

pub(crate) fn is_effectively_empty_dir(path: &Path) -> bool {
    let Ok(metadata) = std::fs::symlink_metadata(path) else {
        return false;
    };
    if !metadata.is_dir() || is_link_metadata(&metadata) {
        return false;
    }
    let entries = match std::fs::read_dir(path) {
        Ok(entries) => entries,
        Err(_) => return false,
    };
    for entry in entries {
        let Ok(entry) = entry else {
            return false;
        };
        let child = entry.path();
        let Ok(metadata) = std::fs::symlink_metadata(&child) else {
            return false;
        };
        if is_link_metadata(&metadata) {
            return false;
        }
        if metadata.is_dir() {
            if !is_effectively_empty_dir(&child) {
                return false;
            }
            continue;
        }
        if !metadata.is_file() || !is_ignored_empty_dir_file(&child) {
            return false;
        }
    }
    true
}

pub(crate) fn normalize_trash_empty_dirs(paths: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    let mut dirs: Vec<String> = paths
        .into_iter()
        .filter(|path| !path.trim().is_empty())
        .filter(|path| seen.insert(normalize_path_key(path)))
        .collect();
    dirs.sort_by_key(|path| {
        std::cmp::Reverse(Path::new(path).components().count())
    });
    dirs
}

#[tauri::command]
pub async fn move_paths_to_trash(paths: Vec<String>, empty_dirs: Option<Vec<String>>) -> AppResult<TrashResult> {
    let mut trashed = Vec::new();
    let mut failed = Vec::new();
    for path in paths {
        let target = Path::new(&path);
        if !target.is_absolute() {
            failed.push(path);
            continue;
        }
        // 已不存在的路径视为已删除，无需报错
        if !target.exists() {
            trashed.push(path);
            continue;
        }
        if !target.is_file() {
            failed.push(path);
            continue;
        }
        match trash::delete(target) {
            Ok(()) => trashed.push(path),
            Err(_) => failed.push(path),
        }
    }
    for path in normalize_trash_empty_dirs(empty_dirs.unwrap_or_default()) {
        let target = Path::new(&path);
        if !target.exists() {
            trashed.push(path);
            continue;
        }
        if !target.is_absolute() || target.parent().is_none() || !target.is_dir() {
            continue;
        }
        if !is_effectively_empty_dir(target) {
            continue;
        }
        match trash::delete(target) {
            Ok(()) => trashed.push(path),
            Err(_) => failed.push(path),
        }
    }
    Ok(TrashResult { trashed, failed })
}

