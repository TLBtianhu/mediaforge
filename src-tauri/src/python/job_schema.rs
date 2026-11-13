//! The shared job-file contract; readers never repair or rewrite unknown records.
use crate::error::{AppError, AppResult};
use serde_json::{json, Value};

pub(super) fn parse(text: &str) -> AppResult<Value> {
    use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
    struct Strict(Value);
    impl<'de> Deserialize<'de> for Strict {
        fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
            struct JsonVisitor;
            impl<'de> Visitor<'de> for JsonVisitor {
                type Value = Strict;
                fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                    formatter.write_str("JSON without duplicate keys")
                }
                fn visit_bool<E: serde::de::Error>(self, value: bool) -> Result<Strict, E> {
                    Ok(Strict(json!(value)))
                }
                fn visit_i64<E: serde::de::Error>(self, value: i64) -> Result<Strict, E> {
                    Ok(Strict(json!(value)))
                }
                fn visit_u64<E: serde::de::Error>(self, value: u64) -> Result<Strict, E> {
                    Ok(Strict(json!(value)))
                }
                fn visit_f64<E: serde::de::Error>(self, value: f64) -> Result<Strict, E> {
                    serde_json::Number::from_f64(value)
                        .map(|n| Strict(Value::Number(n)))
                        .ok_or_else(|| E::custom("nonfinite JSON number"))
                }
                fn visit_str<E: serde::de::Error>(self, value: &str) -> Result<Strict, E> {
                    Ok(Strict(json!(value)))
                }
                fn visit_string<E: serde::de::Error>(self, value: String) -> Result<Strict, E> {
                    Ok(Strict(Value::String(value)))
                }
                fn visit_unit<E: serde::de::Error>(self) -> Result<Strict, E> {
                    Ok(Strict(Value::Null))
                }
                fn visit_none<E: serde::de::Error>(self) -> Result<Strict, E> {
                    Ok(Strict(Value::Null))
                }
                fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Strict, A::Error> {
                    let mut values = Vec::new();
                    while let Some(value) = seq.next_element::<Strict>()? {
                        values.push(value.0);
                    }
                    Ok(Strict(Value::Array(values)))
                }
                fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Strict, A::Error> {
                    let mut values = serde_json::Map::new();
                    while let Some((key, value)) = map.next_entry::<String, Strict>()? {
                        if values.insert(key, value.0).is_some() {
                            return Err(serde::de::Error::custom("duplicate JSON key"));
                        }
                    }
                    Ok(Strict(Value::Object(values)))
                }
            }
            deserializer.deserialize_any(JsonVisitor)
        }
    }
    Ok(serde_json::from_str::<Strict>(text)?.0)
}

pub(crate) fn empty_runtime() -> Value {
    json!({"stage": null, "progress": null, "nodes": {}, "order": []})
}

