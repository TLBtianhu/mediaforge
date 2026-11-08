use super::*;
use serde_json::json;

#[derive(Clone, Copy, Debug)]
enum Mode { Explicit, Tasks, Orphans }
const MODES: [Mode; 3] = [Mode::Explicit, Mode::Tasks, Mode::Orphans];

struct Fixture {
    root: tempfile::TempDir,
    data_root: PathBuf,
    _model: std::sync::MutexGuard<'static, ()>,
}

impl Fixture {
    fn new() -> Self {
        let guard = storage::model_reference_test_guard();
        let root = tempfile::tempdir().unwrap();
        let data_root = root.path().to_path_buf();
        Self { root, data_root, _model: guard }
    }

    fn path(&self) -> &Path { &self.data_root }

    fn project(&self, id: &str, source: Option<&str>) -> PathBuf {
        let directory = self.path().join("editor-projects").join(id);
        fs::create_dir_all(&directory).unwrap();
        let mut project = json!({"version":4,"id":id,"name":id,"sources":[],"tracks":[]});
        if let Some(source) = source { project["sourceTaskId"] = source.into(); }
        fs::write(directory.join("project.json"), project.to_string()).unwrap();
        directory
    }

    fn patch(&self, id: &str, edit: impl FnOnce(&mut Value)) {
        let path = self.path().join("editor-projects").join(id).join("project.json");
        let mut project: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        edit(&mut project);
        fs::write(path, project.to_string()).unwrap();
    }

    fn job(&self, command: &str, payload: Value, result: Value) {
        let directory = self.path().join("agent-jobs");
        fs::create_dir_all(&directory).unwrap();
        let record = json!({"schemaVersion":2,"jobId":"consumer","command":command,
            "source":"agent","status":"failed","fraction":0,"message":"fixture","error":"failed",
            "createdAt":1,"updatedAt":2,"startedAt":1,"finishedAt":2,"owner":null,
            "runtime":{"stage":null,"progress":null,"nodes":{},"order":[]},"logs":[],
            "payload":payload,"result":result});
        fs::write(directory.join("consumer.json"), record.to_string()).unwrap();
    }

    fn registry(&self, models: Value) {
        let settings = self.path().join("settings");
        fs::create_dir_all(&settings).unwrap();
        fs::write(settings.join("user_models.json"), json!({"version":1,"models":models}).to_string()).unwrap();
    }

    fn remove(&self, mode: Mode, ids: &[&str], protected: &[PathBuf]) -> AppResult<EditorProjectCleanupResult> {
        let ids = ids.iter().map(|id| id.to_string()).collect::<Vec<_>>();
        let tasks = ids.iter().map(|id| id.strip_prefix("edit_").unwrap_or(id).to_string()).collect::<Vec<_>>();
        let selection = match mode {
            Mode::Explicit => Selection::Explicit,
            Mode::Tasks => Selection::Tasks(&tasks),
            Mode::Orphans => Selection::Orphans,
        };
        delete_projects(self.path(), &ids, selection, protected, |_| vec![])
    }
}

fn assert_blocked(result: EditorProjectCleanupResult, id: &str) {
    assert!(result.deleted_project_ids.is_empty());
    assert!(result.blocked_project_ids.iter().any(|value| value == id));
}

#[test]
fn all_modes_remove_only_selected_current_projects_and_report_missing_entries() {
    for mode in MODES {
        let fixture = Fixture::new();
        let selected = fixture.project("edit_a", Some("a"));
        let untouched = fixture.project("edit_b", Some("b"));
        let recording = selected.join("recordings/take.wav");
        fs::create_dir_all(recording.parent().unwrap()).unwrap();
        fs::write(&recording, b"owned recording").unwrap();
        fixture.patch("edit_a", |project| project["sources"] = json!([{"path":recording}]));
        let result = fixture.remove(mode, &["edit_a", "edit_missing", "edit_a"], &[]).unwrap();
        assert_eq!(result.deleted_project_ids, vec!["edit_a"], "{mode:?}");
        assert_eq!(result.missing_project_ids, vec!["edit_missing"], "{mode:?}");
        assert!(!selected.exists());
        assert!(untouched.join("project.json").is_file());
    }
}

