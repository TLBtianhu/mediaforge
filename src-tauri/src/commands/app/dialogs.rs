//! 文件与目录选择对话框。
//!
//! 文件和目录选择。只有输入选择会记住预览来源。

use crate::error::AppResult;
use std::path::{Path, PathBuf};
use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, FilePath};

fn remember_dialog_selection(app: &AppHandle, files: &[FilePath]) {
    let paths = files.iter().filter_map(FilePath::as_path).map(Path::to_path_buf).collect::<Vec<_>>();
    super::scan::remember_input_selection(app, &paths);
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PreviewInputKind {
    Image,
    Video,
    Library,
}

#[tauri::command]
pub async fn pick_preview_files(app: AppHandle, kind: PreviewInputKind) -> AppResult<Vec<String>> {
    let (name, extensions): (&str, &[&str]) = match kind {
        PreviewInputKind::Image => ("Images", super::scan::IMAGE_EXTENSIONS),
        PreviewInputKind::Video => ("Videos", &["mp4", "mov", "mkv", "avi", "webm", "flv", "wmv", "m4v"]),
        PreviewInputKind::Library => ("Media", &[
            "mp4", "mov", "mkv", "avi", "webm", "mp3", "wav", "flac", "m4a", "aac", "ogg",
            "jpg", "jpeg", "png", "webp", "gif",
        ]),
    };
    let files = app.dialog().file().add_filter(name, extensions).blocking_pick_files().unwrap_or_default();
    remember_dialog_selection(&app, &files);
    Ok(files.into_iter().map(|path| path.to_string()).collect())
}

#[tauri::command]
pub async fn pick_preview_folders(app: AppHandle) -> AppResult<Vec<String>> {
    let folders = app.dialog().file().blocking_pick_folders().unwrap_or_default();
    remember_dialog_selection(&app, &folders);
    Ok(folders.into_iter().map(|path| path.to_string()).collect())
}

#[tauri::command]
pub async fn pick_audio_files(app: AppHandle) -> AppResult<Vec<String>> {
    let files = app
        .dialog()
        .file()
        .add_filter(
            "Audio",
            &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"],
        )
        .blocking_pick_files()
        .unwrap_or_default();
    remember_dialog_selection(&app, &files);
    Ok(files.into_iter().map(|p| p.to_string()).collect())
}

/// Pick a model weights file. Extensions mirror what pymss can actually load with `torch.load`;
/// `.safetensors` is deliberately absent because pymss has no safetensors path at all.
///
/// `title` is supplied by the caller so it can be localised, and so that back-to-back weights
/// and config dialogs are told apart by more than their file filter.
#[tauri::command]
pub async fn pick_model_weights_file(app: AppHandle, title: Option<String>) -> AppResult<Option<String>> {
    let mut builder = app
        .dialog()
        .file()
        .add_filter("Model weights", &["ckpt", "pth", "th", "pt", "bin"]);
    if let Some(title) = title.as_deref().filter(|value| !value.trim().is_empty()) {
        builder = builder.set_title(title);
    }
    Ok(builder.blocking_pick_file().map(|p| p.to_string()))
}

#[tauri::command]
pub async fn pick_model_config_file(app: AppHandle, title: Option<String>) -> AppResult<Option<String>> {
    let mut builder = app.dialog().file().add_filter("Model config", &["yaml", "yml"]);
    if let Some(title) = title.as_deref().filter(|value| !value.trim().is_empty()) {
        builder = builder.set_title(title);
    }
    Ok(builder.blocking_pick_file().map(|p| p.to_string()))
}

#[tauri::command]
pub async fn pick_single_audio_file(app: AppHandle) -> AppResult<Option<String>> {
    let file = app
        .dialog()
        .file()
        .add_filter(
            "Audio",
            &["wav", "mp3", "flac", "m4a", "aac", "ogg", "opus"],
        )
        .blocking_pick_file();
    remember_dialog_selection(&app, file.as_slice());
    Ok(file.map(|path| path.to_string()))
}

pub(crate) fn path_to_string(path: &Path) -> String {
    path.to_string_lossy().to_string()
}


#[tauri::command]
pub async fn pick_media_files(app: AppHandle) -> AppResult<Vec<String>> {
    let files = app
        .dialog()
        .file()
        .add_filter(
            "Media / 媒体",
            &[
                "wav", "mp3", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mkv", "mov", "avi",
                "webm", "flv",
            ],
        )
        .blocking_pick_files()
        .unwrap_or_default();
    remember_dialog_selection(&app, &files);
    Ok(files.into_iter().map(|p| p.to_string()).collect())
}

#[tauri::command]
pub async fn pick_input_folder(app: AppHandle) -> AppResult<Option<String>> {
    let folder = app.dialog().file().blocking_pick_folder();
    remember_dialog_selection(&app, folder.as_slice());
    Ok(folder.map(|path| path.to_string()))
}

#[tauri::command]
pub async fn pick_output_folder(app: AppHandle) -> AppResult<Option<String>> {
    Ok(app
        .dialog()
        .file()
        .blocking_pick_folder()
        .map(|p| p.to_string()))
}

/// Opens a "Save As" dialog seeded with `default_name` and writes `content` to
/// the chosen path. Returns the saved path, or `None` if the user cancelled.
#[tauri::command]
pub async fn save_text_file_dialog(
    app: AppHandle,
    default_name: String,
    content: String,
) -> AppResult<Option<String>> {
    let mut builder = app.dialog().file();
    if !default_name.trim().is_empty() {
        builder = builder.set_file_name(&default_name);
        if let Some(ext) = std::path::Path::new(&default_name)
            .extension()
            .and_then(|value| value.to_str())
        {
            builder = builder.add_filter(ext.to_uppercase(), &[ext]);
        }
    }
    let Some(path) = builder.blocking_save_file() else {
        return Ok(None);
    };
    let path_string = path.to_string();
    let path_buf = PathBuf::from(&path_string);
    if let Some(parent) = path_buf.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path_buf, content.as_bytes())?;
    Ok(Some(path_string))
}
