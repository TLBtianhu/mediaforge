//! 编辑器音频预览：元数据、PCM 分块解码与波形峰值。

use super::editor_project_dir;
use crate::commands::app::scan::is_audio_file;
use crate::error::{AppError, AppResult};
use crate::python::worker::{bundled_bin_dirs, run_worker_with_payload};
use serde_json::Value;
use std::io::Read;
use std::path::{Path, PathBuf};
use tauri::AppHandle;

#[tauri::command]
pub async fn get_audio_metadata(app: AppHandle, payload: Value) -> AppResult<Value> {
    require_selected_audio(&app, payload["path"].as_str().unwrap_or(""))?;
    run_worker_with_payload(&app, "audio_metadata", Some(payload))
}

fn require_selected_audio(app: &AppHandle, path: &str) -> AppResult<()> {
    let path = Path::new(path);
    if !is_audio_file(path) || !crate::preview_assets::is_allowed(app, path) {
        return Err(AppError::Worker("editor audio has not been selected or linked".into()));
    }
    Ok(())
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
    let mut command = crate::process_command::hidden_command(ffmpeg);
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
    require_selected_audio(&app, &path)?;
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
    let source = payload["path"].as_str().unwrap_or("").to_string();
    require_selected_audio(&app, &source)?;
    let project_id = payload["projectId"].as_str().unwrap_or("shared").to_string();
    let cache_dir = editor_project_dir(&app, &project_id)?.join("peaks");
    let object = payload.as_object_mut().ok_or_else(|| AppError::Worker("invalid waveform request".into()))?;
    object.insert("dataRoot".into(), serde_json::json!(crate::storage::data_root_dir(&app)?));
    object.insert("cacheDir".into(), serde_json::json!(cache_dir));
    let result = run_worker_with_payload(&app, "waveform_peaks", Some(payload))?;
    if let Some(path) = result["peaksPath"].as_str() {
        remember_managed_waveform(&app, &project_id, Path::new(path), Path::new(&source));
    }
    Ok(result)
}

pub(super) fn remember_managed_waveform(app: &AppHandle, project_id: &str, path: &Path, source: &Path) {
    let Ok(project) = editor_project_dir(app, project_id) else { return; };
    crate::preview_assets::remember_waveform(app, path, |file| {
        is_managed_waveform_cache(&project.join("peaks"), path, source, file)
    });
}

fn is_managed_waveform_cache(directory: &Path, path: &Path, source: &Path, mut file: impl Read) -> bool {
    let Some(parent) = path.parent() else { return false; };
    if !source.is_absolute() || !is_audio_file(source) {
        return false;
    }
    let (Ok(parent), Ok(directory)) = (parent.canonicalize(), directory.canonicalize()) else { return false; };
    if parent != directory { return false; }
    let name = path.file_name().and_then(|name| name.to_str()).unwrap_or("");
    let Some((source_and_digest, resolution)) = name.strip_suffix("_v2.json").and_then(|name| name.rsplit_once('_')) else {
        return false;
    };
    let Some((stem, digest)) = source_and_digest.rsplit_once('_') else { return false; };
    if source.file_stem().and_then(|stem| stem.to_str()) != Some(stem)
        || digest.len() != 16 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit())
        || !resolution.parse::<u64>().is_ok_and(|resolution| (80..=12000).contains(&resolution)) {
        return false;
    }
    let mut content = String::new();
    if file.read_to_string(&mut content).is_err() { return false; }
    let Ok(data) = crate::python::result_jobs::parse_reference_json(&content) else { return false; };
    let same_path = |value: &Value, expected: &Path| {
        value.as_str().is_some_and(|value| Path::new(value) == expected)
    };
    let number_array = |value: &Value| value.as_array().is_some_and(|values| values.iter().all(Value::is_number));
    data.as_object().is_some_and(|data| data.len() == 8)
        && same_path(&data["path"], source) && same_path(&data["peaksPath"], path)
        && number_array(&data["peaks"])
        && data["channelPeaks"].as_array().is_some_and(|values| values.iter().all(number_array))
        && data["resolution"].as_u64() == resolution.parse().ok()
        && ["duration", "sampleRate", "channels"].iter().all(|key| data[*key].is_number())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_owned_waveform_cache_for_this_source_is_previewable() {
        let temp = tempfile::tempdir().unwrap();
        let cache = temp.path().join("editor-projects/owned/peaks");
        std::fs::create_dir_all(&cache).unwrap();
        let source = temp.path().join("take.wav");
        let path = cache.join("take_0123456789abcdef_1400_v2.json");
        let mut waveform = json!({"path":source, "peaksPath":path, "peaks":[0.2],
            "channelPeaks":[[0.2]], "resolution":1400, "duration":1.0, "sampleRate":48000, "channels":1});
        let verify = |directory: &Path, source: &Path, waveform: &Value| {
            let bytes = serde_json::to_vec(waveform).unwrap();
            is_managed_waveform_cache(directory, &path, source, bytes.as_slice())
        };
        assert!(verify(&cache, &source, &waveform));
        assert!(!verify(&cache, &temp.path().join("other.wav"), &waveform));
        assert!(!verify(&temp.path().join("editor-projects/another/peaks"), &source, &waveform));
        waveform["foreign"] = json!("not waveform data");
        assert!(!verify(&cache, &source, &waveform));
    }
}