pub(crate) fn validate(value: &Value) -> AppResult<()> {
    let bad =
        || AppError::Worker("unsupported or invalid job schema; original file preserved".into());
    let object = value.as_object().ok_or_else(bad)?;
    const FIELDS: &[&str] = &[
        "schemaVersion",
        "jobId",
        "command",
        "source",
        "status",
        "fraction",
        "message",
        "error",
        "result",
        "createdAt",
        "updatedAt",
        "startedAt",
        "finishedAt",
        "owner",
        "runtime",
        "logs",
        "cancelTaskId",
        "payload",
        "payloadRef",
        "audio",
        "presentation",
    ];
    if object.keys().any(|key| !FIELDS.contains(&key.as_str())) {
        return Err(bad());
    }
    if value.get("schemaVersion").and_then(Value::as_u64) != Some(2) {
        return Err(bad());
    }
    for key in ["jobId", "command", "source", "status", "message"] {
        if !object.get(key).is_some_and(Value::is_string) {
            return Err(bad());
        }
    }
    if value["command"].as_str().unwrap().trim().is_empty() {
        return Err(bad());
    }
    if !super::task_journal::valid_id(value["jobId"].as_str().unwrap())
        || !matches!(value["source"].as_str(), Some("manual" | "agent"))
        || !matches!(
            value["status"].as_str(),
            Some("queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted")
        )
    {
        return Err(bad());
    }
    for key in ["createdAt", "updatedAt", "fraction"] {
        if !object
            .get(key)
            .and_then(Value::as_f64)
            .is_some_and(f64::is_finite)
        {
            return Err(bad());
        }
    }
    if !(0.0..=1.0).contains(&value["fraction"].as_f64().unwrap()) {
        return Err(bad());
    }
    if value["createdAt"].as_f64().unwrap() <= 0.0 || value["updatedAt"].as_f64().unwrap() <= 0.0 {
        return Err(bad());
    }
    for key in ["startedAt", "finishedAt"] {
        if !object
            .get(key)
            .is_some_and(|v| v.is_null() || v.as_f64().is_some_and(|n| n.is_finite() && n > 0.0))
        {
            return Err(bad());
        }
    }
    if !object
        .get("error")
        .is_some_and(|v| v.is_null() || v.is_string())
        || !object.contains_key("result")
        || !object
            .get("result")
            .is_some_and(|v| v.is_null() || v.is_object())
        || !object
            .get("owner")
            .is_some_and(|v| v.is_null() || v.is_object())
        || !object
            .get("logs")
            .and_then(Value::as_array)
            .is_some_and(|rows| rows.iter().all(Value::is_object))
    {
        return Err(bad());
    }
    if super::task_journal::terminal(value) && value["finishedAt"].is_null() {
        return Err(bad());
    }
    if value["status"] == "running" && value["owner"].is_null() {
        return Err(bad());
    }
    if let Some(owner) = value["owner"].as_object() {
        if !matches!(
            owner.get("kind").and_then(Value::as_str),
            Some("tauri-worker" | "operation-kernel")
        ) || !owner
            .get("pid")
            .and_then(Value::as_u64)
            .is_some_and(|pid| pid > 0)
            || owner
                .keys()
                .any(|key| !["kind", "pid", "kernelId", "workerPid"].contains(&key.as_str()))
            || owner
                .get("workerPid")
                .is_some_and(|v| !v.as_u64().is_some_and(|pid| pid > 0))
            || (owner.get("kind").and_then(Value::as_str) == Some("operation-kernel")
                && !owner
                    .get("kernelId")
                    .and_then(Value::as_str)
                    .is_some_and(|id| !id.is_empty()))
        {
            return Err(bad());
        }
    }
    if object
        .get("cancelTaskId")
        .is_some_and(|v| !v.as_str().is_some_and(super::task_journal::valid_id))
    {
        return Err(bad());
    }
    let runtime = object
        .get("runtime")
        .and_then(Value::as_object)
        .ok_or_else(bad)?;
    if runtime.len() != 4 {
        return Err(bad());
    }
    if !runtime
        .get("stage")
        .is_some_and(|v| v.is_null() || v.is_string())
        || !runtime
            .get("progress")
            .is_some_and(|v| v.is_null() || v.is_object())
        || !runtime.get("nodes").is_some_and(Value::is_object)
        || !runtime
            .get("order")
            .and_then(Value::as_array)
            .is_some_and(|ids| ids.iter().all(Value::is_string))
    {
        return Err(bad());
    }
    if value.get("audio").is_some() {
        super::audio_jobs::validate_record(value)?;
    } else if !value.get("payload").is_some_and(Value::is_object)
        || value.get("payloadRef").is_some()
        || value.get("presentation").is_some()
    {
        return Err(bad());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shared_current_contract_conformance() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/jobs/current-contract.json"
        ))
        .unwrap();
        let rows = fixture["records"].as_array().unwrap();
        for row in rows {
            validate(row).unwrap();
        }
        super::super::audio_jobs::validate_library(rows).unwrap();
        for case in fixture["invalidRecords"].as_array().unwrap() {
            let mut row = rows
                .iter()
                .find(|row| row["jobId"] == case["base"])
                .unwrap()
                .clone();
            if let Some(patch) = case["set"].as_object() {
                for (key, value) in patch {
                    row[key] = value.clone();
                }
            }
            if let Some(keys) = case["remove"].as_array() {
                for key in keys {
                    row.as_object_mut().unwrap().remove(key.as_str().unwrap());
                }
            }
            assert!(validate(&row).is_err(), "accepted {}", case["name"]);
        }
    }
    #[test]
    fn raw_json_rejects_duplicate_nested_keys_and_bom() {
        assert!(parse(r#"{"schemaVersion":1,"schemaVersion":2}"#).is_err());
        assert!(parse(r#"{"payload":{"model":"a","model":"b"}}"#).is_err());
        assert!(parse("\u{feff}{}").is_err());
        assert_eq!(
            parse("{\"value\":[null,true,2,2.5,\"x\"]}").unwrap()["value"]
                .as_array()
                .unwrap()
                .len(),
            5
        );
    }
}
