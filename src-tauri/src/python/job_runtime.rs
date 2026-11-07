//! Lossless worker progress and structured logs, independent of the UI lifetime.
use super::protocol::WorkerEnvelope;
use serde_json::{json, Value};

pub(super) fn log(snapshot: &mut Value, event: &WorkerEnvelope) {
    let structured = event
        .payload
        .get("record")
        .or_else(|| event.payload.get("logRecord"))
        .filter(|v| v.is_object());
    let mut record = structured.unwrap_or(&event.payload).clone();
    if !record.is_object() {
        return;
    }
    if structured.is_none() && event.event_type == "error" {
        record.as_object_mut().unwrap().retain(|key, _| {
            [
                "message",
                "code",
                "nodeId",
                "recoverable",
                "detail",
                "context",
                "errorType",
            ]
            .contains(&key.as_str())
        });
    }
    if record.get("schemaVersion").is_none() {
        record["schemaVersion"] = json!(1);
    }
    if record.get("id").is_none() {
        record["id"] = json!(format!(
            "{}-{}",
            snapshot["jobId"].as_str().unwrap(),
            super::task_journal::now().to_bits()
        ));
    }
    if record.get("level").is_none() {
        record["level"] = json!(if event.event_type == "error" {
            "error"
        } else {
            "info"
        });
    }
    if record.get("taskId").is_none() {
        record["taskId"] = snapshot["jobId"].clone();
    }
    if record.get("timestamp").is_none() {
        record["timestamp"] = event
            .timestamp
            .as_ref()
            .map(|v| json!(v))
            .unwrap_or_else(|| json!(chrono::Utc::now().to_rfc3339()));
    }
    if record.get("event").is_none() {
        record["event"] = json!(event.event_type);
    }
    let logs = snapshot["logs"].as_array_mut().unwrap();
    if let Some(id) = record.get("id") {
        if logs.iter().any(|row| row.get("id") == Some(id)) {
            return;
        }
    }
    logs.push(record);
    if logs.len() > 1200 {
        logs.drain(..logs.len() - 1200);
    }
}

pub(super) fn observe(snapshot: &mut Value, event: &WorkerEnvelope) {
    let payload = &event.payload;
    if let Some(stage) = payload.get("stage").filter(|v| v.is_string()) {
        snapshot["runtime"]["stage"] = stage.clone();
    }
    if matches!(
        event.event_type.as_str(),
        "task.progress" | "workflow_execution" | "task.stage"
    ) {
        snapshot["runtime"]["progress"] = payload.clone();
    }
    if event.event_type == "task.started" {
        snapshot["runtime"]["stage"] = json!("preparing");
    }
    if event.event_type != "workflow_execution" || payload["version"] != 1 {
        return;
    }
    let Some(kind) = payload["event"].as_str() else {
        return;
    };
    if matches!(
        kind,
        "execution_started" | "execution_completed" | "execution_failed" | "execution_cancelled"
    ) || kind.starts_with("node_")
    {
        snapshot["runtime"]["stage"] = json!("separating");
    }
    if !kind.starts_with("node_") {
        return;
    }
    let Some(id) = payload["nodeId"]
        .as_str()
        .filter(|id| !id.trim().is_empty())
    else {
        return;
    };
    let status = match kind {
        "node_started" | "node_progress" => "running",
        "node_completed" => "done",
        "node_failed" => "failed",
        "node_cancelled" => "cancelled",
        "node_skipped" => "skipped",
        _ => return,
    };
    let fraction = payload["nodeProgress"]
        .as_f64()
        .filter(|v| v.is_finite())
        .unwrap_or(if status == "done" { 1.0 } else { 0.0 })
        .clamp(0.0, 1.0);
    if snapshot["runtime"]["nodes"].get(id).is_none() {
        snapshot["runtime"]["order"]
            .as_array_mut()
            .unwrap()
            .push(json!(id));
    }
    snapshot["runtime"]["nodes"][id] = json!({"status": status, "progress": fraction * 100.0, "message": payload.get("message").and_then(Value::as_str).unwrap_or("")});
}

pub(super) fn finish(snapshot: &mut Value, status: &str) {
    snapshot["runtime"]["stage"] = json!(status);
    if let Some(nodes) = snapshot["runtime"]["nodes"].as_object_mut() {
        for node in nodes.values_mut() {
            if node["status"] == "running"
                && matches!(status, "failed" | "cancelled" | "interrupted")
            {
                node["status"] = json!(if status == "cancelled" {
                    "cancelled"
                } else {
                    "failed"
                });
            }
        }
    }
}
