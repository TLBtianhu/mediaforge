//! Reusable media recipes, deliberately separate from audio graph/simple formats.
use crate::error::{AppError, AppResult};
use serde_json::{json, Value};
use std::collections::HashSet;

const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;
fn invalid() -> AppError { AppError::Worker("invalid media workflow preset".into()) }
fn id_valid(value: &str) -> bool {
    !value.is_empty() && value.len() <= 120 && value.as_bytes()[0].is_ascii_alphanumeric()
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}
fn timestamp(value: &Value) -> AppResult<u64> {
    value.as_u64().filter(|value| *value > 0 && *value <= MAX_SAFE_INTEGER).ok_or_else(invalid)
}
fn name(value: &Value) -> AppResult<&str> {
    value.as_str().map(str::trim).filter(|value| !value.is_empty() && value.encode_utf16().count() <= 120).ok_or_else(invalid)
}

pub(crate) fn definition(value: &Value) -> AppResult<Value> {
    if value.get("version").and_then(Value::as_u64) != Some(1) { return Err(invalid()); }
    let modality = value.get("modality").and_then(Value::as_str).filter(|value| matches!(*value, "image" | "video")).ok_or_else(invalid)?;
    let raw_steps = value.get("steps").and_then(Value::as_array).filter(|steps| !steps.is_empty() && steps.len() <= 64).ok_or_else(invalid)?;
    let mut steps = Vec::new();
    for step in raw_steps {
        let capability = step.get("capability").and_then(Value::as_str).filter(|value| !value.trim().is_empty() && value.encode_utf16().count() <= 128).ok_or_else(invalid)?;
        let bindings = step.get("bindings").and_then(Value::as_object).filter(|value| !value.is_empty() && value.len() <= 32).ok_or_else(invalid)?;
        for (role, model) in bindings {
            if role.trim().is_empty() || role.encode_utf16().count() > 128
                || !model.as_str().is_some_and(|value| !value.trim().is_empty() && value.encode_utf16().count() <= 256) { return Err(invalid()); }
        }
        let params = step.get("params").and_then(Value::as_object).ok_or_else(invalid)?;
        steps.push(json!({"capability": capability.trim(), "bindings": bindings, "params": params}));
    }
    let definition = json!({"version": 1, "modality": modality, "steps": steps});
    if serde_json::to_vec(&definition)?.len() > 256 * 1024 { return Err(invalid()); }
    Ok(definition)
}

pub(crate) fn normalize_state(value: Value) -> AppResult<Value> {
    if value.is_null() { return Ok(json!({"version": 1, "presets": []})); }
    if value.get("version").and_then(Value::as_u64) != Some(1) { return Err(invalid()); }
    let entries = value.get("presets").and_then(Value::as_array).filter(|entries| entries.len() <= 1000).ok_or_else(invalid)?;
    let mut ids = HashSet::new();
    let mut presets = Vec::new();
    for entry in entries {
        let id = entry.get("id").and_then(Value::as_str).filter(|value| id_valid(value)).ok_or_else(invalid)?;
        if !ids.insert(id.to_string()) { return Err(invalid()); }
        let created = timestamp(&entry["createdAt"])?;
        let updated = timestamp(&entry["updatedAt"])?;
        if updated < created { return Err(invalid()); }
        presets.push(json!({"id": id, "name": name(&entry["name"])?, "definition": definition(&entry["definition"])?, "createdAt": created, "updatedAt": updated}));
    }
    presets.sort_by(|a, b| b["updatedAt"].as_u64().cmp(&a["updatedAt"].as_u64()).then_with(|| a["id"].as_str().cmp(&b["id"].as_str())));
    Ok(json!({"version": 1, "presets": presets}))
}

