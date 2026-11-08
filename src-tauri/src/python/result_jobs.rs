//! Canonical result-job removal. The request contains IDs, never caller paths.
use super::task_journal;
use crate::error::{AppError, AppResult};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

#[derive(Debug, Serialize, PartialEq)]
pub(crate) struct PathFailure { pub path: String, pub error: String }
#[derive(Debug, Serialize, PartialEq)]
pub(crate) struct RetainedPath { pub path: String, pub reason: String }
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct RemovalResult {
    pub schema_version: u32,
    pub checked_job_ids: Vec<String>,
    pub removed_job_ids: Vec<String>,
    pub trashed: Vec<String>,
    pub failed: Vec<PathFailure>,
    pub retained: Vec<RetainedPath>,
}

pub(crate) struct Protection { pub roots: Vec<PathBuf> }

#[derive(Clone, Copy, PartialEq)]
enum RemovalKind { Generic, Audio }

pub(crate) fn parse_reference_json(text: &str) -> AppResult<Value> { super::job_schema::parse(text) }

pub(crate) struct PathReferences(Vec<String>);
impl PathReferences {
    pub(crate) fn from_paths(paths: &[PathBuf]) -> Self { Self(paths.iter().map(|path| key(path)).collect()) }
    pub(crate) fn includes_directory(&self, directory: &Path) -> bool {
        let directory = key(directory);
        self.0.iter().any(|reference| within(reference, &directory))
    }
    pub(crate) fn overlaps_directory(&self, directory: &Path) -> bool {
        let directory = key(directory);
        self.0.iter().any(|reference| within(reference, &directory) || within(&directory, reference))
    }
}

/// A conservative in-memory view of the current catalog, never another index.
pub(crate) fn snapshot_path_references(records: &[Value]) -> PathReferences {
    let mut paths = Vec::new();
    for record in records { references(record, true, &mut paths); }
    paths.sort();paths.dedup();PathReferences(paths)
}

fn invalid(message: &str) -> AppError { AppError::Worker(message.into()) }

fn linked(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    { use std::os::windows::fs::MetadataExt; if metadata.file_attributes() & 0x400 != 0 { return true; } }
    metadata.file_type().is_symlink()
}

pub(crate) fn plain_path(path: &Path) -> Result<(), &'static str> {
    if !path.is_absolute() || path.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir)) {
        return Err("path_not_absolute_or_normal");
    }
    #[cfg(windows)]
    if !matches!(path.components().next(), Some(Component::Prefix(prefix)) if matches!(prefix.kind(), std::path::Prefix::Disk(_))) {
        return Err("unsupported_path_prefix");
    }
    for parent in path.ancestors() {
        match fs::symlink_metadata(parent) {
            Ok(metadata) if linked(&metadata) => return Err("link_or_reparse_path"),
            Ok(_) => (),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(_) => return Err("path_inspection_failed"),
        }
    }
    Ok(())
}

fn key(path: &Path) -> String {
    let path = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let value = path.to_string_lossy().replace('\\', "/");
    let value = value.strip_prefix("//?/").unwrap_or(&value).trim_end_matches('/').to_string();
    #[cfg(windows)]
    { value.to_lowercase() }
    #[cfg(not(windows))]
    { value }
}

fn within(path: &str, root: &str) -> bool { path == root || path.strip_prefix(root).is_some_and(|rest| rest.starts_with('/')) }

// Payloads may contain graph input bindings several levels deep. Only declared
// output-location fields are omitted, and never when inside an input binding.
fn references(value: &Value, bindings: bool, output: &mut Vec<String>) {
    // Current graph save nodes contain output widgets, not source assets. A
    // live writer still protects these locations through bindings=true.
    if !bindings && matches!(value.get("type").and_then(Value::as_str),
        Some("pymss_save_audio" | "SaveAudio" | "SaveAudioMP3" | "SaveAudioOpus" | "SaveAudioAdvanced")) { return; }
    match value {
        Value::String(value) if Path::new(value).is_absolute() => output.push(key(Path::new(value))),
        Value::Array(values) => for value in values { references(value, bindings, output); },
        Value::Object(values) => for (name, value) in values {
            let input = bindings || matches!(name.as_str(), "input" | "inputs");
            if !input && matches!(name.as_str(), "output" | "outputDir" | "output_dir" | "outputRoot" | "exportDir" | "workbenchOutputs" | "outputDirectory" | "outputDirectories") { continue; }
            references(value, input, output);
        },
        _ => (),
    }
}

fn editor_references(root: &Path) -> AppResult<Vec<String>> {
    let directory = root.join("editor-projects");
    match fs::symlink_metadata(&directory) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
        Ok(_) => (),
    }
    plain_path(&directory).map_err(invalid)?;
    let mut refs = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        if linked(&fs::symlink_metadata(entry.path())?) { return Err(invalid("editor reference index contains a link")); }
        if !entry.file_type()?.is_dir() { continue; }
        let path = entry.path().join("project.json");
        plain_path(&path).map_err(invalid)?;
        let project = super::job_schema::parse(&fs::read_to_string(path)?)?;
        crate::storage::validate_editor_project(&project)?;
        refs.extend(project_path_references(&project)?.0);
    }
    Ok(refs)
}

fn model_references(root: &Path) -> AppResult<Vec<String>> {
    let path = root.join("settings/user_models.json");
    match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
        Ok(_) => (),
    }
    plain_path(&path).map_err(invalid)?;
    let registry = super::job_schema::parse(&fs::read_to_string(&path)?)?;
    if registry["version"].as_u64() != Some(1) { return Err(invalid("unsupported user model registry schema")); }
    let entries = registry.get("models").and_then(Value::as_array).ok_or_else(|| invalid("invalid user model registry"))?;
    for entry in entries {
        if !entry.is_object() || ["name", "model_type", "model_path"].iter().any(|key| {
            !entry[*key].as_str().is_some_and(|value| !value.trim().is_empty())
        }) { return Err(invalid("invalid user model registry entry")); }
        if !absolute_reference(&entry["model_path"], false)
            || !absolute_reference(&entry["config_path"], true) {
            return Err(invalid("user model paths cannot be resolved safely"));
        }
    }
    let mut refs = Vec::new();
    references(&registry, true, &mut refs);
    Ok(refs)
}

pub(crate) fn model_path_references(root: &Path) -> AppResult<PathReferences> {
    model_references(root).map(PathReferences)
}

fn absolute_reference(value: &Value, optional: bool) -> bool {
    if optional && value.is_null() { return true; }
    value.as_str().is_some_and(|path| {
        (optional && path.is_empty()) || (!path.trim().is_empty() && !path.contains('\0')
            && Path::new(path).is_absolute() && plain_path(Path::new(path)).is_ok())
    })
}

/// relativePath/originRoot are relink hints, never a base for resolving path.
pub(crate) fn project_path_references(project: &Value) -> AppResult<PathReferences> {
    super::editor_references::persisted_project_references(project)
}

fn relative_file(value: &Value) -> bool {
    value.as_str().is_some_and(|path| !path.trim().is_empty() && !Path::new(path).is_absolute())
}

pub(crate) fn snapshot_has_unresolved_paths(records: &[Value]) -> bool {
    unresolved_audio_input(records) || records.iter().any(|row| {
        (row.get("audio").is_none() && unresolved_generic_input(row["command"].as_str().unwrap_or(""), &row["payload"]))
            || (!task_journal::terminal(row) && ["output", "outputDir", "output_dir", "outputRoot", "exportDir"]
                .iter().any(|name| relative_file(&row["payload"][*name])))
            || candidates(row).iter().any(|(path, _)| !Path::new(path).is_absolute())
    })
}

fn managed_editor_export(root: &Path, row: &Value, target: &Path) -> bool {
    if row["command"] != "export_editor_mix" { return false; }
    let project = &row["payload"]["project"];
    if super::editor_references::export_project_references(project).is_err() { return false; }
    let Some(id) = project["id"].as_str() else { return false; };
    if crate::commands::app::editor::validate_editor_project_id(id).is_err() || !task_journal::valid_id(id) { return false; }
    let Some(declared) = row.pointer("/payload/exportDir").and_then(Value::as_str).map(Path::new) else { return false; };
    let expected = root.join("editor-projects").join(id).join("exports");
    if plain_path(declared).is_err() || plain_path(&expected).is_err() || key(declared) != key(&expected) { return false; }
    let normalized = key(target);
    let directory = key(&expected);
    if normalized == directory || !within(&normalized, &directory) { return false; }
    if !target.extension().and_then(|value| value.to_str()).is_some_and(|value| matches!(value.to_ascii_lowercase().as_str(), "wav" | "flac")) { return false; }
    // The current saved project must confirm ownership too. A missing, changed,
    // unreadable or linked project never grants an application-data exception.
    let saved = expected.parent().unwrap().join("project.json");
    if plain_path(&saved).is_err() { return false; }
    fs::read_to_string(saved).ok().and_then(|text| super::job_schema::parse(&text).ok())
        .is_some_and(|saved| crate::storage::validate_editor_project(&saved).is_ok() && saved["id"] == id)
}