#[test]
fn all_modes_preserve_model_weights_and_config_files_inside_the_selected_project() {
    for mode in MODES {
        for field in ["model_path", "config_path"] {
            let fixture = Fixture::new();
            let project = fixture.project("edit_a", Some("a"));
            let referenced = project.join(if field == "model_path" { "weights.custom" } else { "config.custom" });
            fs::write(&referenced, b"model asset sentinel").unwrap();
            let outside = fixture.path().join("outside.weights");
            fs::write(&outside, b"weights").unwrap();
            let mut model = json!({"name":"test","model_type":"bs_roformer","model_path":outside,"config_path":null});
            model[field] = json!(referenced);
            fixture.registry(json!([model]));
            let registry = fixture.path().join("settings/user_models.json");
            let before = fs::read(&registry).unwrap();
            assert_blocked(fixture.remove(mode, &["edit_a"], &[]).unwrap(), "edit_a");
            assert_eq!(fs::read(&referenced).unwrap(), b"model asset sentinel");
            assert_eq!(fs::read(registry).unwrap(), before);
        }
    }
}

#[test]
fn all_modes_preserve_sources_used_by_an_independent_closed_project() {
    for mode in MODES {
        let fixture = Fixture::new();
        let selected = fixture.project("edit_a", Some("a"));
        let shared = selected.join("recordings/take.wav");
        fs::create_dir_all(shared.parent().unwrap()).unwrap();
        fs::write(&shared, b"shared audio").unwrap();
        let other = fixture.project("independent", None);
        fixture.patch("independent", |project| project["sources"] = json!([{"path":shared}]));
        let before = fs::read(other.join("project.json")).unwrap();
        assert_blocked(fixture.remove(mode, &["edit_a"], &[]).unwrap(), "edit_a");
        assert_eq!(fs::read(shared).unwrap(), b"shared audio");
        assert_eq!(fs::read(other.join("project.json")).unwrap(), before);
    }
}

#[test]
fn batch_cross_references_are_preserved_in_both_selection_orders() {
    for mode in MODES {
        for ids in [["edit_a", "edit_b"], ["edit_b", "edit_a"]] {
            let fixture = Fixture::new();
            let a = fixture.project("edit_a", Some("a"));
            let b = fixture.project("edit_b", Some("b"));
            fs::write(a.join("a.wav"), b"a").unwrap();
            fs::write(b.join("b.wav"), b"b").unwrap();
            fixture.patch("edit_a", |project| project["sources"] = json!([{"path":b.join("b.wav")} ]));
            fixture.patch("edit_b", |project| project["sources"] = json!([{"path":a.join("a.wav")} ]));
            let result = fixture.remove(mode, &ids, &[]).unwrap();
            assert!(result.deleted_project_ids.is_empty());
            assert_eq!(result.blocked_project_ids, vec!["edit_a", "edit_b"]);
            assert_eq!(fs::read(a.join("a.wav")).unwrap(), b"a");
            assert_eq!(fs::read(b.join("b.wav")).unwrap(), b"b");
        }
    }
}

#[test]
fn a_blocked_batch_member_prevents_deleting_an_unreferenced_sibling() {
    for mode in MODES {
        let fixture = Fixture::new();
        let a = fixture.project("edit_a", Some("a"));
        let b = fixture.project("edit_b", Some("b"));
        let outside = fixture.project("independent", None);
        fs::write(b.join("used.wav"), b"shared").unwrap();
        fixture.patch("independent", |project| project["sources"] = json!([{"path":b.join("used.wav")} ]));
        let result = fixture.remove(mode, &["edit_a", "edit_b"], &[]).unwrap();
        assert_blocked(result, "edit_b");
        assert!(a.exists());assert!(b.exists());assert!(outside.exists());
    }
}

#[test]
fn all_modes_reject_relative_editor_sources_assets_plugins_and_result_roots_without_guessing_a_base() {
    for mode in MODES {
        for field in ["sources", "assets", "plugin", "sourceResultDir"] {
            let fixture = Fixture::new();
            let a = fixture.project("edit_a", Some("a"));
            let other = fixture.project("independent", None);
            fixture.patch("independent", |project| match field {
                "sources" | "assets" => project[field] = json!([{"path":"../edit_a/take.wav",
                    "originRoot":fixture.path(),"relativePath":"edit_a/take.wav"}]),
                "plugin" => project["tracks"] = json!([{"autoName":true,"clips":[],"pluginChain":[{"path":"../edit_a/plugin.dll"}]}]),
                _ => project["sourceResultDir"] = json!("../edit_a"),
            });
            let before = fs::read(other.join("project.json")).unwrap();
            assert!(fixture.remove(mode, &["edit_a"], &[]).is_err(), "{mode:?}/{field}");
            assert!(a.exists());assert_eq!(fs::read(other.join("project.json")).unwrap(), before);
        }
    }
}

