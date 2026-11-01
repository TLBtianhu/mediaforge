//! 编辑器录音：临时 PCM 的追加、完成、取消与 WAV 封装。

use super::{editor_project_dir, now_millis};
use crate::error::{AppError, AppResult};
use base64::Engine as _;
use serde::Serialize;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use tauri::AppHandle;

fn editor_recordings_dir(app: &AppHandle, project_id: &str) -> AppResult<PathBuf> {
    Ok(editor_project_dir(app, project_id)?.join("recordings"))
}

pub(crate) fn validate_recording_id(recording_id: &str) -> AppResult<&str> {
    if recording_id.starts_with("recording_")
        && recording_id.len() <= 96
        && super::safe_file_name(recording_id) == recording_id
    {
        Ok(recording_id)
    } else {
        Err(AppError::Worker("invalid recording id".into()))
    }
}

pub(crate) fn pcm_wav_header(
    data_len: u32,
    sample_rate: u32,
    channels: u16,
) -> AppResult<[u8; 44]> {
    if !(8_000..=192_000).contains(&sample_rate) || !(1..=2).contains(&channels) {
        return Err(AppError::Worker("invalid recording audio format".into()));
    }
    let byte_rate = sample_rate
        .checked_mul(channels as u32)
        .and_then(|value| value.checked_mul(2))
        .ok_or_else(|| AppError::Worker("recording audio format is too large".into()))?;
    let riff_size = data_len
        .checked_add(36)
        .ok_or_else(|| AppError::Worker("recording is too large".into()))?;
    let block_align = channels * 2;
    let mut header = [0u8; 44];
    header[0..4].copy_from_slice(b"RIFF");
    header[4..8].copy_from_slice(&riff_size.to_le_bytes());
    header[8..12].copy_from_slice(b"WAVE");
    header[12..16].copy_from_slice(b"fmt ");
    header[16..20].copy_from_slice(&16u32.to_le_bytes());
    header[20..22].copy_from_slice(&1u16.to_le_bytes());
    header[22..24].copy_from_slice(&channels.to_le_bytes());
    header[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    header[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    header[32..34].copy_from_slice(&block_align.to_le_bytes());
    header[34..36].copy_from_slice(&16u16.to_le_bytes());
    header[36..40].copy_from_slice(b"data");
    header[40..44].copy_from_slice(&data_len.to_le_bytes());
    Ok(header)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditorRecordingHandle {
    recording_id: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EditorRecordingResult {
    path: String,
    name: String,
    duration: f64,
    sample_rate: u32,
    channels: u16,
}

#[tauri::command]
pub async fn begin_editor_recording(
    app: AppHandle,
    project_id: String,
) -> AppResult<EditorRecordingHandle> {
    let recordings_dir = editor_recordings_dir(&app, &project_id)?;
    std::fs::create_dir_all(&recordings_dir)?;
    let base = now_millis();
    for suffix in 0..1000u16 {
        let recording_id = if suffix == 0 {
            format!("recording_{base}")
        } else {
            format!("recording_{base}_{suffix}")
        };
        let partial_path = recordings_dir.join(format!("{recording_id}.pcm.partial"));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(partial_path)
        {
            Ok(_) => return Ok(EditorRecordingHandle { recording_id }),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        }
    }
    Err(AppError::Worker(
        "could not allocate a recording file".into(),
    ))
}

#[tauri::command]
pub async fn append_editor_recording_chunk(
    app: AppHandle,
    project_id: String,
    recording_id: String,
    data_base64: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data_base64)
        .map_err(|error| AppError::Worker(format!("invalid recording chunk: {error}")))?;
    if bytes.is_empty() || bytes.len() > 2 * 1024 * 1024 || bytes.len() % 2 != 0 {
        return Err(AppError::Worker("invalid recording chunk size".into()));
    }
    let partial_path =
        editor_recordings_dir(&app, &project_id)?.join(format!("{recording_id}.pcm.partial"));
    let mut file = OpenOptions::new().append(true).open(partial_path)?;
    file.write_all(&bytes)?;
    Ok(())
}

#[tauri::command]
pub async fn finish_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
    sample_rate: u32,
    channels: u16,
) -> AppResult<EditorRecordingResult> {
    let recording_id = validate_recording_id(&recording_id)?;
    let recordings_dir = editor_recordings_dir(&app, &project_id)?;
    let partial_path = recordings_dir.join(format!("{recording_id}.pcm.partial"));
    let data_len_u64 = std::fs::metadata(&partial_path)?.len();
    if data_len_u64 == 0 || data_len_u64 % 2 != 0 || data_len_u64 > u32::MAX as u64 - 36 {
        return Err(AppError::Worker(
            "recording does not contain valid PCM audio".into(),
        ));
    }
    let data_len = data_len_u64 as u32;
    let output_path = recordings_dir.join(format!("{recording_id}.wav"));
    let output_partial_path = recordings_dir.join(format!("{recording_id}.wav.partial"));
    let mut input = std::fs::File::open(&partial_path)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output_partial_path)?;
    let write_result = (|| -> AppResult<()> {
        output.write_all(&pcm_wav_header(data_len, sample_rate, channels)?)?;
        std::io::copy(&mut input, &mut output)?;
        output.flush()?;
        output.sync_all()?;
        Ok(())
    })();
    drop(output);
    if let Err(error) = write_result {
        let _ = std::fs::remove_file(&output_partial_path);
        return Err(error);
    }
    std::fs::rename(&output_partial_path, &output_path)?;
    std::fs::remove_file(partial_path)?;

    let duration = data_len as f64 / (sample_rate as f64 * channels as f64 * 2.0);
    Ok(EditorRecordingResult {
        path: output_path.to_string_lossy().to_string(),
        name: format!("{recording_id}.wav"),
        duration,
        sample_rate,
        channels,
    })
}

#[tauri::command]
pub async fn cancel_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let partial_path =
        editor_recordings_dir(&app, &project_id)?.join(format!("{recording_id}.pcm.partial"));
    let wav_partial_path =
        editor_recordings_dir(&app, &project_id)?.join(format!("{recording_id}.wav.partial"));
    let mut first_error = None;
    for path in [partial_path, wav_partial_path] {
        if let Err(error) = std::fs::remove_file(path) {
            if error.kind() != std::io::ErrorKind::NotFound && first_error.is_none() {
                first_error = Some(error);
            }
        }
    }
    match first_error {
        Some(error) => Err(error.into()),
        None => Ok(()),
    }
}

#[tauri::command]
pub async fn discard_editor_recording(
    app: AppHandle,
    project_id: String,
    recording_id: String,
) -> AppResult<()> {
    let recording_id = validate_recording_id(&recording_id)?;
    let output_path = editor_recordings_dir(&app, &project_id)?.join(format!("{recording_id}.wav"));
    match std::fs::remove_file(output_path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}
