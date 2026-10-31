//! 窗口控制命令：关闭、最小化、最大化、拖动。
//!
//! 从 app_cmd.rs 拆出；只依赖 WebviewWindow。

use crate::error::{AppError, AppResult};


#[tauri::command]
pub async fn close_current_window(window: tauri::WebviewWindow) -> AppResult<()> {
    window
        .destroy()
        .map_err(|error| AppError::Worker(error.to_string()))
}

#[tauri::command]
pub async fn minimize_current_window(window: tauri::WebviewWindow) -> AppResult<()> {
    window
        .minimize()
        .map_err(|error| AppError::Worker(error.to_string()))
}

#[tauri::command]
pub async fn toggle_maximize_current_window(window: tauri::WebviewWindow) -> AppResult<bool> {
    let maximized = window
        .is_maximized()
        .map_err(|error| AppError::Worker(error.to_string()))?;
    if maximized {
        window
            .unmaximize()
            .map_err(|error| AppError::Worker(error.to_string()))?;
        Ok(false)
    } else {
        window
            .maximize()
            .map_err(|error| AppError::Worker(error.to_string()))?;
        Ok(true)
    }
}

#[tauri::command]
pub async fn is_current_window_maximized(window: tauri::WebviewWindow) -> AppResult<bool> {
    window
        .is_maximized()
        .map_err(|error| AppError::Worker(error.to_string()))
}

#[tauri::command]
pub async fn start_drag_current_window(window: tauri::WebviewWindow) -> AppResult<()> {
    window
        .start_dragging()
        .map_err(|error| AppError::Worker(error.to_string()))
}
