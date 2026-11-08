//! Canonical result-job removal. The request contains IDs, never caller paths.
use super::task_journal;
use crate::error::{AppError, AppResult};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

const COMMANDS: &[&str] = &["infer", "infer_workflow", "audio_tools", "media_tool", "media_image",
    "media_video", "media_workflow", "media_convert", "run_operation"];

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

fn invalid(message: &str) -> AppError { AppError::Worker(message.into()) }

fn linked(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    { use std::os::windows::fs::MetadataExt; if metadata.file_attributes() & 0x400 != 0 { return true; } }
    metadata.file_type().is_symlink()
}

fn plain_path(path: &Path) -> Result<(), &'static str> {
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
    match value {
        Value::String(value) if Path::new(value).is_absolute() => output.push(key(Path::new(value))),
        Value::Array(values) => for value in values { references(value, bindings, output); },
        Value::Object(values) => for (name, value) in values {
            let input = bindings || matches!(name.as_str(), "input" | "inputs" | "workbenchOutputs");
            if !input && matches!(name.as_str(), "output" | "outputDir" | "output_dir" | "outputRoot") { continue; }
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
        references(&project, true, &mut refs);
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
    let entries = registry.get("models").and_then(Value::as_array).ok_or_else(|| invalid("invalid user model registry"))?;
    for entry in entries {
        if !entry.is_object() || ["name", "model_type", "model_path"].iter().any(|key| {
            !entry[*key].as_str().is_some_and(|value| !value.trim().is_empty())
        }) { return Err(invalid("invalid user model registry entry")); }
    }
    let mut refs = Vec::new();
    references(&registry, true, &mut refs);
    Ok(refs)
}

fn candidates(row: &Value) -> Vec<(String, Option<&'static str>)> {
    let result = &row["result"];
    let command = row["command"].as_str().unwrap();
    let mut paths = Vec::new();
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
    remove_with(root, ids, delete_files, check_only, protection, editor_open,
                &mut crate::commands::app::recycle::recycle_file)
}

fn remove_with(root: &Path, ids: &[String], delete_files: bool, check_only: bool, protection: &Protection,
               editor_open: impl Fn() -> bool, recycle: &mut impl FnMut(&Path) -> Result<(), String>) -> AppResult<RemovalResult> {
    let selected = ids.iter().cloned().collect::<BTreeSet<_>>();
    if selected.iter().any(|id| !task_journal::valid_id(id)) { return Err(invalid("invalid result job id")); }
    let mut response = RemovalResult { schema_version: 2, checked_job_ids: selected.iter().cloned().collect(),
        removed_job_ids: vec![], trashed: vec![], failed: vec![], retained: vec![] };
    if selected.is_empty() { return Ok(response); }
    for path in [root.to_path_buf(), root.join("agent-jobs"), root.join("agent-jobs/.owners"), root.join("agent-jobs/.writes")] {
        plain_path(&path).map_err(invalid)?;
    }
    let _catalog = task_journal::catalog_lock(root)?;
    let library = task_journal::read_all_unlocked(root)?;
    for id in &selected {
        let row = library.iter().find(|row| row["jobId"] == *id).ok_or_else(|| invalid("result job is missing"))?;
        if row.get("audio").is_some() || !COMMANDS.contains(&row["command"].as_str().unwrap()) {
            return Err(invalid("only generic result jobs can be removed here"));
        }
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
        if delete_files {
            let mut input_refs = Vec::new();
            for row in &library {
                let active_other = !selected.contains(row["jobId"].as_str().unwrap()) && !task_journal::terminal(row);
                references(&row["payload"], active_other, &mut input_refs);
                if let Some(audio) = row.get("audio") { references(&audio["request"], true, &mut input_refs); }
                if !selected.contains(row["jobId"].as_str().unwrap()) {
                    references(&row["result"], false, &mut input_refs);
                    references(&row["runtime"], false, &mut input_refs);
                }
            }
            let editor_refs = editor_references(root);
            let registry_ok = model_references(root).map(|refs| input_refs.extend(refs)).is_ok();
            let library_present = match fs::symlink_metadata(root.join("library.db")) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
                _ => true,
            };
            let protected = protection.roots.iter().map(|path| key(path)).collect::<Vec<_>>();
            let root_key = key(root);
            let outputs_key = key(&root.join("outputs"));
            let mut outputs = BTreeMap::new();
            for row in &rows { for (path, reason) in candidates(row) {
                outputs.entry(key(Path::new(&path))).and_modify(|entry: &mut (String, Option<&'static str>)| {
                    if reason.is_some() { entry.1 = reason; }
                }).or_insert((path, reason));
            } }
            for (_, (path, forced)) in outputs {
                let target = Path::new(&path);
                let normalized = key(target);
                let reason = forced.or_else(|| plain_path(target).err())
                    .or_else(|| (*model_guard > 0).then_some("model_registry_busy"))
                    .or_else(|| (within(&normalized, &root_key) && !within(&normalized, &outputs_key)).then_some("protected_app_data"))
                    .or_else(|| protected.iter().any(|root| within(&normalized, root)).then_some("protected_asset_or_program"))
                    .or_else(|| input_refs.iter().any(|reference| within(&normalized, reference)).then_some("input_or_other_job_reference"))
                    .or_else(|| editor_open().then_some("editor_open"))
                    .or_else(|| library_present.then_some("library_index_unavailable"))
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
        fn save(&self, row: &Value) -> PathBuf {
            super::super::job_schema::validate(row).unwrap();
            let path = task_journal::job_path(&self.root, row["jobId"].as_str().unwrap()).unwrap();
            fs::write(&path, serde_json::to_vec(row).unwrap()).unwrap(); path
        }
        fn remove(&self, ids: &[&str], files: bool, check: bool, open: bool,
                  recycle: &mut impl FnMut(&Path) -> Result<(), String>) -> AppResult<RemovalResult> {
            remove_with(&self.root, &ids.iter().map(|id| id.to_string()).collect::<Vec<_>>(), files, check,
                &Protection { roots: vec![] }, || open, recycle)
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
        let result=remove_with(&f.root,&["job".into()],true,false,&Protection{roots:vec![f.output.clone()]},||false,&mut never_recycle).unwrap();
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
                fs::write(dir.join("project.json"),json!({"version":4,"sources":[{"path":output}],"tracks":[]}).to_string()).unwrap();
            } else { let dir=f.root.join("settings");fs::create_dir_all(&dir).unwrap();fs::write(dir.join("user_models.json"),json!({"models":[{"name":"test","model_type":"test","model_path":output}]}).to_string()).unwrap(); }
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
            let project=project.clone();let sender=started.clone();
            writer=Some(std::thread::spawn(move || {
                crate::storage::write_editor_project_file(&project,&json!({"version":4,"sources":[],"tracks":[]})).unwrap();
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
