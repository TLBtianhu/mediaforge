//! 回收站：把文件或空目录移到系统回收站。
//!
//! 从 app_cmd.rs 拆出；使用 trash crate，失败项逐个返回，不中断整批。

use crate::error::AppResult;
use crate::storage;
use serde::Serialize;
use std::path::{Path, PathBuf};
use tauri::AppHandle;

use crate::commands::app::editor::normalize_path_key;

#[derive(Serialize)]
pub struct TrashResult {
    pub trashed: Vec<String>,
    pub failed: Vec<String>,
}

/// Trash is limited to the app-owned output and editor-project trees. Renderer
/// paths are untrusted input, so a compromised renderer must not be able to move
/// arbitrary documents (or the program directory) into the recycle bin. Removing
/// user-selected input files would need an explicit, user-confirmed allowlist.
fn trash_roots(app: &AppHandle) -> AppResult<Vec<PathBuf>> {
    let data = storage::data_root_dir(app)?;
    let data = canonical_scope(&data).unwrap_or(data);
    Ok(vec![data.join("outputs"), data.join("editor-projects")])
}

/// Canonicalize and drop the Windows verbatim prefix; the same directory must
/// compare equal whether it came from the app root or from a renderer path.
fn canonical_scope(path: &Path) -> Option<PathBuf> {
    let canonical = std::fs::canonicalize(path).ok()?;
    #[cfg(windows)]
    {
        let text = canonical.to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\") {
            let rest = rest
                .strip_prefix("UNC\\")
                .map(|unc| format!(r"\\{unc}"))
                .unwrap_or_else(|| rest.to_string());
            return Some(PathBuf::from(rest));
        }
    }
    Some(canonical)
}

fn resolve_for_scope(path: &Path) -> Option<PathBuf> {
    canonical_scope(path).or_else(|| {
        let parent = path.parent()?;
        let name = path.file_name()?;
        Some(canonical_scope(parent)?.join(name))
    })
}

fn within_roots(path: &Path, roots: &[PathBuf]) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

fn trash_target_allowed(path: &Path, roots: &[PathBuf]) -> bool {
    path.is_absolute()
        && resolve_for_scope(path).is_some_and(|resolved| within_roots(&resolved, roots))
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
pub async fn move_paths_to_trash(app: AppHandle, paths: Vec<String>, empty_dirs: Option<Vec<String>>) -> AppResult<TrashResult> {
    let roots = trash_roots(&app)?;
    let mut trashed = Vec::new();
    let mut failed = Vec::new();
    for path in paths {
        let target = Path::new(&path);
        if !target.is_absolute() {
            failed.push(path);
            continue;
        }
        if !trash_target_allowed(target, &roots) {
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
        if !trash_target_allowed(target, &roots) {
            failed.push(path);
            continue;
        }
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


#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn fixture() -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("mf-trash-scope-{stamp}"));
        fs::create_dir_all(root.join("outputs")).unwrap();
        fs::create_dir_all(root.join("editor-projects")).unwrap();
        root
    }

    fn write_file(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"x").unwrap();
    }

    #[test]
    fn only_app_owned_output_and_editor_trees_are_allowed() {
        let root = fixture();
        let roots = vec![root.join("outputs"), root.join("editor-projects")];
        let output = root.join("outputs/job/result.wav");
        write_file(&output);
        assert!(trash_target_allowed(&output, &roots));
        let editor = root.join("editor-projects/p/project.json");
        write_file(&editor);
        assert!(trash_target_allowed(&editor, &roots));
        let outside = root.join("documents/notes.txt");
        write_file(&outside);
        assert!(!trash_target_allowed(&outside, &roots));
        assert!(!trash_target_allowed(&root.join("documents/missing.txt"), &roots));
        assert!(trash_target_allowed(&root.join("outputs/missing.wav"), &roots));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn traversal_and_relative_paths_cannot_escape_the_roots() {
        let root = fixture();
        let roots = vec![root.join("outputs")];
        write_file(&root.join("secret.txt"));
        assert!(!trash_target_allowed(&root.join("outputs/../secret.txt"), &roots));
        assert!(!trash_target_allowed(Path::new("outputs/relative.wav"), &roots));
        fs::remove_dir_all(&root).unwrap();
    }
}
