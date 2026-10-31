use crate::error::{AppError, AppResult};
use crate::model_dir_migration::{
    self, CancelModelDirMigrationRequest, ConfirmModelDirMigrationSwitchRequest,
    PrepareModelDirChangeRequest, RespondModelDirMigrationConflictRequest,
    StartModelDirMigrationRequest,
};
use crate::python::worker::{
    run_worker_with_payload, spawn_worker_background, kill_process_tree,
};
use crate::session_log;
use crate::state::AppState;
use crate::commands::app::scan::collect_audio_files;
use crate::storage;
use crate::commands::app::dialogs::path_to_string;
use crate::commands::app::editor::{file_name_from_path, LinkedEditorAsset};
use crate::workflow_editors::{RegistrySnapshot, WorkflowEditors};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_dialog::DialogExt;













pub(crate) fn chrono_like_timestamp() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis())
        .unwrap_or_default()
}


pub(crate) fn normalize_path_key(value: &str) -> String {
    if cfg!(windows) {
        value.replace('\\', "/").to_ascii_lowercase()
    } else {
        value.to_string()
    }
}

fn normalized_relative_path(path: &Path) -> Option<String> {
    let parts: Vec<String> = path
        .components()
        .filter_map(|component| match component {
            std::path::Component::Normal(value) => value.to_str().map(str::to_string),
            _ => None,
        })
        .filter(|part| !part.is_empty())
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join("/"))
    }
}

fn relative_path_from_root(root: &Path, target: &Path) -> Option<String> {
    target
        .strip_prefix(root)
        .ok()
        .and_then(normalized_relative_path)
}

pub(crate) fn linked_asset_from_path(
    path: &Path,
    origin_kind: &str,
    origin_root: Option<&Path>,
    relative_path: Option<&Path>,
) -> LinkedEditorAsset {
    let relative = relative_path
        .and_then(normalized_relative_path)
        .or_else(|| origin_root.and_then(|root| relative_path_from_root(root, path)))
        .or_else(|| path.file_name().and_then(|value| value.to_str()).map(str::to_string));
    LinkedEditorAsset {
        path: path_to_string(path),
        name: file_name_from_path(&path_to_string(path)),
        origin_kind: origin_kind.to_string(),
        origin_root: origin_root.map(path_to_string),
        relative_path: relative,
        missing: !path.is_file(),
    }
}

fn detect_editor_source_origin(project: &Value, source: &Value) -> (String, Option<String>, Option<String>) {
    let role = source.get("role").and_then(Value::as_str).unwrap_or("reference");
    let path = source.get("path").and_then(Value::as_str).unwrap_or_default();
    let source_path = PathBuf::from(path);

    let stored_kind = source
        .get("originKind")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let stored_root = source
        .get("originRoot")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());
    let stored_relative = source
        .get("relativePath")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|value| !value.trim().is_empty());

    if stored_kind.is_some() || stored_root.is_some() || stored_relative.is_some() {
        return (
            stored_kind.unwrap_or_else(|| if role == "stem" { "task-result" } else { "external" }.to_string()),
            stored_root,
            stored_relative.or_else(|| source_path.file_name().and_then(|value| value.to_str()).map(str::to_string)),
        );
    }

    if role == "stem" {
        if let Some(result_dir) = project
            .get("sourceResultDir")
            .and_then(Value::as_str)
            .map(PathBuf::from)
        {
            let relative = relative_path_from_root(&result_dir, &source_path)
                .or_else(|| source_path.file_name().and_then(|value| value.to_str()).map(str::to_string));
            return ("task-result".to_string(), Some(path_to_string(&result_dir)), relative);
        }
    }

    let parent = source_path.parent().map(path_to_string);
    let relative = source_path.file_name().and_then(|value| value.to_str()).map(str::to_string);
    ("legacy".to_string(), parent, relative)
}

