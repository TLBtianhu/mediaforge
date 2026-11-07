"""Core schema-2 job storage shared by the desktop and operation kernel."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

from core.worker_runtime_lock import runtime_lock

SCHEMA_VERSION = 2
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
STATUSES = TERMINAL_STATES | {"queued", "running"}
COMMON_FIELDS = {
    "schemaVersion", "jobId", "command", "source", "status", "fraction", "message", "error", "result",
    "createdAt", "updatedAt", "startedAt", "finishedAt", "owner", "runtime", "logs",
}
OPTIONAL_FIELDS = {"payload", "payloadRef", "audio", "presentation", "cancelTaskId"}


class JobStoreError(ValueError):
    """An existing record is invalid; callers must preserve it and surface this error."""


def valid_job_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    device = value.split(".", 1)[0].upper()
    return (
        value.casefold() != "index" and device not in {"CON", "PRN", "AUX", "NUL"}
        and not re.fullmatch(r"(?:COM|LPT)[1-9]", device)
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value))
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise JobStoreError(message)


def _number(value: Any) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def validate_record(record: Any, job_id: str | None = None) -> dict[str, Any]:
    _require(isinstance(record, dict), "job record must be an object")
    _require(type(record.get("schemaVersion")) is int and record["schemaVersion"] == SCHEMA_VERSION,
             "unsupported or missing job schemaVersion; expected 2")
    _require(COMMON_FIELDS <= record.keys(), f"missing job fields: {sorted(COMMON_FIELDS - record.keys())}")
    _require(not (record.keys() - COMMON_FIELDS - OPTIONAL_FIELDS), "unknown job fields")
    _require(valid_job_id(record["jobId"]), "invalid job id")
    _require(job_id is None or record["jobId"] == job_id, "jobId does not match its filename")
    _require(isinstance(record["command"], str) and bool(record["command"].strip()), "invalid job command")
    _require(isinstance(record["source"], str) and record["source"] in {"manual", "agent"}, "invalid job source")
    _require(isinstance(record["status"], str) and record["status"] in STATUSES, "invalid job status")
    _require(_number(record["fraction"]) and 0 <= record["fraction"] <= 1, "invalid job fraction")
    _require(isinstance(record["message"], str), "invalid job message")
    _require(record["error"] is None or isinstance(record["error"], str), "invalid job error")
    _require(record["result"] is None or isinstance(record["result"], dict), "invalid job result")
    _require(_number(record["createdAt"]) and record["createdAt"] > 0, "invalid createdAt")
    _require(_number(record["updatedAt"]) and record["updatedAt"] > 0, "invalid updatedAt")
    for key in ("startedAt", "finishedAt"):
        _require(record[key] is None or (_number(record[key]) and record[key] > 0), f"invalid {key}")
    _require(record["status"] not in TERMINAL_STATES or record["finishedAt"] is not None,
             "terminal job requires finishedAt")
    owner = record["owner"]
    if owner is not None:
        _require(isinstance(owner, dict), "invalid job owner")
        _require(isinstance(owner.get("kind"), str) and owner["kind"] in {"operation-kernel", "tauri-worker"}, "invalid owner kind")
        _require(type(owner.get("pid")) is int and owner["pid"] > 0, "invalid owner pid")
        _require(not (owner.keys() - {"kind", "pid", "kernelId", "workerPid"}), "unknown owner fields")
        if owner["kind"] == "operation-kernel":
            _require(isinstance(owner.get("kernelId"), str) and bool(owner["kernelId"]), "missing kernel owner id")
        if "workerPid" in owner:
            _require(type(owner["workerPid"]) is int and owner["workerPid"] > 0, "invalid worker pid")
    _require(record["status"] != "running" or owner is not None, "running job requires an owner")
    runtime = record["runtime"]
    _require(isinstance(runtime, dict) and set(runtime) == {"stage", "progress", "nodes", "order"}, "invalid runtime fields")
    _require(runtime["stage"] is None or isinstance(runtime["stage"], str), "invalid runtime stage")
    _require(runtime["progress"] is None or isinstance(runtime["progress"], dict), "invalid runtime progress")
    _require(isinstance(runtime["nodes"], dict), "invalid runtime nodes")
    _require(isinstance(runtime["order"], list) and all(isinstance(item, str) for item in runtime["order"]), "invalid runtime order")
    _require(isinstance(record["logs"], list) and all(isinstance(item, dict) for item in record["logs"]), "invalid job logs")
    if "cancelTaskId" in record:
        _require(valid_job_id(record["cancelTaskId"]), "invalid cancelTaskId")
    if "presentation" in record:
        presentation = record["presentation"]
        _require(isinstance(presentation, dict) and not (presentation.keys() - {"workflowId", "workbench", "sourceUpdatedAt"}),
                 "invalid presentation fields")
        if "workflowId" in presentation:
            _require(isinstance(presentation["workflowId"], str) and bool(presentation["workflowId"].strip()), "invalid presentation workflowId")
        if "sourceUpdatedAt" in presentation:
            _require(_number(presentation["sourceUpdatedAt"]), "invalid presentation sourceUpdatedAt")
        if "workbench" in presentation:
            workbench = presentation["workbench"]
            _require(isinstance(workbench, dict) and set(workbench) == {"nativeGraph"}
                     and type(workbench["nativeGraph"]) is bool, "invalid workbench presentation")
    audio = record.get("audio")
    if audio is None:
        _require("audio" not in record and "payloadRef" not in record, "generic job cannot reference an audio payload")
        _require(isinstance(record.get("payload"), dict), "job requires a payload object")
        _require("presentation" not in record, "generic job cannot contain audio presentation")
    else:
        _require(record["source"] == "manual" and record["command"] in {"infer", "infer_workflow"}, "invalid audio command/source")
        _require(isinstance(audio, dict), "invalid audio record")
        required = {"groupId", "primaryJobId", "index", "request"}
        _require(required <= audio.keys() and not (audio.keys() - required - {"memberJobIds"}), "invalid audio fields")
        _require(valid_job_id(audio["groupId"]) and valid_job_id(audio["primaryJobId"]), "invalid audio group id")
        _require(type(audio["index"]) is int and audio["index"] >= 0, "invalid audio index")
        request = audio["request"]
        _require(isinstance(request, dict) and {"input", "output"} <= request.keys(), "invalid audio request")
        _require(not (request.keys() - {"input", "output", "inputIndex", "inputs", "workbenchOutputs"}), "unknown audio request fields")
        _require((request["input"] is None or (isinstance(request["input"], str) and bool(request["input"].strip())))
                 and isinstance(request["output"], str) and bool(request["output"].strip()), "invalid audio input/output")
        if "inputIndex" in request:
            _require(type(request["inputIndex"]) is int and request["inputIndex"] >= 1, "invalid inputIndex")
        for key in ("inputs", "workbenchOutputs"):
            if key in request:
                _require(isinstance(request[key], dict)
                         and all(isinstance(name, str) and isinstance(value, str) for name, value in request[key].items()), f"invalid audio {key}")
        primary = audio["primaryJobId"] == record["jobId"]
        if primary:
            members = audio.get("memberJobIds")
            _require(isinstance(record.get("payload"), dict) and "payloadRef" not in record, "audio primary requires common payload")
            _require(isinstance(members, list) and bool(members) and all(valid_job_id(item) for item in members)
                     and len(members) == len(set(members)) and members[0] == record["jobId"], "invalid audio membership")
            _require(audio["index"] == 0, "audio primary must have index 0")
            _require(not ({"taskId", "requestId", "tasks", "input", "inputs", "workbenchOutputs"} & record["payload"].keys()), "primary payload contains leaf requests")
            if "inferenceParamsVersion" in record["payload"]:
                version = record["payload"]["inferenceParamsVersion"]
                _require(type(version) is int and version == 3, "unsupported inferenceParamsVersion; expected 3")
            _require(isinstance(record["payload"].get("output"), str) and bool(record["payload"]["output"].strip()), "primary payload requires batch output")
            if record["command"] == "infer":
                _require(isinstance(record["payload"].get("model"), str) and bool(record["payload"]["model"].strip()), "audio primary requires model")
            else:
                _require(isinstance(record["payload"].get("workflow"), dict)
                         and isinstance(record["payload"].get("workflowName"), str) and bool(record["payload"]["workflowName"].strip()),
                         "audio primary requires workflow and workflowName")
        else:
            _require(record.get("payloadRef") == audio["primaryJobId"] and "payload" not in record
                     and "memberJobIds" not in audio and "presentation" not in record, "invalid audio child payload reference")
            _require(audio["index"] > 0, "audio child must have a positive index")
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise JobStoreError(f"job is not finite JSON: {error}") from error
    return record


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_job(path: Path) -> dict[str, Any] | None:
    try:
        _require(not path.is_symlink(), "job file cannot be a symlink")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(JobStoreError(f"invalid JSON number: {value}")))
        return validate_record(value, path.stem)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as error:
        raise JobStoreError(f"Invalid job file {path}: {error}") from error


def catalog_lock(jobs_root: Path):
    return runtime_lock(jobs_root / ".catalog.lock", timeout=5)


def read_jobs_unlocked(jobs_root: Path) -> dict[str, dict[str, Any]]:
    _require(not any(jobs_root.glob("*.removing")), "incomplete job removal transaction; files preserved")
    records = {}
    for path in sorted(jobs_root.glob("*.json")):
        value = read_job(path)
        if value is not None:
            records[value["jobId"]] = value
    for value in records.values():
        if "audio" in value:
            _audio_primary(value, records)
    return records


def read_jobs(jobs_root: Path) -> dict[str, dict[str, Any]]:
    with catalog_lock(jobs_root):
        return read_jobs_unlocked(jobs_root)


def _audio_primary(record: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    audio = record["audio"]
    primary = records.get(audio["primaryJobId"])
    _require(primary is not None and isinstance(primary.get("audio"), dict), "audio payload primary is missing")
    primary_audio = primary["audio"]
    _require(primary_audio["primaryJobId"] == primary["jobId"] and primary_audio["groupId"] == audio["groupId"]
             and primary["command"] == record["command"], "audio reference crosses groups")
    members = primary_audio["memberJobIds"]
    _require(audio["index"] < len(members) and members[audio["index"]] == record["jobId"], "audio membership/index mismatch")
    for index, job_id in enumerate(members):
        member = records.get(job_id)
        _require(member is not None and isinstance(member.get("audio"), dict), "audio member job is missing")
        _require(member["audio"]["primaryJobId"] == primary["jobId"] and member["audio"]["groupId"] == audio["groupId"]
                 and member["audio"]["index"] == index, "invalid audio group membership")
    return primary


def resolve_payload(record: dict[str, Any], jobs_root: Path) -> dict[str, Any]:
    validate_record(record)
    records = read_jobs(jobs_root)
    current = records.get(record["jobId"])
    _require(current is not None, "job no longer exists")
    record = current
    if "audio" not in record:
        return copy.deepcopy(record["payload"])
    primary = _audio_primary(record, records)
    return {**copy.deepcopy(primary["payload"]),
            "taskId": record["jobId"], "tasks": [{**copy.deepcopy(record["audio"]["request"]), "taskId": record["jobId"]}]}


def write_lock(jobs_root: Path, job_id: str):
    _require(valid_job_id(job_id), "invalid job id")
    return runtime_lock(jobs_root / ".writes" / f"{job_id}.lock", timeout=2)