#[test]
fn all_modes_reject_unresolved_current_job_inputs_before_removing_any_project() {
    for mode in MODES {
        for (command, payload) in [
            ("audio_tools", json!({"inputPath":"../editor-projects/edit_a/take.wav"})),
            ("run_operation", json!({"inputs":{"audio":{"kind":"audio_file","path":"../edit_a/take.wav"}}})),
            ("import_custom_model", json!({"name":"fixture","modelType":"demucs","modelPath":"../edit_a/weights.pt"})),
            ("relink_custom_model", json!({"modelPath":"../edit_a/weights.pt"})),
            ("waveform_peaks", json!({"path":"../edit_a/take.wav"})),
            ("infer_workflow", json!({"workflow":{"nodes":[{"type":"pymss_load_audio_batch","widgets_values":["../editor-projects",true,"name",""]}]}})),
        ] {
            let fixture = Fixture::new();
            let project = fixture.project("edit_a", Some("a"));
            fixture.job(command, payload, Value::Null);
            let job = fixture.path().join("agent-jobs/consumer.json");
            let before = fs::read(&job).unwrap();
            assert!(fixture.remove(mode, &["edit_a"], &[]).is_err(), "{mode:?}/{command}");
            assert!(project.exists());assert_eq!(fs::read(job).unwrap(), before);
        }
    }
}

#[test]
fn all_modes_preserve_a_directory_used_by_a_current_job_including_ancestor_folder_inputs() {
    for mode in MODES {
        for ancestor in [false, true] {
            let fixture = Fixture::new();
            let project = fixture.project("edit_a", Some("a"));
            let path = if ancestor { project.parent().unwrap().to_path_buf() } else { project.join("take.wav") };
            if !ancestor { fs::write(&path, b"audio").unwrap(); }
            fixture.job("infer_workflow", json!({"workbench":true,"workflow":{"nodes":[{
                "type":"pymss_load_audio_batch","widgets_values":[path,true,"name",""]}]}}), Value::Null);
            assert_blocked(fixture.remove(mode, &["edit_a"], &[]).unwrap(), "edit_a");
            assert!(project.exists());
        }
    }
}

#[test]
fn all_modes_preserve_configured_asset_or_program_roots_at_any_overlap_depth() {
    for mode in MODES {
        for relation in ["inside", "same", "parent"] {
            let fixture = Fixture::new();
            let project = fixture.project("edit_a", Some("a"));
            let protected = match relation {
                "inside" => project.join("custom-models"),
                "same" => project.clone(),
                _ => project.parent().unwrap().to_path_buf(),
            };
            fs::create_dir_all(&protected).unwrap();
            let marker = project.join("sentinel");fs::write(&marker, b"protected").unwrap();
            assert_blocked(fixture.remove(mode, &["edit_a"], &[protected]).unwrap(), "edit_a");
            assert_eq!(fs::read(marker).unwrap(), b"protected");
        }
    }
}

#[test]
fn portable_and_common_program_ancestors_allow_the_managed_project_area_for_all_modes() {
    for mode in MODES {
        for layout in ["portable", "same_data_root", "common_ancestor"] {
            let mut fixture = Fixture::new();
            let container = fixture.root.path().to_path_buf();
            fixture.data_root = match layout {
                "same_data_root" => container.clone(),
                "common_ancestor" => container.join("profiles/current/data"),
                _ => container.join("data"),
            };
            let project = fixture.project("edit_a", Some("a"));
            let program = if layout == "common_ancestor" { container.join("app") } else { container.clone() };
            fs::create_dir_all(&program).unwrap();
            let executable = program.join("MediaForge.exe");fs::write(&executable, b"program sentinel").unwrap();
            let resources = program.join("resources");fs::create_dir_all(&resources).unwrap();
            fs::write(resources.join("worker.py"), b"resource sentinel").unwrap();
            let assets = vec![container.join("models"), container.join("runtime-envs"), container.join("python-runtime")];
            let policy = super::super::editor_project_protection_from(fixture.path(), assets, &executable,
                &[container.clone(), program, resources.clone(), fixture.path().to_path_buf()]);
            let orphans = list_orphaned_projects(fixture.path(), &policy.roots).unwrap();
            assert_eq!(orphans.iter().map(|project| project.project_id.as_str()).collect::<Vec<_>>(), vec!["edit_a"], "{mode:?}/{layout}");
            let result = fixture.remove(mode, &["edit_a"], &policy.roots).unwrap();
            assert_eq!(result.deleted_project_ids, vec!["edit_a"], "{mode:?}/{layout}");
            assert!(!project.exists());
            assert_eq!(fs::read(executable).unwrap(), b"program sentinel");
            assert_eq!(fs::read(resources.join("worker.py")).unwrap(), b"resource sentinel");
        }
    }
}