pub(crate) fn enrich_editor_project_sources(project: &mut Value) -> bool {
    let project_snapshot = project.clone();
    let Some(sources) = project.get_mut("sources").and_then(Value::as_array_mut) else {
        return false;
    };

    let mut changed = false;
    for source in sources.iter_mut() {
        let Some(source_object) = source.as_object_mut() else {
            continue;
        };
        let path = source_object
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let source_path = PathBuf::from(&path);
        let missing = !source_path.is_file();
        if source_object.get("missing").and_then(Value::as_bool) != Some(missing) {
            source_object.insert("missing".into(), Value::Bool(missing));
            changed = true;
        }

        let source_value = Value::Object(source_object.clone());
        let (origin_kind, origin_root, relative_path) =
            detect_editor_source_origin(&project_snapshot, &source_value);

        if source_object
            .get("originKind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
        {
            source_object.insert("originKind".into(), Value::String(origin_kind));
            changed = true;
        }

        if !source_object.contains_key("originRoot") {
            source_object.insert(
                "originRoot".into(),
                origin_root.map(Value::String).unwrap_or(Value::Null),
            );
            changed = true;
        }

        if !source_object.contains_key("relativePath") {
            source_object.insert(
                "relativePath".into(),
                relative_path.map(Value::String).unwrap_or(Value::Null),
            );
            changed = true;
        }
    }

    changed
}

fn source_relative_parts(relative_path: &str) -> Vec<String> {
    relative_path
        .split(['/', '\\'])
        .filter(|part| !part.trim().is_empty())
        .map(str::to_string)
        .collect()
}

fn path_tail_matches(path: &Path, relative_path: &str) -> bool {
    let rel_parts = source_relative_parts(relative_path);
    if rel_parts.is_empty() {
        return false;
    }
    let path_parts: Vec<String> = path
        .components()
        .filter_map(|component| match component {
            std::path::Component::Normal(value) => value.to_str().map(str::to_string),
            _ => None,
        })
        .collect();
    if path_parts.len() < rel_parts.len() {
        return false;
    }
    let tail = &path_parts[path_parts.len() - rel_parts.len()..];
    tail.iter()
        .map(|part| part.to_ascii_lowercase())
        .eq(rel_parts.iter().map(|part| part.to_ascii_lowercase()))
}

pub(crate) fn derive_relink_root(picked_path: &Path, relative_path: Option<&str>) -> Option<PathBuf> {
    let relative_path = relative_path?;
    if !path_tail_matches(picked_path, relative_path) {
        return None;
    }
    let component_count = source_relative_parts(relative_path).len();
    if component_count == 0 {
        return None;
    }
    let mut cursor = picked_path.to_path_buf();
    for _ in 0..component_count {
        cursor = cursor.parent()?.to_path_buf();
    }
    Some(cursor)
}

pub(crate) fn build_file_name_index(root: &Path) -> std::collections::HashMap<String, Vec<PathBuf>> {
    let mut files = Vec::new();
    let mut warnings = Vec::new();
    collect_audio_files(root, &mut files, &mut warnings);
    let mut index = std::collections::HashMap::<String, Vec<PathBuf>>::new();
    for file in files {
        let path = PathBuf::from(&file);
        if let Some(name) = path.file_name().and_then(|value| value.to_str()) {
            index.entry(name.to_ascii_lowercase()).or_default().push(path);
        }
    }
    index
}

pub(crate) fn preferred_match_by_relative_path<'a>(candidates: &'a [PathBuf], relative_path: Option<&str>) -> Option<&'a PathBuf> {
    let relative_path = relative_path?;
    let mut filtered = candidates.iter().filter(|path| path_tail_matches(path, relative_path));
    let first = filtered.next()?;
    if filtered.next().is_some() {
        None
    } else {
        Some(first)
    }
}

pub(crate) fn build_relink_candidate(
    relink_root: &Path,
    relative_path: Option<&str>,
    file_name_index: &std::collections::HashMap<String, Vec<PathBuf>>,
) -> Option<PathBuf> {
    if let Some(relative) = relative_path {
        // Join segment-by-segment so PathBuf inserts the platform separator,
        // instead of hardcoding a Windows backslash.
        let mut direct = relink_root.to_path_buf();
        for part in source_relative_parts(relative) {
            direct.push(part);
        }
        if direct.is_file() {
            return Some(direct);
        }
    }

    let file_name = relative_path
        .and_then(|value| source_relative_parts(value).last().cloned())
        .map(|value| value.to_ascii_lowercase())?;
    let candidates = file_name_index.get(&file_name)?;
    if let Some(preferred) = preferred_match_by_relative_path(candidates, relative_path) {
        return Some(preferred.clone());
    }
    if candidates.len() == 1 {
        return candidates.first().cloned();
    }
    None
}




