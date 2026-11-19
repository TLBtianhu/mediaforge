//! 媒体扫描：音频/视频/图片路径收集、类型判定、去重与定位。
//!
//! 从 app_cmd.rs 拆出；都是本地文件系统操作，不经过 worker。

use crate::error::AppResult;
use crate::storage;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::{AppHandle, Manager};

pub(crate) const AUDIO_EXTENSIONS: &[&str] = &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"];
pub(crate) const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mkv", "mov", "avi", "webm", "flv"];

/// Folder picks and drops allow the input scanners to register individual media
/// files. A selected directory is never itself served by the preview protocol.
const MAX_SELECTED_FOLDERS: usize = 256;

#[derive(Default)]
pub(crate) struct SelectedInputFolders {
    entries: Mutex<HashMap<PathBuf, SelectedFolder>>,
    clock: AtomicU64,
}

struct SelectedFolder {
    identity: [u64; 3],
    // Keep the original directory identity alive even after a rename or delete.
    _handle: File,
    used: u64,
}

impl SelectedInputFolders {
    fn remember(&self, path: &Path) {
        let Some(directory) = plain_selection_path(path) else { return; };
        let Some((handle, identity)) = open_selected_directory(&directory) else { return; };
        let used = self.clock.fetch_add(1, Ordering::Relaxed);
        let mut entries = self.entries.lock().unwrap_or_else(|error| error.into_inner());
        entries.insert(directory, SelectedFolder { identity, _handle: handle, used });
        while entries.len() > MAX_SELECTED_FOLDERS {
            let oldest = entries.iter().min_by_key(|(_, selected)| selected.used).map(|(path, _)| path.clone());
            if let Some(oldest) = oldest { entries.remove(&oldest); } else { break; }
        }
    }

    fn includes(&self, path: &Path) -> bool {
        let Some(candidate) = plain_selection_path(path) else { return false; };
        let used = self.clock.fetch_add(1, Ordering::Relaxed);
        self.entries.lock().unwrap_or_else(|error| error.into_inner())
            .iter_mut().any(|(directory, selected)| {
                // Canonical path components retain their case. A Windows
                // directory may contain distinct case-sensitive siblings.
                let matches = candidate.starts_with(directory) && plain_selection_path(directory).as_ref() == Some(directory)
                    && open_selected_directory(directory).is_some_and(|(_, identity)| identity == selected.identity);
                if matches { selected.used = used; }
                matches
            })
    }
}

fn plain_selection_path(path: &Path) -> Option<PathBuf> {
    let key = crate::preview_assets::path_key(path)?;
    for parent in path.ancestors() {
        let metadata = fs::symlink_metadata(parent).ok()?;
        if metadata.is_symlink() {
            return None;
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { return None; }
        }
    }
    let canonical = fs::canonicalize(path).ok()?;
    (crate::preview_assets::path_key(&canonical)? == key).then_some(canonical)
}

#[cfg(unix)]
fn open_selected_directory(path: &Path) -> Option<(File, [u64; 3])> {
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    let handle = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY).open(path).ok()?;
    let metadata = handle.metadata().ok()?;
    let identity = [metadata.dev(), metadata.ino(), 0];
    Some((handle, identity))
}

#[cfg(windows)]
fn open_selected_directory(path: &Path) -> Option<(File, [u64; 3])> {
    use std::os::windows::{fs::OpenOptionsExt, io::AsRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_DIRECTORY,
        FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    };
    let handle = OpenOptions::new().read(true)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT).open(path).ok()?;
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(handle.as_raw_handle(), &mut info) } == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return None;
    }
    let identity = [
        u64::from(info.dwVolumeSerialNumber),
        (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        (u64::from(info.ftCreationTime.dwHighDateTime) << 32) | u64::from(info.ftCreationTime.dwLowDateTime),
    ];
    Some((handle, identity))
}

/// Called only by native picker completions and native webview drop events.
pub(crate) fn remember_input_selection(app: &AppHandle, paths: &[PathBuf]) {
    for path in paths {
        if path.is_dir() {
            if let Some(folders) = app.try_state::<SelectedInputFolders>() {
                folders.remember(path);
            }
        } else {
            crate::preview_assets::remember(app, path);
        }
    }
}

pub(crate) fn is_selected_input(app: &AppHandle, path: &Path) -> bool {
    crate::preview_assets::is_allowed(app, path)
        || app.try_state::<SelectedInputFolders>().is_some_and(|folders| folders.includes(path))
}

pub(crate) fn remember_scanned_inputs(app: &AppHandle, paths: &[String]) {
    let Some(folders) = app.try_state::<SelectedInputFolders>() else { return; };
    for path in paths.iter().map(Path::new) {
        if folders.includes(path) {
            crate::preview_assets::remember(app, path);
        }
    }
}