#[test]
fn project_policy_keeps_asset_ancestors_and_program_or_resource_files_inside_a_project_protected() {
    for mode in MODES {
        for protected_kind in ["model_ancestor", "runtime_ancestor", "embedded_executable", "embedded_program", "embedded_resource"] {
            let mut fixture = Fixture::new();
            let container = fixture.root.path().to_path_buf();
            fixture.data_root = container.join("data");
            let project = fixture.project("edit_a", Some("a"));
            let executable = if protected_kind == "embedded_executable" { project.join("MediaForge.exe") }
                else { container.join("MediaForge.exe") };
            fs::write(&executable, b"program sentinel").unwrap();
            let mut assets = vec![container.join("models"), container.join("runtime-envs"), container.join("python-runtime")];
            match protected_kind {
                "model_ancestor" => assets[0] = container.clone(),
                "runtime_ancestor" => assets[1] = container.clone(),
                _ => (),
            }
            let embedded = project.join(if protected_kind == "embedded_resource" { "resources" } else { "program" });
            let mut program_roots = vec![container.clone()];
            if matches!(protected_kind, "embedded_program" | "embedded_resource") {
                fs::create_dir_all(&embedded).unwrap();
                fs::write(embedded.join("worker.py"), b"embedded resource sentinel").unwrap();
                program_roots.push(embedded.clone());
            }
            let original_project = fs::read(project.join("project.json")).unwrap();
            let expected_asset = assets[if protected_kind == "runtime_ancestor" { 1 } else { 0 }].clone();
            let policy = super::super::editor_project_protection_from(fixture.path(), assets, &executable, &program_roots);
            assert!(policy.roots.contains(&executable));
            if matches!(protected_kind, "model_ancestor" | "runtime_ancestor") { assert!(policy.roots.contains(&expected_asset)); }
            assert!(list_orphaned_projects(fixture.path(), &policy.roots).unwrap().is_empty());
            assert_blocked(fixture.remove(mode, &["edit_a"], &policy.roots).unwrap(), "edit_a");
            assert_eq!(fs::read(project.join("project.json")).unwrap(), original_project);
            assert_eq!(fs::read(executable).unwrap(), b"program sentinel");
            if embedded.exists() {assert_eq!(fs::read(embedded.join("worker.py")).unwrap(), b"embedded resource sentinel");}
        }
    }
}

