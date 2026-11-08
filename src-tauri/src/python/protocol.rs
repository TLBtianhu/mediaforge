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

#[derive(Debug, Deserialize)]
struct Manifest {
    events: ManifestEvents,
}

#[derive(Debug)]
pub struct EventVocabulary {
    pub canonical: Vec<String>,
    pub terminal: Vec<String>,
}

fn vocabulary() -> &'static EventVocabulary {
    static VOCABULARY: OnceLock<EventVocabulary> = OnceLock::new();
    VOCABULARY.get_or_init(|| {
        let manifest: Manifest = serde_json::from_str(MANIFEST_JSON)
            .expect("manifest/protocol.json must stay valid JSON");
        EventVocabulary {
            canonical: manifest.events.canonical,
            terminal: manifest.events.terminal,
        }
    })
}

pub fn is_canonical_event(kind: &str) -> bool {
    vocabulary().canonical.iter().any(|name| name == kind)
}

pub fn is_terminal_event(kind: &str) -> bool {
    vocabulary().terminal.iter().any(|name| name == kind)
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
        let vocabulary = vocabulary();
        assert!(!vocabulary.canonical.is_empty(), "canonical events must not be empty");
        for name in &vocabulary.canonical {
            assert!(
                name.starts_with("task.") || name.starts_with("asset."),
                "event {name} must be namespaced"
            );
        }
        for terminal in &vocabulary.terminal {
            assert!(
                vocabulary.canonical.contains(terminal),
                "terminal event {terminal} must be canonical"
            );
        }
        assert!(vocabulary.terminal.iter().any(|name| name == "task.result"));
    }

    #[test]
    fn journal_accepts_every_canonical_task_event_but_no_asset_event() {
        for name in &vocabulary().canonical {
            if name.starts_with("task.") {
                assert!(is_journal_event(name), "journal must observe {name}");
            } else {
                assert!(!is_journal_event(name), "journal must not observe {name}");
            }
        }
    }

    #[test]
    fn journal_only_events_stay_out_of_the_canonical_vocabulary() {
        for name in JOURNAL_ONLY_EVENTS {
            assert!(!is_canonical_event(name), "{name} is internal and must not be canonical");
            assert!(is_journal_event(name));
        }
    }
}