#[cfg(test)]
mod selection_tests {
    use super::*;

    #[test]
    fn selected_folder_excludes_other_paths_and_replacement_directories() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let folder = root.join("selected");
        let input = folder.join("track.wav");
        let other = root.join("other.wav");
        fs::create_dir(&folder).unwrap();
        fs::write(&input, b"selected").unwrap();
        fs::write(&other, b"unselected").unwrap();

        let selected = SelectedInputFolders::default();
        selected.remember(&folder);
        assert!(selected.includes(&folder));
        assert!(selected.includes(&input));
        assert!(!selected.includes(&other));
        assert!(!selected.includes(Path::new("track.wav")));
        assert!(!selected.includes(&folder.join("..").join("other.wav")));

        fs::rename(&folder, root.join("formerly-selected")).unwrap();
        fs::create_dir(&folder).unwrap();
        fs::write(&input, b"replacement").unwrap();
        assert!(!selected.includes(&input));
        selected.remember(&folder);
        assert!(selected.includes(&input));
    }

    #[test]
    fn selected_folder_handles_form_a_bounded_lru() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let selected = SelectedInputFolders::default();
        for index in 0..MAX_SELECTED_FOLDERS {
            let folder = root.join(format!("folder-{index}"));
            fs::create_dir(&folder).unwrap();
            selected.remember(&folder);
        }
        assert!(selected.includes(&root.join("folder-0")));
        for index in MAX_SELECTED_FOLDERS..MAX_SELECTED_FOLDERS + 2 {
            let folder = root.join(format!("folder-{index}"));
            fs::create_dir(&folder).unwrap();
            selected.remember(&folder);
        }
        assert_eq!(selected.entries.lock().unwrap().len(), MAX_SELECTED_FOLDERS);
        assert!(selected.includes(&root.join("folder-0")));
        assert!(!selected.includes(&root.join("folder-1")));
        assert!(selected.includes(&root.join(format!("folder-{}", MAX_SELECTED_FOLDERS + 1))));
    }

    #[cfg(unix)]
    #[test]
    fn selected_folder_does_not_authorize_paths_crossing_a_link() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().canonicalize().unwrap();
        let folder = root.join("selected");
        let other = root.join("other");
        fs::create_dir(&folder).unwrap();
        fs::create_dir(&other).unwrap();
        fs::write(other.join("track.wav"), b"unselected").unwrap();
        std::os::unix::fs::symlink(&other, folder.join("link")).unwrap();

        let selected = SelectedInputFolders::default();
        selected.remember(&folder);
        selected.remember(&folder.join("link"));
        assert!(!selected.includes(&folder.join("link/track.wav")));
        assert!(!selected.includes(&other.join("track.wav")));
    }
}

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
    collect_supported_files(dir, is_audio_file, true, results, warnings);
}

fn collect_supported_files(
    dir: &Path,
    supported: fn(&Path) -> bool,
    recursive: bool,
    results: &mut Vec<String>,
    warnings: &mut Vec<String>,
) {
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
                let metadata = match std::fs::symlink_metadata(&path) {
                    Ok(metadata) => metadata,
                    Err(error) => {
                        warnings.push(format!("{}: {}", path.display(), error));
                        continue;
                    }
                };
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if metadata.file_attributes() & 0x400 != 0 { continue; }
                }
                if metadata.is_dir() && recursive {
                    collect_supported_files(&path, supported, recursive, results, warnings);
                } else if metadata.is_file() && supported(&path) {
                    results.push(path.to_string_lossy().to_string());
                }
            }
            Err(error) => warnings.push(format!("{}: {}", dir.display(), error)),
        }
    }
}

fn collect_media_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    collect_supported_files(dir, is_media_file, true, results, warnings);
}

fn collect_audio_files_shallow(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    collect_supported_files(dir, is_audio_file, false, results, warnings);
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
pub(crate) const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"];

fn is_image_file(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| IMAGE_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn collect_image_files(dir: &Path, results: &mut Vec<String>, warnings: &mut Vec<String>) {
    collect_supported_files(dir, is_image_file, true, results, warnings);
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
pub async fn scan_image_paths(app: AppHandle, paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
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
    remember_scanned_inputs(&app, &files);
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths(app: AppHandle, paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
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
    remember_scanned_inputs(&app, &files);
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_audio_paths_with_options(
    app: AppHandle,
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
    remember_scanned_inputs(&app, &files);
    Ok(ScanAudioPathsResult { files, warnings })
}

#[tauri::command]
pub async fn scan_media_paths(app: AppHandle, paths: Vec<String>) -> AppResult<ScanAudioPathsResult> {
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
    remember_scanned_inputs(&app, &files);
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