fn unresolved_graph_nodes(payload: &Value, request: &Value) -> bool {
    let input = request["input"].as_str().filter(|value| !value.trim().is_empty());
    let inputs = request["inputs"].as_object();
    // Only the non-workbench executor replaces embedded load widgets with
    // the global picker. Workbench graphs keep independent load sources.
    if input.is_some() && payload["workbench"].as_bool() != Some(true) { return false; }
    let Some(nodes) = payload.pointer("/workflow/nodes").and_then(Value::as_array) else { return false; };
    for node in nodes {
        let slot = match node["type"].as_str() {
            Some("pymss_load_audio" | "LoadAudio") => 1,
            Some("pymss_load_audio_batch") => 3,
            _ => continue,
        };
        let widgets = node["widgets_values"].as_array();
        let widget = widgets.and_then(|values| values.first()).and_then(Value::as_str).unwrap_or("");
        let name = widgets.and_then(|values| values.get(slot)).and_then(Value::as_str).unwrap_or("");
        let explicitly_bound = !name.is_empty() && inputs.and_then(|values| values.get(name)).and_then(Value::as_str)
            .is_some_and(|path| Path::new(path).is_absolute());
        if !explicitly_bound && !widget.is_empty() && !Path::new(widget).is_absolute() { return true; }
    }
    false
}

fn unresolved_audio_input(library: &[Value]) -> bool {
    let by_id = library.iter().map(|row| (row["jobId"].as_str().unwrap(), row)).collect::<BTreeMap<_, _>>();
    for row in library.iter().filter(|row| row.get("audio").is_some()) {
        let request = &row["audio"]["request"];
        let input = request["input"].as_str().filter(|value| !value.trim().is_empty());
        if input.is_some_and(|value| !Path::new(value).is_absolute()) { return true; }
        let inputs = request["inputs"].as_object();
        if inputs.is_some_and(|values| values.values().filter_map(Value::as_str)
            .any(|value| !value.is_empty() && !Path::new(value).is_absolute())) { return true; }
        let primary = by_id[row["audio"]["primaryJobId"].as_str().unwrap()];
        if unresolved_graph_nodes(&primary["payload"], request) { return true; }
    }
    false
}

fn unresolved_generic_input(command: &str, payload: &Value) -> bool {
    if matches!(command, "import_custom_model" | "relink_custom_model") {
        return ["modelPath", "configPath"].iter().any(|name| relative_file(&payload[*name]));
    }
    if command == "waveform_peaks" {
        return ["path", "cacheDir"].iter().any(|name| relative_file(&payload[*name]));
    }
    if !super::protocol::is_result_job_command(command) { return false; }
    if command == "export_editor_mix" { return super::editor_references::export_project_references(&payload["project"]).is_err(); }
    if command == "run_operation" {
        // Plain strings and scalar.value are operation values, not file paths.
        return payload["inputs"].as_object().is_some_and(|inputs| inputs.values().any(|input| {
            matches!(input["kind"].as_str(), Some("file" | "audio_file" | "path")) && relative_file(&input["path"])
        }));
    }
    if ["input", "inputPath"].iter().any(|name| relative_file(&payload[*name])) { return true; }
    if payload["inputs"].as_array().is_some_and(|inputs| inputs.iter().any(relative_file)) { return true; }
    if command == "audio_tools" && ["referencePath", "estimatedPath"].iter().any(|name| relative_file(&payload[*name])) { return true; }
    if command == "infer_workflow" {
        if payload["inputs"].as_object().is_some_and(|inputs| inputs.values().any(relative_file)) { return true; }
        if unresolved_graph_nodes(payload, payload) { return true; }
    }
    if matches!(command, "infer" | "infer_workflow") && payload["tasks"].as_array().is_some_and(|tasks| {
        tasks.iter().any(|task| unresolved_generic_input(command, task))
    }) { return true; }
    false
}

fn candidates(row: &Value) -> Vec<(String, Option<&'static str>)> {
    let result = &row["result"];
    let command = row["command"].as_str().unwrap();
    let mut paths = Vec::new();
    if command == "export_editor_mix" {
        if let Some(path) = result["path"].as_str().filter(|path| !path.trim().is_empty()) { paths.push((path.into(), None)); }
        return paths;
    }
    if command == "run_operation" {
        if let Some(outputs) = result["outputs"].as_object() {
            for output in outputs.values() {
                if output["kind"] == "audio_file" {
                    if let Some(path) = output["path"].as_str() { paths.push((path.into(), None)); }
                }
            }
        }
        return paths;
    }
    if let Some(outputs) = result["outputs"].as_array() {
        for output in outputs {
            if let Some(path) = output["path"].as_str() {
                let not_file = output.get("kind").is_some_and(|kind| !matches!(kind.as_str(), Some("file" | "audio_file" | "image_file" | "video_file")));
                paths.push((path.into(), not_file.then_some("output_descriptor_not_file")));
            }
        }
    }
    if command == "audio_tools" {
        let operation = row.pointer("/payload/operation").and_then(Value::as_str).unwrap_or("");
        if matches!(operation, "convert" | "slicer" | "asr" | "loudness") {
            if let Some(outputs) = result["outputPaths"].as_array() {
                for output in outputs { if let Some(path) = output.as_str() { paths.push((path.into(), None)); } }
            }
        } else if matches!(operation, "merge" | "midi") {
            if let Some(path) = result["outputPath"].as_str() {
                paths.push((path.into(), (operation == "midi").then_some("output_ownership_uncertain")));
            }
        }
    }
    paths
}

pub(crate) fn remove(root: &Path, ids: &[String], delete_files: bool, check_only: bool, protection: &Protection,
                     editor_open: impl Fn() -> bool) -> AppResult<RemovalResult> {
    remove_with(root, ids, RemovalKind::Generic, delete_files, check_only, protection, editor_open,
                &mut crate::commands::app::recycle::recycle_file)
}

pub(crate) fn remove_audio(root: &Path, ids: &[String], delete_files: bool, check_only: bool, protection: &Protection,
                          editor_open: impl Fn() -> bool) -> AppResult<RemovalResult> {
    remove_with(root, ids, RemovalKind::Audio, delete_files, check_only, protection, editor_open,
                &mut crate::commands::app::recycle::recycle_file)
}

