//! 编辑器音频预览：元数据、PCM 分块解码与波形峰值。

use super::editor_project_dir;
use crate::error::{AppError, AppResult};
use crate::python::worker::{bundled_bin_dirs, run_worker_with_payload};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::AppHandle;

#[tauri::command]
pub async fn get_audio_metadata(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "audio_metadata", Some(payload))
}

const EDITOR_PREVIEW_CHUNK_SECONDS: u64 = 4;

pub(crate) fn decode_editor_preview_chunk(
    ffmpeg: &Path,
    path: &Path,
    index: u64,
    sample_rate: u32,
    channels: u32,
) -> AppResult<Vec<u8>> {
    if !(8_000..=192_000).contains(&sample_rate) || !(1..=2).contains(&channels) || index > 216_000
    {
        return Err(AppError::Worker("Invalid preview chunk parameters".into()));
    }
    if !path.is_absolute() || !path.is_file() {
        return Err(AppError::Worker("Preview audio file is unavailable".into()));
    }
    let mut command = Command::new(ffmpeg);
    let start = index * EDITOR_PREVIEW_CHUNK_SECONDS;
    let warm_start = start.saturating_sub(1);
    // Warm compressed decoders before the boundary, preserve original stream
    // timestamps, and trim there. A direct input seek can discard AAC priming
    // samples twice or begin MP3 decoding without its previous-frame state.
    command.args([
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-copyts",
        "-start_at_zero",
    ]);
    // Even an input seek to zero can reapply MP4/AAC priming. The first chunk
    // must follow the normal decode path from the beginning.
    if start > 0 {
        command.arg("-ss").arg(warm_start.to_string());
    }
    command.arg("-i").arg(path)
        .args(["-map", "0:a:0", "-vn", "-af"])
        // Reset the warmup window's timestamps before resampling, keeping its
        // phase on the same sample grid as a full decode (including MP4/AAC).
        // Trim AFTER resampling so both boundaries retain neighboring samples.
        .arg(format!("atrim=start={warm_start},asetpts=PTS-{warm_start}/TB,aresample={sample_rate}:first_pts=0,atrim=start={}:duration={EDITOR_PREVIEW_CHUNK_SECONDS},asetpts=PTS-STARTPTS", start - warm_start))
        .arg("-t").arg(EDITOR_PREVIEW_CHUNK_SECONDS.to_string())
        .arg("-ar").arg(sample_rate.to_string())
        .arg("-ac").arg(channels.to_string())
        .args(["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let output = command.output()?;
    if !output.status.success() {
        return Err(AppError::Worker(format!(
            "Preview decode failed: {}",
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    let frame_bytes = channels as usize * 4;
    let limit = EDITOR_PREVIEW_CHUNK_SECONDS as usize * sample_rate as usize * frame_bytes;
    if output.stdout.len() > limit || output.stdout.len() % frame_bytes != 0 {
        return Err(AppError::Worker("Invalid decoded preview chunk".into()));
    }
    Ok(output.stdout)
}

#[tauri::command]
pub async fn editor_preview_chunk(
    app: AppHandle,
    path: String,
    index: u64,
    sample_rate: u32,
    channels: u32,
) -> AppResult<tauri::ipc::Response> {
    let ffmpeg_name = if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    let ffmpeg = bundled_bin_dirs(&app)?
        .into_iter()
        .map(|dir| dir.join(ffmpeg_name))
        .find(|path| path.is_file())
        .unwrap_or_else(|| PathBuf::from(ffmpeg_name));
    let data = tauri::async_runtime::spawn_blocking(move || {
        decode_editor_preview_chunk(&ffmpeg, Path::new(&path), index, sample_rate, channels)
    })
    .await
    .map_err(|error| AppError::Worker(error.to_string()))??;
    Ok(tauri::ipc::Response::new(data))
}

#[tauri::command]
pub async fn generate_waveform_peaks(app: AppHandle, payload: Value) -> AppResult<Value> {
    let mut payload = payload;
    if let Some(object) = payload.as_object_mut() {
        if !object.contains_key("cacheDir") {
            let project_id = object
                .get("projectId")
                .and_then(Value::as_str)
                .unwrap_or("shared");
            let cache_dir = editor_project_dir(&app, project_id)?.join("peaks");
            std::fs::create_dir_all(&cache_dir)?;
            object.insert(
                "cacheDir".into(),
                Value::String(cache_dir.to_string_lossy().to_string()),
            );
        }
    }
    run_worker_with_payload(&app, "waveform_peaks", Some(payload))
}