#[cfg(test)]
mod tests {
    use crate::commands::app::trash::{is_effectively_empty_dir, normalize_trash_empty_dirs};
    use crate::commands::app::editor::{pcm_wav_header, validate_recording_id};
    use crate::python::worker::cleanup_editor_export_temp_files;
    use std::process::Command;
    use crate::commands::app::scan::reveal_target_path;
    use crate::commands::app::editor::{decode_editor_preview_chunk, editor_project_id_for_task_id, editor_project_ids_for_task_ids, orphaned_editor_projects, remove_editor_project_dirs, safe_file_name, validate_editor_project_id};
    use crate::commands::app::debug::validate_debug_runtime_override;
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_test_dir(name: &str) -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time should be after unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!("pymss-studio-{name}-{unique}"))
    }

    #[test]
    fn editor_project_id_validation_preserves_normal_project_identity() {
        for project_id in [
            "edit_task_1720000000000_abc12",
            "edit_blank_1720000000000",
            "project-with.dots",
        ] {
            assert_eq!(validate_editor_project_id(project_id).unwrap(), project_id);
        }
        let longest_id = "a".repeat(96);
        assert_eq!(validate_editor_project_id(&longest_id).unwrap(), longest_id);
    }

    #[test]
    fn editor_project_id_validation_rejects_path_and_label_aliases() {
        for project_id in [
            "", " ", ".", "..", "../project", "project/name", "project\\name",
            "project:name", "project?name", "project.", "_project", "project_", "目录",
        ] {
            assert!(validate_editor_project_id(project_id).is_err(), "accepted {project_id}");
        }
        let long_a = format!("{}a", "x".repeat(96));
        let long_b = format!("{}b", "x".repeat(96));
        assert_eq!(safe_file_name(&long_a), safe_file_name(&long_b));
        assert!(validate_editor_project_id(&long_a).is_err());
        assert!(validate_editor_project_id(&long_b).is_err());
    }

    #[test]
    fn effectively_empty_dir_allows_ds_store_and_empty_children() {
        let root = temp_test_dir("empty-dir-ds-store");
        let child = root.join("album").join("track");
        fs::create_dir_all(&child).expect("create nested test dirs");
        fs::write(root.join(".DS_Store"), b"finder metadata").expect("write ds store");
        fs::write(child.join(".DS_Store"), b"finder metadata").expect("write nested ds store");

        assert!(is_effectively_empty_dir(&root));

        fs::remove_dir_all(&root).expect("remove test dir");
    }

    #[test]
    fn effectively_empty_dir_rejects_user_files() {
        let root = temp_test_dir("empty-dir-user-file");
        fs::create_dir_all(&root).expect("create test dir");
        fs::write(root.join("vocals.wav"), b"audio").expect("write user file");

        assert!(!is_effectively_empty_dir(&root));

        fs::remove_dir_all(&root).expect("remove test dir");
    }

    #[test]
    fn editor_export_temp_cleanup_only_removes_the_cancelled_task_files() {
        let root = temp_test_dir("editor-export-temp-cleanup");
        fs::create_dir_all(&root).expect("create export temp root");
        let cancelled = root.join(".pymss-export-task_one-abcd.wav.part");
        let mix_buffer = root.join(".pymss-export-task_one-abcd.mix.part");
        let cancel_token = root.join(".pymss-export-task_one.cancel");
        let other = root.join(".pymss-export-task_two-abcd.wav.part");
        let completed = root.join("mix.wav");
        fs::write(&cancelled, b"partial").expect("write cancelled export temp");
        fs::write(&mix_buffer, b"partial mix").expect("write cancelled mix temp");
        fs::write(&cancel_token, b"cancel").expect("write cancelled export token");
        fs::write(&other, b"partial").expect("write other export temp");
        fs::write(&completed, b"audio").expect("write completed export");

        cleanup_editor_export_temp_files(&root, "task:one");

        assert!(!cancelled.exists());
        assert!(!mix_buffer.exists());
        assert!(!cancel_token.exists());
        assert!(other.exists());
        assert!(completed.exists());
        fs::remove_dir_all(&root).expect("remove export temp root");
    }

    #[test]
    fn reveal_target_falls_back_to_existing_parent_for_missing_file() {
        let root = temp_test_dir("reveal-missing-file");
        let missing = root.join("runtime").join("install.log");
        fs::create_dir_all(missing.parent().unwrap()).expect("create reveal parent");

        assert_eq!(reveal_target_path(&missing), missing.parent().unwrap());

        fs::remove_dir_all(&root).expect("remove reveal test dir");
    }

    #[test]
    fn reveal_target_uses_the_file_parent_for_existing_file() {
        let root = temp_test_dir("reveal-existing-file");
        let file = root.join("install.log");
        fs::create_dir_all(&root).expect("create reveal root");
        fs::write(&file, b"log").expect("write reveal file");

        assert_eq!(reveal_target_path(&file), root.as_path());

        fs::remove_dir_all(&root).expect("remove reveal test dir");
    }

    #[test]
    fn pcm_wav_header_describes_mono_pcm16_audio() {
        let header = pcm_wav_header(96_000, 48_000, 1).expect("create wav header");

        assert_eq!(&header[0..4], b"RIFF");
        assert_eq!(u32::from_le_bytes(header[4..8].try_into().unwrap()), 96_036);
        assert_eq!(&header[8..12], b"WAVE");
        assert_eq!(u16::from_le_bytes(header[20..22].try_into().unwrap()), 1);
        assert_eq!(u16::from_le_bytes(header[22..24].try_into().unwrap()), 1);
        assert_eq!(u32::from_le_bytes(header[24..28].try_into().unwrap()), 48_000);
        assert_eq!(u32::from_le_bytes(header[28..32].try_into().unwrap()), 96_000);
        assert_eq!(u16::from_le_bytes(header[34..36].try_into().unwrap()), 16);
        assert_eq!(u32::from_le_bytes(header[40..44].try_into().unwrap()), 96_000);
    }

    #[test]
    fn recording_id_validation_rejects_path_components() {
        assert!(validate_recording_id("recording_1720000000000").is_ok());
        assert!(validate_recording_id("../recording_1720000000000").is_err());
        assert!(validate_recording_id("recording_foo/bar").is_err());
        assert!(validate_recording_id("other_1720000000000").is_err());
    }

    #[test]
    fn debug_runtime_override_validates_the_interpreter_and_backend() {
        let root = temp_test_dir("runtime-debug-override");
        let python = root.join("cuda").join("bin").join("python");
        fs::create_dir_all(python.parent().unwrap()).expect("create runtime environment");
        fs::write(&python, b"stub").expect("write runtime interpreter");

        let (backend, resolved) = validate_debug_runtime_override(" CUDA ", &python.to_string_lossy())
            .expect("validate runtime override");
        assert_eq!(backend, "cuda");
        assert_eq!(PathBuf::from(resolved), python.canonicalize().unwrap());
        assert!(validate_debug_runtime_override("cpu", &python.to_string_lossy()).is_err());
        assert!(validate_debug_runtime_override("cuda", "missing/python").is_err());

        fs::remove_dir_all(&root).expect("remove runtime override test root");
    }

    #[test]
    fn editor_project_ids_for_tasks_are_safe_and_deduplicated() {
        let task_ids = vec![
            " task/one ".to_string(),
            "task/one".to_string(),
            "".to_string(),
            "task-two".to_string(),
        ];

        assert_eq!(
            editor_project_ids_for_task_ids(&task_ids),
            vec!["edit_task_one".to_string(), "edit_task-two".to_string()]
        );
    }

    #[test]
    fn editor_project_cleanup_removes_existing_dirs_and_reports_missing_ones() {
        let root = temp_test_dir("editor-project-cleanup");
        let existing_id = "edit_existing".to_string();
        let missing_id = "edit_missing".to_string();
        let recordings = root.join(&existing_id).join("recordings");
        fs::create_dir_all(&recordings).expect("create editor project directory");
        fs::write(recordings.join("take.wav"), b"audio").expect("write editor recording");
        fs::write(
            root.join(&existing_id).join("project.json"),
            r#"{"id":"edit_existing","sourceTaskId":"existing"}"#,
        ).expect("write editor project ownership");

        let result = remove_editor_project_dirs(
            &root,
            &[existing_id.clone(), missing_id.clone()],
            &["existing".to_string()],
        );

        assert_eq!(result.deleted_project_ids, vec![existing_id.clone()]);
        assert_eq!(result.missing_project_ids, vec![missing_id]);
        assert!(result.blocked_project_ids.is_empty());
        assert!(result.failed_project_ids.is_empty());
        assert!(!root.join(existing_id).exists());
        fs::remove_dir_all(&root).expect("remove editor cleanup test root");
    }

    #[test]
    fn orphaned_editor_projects_only_returns_unlinked_task_projects() {
        let root = temp_test_dir("orphaned-editor-projects");
        let active_dir = root.join("edit_active");
        let orphan_dir = root.join("edit_orphan");
        let blank_dir = root.join("edit_blank_1");
        fs::create_dir_all(&active_dir).expect("create active editor project");
        fs::create_dir_all(&orphan_dir).expect("create orphaned editor project");
        fs::create_dir_all(&blank_dir).expect("create blank editor project");
        fs::write(
            active_dir.join("project.json"),
            r#"{"id":"edit_active","name":"Active","sourceTaskId":"task-active"}"#,
        )
        .expect("write active editor project");
        fs::write(
            orphan_dir.join("project.json"),
            r#"{"id":"edit_orphan","name":"Orphan","sourceTaskId":"task-orphan"}"#,
        )
        .expect("write orphaned editor project");
        fs::write(
            blank_dir.join("project.json"),
            r#"{"id":"edit_blank_1","name":"Blank"}"#,
        )
        .expect("write blank editor project");

        let projects = orphaned_editor_projects(&root, &["task-active".to_string()])
            .expect("list orphaned editor projects");

        assert_eq!(projects.len(), 1);
        assert_eq!(projects[0].project_id, "edit_orphan");
        assert_eq!(projects[0].source_task_id, "task-orphan");
        fs::remove_dir_all(&root).expect("remove orphaned project test root");
    }

    #[test]
    fn editor_project_cleanup_rejects_lossy_task_id_collisions_and_unknown_ownership() {
        let root = temp_test_dir("editor-project-ownership");
        let project_id = editor_project_id_for_task_id("a:b");
        assert_eq!(project_id, editor_project_id_for_task_id("a/b"));
        let project_dir = root.join(&project_id);
        fs::create_dir_all(&project_dir).expect("create project directory");
        let project_path = project_dir.join("project.json");
        fs::write(&project_path, r#"{"id":"edit_a_b","sourceTaskId":"a:b"}"#).unwrap();
        let result = remove_editor_project_dirs(&root, &[project_id.clone()], &["a/b".to_string()]);
        assert_eq!(result.failed_project_ids, vec![project_id.clone()]);
        assert!(project_dir.is_dir());
        for content in [r#"{"id":"edit_a_b"}"#, r#"{"id":"other","sourceTaskId":"a:b"}"#, "invalid"] {
            fs::write(&project_path, content).unwrap();
            let result = remove_editor_project_dirs(&root, &[project_id.clone()], &["a:b".to_string()]);
            assert_eq!(result.failed_project_ids, vec![project_id.clone()]);
            assert!(project_dir.is_dir());
        }
        fs::write(&project_path, r#"{"id":"edit_a_b","sourceTaskId":"a:b"}"#).unwrap();
        let result = remove_editor_project_dirs(&root, &[project_id.clone()], &[" a:b ".to_string()]);
        assert_eq!(result.deleted_project_ids, vec![project_id]);
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn native_path_keys_preserve_posix_case_and_literal_backslashes() {
        if cfg!(windows) {
            assert_eq!(normalize_path_key("C:\\Audio\\Song.wav"), normalize_path_key("c:/audio/song.wav"));
        } else {
            assert_ne!(normalize_path_key("/audio/Foo.wav"), normalize_path_key("/audio/foo.wav"));
            assert_ne!(normalize_path_key("/audio/a\\b.wav"), normalize_path_key("/audio/a/b.wav"));
            assert_eq!(normalize_trash_empty_dirs(vec!["/audio/Foo".into(), "/audio/foo".into()]).len(), 2);
        }
    }

    #[test]
    fn empty_directory_checks_do_not_follow_links_or_directory_cycles() {
        let root = temp_test_dir("empty-directory-links");
        fs::create_dir_all(&root).unwrap();
        assert!(is_effectively_empty_dir(&root));
        fs::write(root.join(".DS_Store"), b"metadata").unwrap();
        assert!(is_effectively_empty_dir(&root));
        let link = root.join("loop");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&root, &link).unwrap();
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let status = std::process::Command::new("cmd.exe")
                .raw_arg(format!("/c mklink /J \"{}\" \"{}\"", link.display(), root.display()))
                .output().expect("create directory junction");
            assert!(status.status.success());
        }
        assert!(!is_effectively_empty_dir(&link));
        assert!(!is_effectively_empty_dir(&root));
        let result = remove_editor_project_dirs(&root, &["loop".to_string()], &["task".to_string()]);
        assert_eq!(result.failed_project_ids, vec!["loop".to_string()]);
        assert!(root.is_dir());
        #[cfg(unix)]
        fs::remove_file(&link).unwrap();
        #[cfg(windows)]
        fs::remove_dir(&link).unwrap();
        assert!(is_effectively_empty_dir(&root));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn invalid_requests_are_rejected_before_spawning() {
        for (rate, channels, index) in [(0, 2, 0), (48_000, 3, 0), (48_000, 2, u64::MAX)] {
            assert!(decode_editor_preview_chunk(Path::new("not-a-program"), Path::new("/missing"), index, rate, channels).is_err());
        }
        assert!(decode_editor_preview_chunk(Path::new("not-a-program"), Path::new("relative.wav"), 0, 48_000, 2).is_err());
    }

    fn preview_wave_fixture(rate: u32) -> PathBuf {
        let mut pcm = Vec::new();
        for frame in 0..rate * 9 {
            let sample = ((frame % 997) as i16 - 498) * 40;
            pcm.extend_from_slice(&sample.to_le_bytes());
            pcm.extend_from_slice(&(-sample).to_le_bytes());
        }
        let mut wav = b"RIFF".to_vec();
        wav.extend_from_slice(&(pcm.len() as u32 + 36).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&rate.to_le_bytes());
        wav.extend_from_slice(&(rate * 4).to_le_bytes());
        wav.extend_from_slice(&4u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
        wav.extend_from_slice(&pcm);
        let path = temp_test_dir("preview-chunks").with_extension("wav");
        std::fs::write(&path, wav).unwrap();
        path
    }

    #[test]
    fn consecutive_chunks_match_a_full_decode_without_missing_or_repeating_samples() {
        let ffmpeg = Path::new("ffmpeg");
        if Command::new(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping native codec check");
            return;
        }
        for source_rate in [44_100, 48_000, 96_000] {
            let path = preview_wave_fixture(source_rate);
            for rate in [44_100, 48_000] {
                let full = Command::new(ffmpeg).args(["-v", "error", "-i"]).arg(&path)
                    .arg("-ar").arg(rate.to_string())
                    .args(["-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]).output().unwrap();
                let pieces: Vec<_> = (0..3).map(|index| decode_editor_preview_chunk(ffmpeg, &path, index, rate, 2).unwrap()).collect();
                assert!(full.status.success());
                assert_eq!(pieces[0].len(), rate as usize * 4 * 2 * 4);
                assert_eq!(pieces[2].len(), rate as usize * 2 * 4);
                assert_eq!(pieces.concat(), full.stdout, "resampling {source_rate} to {rate} changed chunk samples");
            }
            std::fs::remove_file(path).unwrap();
        }
    }

    #[test]
    fn compressed_preview_chunks_keep_gapless_timing_and_sample_alignment() {
        let ffmpeg = Path::new("ffmpeg");
        if Command::new(ffmpeg).arg("-version").output().is_err() {
            eprintln!("FFmpeg not on PATH; skipping compressed codec check");
            return;
        }
        let wave = preview_wave_fixture(44_100);
        for (codec, extension) in [("libmp3lame", "mp3"), ("aac", "m4a"), ("aac", "aac"), ("libopus", "opus")] {
            let encoded = wave.with_extension(extension);
            let mut encoder = Command::new(ffmpeg);
            encoder.args(["-v", "error", "-y", "-i"]).arg(&wave).arg("-c:a").arg(codec);
            // Make the AAC fixture deterministic, without random perceptual noise.
            if codec == "aac" { encoder.args(["-aac_pns", "0"]); }
            let encoded_result = encoder.arg(&encoded).output().unwrap();
            if !encoded_result.status.success() {
                eprintln!("Encoder {codec} unavailable; skipping its fixture");
                continue;
            }
            for rate in [44_100, 48_000] {
                let full = Command::new(ffmpeg).args(["-v", "error", "-threads", "1", "-i"]).arg(&encoded)
                    .arg("-ar").arg(rate.to_string())
                    .args(["-ac", "2", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"])
                    .output().unwrap();
                assert!(full.status.success());
                let pieces: Vec<_> = (0..3).map(|index| decode_editor_preview_chunk(ffmpeg, &encoded, index, rate, 2).unwrap()).collect();
                let joined = pieces.concat();
                assert_eq!(joined.len(), full.stdout.len(), "{extension} at {rate} lost samples at a chunk boundary");
                let max_error = joined.chunks_exact(4).zip(full.stdout.chunks_exact(4))
                    .map(|(a, b)| (f32::from_le_bytes(a.try_into().unwrap()) - f32::from_le_bytes(b.try_into().unwrap())).abs())
                    .fold(0.0f32, f32::max);
                assert!(max_error < 0.00001, "{extension} at {rate} chunk alignment error: {max_error}");
            }
            fs::remove_file(&encoded).unwrap();
        }
        fs::remove_file(wave).unwrap();
    }
}