fn remove_with(root: &Path, ids: &[String], kind: RemovalKind, delete_files: bool, check_only: bool, protection: &Protection,
               editor_open: impl Fn() -> bool, recycle: &mut impl FnMut(&Path) -> Result<(), String>) -> AppResult<RemovalResult> {
    let requested = ids.iter().cloned().collect::<BTreeSet<_>>();
    if requested.iter().any(|id| !task_journal::valid_id(id)) { return Err(invalid("invalid result job id")); }
    let mut response = RemovalResult { schema_version: 2, checked_job_ids: vec![],
        removed_job_ids: vec![], trashed: vec![], failed: vec![], retained: vec![] };
    if requested.is_empty() { return Ok(response); }
    for path in [root.to_path_buf(), root.join("agent-jobs"), root.join("agent-jobs/.owners"), root.join("agent-jobs/.writes")] {
        plain_path(&path).map_err(invalid)?;
    }
    let _catalog = task_journal::catalog_lock(root)?;
    let library = task_journal::read_all_unlocked(root)?;
    let mut groups = BTreeSet::new();
    for id in &requested {
        let row = library.iter().find(|row| row["jobId"] == *id).ok_or_else(|| invalid("result job is missing"))?;
        match kind {
            RemovalKind::Audio => {
                groups.insert(row.pointer("/audio/primaryJobId").and_then(Value::as_str)
                    .ok_or_else(|| invalid("audio deletion requires audio group jobs"))?.to_string());
            }
            RemovalKind::Generic if row.get("audio").is_some() || !super::protocol::is_result_job_command(row["command"].as_str().unwrap()) => {
                return Err(invalid("only generic result jobs can be removed here"));
            }
            _ => (),
        }
    }
    let selected = if kind == RemovalKind::Audio {
        library.iter().filter(|row| row.pointer("/audio/primaryJobId").and_then(Value::as_str).is_some_and(|id| groups.contains(id)))
            .map(|row| row["jobId"].as_str().unwrap().to_string()).collect::<BTreeSet<_>>()
    } else { requested };
    response.checked_job_ids = selected.iter().cloned().collect();
    for id in &selected {
        let row = library.iter().find(|row| row["jobId"] == *id).ok_or_else(|| invalid("result job is missing"))?;
        if !task_journal::terminal(row) { return Err(invalid("cannot remove an active result job")); }
        if library.iter().any(|row| !selected.contains(row["jobId"].as_str().unwrap()) && row["payloadRef"] == *id) {
            return Err(invalid("result job is still a payload anchor"));
        }
    }
    let _owners = selected.iter().map(|id| task_journal::owner_lock(root, id)).collect::<AppResult<Vec<_>>>()?;
    let _writes = selected.iter().map(|id| task_journal::write_lock(root, id)).collect::<AppResult<Vec<_>>>()?;
    let rows = selected.iter().map(|id| task_journal::read_job(&task_journal::job_path(root, id)?)?
        .ok_or_else(|| invalid("result job disappeared"))).collect::<AppResult<Vec<_>>>()?;
    if rows.iter().any(|row| !task_journal::terminal(row)) { return Err(invalid("result job is no longer terminal")); }
    crate::storage::with_editor_project_guard(|| {
        // New registrations cannot begin between the reference snapshot and recycle.
        let model_guard = crate::storage::model_reference_snapshot_guard();
        // Hold the shared asset lease (and read transaction) through recycling.
        let library_snapshot = delete_files.then(|| super::library_references::snapshot(root));
        if delete_files {
            let mut input_refs = Vec::new();
            let unresolved_graph = unresolved_audio_input(&library);
            let unresolved_input = library.iter().filter(|row| row.get("audio").is_none())
                .any(|row| unresolved_generic_input(row["command"].as_str().unwrap(), &row["payload"])
                    || (!task_journal::terminal(row) && row["command"] == "export_editor_mix" && relative_file(&row["payload"]["exportDir"])));
            let active_audio_groups = library.iter().filter(|row| !task_journal::terminal(row))
                .filter_map(|row| row.pointer("/audio/primaryJobId").and_then(Value::as_str)).collect::<BTreeSet<_>>();
            for row in &library {
                let active_group = row.pointer("/audio/primaryJobId").and_then(Value::as_str).is_some_and(|primary| active_audio_groups.contains(primary));
                let active_other = !selected.contains(row["jobId"].as_str().unwrap()) && (!task_journal::terminal(row) || active_group);
                references(&row["payload"], active_other, &mut input_refs);
                if let Some(audio) = row.get("audio") { references(&audio["request"], active_other, &mut input_refs); }
                if !selected.contains(row["jobId"].as_str().unwrap()) {
                    references(&row["result"], false, &mut input_refs);
                    references(&row["runtime"], false, &mut input_refs);
                }
            }
            let editor_refs = editor_references(root);
            let registry_ok = model_references(root).map(|refs| input_refs.extend(refs)).is_ok();
            let library_paths = match library_snapshot.as_ref().unwrap() {
                Ok(snapshot) => Some(snapshot.paths().iter().map(|path| key(path)).collect::<Vec<_>>()),
                Err(_) => None,
            };
            let protected = protection.roots.iter().map(|path| key(path)).collect::<Vec<_>>();
            let root_key = key(root);
            let outputs_key = key(&root.join("outputs"));
            let mut outputs = BTreeMap::new();
            let mut managed_exports = BTreeSet::new();
            for row in &rows { for (path, reason) in candidates(row) {
                if managed_editor_export(root, row, Path::new(&path)) { managed_exports.insert(key(Path::new(&path))); }
                outputs.entry(key(Path::new(&path))).and_modify(|entry: &mut (String, Option<&'static str>)| {
                    if reason.is_some() { entry.1 = reason; }
                }).or_insert((path, reason));
            } }
            for (_, (path, forced)) in outputs {
                let target = Path::new(&path);
                let normalized = key(target);
                let reason = forced.or_else(|| plain_path(target).err())
                    .or_else(|| unresolved_input.then_some("unresolved_input_reference"))
                    .or_else(|| unresolved_graph.then_some("unresolved_graph_input"))
                    .or_else(|| (*model_guard > 0).then_some("model_registry_busy"))
                    .or_else(|| (within(&normalized, &root_key) && !within(&normalized, &outputs_key) && !managed_exports.contains(&normalized)).then_some("protected_app_data"))
                    .or_else(|| protected.iter().any(|root| within(&normalized, root)).then_some("protected_asset_or_program"))
                    .or_else(|| input_refs.iter().any(|reference| within(&normalized, reference)).then_some("input_or_other_job_reference"))
                    .or_else(|| editor_open().then_some("editor_open"))
                    .or_else(|| match &library_paths {
                        Some(paths) => paths.iter().any(|reference| within(&normalized, reference)).then_some("library_reference"),
                        None => Some("library_index_unavailable"),
                    })
                    .or_else(|| (!registry_ok).then_some("model_registry_unavailable"))
                    .or_else(|| match &editor_refs {
                        Ok(refs) => refs.iter().any(|reference| within(&normalized, reference)).then_some("editor_reference"),
                        Err(_) => Some("editor_index_unavailable"),
                    });
                if let Some(reason) = reason {
                    response.retained.push(RetainedPath { path, reason: reason.into() });
                    continue;
                }
                match fs::symlink_metadata(target) {
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => { if !check_only { response.trashed.push(path); } },
                    Ok(metadata) if metadata.is_file() && !linked(&metadata) => {
                        if !check_only {
                            match recycle(target) {
                                Ok(()) => response.trashed.push(path),
                                Err(error) => response.failed.push(PathFailure { path, error }),
                            }
                        }
                    },
                    _ => response.retained.push(RetainedPath { path, reason: "not_a_regular_file_or_unreadable".into() }),
                }
            }
        }
        if check_only || !response.failed.is_empty() { return Ok(response); }
        for id in &selected {
            let cancel = root.join("agent-jobs").join(format!("{id}.cancel"));
            plain_path(&cancel).map_err(invalid)?;
            match fs::remove_file(cancel) {
                Ok(()) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(error) => return Err(error.into()),
            }
        }
        let nonce = format!("{}-{}", std::process::id(), task_journal::now().to_bits());
        let mut renamed = Vec::new();
        for id in &selected {
            let original = task_journal::job_path(root, id)?;
            let pending = original.with_file_name(format!(".{id}.{nonce}.removing"));
            if let Err(error) = fs::rename(&original, &pending) {
                for (original, pending) in renamed.iter().rev() { let _ = fs::rename(pending, original); }
                return Err(error.into());
            }
            renamed.push((original, pending));
        }
        // These are only our exact journal transaction files, never output paths.
        for (_, pending) in renamed { fs::remove_file(pending)?; }
        response.removed_job_ids = response.checked_job_ids.clone();
        Ok(response)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fixture { _temp: tempfile::TempDir, root: PathBuf, output: PathBuf, _serial: std::sync::MutexGuard<'static, ()> }
    impl Fixture {
        fn new() -> Self {
            let serial = crate::storage::model_reference_test_guard();
            let temp = tempfile::tempdir().unwrap();
            let root = temp.path().join("data");
            let output = temp.path().join("outputs");
            fs::create_dir_all(root.join("agent-jobs")).unwrap();
            fs::create_dir_all(&output).unwrap();
            Self { _temp: temp, root, output, _serial: serial }
        }
        fn file(&self, name: &str) -> PathBuf { let path = self.output.join(name); fs::write(&path, name).unwrap(); path }
        fn row(&self, id: &str, output: &Path) -> Value {
            json!({"schemaVersion":2,"jobId":id,"command":"media_image","source":"agent","status":"completed",
                "fraction":1,"message":"done","error":null,"result":{"outputs":[{"path":output}]},
                "createdAt":1,"updatedAt":2,"startedAt":1,"finishedAt":2,"owner":null,"payload":{},
                "runtime":{"stage":null,"progress":null,"nodes":{},"order":[]},"logs":[]})
        }
        fn export(&self, id: &str, output: &Path, source: &Path) -> Value {
            let mut row=self.row(id,output);row["command"]=json!("export_editor_mix");
            row["payload"]=json!({"project":{"version":4,"id":"edit_fixture","assets":[{"id":"source","path":source}],"tracks":[]},"exportDir":output.parent().unwrap()});
            row["result"]=json!({"path":output,"duration":1.5,"sampleRate":44100,"channels":2,"format":"wav"});row
        }
        fn persisted_export_project(&self, row: &Value) -> Value {
            json!({"version":4,"id":"edit_fixture","sources":row["payload"]["project"]["assets"],"tracks":[]})
        }
        fn managed_export(&self) -> (Value, PathBuf, PathBuf) {
            let project=self.root.join("editor-projects/edit_fixture");let exports=project.join("exports");
            fs::create_dir_all(&exports).unwrap();let output=exports.join("mix.wav");fs::write(&output,"fixture mix").unwrap();
            let source=self.file("source.wav");let row=self.export("export",&output,&source);
            fs::write(project.join("project.json"),self.persisted_export_project(&row).to_string()).unwrap();(row,output,source)
        }
        fn save(&self, row: &Value) -> PathBuf {
            super::super::job_schema::validate(row).unwrap();
            let path = task_journal::job_path(&self.root, row["jobId"].as_str().unwrap()).unwrap();
            fs::write(&path, serde_json::to_vec(row).unwrap()).unwrap(); path
        }
        fn remove(&self, ids: &[&str], files: bool, check: bool, open: bool,
                  recycle: &mut impl FnMut(&Path) -> Result<(), String>) -> AppResult<RemovalResult> {
            remove_with(&self.root, &ids.iter().map(|id| id.to_string()).collect::<Vec<_>>(), RemovalKind::Generic, files, check,
                &Protection { roots: vec![] }, || open, recycle)
        }
        fn audio(&self, prefix: &str, outputs: &[PathBuf]) -> Vec<Value> {
            let ids = (0..outputs.len()).map(|index| format!("{prefix}_{index}")).collect::<Vec<_>>();
            outputs.iter().enumerate().map(|(index, output)| {
                let mut row = self.row(&ids[index], output);
                row["source"]=json!("manual"); row["command"]=json!("infer_workflow");
                row["audio"]=json!({"groupId":ids[0],"primaryJobId":ids[0],"index":index,
                    "request":{"input":self.file(&format!("{prefix}-input-{index}.wav")),"output":self.output.join(format!("{prefix}-{index}")),
                        "workbenchOutputs":{"saved":output}}});
                if index==0 {
                    row["audio"]["memberJobIds"]=json!(ids);
                    row["payload"]=json!({"workflowName":"fixture","workflow":{"version":0.4,"nodes":[],"links":[]},"output":self.output});
                } else {
                    row.as_object_mut().unwrap().remove("payload");row["payloadRef"]=json!(ids[0]);
                }
                row
            }).collect()
        }
        fn remove_audio(&self, ids: &[&str], files: bool, check: bool, open: bool,
                        recycle: &mut impl FnMut(&Path) -> Result<(), String>) -> AppResult<RemovalResult> {
            remove_with(&self.root, &ids.iter().map(|id| id.to_string()).collect::<Vec<_>>(), RemovalKind::Audio,
                files, check, &Protection { roots: vec![] }, || open, recycle)
        }
    }

    #[cfg(windows)]
    #[test]
    fn current_library_protects_exact_sources_and_thumbnails_but_allows_unrelated_outputs() {
        let f=Fixture::new();let source=f.file("source.wav");let thumbnail=f.file("thumb.jpg");let free=f.file("unrelated.wav");
        for (id,path) in [("source",&source),("thumb",&thumbnail),("free",&free)] {f.save(&f.row(id,path));}
        super::super::library_references::create_fixture(&f.root,&[(source.clone(),thumbnail.clone())]).unwrap();
        let before=fs::read(f.root.join("library.db")).unwrap();let mut recycled=vec![];
        let response=f.remove(&["source","thumb","free"],true,false,false,&mut |path| {
            // The native read transaction and cross-process lease survive the complete recycle callback.
            assert!(super::super::library_references::AssetLease::acquire(&f.root,std::time::Duration::ZERO).is_err());
            recycled.push(path.to_path_buf());fs::remove_file(path).map_err(|error|error.to_string())
        }).unwrap();
        assert_eq!(recycled,vec![free]);assert_eq!(response.removed_job_ids.len(),3);
        assert_eq!(response.retained.len(),2);assert!(response.retained.iter().all(|row|row.reason=="library_reference"));
        assert!(source.is_file());assert!(thumbnail.is_file());assert_eq!(fs::read(f.root.join("library.db")).unwrap(),before);
    }

    #[cfg(windows)]
    #[test]
    fn valid_empty_library_does_not_prevent_recycling_and_missing_library_is_not_created() {
        for present in [true,false] {
            let f=Fixture::new();let output=f.file("free.wav");f.save(&f.row("free",&output));
            if present {super::super::library_references::create_fixture(&f.root,&[]).unwrap();}
            let response=f.remove(&["free"],true,false,false,&mut |path|fs::remove_file(path).map_err(|error|error.to_string())).unwrap();
            assert_eq!(response.trashed.len(),1);assert!(response.retained.is_empty());assert!(!output.exists());
            assert_eq!(f.root.join("library.db").exists(),present);
        }
    }
    fn fake_recycle(path: &Path) -> Result<(), String> { fs::remove_file(path).map_err(|error| error.to_string()) }
    fn never_recycle(_: &Path) -> Result<(), String> { panic!("recycling must not be called") }

    #[test]
    fn metadata_only_and_check_only_preserve_files_and_report_exact_ids() {
        let f = Fixture::new(); let output = f.file("image.png");
        let path = f.save(&f.row("job", &output)); let before = fs::read(&path).unwrap();
        let checked = f.remove(&["job"], true, true, false, &mut never_recycle).unwrap();
        assert_eq!(checked.checked_job_ids, vec!["job"]); assert!(checked.removed_job_ids.is_empty());
        assert!(checked.trashed.is_empty()); assert!(checked.failed.is_empty()); assert_eq!(fs::read(&path).unwrap(), before);
        fs::write(f.root.join("agent-jobs/job.cancel"), "").unwrap();
        let result = f.remove(&["job"], false, false, false, &mut never_recycle).unwrap();
        assert_eq!(result.removed_job_ids, vec!["job"]); assert!(output.is_file()); assert!(!path.exists());
        assert!(!f.root.join("agent-jobs/job.cancel").exists());
        assert!(f.root.join("agent-jobs/.owners/job.lock").exists());
        assert!(f.root.join("agent-jobs/.writes/job.lock").exists());
    }

    #[test]
    fn editor_export_owns_only_its_scalar_result_path_and_never_result_directories_or_sources() {
        for files in [false,true] {
            let f=Fixture::new();let mix=f.file("mix.wav");let source=f.file("source.wav");let decoy=f.file("keep.wav");
            let mut row=f.export("export",&mix,&source);
            row["result"]["outputs"]=json!([{"path":decoy}]);row["result"]["directory"]=json!(f.output);
            row["payload"]["project"]["sourceResultDir"]=json!(source.parent().unwrap().join("original-source-root"));
            let saved=f.save(&row);let before=fs::read(&saved).unwrap();
            let checked=f.remove(&["export"],files,true,false,&mut never_recycle).unwrap();
            assert_eq!(checked.checked_job_ids,vec!["export"]);assert!(checked.trashed.is_empty());assert_eq!(fs::read(&saved).unwrap(),before);
            let result=f.remove(&["export"],files,false,false,&mut fake_recycle).unwrap();
            assert_eq!(result.trashed.len(),if files {1}else{0});assert!(result.retained.is_empty());
            assert_eq!(mix.exists(),!files);assert!(source.exists());assert!(decoy.exists());assert!(f.output.is_dir());
        }
    }

    #[test]
    fn default_editor_export_exception_requires_current_saved_project_and_exact_owned_exports_directory() {
        let f=Fixture::new();let (row,mix,source)=f.managed_export();let project=f.root.join("editor-projects/edit_fixture/project.json");
        let before=fs::read(&project).unwrap();f.save(&row);
        let result=f.remove(&["export"],true,false,false,&mut fake_recycle).unwrap();
        assert_eq!(result.trashed,vec![mix.to_string_lossy()]);assert!(!mix.exists());assert!(source.exists());
        assert_eq!(fs::read(project).unwrap(),before);assert!(mix.parent().unwrap().is_dir());
    }

    #[test]
    fn editor_export_data_exception_rejects_traversal_mismatched_ids_roots_and_sibling_files() {
        for mode in ["traversal","wrong_id","wrong_root","sibling","saved_id","schema","non_audio_file"] {
            let f=Fixture::new();let (mut row,mut mix,_)=f.managed_export();
            match mode {
                "traversal"=>row["payload"]["project"]["id"]=json!("../edit_fixture"),
                "wrong_id"=>row["payload"]["project"]["id"]=json!("different"),
                "wrong_root"=>row["payload"]["exportDir"]=json!(f.output),
                "sibling"=> {mix=f.root.join("editor-projects/edit_fixture/sibling.wav");fs::write(&mix,"keep").unwrap();row["result"]["path"]=json!(mix);},
                "non_audio_file"=> {mix=f.root.join("editor-projects/edit_fixture/exports/project.json");fs::write(&mix,"keep").unwrap();row["result"]["path"]=json!(mix);},
                "saved_id"=> {let mut project=f.persisted_export_project(&row);project["id"]=json!("different");fs::write(f.root.join("editor-projects/edit_fixture/project.json"),project.to_string()).unwrap();},
                "schema"=>row["payload"]["project"]["version"]=json!(3),_=>(),
            }
            f.save(&row);let result=f.remove(&["export"],true,false,false,&mut never_recycle).unwrap();
            assert_eq!(result.retained[0].reason,if mode=="schema" {"unresolved_input_reference"} else {"protected_app_data"},"{mode}");assert!(mix.exists(),"{mode}");
        }
    }

    #[test]
    fn editor_export_does_not_bypass_inputs_other_jobs_library_editor_or_protected_assets() {
        for reason in ["input_or_other_job_reference","other_job","library_index_unavailable","editor_reference","editor_open","protected_asset_or_program","model_registry_busy"] {
            let f=Fixture::new();let (mut row,mix,_)=f.managed_export();
            match reason {
                "input_or_other_job_reference"=>row["payload"]["project"]["assets"][0]["path"]=json!(mix),
                "other_job"=> {f.save(&f.row("consumer",&mix));},
                "library_index_unavailable"=>fs::write(f.root.join("library.db"),"untouched").unwrap(),
                "editor_reference"=> {let mut project=f.persisted_export_project(&row);project["sources"][0]["path"]=json!(mix);fs::write(f.root.join("editor-projects/edit_fixture/project.json"),project.to_string()).unwrap();},_=>(),
            }
            let _model=if reason=="model_registry_busy" {crate::storage::model_reference_mutation("import_custom_model")}else{None};
            f.save(&row);let protection=Protection{roots:if reason=="protected_asset_or_program" {vec![mix.parent().unwrap().into()]}else{vec![]}};
            let result=remove_with(&f.root,&["export".into()],RemovalKind::Generic,true,false,&protection,||reason=="editor_open",&mut never_recycle).unwrap();
            assert_eq!(result.retained[0].reason,if reason=="other_job" {"input_or_other_job_reference"}else{reason});assert!(mix.exists());
        }
    }

    #[test]
    fn editor_source_paths_in_sources_or_assets_fail_closed_when_relative_in_payload_or_saved_project() {
        for collection in ["sources","assets"] {
            for saved_project in [false,true] {
                let f=Fixture::new();let (mut row,mix,_)=f.managed_export();
                if saved_project {let mut project=f.persisted_export_project(&row);project[collection]=json!([{"id":"source","path":"../source.wav"}]);fs::write(f.root.join("editor-projects/edit_fixture/project.json"),project.to_string()).unwrap();}
                else {row["payload"]["project"][collection]=json!([{"id":"source","path":"../source.wav"}]);}
                f.save(&row);let result=f.remove(&["export"],true,false,false,&mut never_recycle).unwrap();
                assert_eq!(result.retained[0].reason,if saved_project {"editor_index_unavailable"}else{"unresolved_input_reference"});assert!(mix.exists());
            }
        }
    }

    #[test]
    fn active_editor_export_protects_its_future_directory_while_terminal_exports_share_a_root_safely() {
        for active in [false,true] {
            let f=Fixture::new();let mix=f.file("selected.wav");let other=f.file("other.wav");let source=f.file("source.wav");
            f.save(&f.row("selected",&mix));let mut row=f.export("other",&other,&source);
            if active {row["status"]=json!("queued");row["finishedAt"]=Value::Null;row["result"]=Value::Null;}
            f.save(&row);let mut calls=0;let result=f.remove(&["selected"],true,false,false,&mut |path|{calls+=1;fake_recycle(path)}).unwrap();
            assert_eq!(calls,if active {0}else{1});assert_eq!(mix.exists(),active);assert!(other.exists());assert!(source.exists());
            if active {assert_eq!(result.retained[0].reason,"input_or_other_job_reference");}
        }
        let f=Fixture::new();let mix=f.file("selected.wav");let source=f.file("source.wav");f.save(&f.row("selected",&mix));
        let mut active=f.export("other",&f.output.join("future.wav"),&source);active["status"]=json!("queued");active["finishedAt"]=Value::Null;
        active["result"]=Value::Null;active["payload"]["exportDir"]=json!("../unknown-exports");f.save(&active);
        let result=f.remove(&["selected"],true,false,false,&mut never_recycle).unwrap();assert_eq!(result.retained[0].reason,"unresolved_input_reference");
    }

    #[test]
    fn editor_export_journal_keeps_original_single_path_and_owner_lease_until_release() {
        let f=Fixture::new();let mix=f.file("mix.wav");let source=f.file("source.wav");let row=f.export("export",&mix,&source);
        let journal=task_journal::WorkerJournal::start(&f.root,"export_editor_mix","export",&row["payload"]).unwrap();
        journal.lock().unwrap().running(42).unwrap();
        journal.lock().unwrap().observe(&super::super::protocol::WorkerEnvelope{event_type:"editor_mix_exported".into(),task_id:Some("export".into()),request_id:None,timestamp:None,payload:row["result"].clone()}).unwrap();
        let saved=task_journal::read_job(&task_journal::job_path(&f.root,"export").unwrap()).unwrap().unwrap();
        assert_eq!(saved["status"],"completed");assert_eq!(saved["result"],row["result"]);assert!(saved["result"].get("outputs").is_none());
        assert!(f.remove(&["export"],true,false,false,&mut never_recycle).is_err());assert!(mix.exists());
        drop(journal);let result=f.remove(&["export"],true,false,false,&mut fake_recycle).unwrap();assert_eq!(result.trashed.len(),1);assert!(source.exists());
    }

    #[test]
    fn active_audio_unsupported_missing_and_live_terminal_lease_are_rejected() {
        for mode in ["queued", "running", "audio", "unsupported", "missing", "leased"] {
            let f = Fixture::new(); let output = f.file("out.wav"); let mut row = f.row("job", &output);
            match mode {
                "queued" => { row["status"]=json!("queued"); row["startedAt"]=Value::Null; row["finishedAt"]=Value::Null; },
                "running" => { row["status"]=json!("running"); row["finishedAt"]=Value::Null; row["owner"]=json!({"kind":"tauri-worker","pid":42}); },
                "audio" => { row["command"]=json!("infer"); row["source"]=json!("manual");
                    row["payload"]=json!({"model":"test","output":f.output});
                    row["audio"]=json!({"groupId":"job","primaryJobId":"job","index":0,"memberJobIds":["job"],"request":{"input":null,"output":f.output}}); },
                "unsupported" => row["command"]=json!("model_download"), _=>(),
            }
            let path=f.save(&row); let before=fs::read(&path).unwrap();
            let _owner=if mode=="leased" { Some(task_journal::owner_lock(&f.root,"job").unwrap()) } else { None };
            let ids=if mode=="missing" { vec!["job","missing"] } else { vec!["job"] };
            assert!(f.remove(&ids,true,false,false,&mut never_recycle).is_err(),"{mode}");
            assert_eq!(fs::read(path).unwrap(),before); assert!(output.is_file());
        }
    }

    #[test]
    fn only_canonical_files_are_recycled_without_scanning_output_or_failed_inputs() {
        let f=Fixture::new(); let output=f.file("out.png"); let input=f.file("source.png"); let unrelated=f.file("keep.txt");
        let mut row=f.row("job",&output); row["result"]["failed"]=json!([{"path":input}]);
        row["result"]["outputDir"]=json!(f.output); f.save(&row);
        let result=f.remove(&["job"],true,false,false,&mut fake_recycle).unwrap();
        assert_eq!(result.trashed,vec![output.to_string_lossy()]); assert!(!output.exists());
        assert!(input.exists()); assert!(unrelated.exists()); assert!(f.output.is_dir());
    }

    #[test]
    fn nonfile_descriptors_and_directories_are_retained_even_when_the_path_is_a_file() {
        for kind in ["scalar","object","array","directory","folder","unknown"] {
            let f=Fixture::new(); let output=f.file("keep.png"); let mut row=f.row("job",&output);
            row["result"]["outputs"][0]["kind"]=json!(kind); f.save(&row);
            let response=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
            assert_eq!(response.retained[0].reason,"output_descriptor_not_file"); assert!(output.exists());
        }
        let f=Fixture::new(); f.save(&f.row("job",&f.output));
        let response=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
        assert_eq!(response.retained[0].reason,"not_a_regular_file_or_unreadable");
    }

    #[test]
    fn nested_input_and_other_job_results_are_protected() {
        for other in [false,true] {
            let f=Fixture::new(); let output=f.file("shared.wav"); let mut row=f.row("job",&output);
            if other { f.save(&f.row("keep",&output)); }
            else { row["payload"]=json!({"graph":{"nodes":[{"inputs":{"output":output}}]}}); }
            f.save(&row);
            let response=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
            assert_eq!(response.retained[0].reason,"input_or_other_job_reference"); assert!(output.exists());
        }
    }

    #[test]
    fn active_future_writer_output_directory_is_protected_before_it_has_a_result() {
        let f=Fixture::new(); let output=f.file("shared.txt"); f.save(&f.row("job",&output));
        let mut active=f.row("future",&output); active["status"]=json!("queued"); active["finishedAt"]=Value::Null;
        active["result"]=Value::Null; active["payload"]=json!({"params":{"outputDir":f.output}}); f.save(&active);
        let response=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
        assert_eq!(response.retained[0].reason,"input_or_other_job_reference"); assert!(output.exists());
    }

    #[test]
    fn model_runtime_and_app_state_paths_are_never_recycled() {
        let f=Fixture::new(); let model=f.file("model.wav");
        f.save(&f.row("job",&model));
        let result=remove_with(&f.root,&["job".into()],RemovalKind::Generic,true,false,&Protection{roots:vec![f.output.clone()]},||false,&mut never_recycle).unwrap();
        assert_eq!(result.retained[0].reason,"protected_asset_or_program"); assert!(model.exists());
        let settings=f.root.join("settings"); fs::create_dir_all(&settings).unwrap();
        let config=settings.join("app.json"); fs::write(&config,"{}").unwrap(); f.save(&f.row("config",&config));
        let result=f.remove(&["config"],true,false,false,&mut never_recycle).unwrap();
        assert_eq!(result.retained[0].reason,"protected_app_data"); assert!(config.exists());
    }

    #[test]
    fn complete_library_index_and_open_editor_fail_closed_without_deleting_records_by_accident() {
        for reason in ["library_index_unavailable","editor_open","editor_index_unavailable","model_registry_unavailable"] {
            let f=Fixture::new(); let output=f.file("keep.wav"); f.save(&f.row("job",&output));
            match reason {
                "library_index_unavailable"=>fs::write(f.root.join("library.db"),b"not queried or modified").unwrap(),
                "editor_index_unavailable"=> { let dir=f.root.join("editor-projects/bad");fs::create_dir_all(&dir).unwrap();fs::write(dir.join("project.json"),"{broken").unwrap(); },
                "model_registry_unavailable"=> { let dir=f.root.join("settings");fs::create_dir_all(&dir).unwrap();fs::write(dir.join("user_models.json"),"{broken").unwrap(); }, _=>(),
            }
            let response=f.remove(&["job"],true,false,reason=="editor_open",&mut never_recycle).unwrap();
            assert_eq!(response.retained[0].reason,reason); assert_eq!(response.removed_job_ids,vec!["job"]); assert!(output.exists());
        }
    }

    #[test]
    fn closed_editor_and_imported_model_references_are_preserved() {
        for editor in [true,false] {
            let f=Fixture::new(); let output=f.file("keep.wav"); f.save(&f.row("job",&output));
            if editor { let dir=f.root.join("editor-projects/project");fs::create_dir_all(&dir).unwrap();
                fs::write(dir.join("project.json"),json!({"version":4,"id":"project","sources":[{"path":output}],"tracks":[]}).to_string()).unwrap();
            } else { let dir=f.root.join("settings");fs::create_dir_all(&dir).unwrap();fs::write(dir.join("user_models.json"),json!({"version":1,"models":[{"name":"test","model_type":"test","model_path":output}]}).to_string()).unwrap(); }
            let result=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
            assert_eq!(result.retained[0].reason,if editor {"editor_reference"} else {"input_or_other_job_reference"}); assert!(output.exists());
        }
    }

    #[test]
    fn recycling_failure_keeps_all_original_job_bytes_and_retry_handles_already_missing_files() {
        let f=Fixture::new(); let a=f.file("a.wav"); let b=f.file("b.wav");
        let pa=f.save(&f.row("a",&a)); let pb=f.save(&f.row("b",&b));
        let before=(fs::read(&pa).unwrap(),fs::read(&pb).unwrap());
        let response=f.remove(&["a","b"],true,false,false,&mut |path| if path==b {Err("fixture recycle unavailable".into())} else {fake_recycle(path)}).unwrap();
        assert!(response.removed_job_ids.is_empty()); assert_eq!(response.failed.len(),1); assert_eq!(response.trashed.len(),1);
        assert_eq!((fs::read(&pa).unwrap(),fs::read(&pb).unwrap()),before); assert!(b.exists()); assert!(!a.exists());
        let retried=f.remove(&["a","b"],true,false,false,&mut fake_recycle).unwrap();
        assert_eq!(retried.removed_job_ids,vec!["a","b"]); assert_eq!(retried.trashed.len(),2); assert!(retried.failed.is_empty());
    }

    #[test]
    fn operation_only_owns_top_level_audio_file_and_midi_does_not_claim_old_reports() {
        let f=Fixture::new(); let audio=f.file("new.wav"); let scalar=f.file("external.wav");
        let mut row=f.row("op",&audio); row["command"]=json!("run_operation");
        row["result"]=json!({"outputs":{"audio":{"kind":"audio_file","path":audio},"external":{"kind":"scalar","value":scalar},
            "nested":{"kind":"object","value":{"audio":{"kind":"audio_file","path":scalar}}}}});f.save(&row);
        let response=f.remove(&["op"],true,false,false,&mut fake_recycle).unwrap();
        assert_eq!(response.trashed.len(),1); assert!(scalar.exists());
        let midi=f.file("old.mid"); let mut row=f.row("midi",&midi);row["command"]=json!("audio_tools");row["payload"]=json!({"operation":"midi"});
        row["result"]=json!({"outputPath":midi});f.save(&row);
        let response=f.remove(&["midi"],true,false,false,&mut never_recycle).unwrap();
        assert_eq!(response.retained[0].reason,"output_ownership_uncertain"); assert!(midi.exists());
    }

    #[test]
    fn output_paths_and_single_output_path_are_current_tool_contracts() {
        for operation in ["convert","slicer","asr","loudness","merge"] {
            let f=Fixture::new();let output=f.file("new.wav");let mut row=f.row("job",&output);
            row["command"]=json!("audio_tools");row["payload"]=json!({"operation":operation});
            row["result"]=if operation=="merge" {json!({"outputPath":output})} else {json!({"outputPaths":[output]})};f.save(&row);
            let result=f.remove(&["job"],true,false,false,&mut fake_recycle).unwrap();assert_eq!(result.trashed.len(),1);assert!(!output.exists());
        }
    }

    #[test]
    fn malformed_catalog_aborts_before_any_selected_file_or_metadata_mutation() {
        let f=Fixture::new();let output=f.file("keep.wav");let path=f.save(&f.row("job",&output));let before=fs::read(&path).unwrap();
        fs::write(f.root.join("agent-jobs/broken.json"),"{broken").unwrap();
        assert!(f.remove(&["job"],true,false,false,&mut never_recycle).is_err());assert_eq!(fs::read(path).unwrap(),before);assert!(output.exists());
    }

    #[test]
    fn audio_leaf_expands_entire_group_for_preflight_metadata_and_file_removal() {
        for files in [false,true] {
            let f=Fixture::new();let a=f.file("a.wav");let b=f.file("b.wav");let rows=f.audio("group",&[a.clone(),b.clone()]);
            for row in &rows {f.save(row);}
            let before=rows.iter().map(|row|fs::read(task_journal::job_path(&f.root,row["jobId"].as_str().unwrap()).unwrap()).unwrap()).collect::<Vec<_>>();
            let checked=f.remove_audio(&["group_1"],files,true,false,&mut never_recycle).unwrap();
            assert_eq!(checked.checked_job_ids,vec!["group_0","group_1"]);assert!(checked.removed_job_ids.is_empty());
            assert!(checked.trashed.is_empty());assert!(checked.retained.is_empty());
            for (row,bytes) in rows.iter().zip(before) { assert_eq!(fs::read(task_journal::job_path(&f.root,row["jobId"].as_str().unwrap()).unwrap()).unwrap(),bytes); }
            fs::write(f.root.join("agent-jobs/group_1.cancel"),"").unwrap();
            let response=f.remove_audio(&["group_1","group_0","group_1"],files,false,false,&mut fake_recycle).unwrap();
            assert_eq!(response.checked_job_ids,checked.checked_job_ids);assert_eq!(response.removed_job_ids,checked.checked_job_ids);
            assert_eq!(response.trashed.len(),if files {2}else{0});assert!(response.retained.is_empty());
            for row in rows { assert!(!task_journal::job_path(&f.root,row["jobId"].as_str().unwrap()).unwrap().exists());
                assert!(Path::new(row["audio"]["request"]["input"].as_str().unwrap()).exists()); }
            assert_eq!(a.exists(),!files);assert_eq!(b.exists(),!files);assert!(!f.root.join("agent-jobs/group_1.cancel").exists());
        }
    }

    #[test]
    fn audio_whole_group_terminal_owner_and_type_checks_precede_any_recycling() {
        for mode in ["queued_sibling","leased_sibling","generic_id","missing_id","bad_library"] {
            let f=Fixture::new();let a=f.file("a.wav");let b=f.file("b.wav");let mut rows=f.audio("group",&[a.clone(),b.clone()]);
            if mode=="queued_sibling" {rows[1]["status"]=json!("queued");rows[1]["finishedAt"]=Value::Null;}
            for row in &rows {f.save(row);}
            let pa=task_journal::job_path(&f.root,"group_0").unwrap();let pb=task_journal::job_path(&f.root,"group_1").unwrap();
            let before=(fs::read(&pa).unwrap(),fs::read(&pb).unwrap());
            let _lease=if mode=="leased_sibling" {Some(task_journal::owner_lock(&f.root,"group_1").unwrap())}else{None};
            if mode=="generic_id" {f.save(&f.row("generic",&a));}
            if mode=="bad_library" {fs::write(f.root.join("agent-jobs/bad.json"),"{}").unwrap();}
            let ids=match mode {"generic_id"=>vec!["group_0","generic"],"missing_id"=>vec!["group_0","missing"],_=>vec!["group_0"]};
            assert!(f.remove_audio(&ids,true,false,false,&mut never_recycle).is_err(),"{mode}");
            assert_eq!((fs::read(pa).unwrap(),fs::read(pb).unwrap()),before);assert!(a.exists());assert!(b.exists());
        }
    }

    #[test]
    fn audio_partial_recycle_failure_keeps_every_group_record_and_retries_missing_outputs() {
        let f=Fixture::new();let a=f.file("a.wav");let b=f.file("b.wav");
        let mut rows=f.audio("first",&[a.clone()]);rows.extend(f.audio("second",&[b.clone()]));for row in &rows {f.save(row);}
        let before=rows.iter().map(|row|fs::read(task_journal::job_path(&f.root,row["jobId"].as_str().unwrap()).unwrap()).unwrap()).collect::<Vec<_>>();
        let response=f.remove_audio(&["first_0","second_0"],true,false,false,&mut |path| if path==b {Err("fixture failed".into())}else{fake_recycle(path)}).unwrap();
        assert!(response.removed_job_ids.is_empty());assert_eq!(response.failed.len(),1);assert_eq!(response.trashed.len(),1);
        for (row,bytes) in rows.iter().zip(before) {assert_eq!(fs::read(task_journal::job_path(&f.root,row["jobId"].as_str().unwrap()).unwrap()).unwrap(),bytes);}
        let retry=f.remove_audio(&["first_0","second_0"],true,false,false,&mut fake_recycle).unwrap();
        assert_eq!(retry.removed_job_ids,vec!["first_0","second_0"]);assert_eq!(retry.trashed.len(),2);assert!(retry.failed.is_empty());
    }

    #[test]
    fn audio_same_root_terminal_history_does_not_claim_selected_outputs_but_active_group_does() {
        for active in [false,true] {
            let f=Fixture::new();let output=f.file("selected.wav");let other=f.file("other.wav");
            for row in f.audio("selected",&[output.clone()]) {f.save(&row);}
            let mut group=f.audio("other",&[other.clone(),other.clone()]);
            group[0]["payload"]["workflow"]["nodes"]=json!([{"type":"pymss_save_audio","widgets_values":[f.output]}]);
            group[0]["payload"]["output"]=json!(f.output.join("unrelated-root"));
            for row in &mut group {row["result"]["outputDirectories"]=json!([f.output]);row["result"]["outputDirectory"]=json!(f.output);}
            if active {group[0]["status"]=json!("cancelled");group[1]["status"]=json!("queued");group[1]["finishedAt"]=Value::Null;group[1]["result"]=Value::Null;}
            for row in &group {f.save(row);}
            let mut calls=0;
            let response=f.remove_audio(&["selected_0"],true,false,false,&mut |path|{calls+=1;fake_recycle(path)}).unwrap();
            assert_eq!(calls,if active {0}else{1});assert_eq!(output.exists(),active);assert!(other.exists());
            if active {assert_eq!(response.retained[0].reason,"input_or_other_job_reference");}
        }
    }

    #[test]
    fn audio_graph_save_widgets_are_outputs_while_load_folder_and_named_input_bindings_are_sources() {
        for source in ["save","folder","named_input"] {
            let f=Fixture::new();let a=f.file("a.wav");let b=f.file("b.wav");let mut rows=f.audio("group",&[a.clone(),b.clone()]);
            rows[0]["payload"]["workflow"]["nodes"]=json!([{"type":"pymss_save_audio","widgets_values":[f.output]}]);
            if source=="folder" {
                rows[0]["payload"]["workflow"]["nodes"].as_array_mut().unwrap().push(json!({"type":"pymss_load_audio_batch","widgets_values":[f.output,false,true,""]}));
                for row in &mut rows {row["audio"]["request"]["input"]=Value::Null;}
            }else if source=="named_input" {
                for row in &mut rows {row["audio"]["request"]["inputs"]=json!({"output":f.output});}
            }
            for row in &rows {f.save(row);}
            let mut calls=0;let response=f.remove_audio(&["group_1"],true,false,false,&mut |path|{calls+=1;fake_recycle(path)}).unwrap();
            assert_eq!(calls,if source=="save" {2}else{0});assert_eq!(a.exists(),source!="save");assert_eq!(b.exists(),source!="save");
            assert_eq!(response.retained.len(),if source=="save" {0}else{2});
        }
    }

    #[test]
    fn audio_unresolved_relative_graph_inputs_are_retained_unless_an_absolute_picker_or_slot_overrides_them() {
        for binding in ["none","picker","slot"] {
            let f=Fixture::new();let output=f.file("keep.wav");let mut rows=f.audio("group",&[output.clone()]);
            rows[0]["payload"]["workflow"]["nodes"]=json!([{"type":"pymss_load_audio","widgets_values":["../source.wav","named"]}]);
            if binding!="picker" {rows[0]["audio"]["request"]["input"]=Value::Null;}
            if binding=="slot" {rows[0]["audio"]["request"]["inputs"]=json!({"named":f.file("source.wav")});}
            f.save(&rows[0]);let mut calls=0;
            let result=f.remove_audio(&["group_0"],true,false,false,&mut |path|{calls+=1;fake_recycle(path)}).unwrap();
            assert_eq!(calls,if binding=="none" {0}else{1});assert_eq!(output.exists(),binding=="none");
            if binding=="none" {assert_eq!(result.retained[0].reason,"unresolved_graph_input");}
        }
    }

    #[test]
    fn workbench_global_picker_does_not_hide_an_independent_relative_graph_input() {
        let f=Fixture::new();let output=f.file("keep.wav");let mut rows=f.audio("group",&[output.clone()]);
        rows[0]["payload"]["workbench"]=json!(true);
        rows[0]["payload"]["workflow"]["nodes"]=json!([
            {"id":1,"type":"input_audio","widgets_values":[]},
            {"id":2,"type":"pymss_load_audio","widgets_values":["../independent-source.wav","independent"]}
        ]);
        assert!(Path::new(rows[0]["audio"]["request"]["input"].as_str().unwrap()).is_absolute());
        f.save(&rows[0]);
        let result=f.remove_audio(&["group_0"],true,false,false,&mut never_recycle).unwrap();
        assert_eq!(result.retained[0].reason,"unresolved_graph_input");
        assert_eq!(result.removed_job_ids,vec!["group_0"]);assert!(output.exists());
    }

    #[test]
    fn audio_outputs_are_protected_by_cross_source_relative_file_inputs_but_not_operation_scalars() {
        let cases = vec![
            ("infer",json!({"input":"../shared.wav"}),true),
            ("infer",json!({"tasks":[{"input":"../shared.wav"}]}),true),
            ("audio_tools",json!({"operation":"asr","inputPath":"../shared.wav"}),true),
            ("audio_tools",json!({"operation":"convert","inputs":["../shared.wav"]}),true),
            ("audio_tools",json!({"operation":"sdr","estimatedPath":"../shared.wav"}),true),
            ("media_workflow",json!({"inputs":["../shared.wav"]}),true),
            ("run_operation",json!({"inputs":{"audio":{"kind":"file","path":"../shared.wav"}}}),true),
            ("run_operation",json!({"inputs":{"audio":{"kind":"audio_file","path":"../shared.wav"}}}),true),
            ("run_operation",json!({"inputs":{"audio":{"kind":"path","path":"../shared.wav"}}}),true),
            ("infer_workflow",json!({"workbench":true,"workflow":{"nodes":[{"type":"LoadAudio","widgets_values":["../shared.wav",""]}]}}),true),
            ("run_operation",json!({"inputs":{"value":{"kind":"scalar","value":"../not-an-input.wav"},"model":"htdemucs"},"params":{"model":"../model-alias"}}),false),
            ("health",json!({"inputPath":"../not-an-input.wav"}),false),
        ];
        for (command,payload,blocked) in cases {
            let f=Fixture::new();let output=f.file("keep.wav");for row in f.audio("group",&[output.clone()]) {f.save(&row);}
            let mut consumer=f.row("consumer",&output);consumer["command"]=json!(command);consumer["payload"]=payload;
            consumer["result"]=Value::Null;f.save(&consumer);
            let mut calls=0;let result=f.remove_audio(&["group_0"],true,false,false,&mut |path|{calls+=1;fake_recycle(path)}).unwrap();
            assert_eq!(calls,if blocked {0}else{1},"{command}");assert_eq!(output.exists(),blocked,"{command}");
            if blocked {assert_eq!(result.retained[0].reason,"unresolved_input_reference");}
            assert!(task_journal::job_path(&f.root,"consumer").unwrap().exists());
        }
    }

    #[test]
    fn audio_uses_the_same_library_editor_and_model_mutation_protection_as_generic_results() {
        for reason in ["library_index_unavailable","editor_open","editor_reference","model_registry_busy"] {
            let f=Fixture::new();let output=f.file("keep.wav");for row in f.audio("group",&[output.clone()]) {f.save(&row);}
            if reason=="library_index_unavailable" {fs::write(f.root.join("library.db"),"not touched").unwrap();}
            if reason=="editor_reference" {let dir=f.root.join("editor-projects/independent");fs::create_dir_all(&dir).unwrap();fs::write(dir.join("project.json"),json!({"version":4,"id":"independent","sources":[{"path":output}],"tracks":[]}).to_string()).unwrap();}
            let _model=if reason=="model_registry_busy" {crate::storage::model_reference_mutation("import_custom_model")}else{None};
            let result=f.remove_audio(&["group_0"],true,false,reason=="editor_open",&mut never_recycle).unwrap();
            assert_eq!(result.removed_job_ids,vec!["group_0"]);assert_eq!(result.retained[0].reason,reason);assert!(output.exists());
        }
    }

    #[test]
    fn malformed_registry_shapes_fail_closed_instead_of_becoming_empty_references() {
        for registry in [json!(null),json!([]),json!({"models":null}),json!({"models":[{}]}),json!({"models":[null]})] {
            let f=Fixture::new();let output=f.file("keep.wav");f.save(&f.row("job",&output));
            fs::create_dir_all(f.root.join("settings")).unwrap();let path=f.root.join("settings/user_models.json");
            fs::write(&path,registry.to_string()).unwrap();let before=fs::read(&path).unwrap();
            let result=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();
            assert_eq!(result.retained[0].reason,"model_registry_unavailable");assert_eq!(fs::read(path).unwrap(),before);assert!(output.exists());
        }
    }

    #[test]
    fn registry_mutations_protect_files_and_unspawned_failure_releases_the_lease() {
        for command in ["import_custom_model","relink_custom_model","remap_custom_model_paths","unregister_custom_model"] {
            let f=Fixture::new();let output=f.file("keep.wav");f.save(&f.row("job",&output));
            let lease=crate::storage::model_reference_mutation(command).unwrap();
            let result=f.remove(&["job"],true,true,false,&mut never_recycle).unwrap();
            assert_eq!(result.retained[0].reason,"model_registry_busy");assert!(output.exists());
            drop(lease); // command construction/spawn failure has no live child.
            let result=f.remove(&["job"],true,false,false,&mut fake_recycle).unwrap();
            assert_eq!(result.trashed.len(),1);assert!(!output.exists());
        }
    }

    #[test]
    fn registry_snapshot_guard_blocks_a_new_writer_until_recycling_finishes() {
        let f=Fixture::new();let output=f.file("out.wav");f.save(&f.row("job",&output));
        let (started,ready)=std::sync::mpsc::channel();let (release,released)=std::sync::mpsc::channel();
        let mut released=Some(released);
        let mut writer=None;
        let result=f.remove(&["job"],true,false,false,&mut |path| {
            let started=started.clone();let released=released.take().unwrap();
            writer=Some(std::thread::spawn(move || {
                let lease=crate::storage::model_reference_mutation("relink_custom_model").unwrap();
                started.send(()).unwrap();released.recv_timeout(std::time::Duration::from_secs(2)).unwrap();drop(lease);
            }));
            assert!(ready.recv_timeout(std::time::Duration::from_millis(30)).is_err(),"new mutation entered during recycle");
            fake_recycle(path)
        }).unwrap();
        assert_eq!(result.trashed.len(),1);
        ready.recv_timeout(std::time::Duration::from_secs(2)).unwrap();release.send(()).unwrap();writer.unwrap().join().unwrap();
        assert_eq!(*crate::storage::model_reference_snapshot_guard(),0);
    }

    #[test]
    fn editor_json_guard_blocks_new_project_save_during_reference_snapshot_and_recycle() {
        let f=Fixture::new();let output=f.file("out.wav");f.save(&f.row("job",&output));
        let project=f.root.join("editor-projects/new/project.json");
        let (started,ready)=std::sync::mpsc::channel();let mut writer=None;
        let result=f.remove(&["job"],true,false,false,&mut |path| {
            let project=project.clone();let sender=started.clone();let root=f.root.clone();
            writer=Some(std::thread::spawn(move || {
                crate::storage::write_editor_project_file(&root,&project,&json!({"version":4,"id":"new","sources":[],"tracks":[]})).unwrap();
                sender.send(()).unwrap();
            }));
            assert!(ready.recv_timeout(std::time::Duration::from_millis(30)).is_err(),"editor save entered during recycle");
            fake_recycle(path)
        }).unwrap();
        assert_eq!(result.trashed.len(),1);ready.recv_timeout(std::time::Duration::from_secs(2)).unwrap();writer.unwrap().join().unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn windows_junction_ancestor_is_retained_without_following_it() {
        let f=Fixture::new();let output=f.file("keep.wav");let alias=f._temp.path().join("junction");
        assert!(alias.is_absolute()&&alias.starts_with(f._temp.path()));
        let command=crate::process_command::hidden_command("cmd.exe").args(["/D","/C","mklink","/J"])
            .arg(&alias).arg(&f.output).output().unwrap();
        assert!(command.status.success(),"{}",String::from_utf8_lossy(&command.stderr));
        let target=alias.join("keep.wav");f.save(&f.row("job",&target));
        let response=f.remove(&["job"],true,false,false,&mut never_recycle);
        // Remove only our exact junction itself before TempDir cleanup.
        fs::remove_dir(&alias).unwrap();
        assert_eq!(response.unwrap().retained[0].reason,"link_or_reparse_path");assert!(output.exists());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_ancestor_is_retained_without_following_it() {
        let f=Fixture::new();let output=f.file("keep.wav");let alias=f._temp.path().join("alias");
        std::os::unix::fs::symlink(&f.output,&alias).unwrap();let target=alias.join("keep.wav");f.save(&f.row("job",&target));
        let result=f.remove(&["job"],true,false,false,&mut never_recycle).unwrap();assert_eq!(result.retained[0].reason,"link_or_reparse_path");assert!(output.exists());
    }
}