pub(crate) fn apply_mutation(value: Value, mutation: &Value, now: u64) -> AppResult<Value> {
    let mut state = normalize_state(value)?;
    let id = mutation.get("id").and_then(Value::as_str).filter(|value| id_valid(value)).ok_or_else(invalid)?;
    let action = mutation.get("action").and_then(Value::as_str).filter(|value| matches!(*value, "upsert" | "delete")).ok_or_else(invalid)?;
    let entries = state["presets"].as_array_mut().unwrap();
    let index = entries.iter().position(|entry| entry["id"] == id);
    let actual = index.and_then(|index| entries[index]["updatedAt"].as_u64()).unwrap_or(0);
    let expected_value = mutation.get("expectedUpdatedAt").filter(|value| !value.is_null());
    let creating = action == "upsert" && expected_value.is_none();
    let expected = if creating { 0 } else { timestamp(expected_value.ok_or_else(invalid)?)? };
    if if creating { index.is_some() } else { actual != expected } {
        return Ok(json!({"state": state, "conflict": {"id": id, "expectedUpdatedAt": expected, "actualUpdatedAt": actual}}));
    }
    if action == "delete" { entries.remove(index.unwrap()); }
    else {
        if creating && entries.len() >= 1000 { return Err(invalid()); }
        let updated = now.max(actual + 1).max(1);
        if updated > MAX_SAFE_INTEGER { return Err(invalid()); }
        let created = index.map(|index| entries[index]["createdAt"].as_u64().unwrap()).unwrap_or(updated);
        let entry = json!({"id": id, "name": name(&mutation["name"])?, "definition": definition(&mutation["definition"])?, "createdAt": created, "updatedAt": updated});
        if let Some(index) = index { entries[index] = entry; } else { entries.push(entry); }
    }
    Ok(json!({"state": normalize_state(state)?}))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn recipe() -> Value { json!({"version":1,"modality":"image","steps":[{"capability":"upscale","bindings":{"upscaler":"media:real-esrgan-x2"},"params":{"scale":2,"tile":0}}]}) }
    fn create(id: &str) -> Value { json!({"action":"upsert","id":id,"name":"  海报超分  ","definition":recipe()}) }

    #[test]
    fn recipe_keeps_parameters_but_excludes_task_paths_and_ids() {
        let mut input = recipe();
        input["inputs"] = json!(["private.png"]); input["outputDir"] = json!("private-output"); input["requestId"] = json!("worker");
        input["steps"][0]["outputDir"] = json!("other-output");
        let saved = definition(&input).unwrap();
        assert!(saved.get("inputs").is_none()); assert!(saved.get("outputDir").is_none()); assert!(saved.get("requestId").is_none());
        assert!(saved["steps"][0].get("outputDir").is_none());
        assert_eq!(saved["steps"][0]["params"]["tile"], 0);
    }
    #[test]
    fn audio_graphs_and_unsupported_envelopes_are_rejected() {
        for bad in [json!({"nodes":[],"links":[]}), json!({"version":2,"modality":"image","steps":[]}), json!({"version":1,"modality":"audio","steps":[]})] { assert!(definition(&bad).is_err()); }
        let mut invalid_step = recipe(); invalid_step["steps"][0]["bindings"] = json!({}); assert!(definition(&invalid_step).is_err());
        invalid_step = recipe(); invalid_step["steps"][0]["params"] = json!([]); assert!(definition(&invalid_step).is_err());
    }
    #[test]
    fn independent_saves_merge_and_same_name_does_not_overwrite() {
        let first = apply_mutation(Value::Null, &create("one"), 100).unwrap();
        let second = apply_mutation(first["state"].clone(), &create("two"), 101).unwrap();
        assert_eq!(second["state"]["presets"].as_array().unwrap().len(), 2);
        assert_eq!(second["state"]["presets"][0]["name"], "海报超分");
    }
    #[test]
    fn stale_update_delete_and_id_reuse_report_conflicts_without_writing() {
        let state = apply_mutation(Value::Null, &create("one"), 100).unwrap()["state"].clone();
        for mutation in [create("one"), json!({"action":"upsert","id":"one","expectedUpdatedAt":99,"name":"stale","definition":recipe()}), json!({"action":"delete","id":"one","expectedUpdatedAt":99})] {
            let response = apply_mutation(state.clone(), &mutation, 101).unwrap(); assert!(response.get("conflict").is_some()); assert_eq!(response["state"], state);
        }
        assert!(apply_mutation(state, &json!({"action":"delete","id":"one"}), 101).is_err());
    }
    #[test]
    fn update_revision_is_monotonic_even_when_clock_moves_backwards() {
        let state = apply_mutation(Value::Null, &create("one"), 100).unwrap()["state"].clone();
        let mut update = create("one"); update["expectedUpdatedAt"] = json!(100);
        let changed = apply_mutation(state, &update, 99).unwrap()["state"].clone();
        assert_eq!(changed["presets"][0]["createdAt"], 100); assert_eq!(changed["presets"][0]["updatedAt"], 101);
        let removed = apply_mutation(changed, &json!({"action":"delete","id":"one","expectedUpdatedAt":101}), 102).unwrap();
        assert!(removed["state"]["presets"].as_array().unwrap().is_empty());
    }
    #[test]
    fn corrupt_or_newer_state_is_never_silently_reset() {
        for state in [json!({"version":2,"presets":[]}), json!({"version":1,"presets":"bad"}), json!({"version":1,"presets":[{"id":"one"}]})] { assert!(apply_mutation(state, &create("two"), 101).is_err()); }
        for id in ["../escape", "", "a/b"] { assert!(apply_mutation(Value::Null, &create(id), 100).is_err()); }
    }
}