#[test]
fn model_registry_busy_unknown_schema_and_invalid_paths_fail_closed_for_all_modes() {
    for mode in MODES {
        for invalid_registry in ["busy", "broken", "old", "future", "duplicate", "relative"] {
            let fixture = Fixture::new();
            let project = fixture.project("edit_a", Some("a"));
            let sibling = fixture.project("edit_b", Some("b"));
            let lease = if invalid_registry == "busy" { storage::model_reference_mutation("import_custom_model") } else { None };
            if invalid_registry != "busy" {
                fixture.registry(json!([]));
                let registry = fixture.path().join("settings/user_models.json");
                match invalid_registry {
                    "broken" => fs::write(&registry, b"{broken").unwrap(),
                    "old" => fs::write(&registry, json!({"models":[]}).to_string()).unwrap(),
                    "future" => fs::write(&registry, json!({"version":2,"models":[]}).to_string()).unwrap(),
                    "duplicate" => fs::write(&registry, r#"{"version":1,"models":[],"models":[]}"#).unwrap(),
                    _ => fixture.registry(json!([{"name":"test","model_type":"bs_roformer","model_path":"relative.weights"}])),
                }
            }
            let before = fs::read(project.join("project.json")).unwrap();
            let sibling_before = fs::read(sibling.join("project.json")).unwrap();
            let registry_path = fixture.path().join("settings/user_models.json");
            let registry_before = fs::read(&registry_path).ok();
            assert!(fixture.remove(mode, &["edit_a", "edit_b"], &[]).is_err(), "{mode:?}/{invalid_registry}");
            assert_eq!(fs::read(project.join("project.json")).unwrap(), before);
            assert_eq!(fs::read(sibling.join("project.json")).unwrap(), sibling_before);
            assert_eq!(fs::read(registry_path).ok(), registry_before);
            drop(lease);
        }
    }
}

#[test]
fn every_selected_tree_is_preflighted_before_any_recursive_delete() {
    for mode in MODES {
        for invalid in ["old", "future", "duplicate", "bad_json", "identity"] {
            let fixture = Fixture::new();
            let a = fixture.project("edit_a", Some("a"));
            let b = fixture.project("edit_b", Some("b"));
            let bad = match invalid {
                "old" => r#"{"id":"edit_b","sourceTaskId":"b","sources":[],"tracks":[]}"#,
                "future" => r#"{"version":5,"id":"edit_b","sourceTaskId":"b","sources":[],"tracks":[]}"#,
                "duplicate" => r#"{"version":4,"id":"edit_b","id":"other","sourceTaskId":"b","sources":[],"tracks":[]}"#,
                "identity" => r#"{"version":4,"id":"other","sourceTaskId":"b","sources":[],"tracks":[]}"#,
                _ => "{broken",
            };
            fs::write(b.join("project.json"), bad).unwrap();
            let before = fs::read(a.join("project.json")).unwrap();
            assert!(fixture.remove(mode, &["edit_a", "edit_b"], &[]).is_err(), "{mode:?}/{invalid}");
            assert_eq!(fs::read(a.join("project.json")).unwrap(), before);
            assert_eq!(fs::read(b.join("project.json")).unwrap(), bad.as_bytes());
        }
    }
}

#[test]
fn open_editor_callback_blocks_the_whole_selected_batch_before_removal() {
    for mode in MODES {
        let fixture = Fixture::new();
        let a = fixture.project("edit_a", Some("a"));let b = fixture.project("edit_b", Some("b"));
        let ids = vec!["edit_a".into(), "edit_b".into()];
        let tasks = vec!["a".into(), "b".into()];
        let selection = match mode { Mode::Explicit => Selection::Explicit, Mode::Tasks => Selection::Tasks(&tasks), Mode::Orphans => Selection::Orphans };
        let result = delete_projects(fixture.path(), &ids, selection, &[], |candidates| {
            // The IPC reports all candidates when any editor window is open,
            // including an unrelated editor with unpersisted source references.
            candidates.to_vec()
        }).unwrap();
        assert_eq!(result.blocked_project_ids, ids);assert!(result.deleted_project_ids.is_empty());
        assert!(a.exists());assert!(b.exists());
    }
}

struct OwnedDirectoryLink(PathBuf);
impl OwnedDirectoryLink {
    fn create(path: &Path, target: &Path) -> Self {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let output = crate::process_command::hidden_command("cmd.exe")
                .raw_arg(format!("/c mklink /J \"{}\" \"{}\"", path.display(), target.display()))
                .output().unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(target, path).unwrap();
        Self(path.to_path_buf())
    }
}
impl Drop for OwnedDirectoryLink {
    fn drop(&mut self) {
        #[cfg(windows)]
        fs::remove_dir(&self.0).unwrap();
        #[cfg(unix)]
        fs::remove_file(&self.0).unwrap();
    }
}

#[test]
fn root_target_project_json_and_nested_reparse_paths_never_remove_external_sentinels() {
    for mode in MODES {
        for placement in ["root", "target", "project_json", "nested"] {
            let fixture = Fixture::new();
            let outside = tempfile::tempdir().unwrap();
            let sentinel = outside.path().join("external-sentinel");fs::write(&sentinel, b"never delete outside").unwrap();
            let projects = fixture.path().join("editor-projects");
            let link = match placement {
                "root" => OwnedDirectoryLink::create(&projects, outside.path()),
                "target" => {fs::create_dir_all(&projects).unwrap();OwnedDirectoryLink::create(&projects.join("edit_a"), outside.path())},
                "project_json" => {
                    let project = fixture.project("edit_a", Some("a"));fs::remove_file(project.join("project.json")).unwrap();
                    OwnedDirectoryLink::create(&project.join("project.json"), outside.path())
                },
                _ => {
                    fixture.project("edit_a", Some("a"));
                    let project = fixture.project("edit_b", Some("b"));fs::create_dir_all(project.join("recordings")).unwrap();
                    OwnedDirectoryLink::create(&project.join("recordings/outside"), outside.path())
                },
            };
            let result = fixture.remove(mode, if placement == "nested" { &["edit_a", "edit_b"] } else { &["edit_a"] }, &[]);
            assert!(result.is_err() || result.unwrap().deleted_project_ids.is_empty(), "{mode:?}/{placement}");
            assert_eq!(fs::read(&sentinel).unwrap(), b"never delete outside");
            if placement == "nested" {assert!(projects.join("edit_a/project.json").is_file());}
            drop(link); // Remove only the test-created link before TempDir cleanup.
        }
    }
}
