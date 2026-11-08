use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::OnceLock;

/// The protocol manifest is embedded at compile time: the event vocabulary has
/// exactly one source, and a mismatch fails the build's tests, not production.
const MANIFEST_JSON: &str = include_str!("../../../manifest/protocol.json");

#[derive(Debug, Deserialize)]
struct ManifestEvents {
    canonical: Vec<String>,
    #[serde(default)]
    terminal: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
struct ManifestBootstrap {
    #[serde(default)]
    worker: Vec<String>,
    #[serde(default)]
    native: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
struct ManifestResultJobs {
    #[serde(default)]
    commands: Vec<String>,
    #[serde(default)]
    operations: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestOutputScope {
    #[serde(default)]
    commands: Vec<String>,
    #[serde(default)]
    audio_tool_commands: Vec<String>,
    #[serde(default)]
    operations: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ManifestExecution {
    #[serde(default)]
    bootstrap: ManifestBootstrap,
    #[serde(default)]
    result_jobs: ManifestResultJobs,
    #[serde(default)]
    output_scope: ManifestOutputScope,
    #[serde(default)]
    background_result: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct Manifest {
    events: ManifestEvents,
    #[serde(default)]
    execution: ManifestExecution,
}

fn manifest() -> &'static Manifest {
    static MANIFEST: OnceLock<Manifest> = OnceLock::new();
    MANIFEST.get_or_init(|| {
        serde_json::from_str(MANIFEST_JSON).expect("manifest/protocol.json must stay valid JSON")
    })
}

pub fn is_canonical_event(kind: &str) -> bool {
    manifest().events.canonical.iter().any(|name| name == kind)
}

pub fn is_terminal_event(kind: &str) -> bool {
    manifest().events.terminal.iter().any(|name| name == kind)
}

/// Commands that must run on the bootstrap interpreter: the worker commands the
/// manifest declares plus native Rust entries that drive pip directly.
/// Commands (and audio-tool operations) that must run inside a temporary
/// output scope so a crash cannot leave half-written files in place.
pub fn needs_audio_output_scope(command: &str, operation: Option<&str>) -> bool {
    let scope = &manifest().execution.output_scope;
    scope.commands.iter().any(|item| item == command)
        || (scope.audio_tool_commands.iter().any(|item| item == command)
            && operation.is_some_and(|op| {
                // Python normalizes audio-tool operations with strip + lower;
                // matching must accept the same spellings.
                let normalized = op.trim().to_ascii_lowercase();
                scope.operations.iter().any(|name| name == &normalized)
            }))
}

/// Commands whose background agent must report a real terminal event; a zero
/// exit code alone is not a result.
pub fn requires_background_result(command: &str) -> bool {
    manifest().execution.background_result.iter().any(|item| item == command)
}

/// Result-bearing job commands: worker commands plus media operation aliases.
pub fn is_result_job_command(command: &str) -> bool {
    let jobs = &manifest().execution.result_jobs;
    jobs.commands
        .iter()
        .chain(jobs.operations.iter())
        .any(|name| name == command)
}

pub fn is_bootstrap_command(command: &str) -> bool {
    let bootstrap = &manifest().execution.bootstrap;
    bootstrap
        .worker
        .iter()
        .chain(bootstrap.native.iter())
        .any(|name| name == command)
}

/// Envelope kinds the task journal consumes although they are not engine stream
/// events: embedded editor progress/results and worker-process diagnostics.
pub const JOURNAL_ONLY_EVENTS: &[&str] = &["workflow_execution", "editor_mix_exported", "error", "worker_log"];

/// Journal handling is limited to `task.*`; `asset.*` has its own lifecycle and
/// must not be folded into task rows.
pub fn is_journal_event(kind: &str) -> bool {
    (kind.starts_with("task.") && is_canonical_event(kind))
        || JOURNAL_ONLY_EVENTS.iter().any(|name| *name == kind)
}

#[derive(Debug, Serialize, Deserialize)]
pub struct WorkerEnvelope {
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(default, rename = "requestId")]
    pub request_id: Option<String>,
    #[serde(default, rename = "taskId")]
    pub task_id: Option<String>,
    #[serde(default)]
    pub timestamp: Option<String>,
    #[serde(default)]
    pub payload: Value,
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_events_are_well_formed() {
        let events = &manifest().events;
        assert!(!events.canonical.is_empty(), "canonical events must not be empty");
        for name in &events.canonical {
            assert!(
                name.starts_with("task.") || name.starts_with("asset."),
                "event {name} must be namespaced"
            );
        }
        for terminal in &events.terminal {
            assert!(
                events.canonical.contains(terminal),
                "terminal event {terminal} must be canonical"
            );
        }
        assert!(events.terminal.iter().any(|name| name == "task.result"));
    }

    #[test]
    fn journal_accepts_every_canonical_task_event_but_no_asset_event() {
        for name in &manifest().events.canonical {
            if name.starts_with("task.") {
                assert!(is_journal_event(name), "journal must observe {name}");
            } else {
                assert!(!is_journal_event(name), "journal must not observe {name}");
            }
        }
    }

    #[test]
    fn bootstrap_commands_come_from_the_manifest() {
        let bootstrap = &manifest().execution.bootstrap;
        assert!(!bootstrap.worker.is_empty(), "bootstrap worker commands must be declared");
        for command in bootstrap.worker.iter().chain(bootstrap.native.iter()) {
            assert!(is_bootstrap_command(command), "{command} must be recognised");
        }
        assert!(is_bootstrap_command("update_runtime_core"));
        assert!(!is_bootstrap_command("infer"));
        assert!(!bootstrap.native.iter().any(|name| bootstrap.worker.contains(name)));
    }

    #[test]
    fn output_scope_and_background_results_match_the_declared_sets() {
        for operation in ["convert", "merge", "slicer", " CONVERT "] {
            assert!(needs_audio_output_scope("audio_tools", Some(operation)));
        }
        assert!(!needs_audio_output_scope("audio_tools", Some("scan")));
        assert!(!needs_audio_output_scope("audio_tools", None));
        assert!(needs_audio_output_scope("run_operation", None));
        assert!(!needs_audio_output_scope("infer", Some("convert")));
        for command in ["audio_tools", "export_editor_mix", "manage_plugin", "run_operation"] {
            assert!(requires_background_result(command));
        }
        assert!(!requires_background_result("list_operations"));
    }

    #[test]
    fn result_job_commands_cover_the_legacy_list() {
        for name in [
            "infer",
            "infer_workflow",
            "audio_tools",
            "media_tool",
            "media_image",
            "media_video",
            "media_workflow",
            "media_convert",
            "run_operation",
            "export_editor_mix",
        ] {
            assert!(is_result_job_command(name), "{name} must stay a result job command");
        }
        assert!(!is_result_job_command("media_probe"));
        assert!(!is_result_job_command("runtime_info"));
    }

    #[test]
    fn journal_only_events_stay_out_of_the_canonical_vocabulary() {
        for name in JOURNAL_ONLY_EVENTS {
            assert!(!is_canonical_event(name), "{name} is internal and must not be canonical");
            assert!(is_journal_event(name));
        }
    }
}
