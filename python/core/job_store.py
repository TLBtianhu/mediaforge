"""Core schema-2 job storage shared by the desktop and operation kernel."""
from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
import json
import math
import os
import re
import stat
import sys
import threading
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
_REMOVAL_PENDING = ".removal-pending"
_REMOVAL_COMMITTED = ".removal-committed"
_FLAT_REMOVAL = re.compile(r"\.(.+)\.([0-9]+-[0-9]+|[0-9a-f]{32})\.removing")


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


def read_job(path: Path, *, job_id: str | None = None) -> dict[str, Any] | None:
    try:
        _require(not path.is_symlink(), "job file cannot be a symlink")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object,
                           parse_constant=lambda value: (_ for _ in ()).throw(JobStoreError(f"invalid JSON number: {value}")))
        return validate_record(value, path.stem if job_id is None else job_id)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as error:
        raise JobStoreError(f"Invalid job file {path}: {error}") from error


def catalog_lock(jobs_root: Path):
    return runtime_lock(jobs_root / ".catalog.lock", timeout=5)


def owner_reap_path(jobs_root: Path, job_id: str) -> Path:
    _require(valid_job_id(job_id), "invalid owner lease job id")
    return jobs_root / ".owners" / "reaping" / f"{job_id}.lock"


@contextmanager
def owner_lock(jobs_root: Path, job_id: str):
    """An exited parent is still an active owner while its worker tree is being reaped."""
    fence = owner_reap_path(jobs_root, job_id)
    owner = jobs_root / ".owners" / f"{job_id}.lock"
    _plain_removal_path(owner.parent, directory=True)
    _plain_removal_path(fence.parent, directory=True)
    _plain_removal_path(owner)
    _plain_removal_path(fence)
    with runtime_lock(owner, timeout=0):
        with runtime_lock(fence, timeout=0):
            pass
        yield


# Job files are immutable between atomic replacements, so an unchanged file
# stamp identifies the same validated record. Nothing persistent is created and
# the map only holds revisions this process has actually read.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, int, int, int], dict[str, Any]]] = {}
_CACHE_LIMIT = 2048


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    return (metadata.st_ino, metadata.st_mtime_ns, metadata.st_size, metadata.st_ctime_ns)


def cached_job(jobs_root: Path, path: Path) -> dict[str, Any] | None:
    """Return the validated record for `path`, re-reading only changed files.

    The fast path returns a **shallow copy** (the top-level dict is per-caller,
    nested containers are shared with the cache). Editing a record in place is
    therefore unsupported: use `read_jobs_copy()` when a caller will mutate one.
    The shallow copy is what stops the common accident (`record["status"] = ...`)
    from poisoning every later read, without paying for a deep copy on each poll.
    """
    stamp = _file_stamp(path)
    key = str(path)
    if stamp is not None:
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
        if entry is not None and entry[0] == stamp:
            return dict(entry[1])
    # A cache miss means the file was just written; coordinate the open with
    # the writer's atomic replacement exactly like the uncached reader did.
    with write_lock(jobs_root, path.stem):
        value = read_job(path)
        if value is not None:
            stamp = _file_stamp(path) or (0, 0, 0, 0)
    if value is None:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    with _CACHE_LOCK:
        _CACHE[key] = (stamp or (0, 0, 0, 0), value)
        # Multiple profiles/tests can be read by one process; keep the map bounded.
        while len(_CACHE) > _CACHE_LIMIT:
            oldest = next(iter(_CACHE))
            if oldest == key:
                break
            _CACHE.pop(oldest, None)
    return dict(value)


def forget_cached_jobs(jobs_root: Path, present: set[str]) -> None:
    """Drop cache entries for files this root no longer has."""
    prefix = str(jobs_root) + os.sep
    with _CACHE_LOCK:
        for key in [key for key in _CACHE if key.startswith(prefix) and key not in present]:
            _CACHE.pop(key, None)


def _plain_removal_path(path: Path, *, directory: bool = False) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    is_expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    _require(is_expected_type(metadata.st_mode) and not (getattr(metadata, "st_file_attributes", 0) & 0x400),
             f"invalid job removal path {path}; files preserved")
    return True


def _removal_layout(jobs_root: Path) -> None:
    jobs_root = jobs_root.absolute()
    for directory in (jobs_root, *jobs_root.parents, jobs_root / ".owners", jobs_root / ".owners" / "reaping", jobs_root / ".writes"):
        _plain_removal_path(directory, directory=True)
    _plain_removal_path(jobs_root / ".catalog.lock")


def _removal_entries(
    jobs_root: Path, residues: list[Path],
) -> tuple[Path | None, dict[str, tuple[Path, str | None]]]:
    _removal_layout(jobs_root)
    directory = None
    if len(residues) == 1 and residues[0].name in {_REMOVAL_PENDING, _REMOVAL_COMMITTED}:
        directory = residues[0]
        _require(_plain_removal_path(directory, directory=True),
                 f"missing job removal directory {directory}; files preserved")
        paths = sorted(directory.iterdir())
    else:
        _require(all(path.name.endswith(".removing") for path in residues),
                 "conflicting job removal transactions; files preserved")
        paths = residues
    entries = {}
    for path in paths:
        if directory is not None:
            _require(path.suffix == ".json", f"unknown job removal entry {path}; files preserved")
            job_id, nonce = path.stem, None
        else:
            match = _FLAT_REMOVAL.fullmatch(path.name)
            _require(match is not None, f"incomplete job removal transaction {path}; files preserved")
            job_id, nonce = match.groups()
            if "-" in nonce:
                pid, bits = nonce.split("-")
                _require(int(pid) <= 0xffffffff and int(bits) <= 0xffffffffffffffff,
                         f"invalid job removal nonce in {path}; files preserved")
        _require(valid_job_id(job_id) and job_id not in entries,
                 f"invalid or duplicate staged job id {job_id}; files preserved")
        _require(_plain_removal_path(path), f"missing staged job {path}; files preserved")
        _require(not _plain_removal_path(jobs_root / f"{job_id}.json"),
                 f"staged job {job_id} collides with a live record; files preserved")
        entries[job_id] = (path, nonce)
    _require(len({os.path.normcase(job_id) for job_id in entries}) == len(entries),
             "duplicate staged job paths; files preserved")
    return directory, entries


def _legacy_removed_audio(
    records: dict[str, dict[str, Any]], staged: dict[str, dict[str, Any]],
    entries: dict[str, tuple[Path, str | None]],
) -> set[str]:
    """Identify flat transactions where the former writer already unlinked part of an audio batch."""
    combined = {**records, **staged}
    by_primary: dict[str, dict[str, dict[str, Any]]] = {}
    for job_id, row in staged.items():
        if "audio" in row:
            by_primary.setdefault(row["audio"]["primaryJobId"], {})[job_id] = row
    removed = set()
    for primary_id, members in by_primary.items():
        primary = combined.get(primary_id)
        expected = primary.get("audio", {}).get("memberJobIds") if primary is not None else None
        _require(primary is None or expected is not None, "invalid staged audio primary; files preserved")
        if expected is not None and all(job_id in combined for job_id in expected):
            continue
        # Flat files lack a commit marker. Complete removal only if an expected
        # member is already gone and the survivors all belong to one Rust delete.
        group_ids = {row["audio"]["groupId"] for row in members.values()}
        commands = {row["command"] for row in members.values()}
        indices = {row["audio"]["index"] for row in members.values()}
        nonces = {entries[job_id][1] for job_id in members}
        nonce = next(iter(nonces))
        _require(len(group_ids) == len(commands) == len(nonces) == 1 and len(indices) == len(members)
                 and nonce is not None and "-" in nonce,
                 "ambiguous incomplete audio removal; files preserved")
        _require(not any(job_id not in members
                         and (row.get("audio", {}).get("primaryJobId") == primary_id
                              or row.get("audio", {}).get("groupId") in group_ids)
                         for job_id, row in combined.items()),
                 "incomplete audio removal has another group member; files preserved")
        # The previous Rust writer unlinked the transaction in sorted job-ID order.
        first_survivor = min(job_id for job_id, (_, staged_nonce) in entries.items() if staged_nonce == nonce)
        missing = (primary_id,) if expected is None else (job_id for job_id in expected if job_id not in combined)
        _require(all(job_id < first_survivor for job_id in missing),
                 "flat removal does not match the original cleanup order; files preserved")
        if expected is not None:
            _require(all(job_id in members for job_id in expected if job_id in combined),
                     "staged audio membership does not match the primary; files preserved")
            for job_id, row in members.items():
                index = row["audio"]["index"]
                _require(index < len(expected) and expected[index] == job_id,
                         "staged audio order does not match the primary; files preserved")
        removed.update(members)
    return removed


def _validate_catalog(records: dict[str, dict[str, Any]]) -> None:
    for value in records.values():
        if "audio" in value:
            primary = _audio_primary(value, records, validate_members=False)
            if primary["jobId"] == value["jobId"]:
                _audio_primary(value, records)


def _recover_removal(
    jobs_root: Path, records: dict[str, dict[str, Any]], directory: Path | None,
    entries: dict[str, tuple[Path, str | None]],
) -> dict[str, dict[str, Any]]:
    with ExitStack() as locks:
        for job_id in sorted(entries):
            locks.enter_context(owner_lock(jobs_root, job_id))
        for job_id in sorted(entries):
            _plain_removal_path(jobs_root / ".writes" / f"{job_id}.lock")
            locks.enter_context(write_lock(jobs_root, job_id))
        staged = {}
        for job_id, (path, _) in entries.items():
            _require(_plain_removal_path(path), f"missing staged job {path}; files preserved")
            row = read_job(path, job_id=job_id)
            _require(row is not None and row["status"] in TERMINAL_STATES,
                     f"missing or active staged job {job_id}; files preserved")
            staged[job_id] = row
        if directory is None:
            removed = _legacy_removed_audio(records, staged, entries)
        else:
            removed = set(staged) if directory.name == _REMOVAL_COMMITTED else set()
        restored = {job_id: row for job_id, row in staged.items() if job_id not in removed}
        recovered = {**records, **restored}
        _validate_catalog(recovered)
        for job_id in restored:
            original = jobs_root / f"{job_id}.json"
            _require(not _plain_removal_path(original), f"job {job_id} appeared during recovery; files preserved")
            entries[job_id][0].rename(original)
        for job_id in sorted(removed):
            entries[job_id][0].unlink()
        if directory is not None:
            directory.rmdir()
        return recovered


def read_jobs_unlocked(jobs_root: Path) -> dict[str, dict[str, Any]]:
    live_paths, residues = [], []
    for path in sorted(jobs_root.glob("*")):
        name = os.path.normcase(path.name)
        if name in {_REMOVAL_PENDING, _REMOVAL_COMMITTED} or name.endswith(".removing"):
            residues.append(path)
        elif name.endswith(".json"):
            live_paths.append(path)
    if residues:
        directory, entries = _removal_entries(jobs_root, residues)
    records = {}
    present: set[str] = set()
    for path in live_paths:
        if not valid_job_id(path.stem):
            # `index.json` is the legacy task-history index, never a job record.
            if path.stem.casefold() == "index":
                continue
            raise JobStoreError(f"Invalid job file {path}: invalid job id")
        # Unchanged files need no open, so a poll does not take a lease per job.
        present.add(str(path))
        if residues:
            _require(_plain_removal_path(path), f"missing catalog job {path}; files preserved")
            _plain_removal_path(jobs_root / ".writes" / f"{path.stem}.lock")
            with write_lock(jobs_root, path.stem):
                value = read_job(path)
            _require(value is not None, f"missing catalog job {path}; files preserved")
        else:
            value = cached_job(jobs_root, path)
        if value is not None:
            records[value["jobId"]] = value
    forget_cached_jobs(jobs_root, present)
    if residues:
        return _recover_removal(jobs_root, records, directory, entries)
    _validate_catalog(records)
    return records


def remove_jobs_unlocked(jobs_root: Path, job_ids: list[str]) -> bool:
    """Commit removal under the caller's leases. Return whether cleanup finished."""
    _require(bool(job_ids) and all(valid_job_id(job_id) for job_id in job_ids)
             and len({os.path.normcase(job_id) for job_id in job_ids}) == len(job_ids),
             "invalid job removal ids")
    _removal_layout(jobs_root)
    pending = jobs_root / _REMOVAL_PENDING
    committed = jobs_root / _REMOVAL_COMMITTED
    _require(not _plain_removal_path(committed, directory=True), "job removal cleanup is pending")
    originals = [jobs_root / f"{job_id}.json" for job_id in job_ids]
    for path in originals:
        _require(_plain_removal_path(path), f"job to remove is missing: {path}")
    pending.mkdir()
    moved = []
    try:
        for original in originals:
            original.rename(pending / original.name)
            moved.append(original)
        pending.rename(committed)
    except OSError:
        for original in reversed(moved):
            _require(not _plain_removal_path(original), f"job appeared during rollback: {original}; files preserved")
            (pending / original.name).rename(original)
        pending.rmdir()
        raise
    try:
        for original in originals:
            (committed / original.name).unlink()
        committed.rmdir()
    except OSError as error:
        print(f"[journal] committed job removal cleanup deferred: {error}", file=sys.stderr)
        return False
    return True


def read_jobs(jobs_root: Path) -> dict[str, dict[str, Any]]:
    with catalog_lock(jobs_root):
        return read_jobs_unlocked(jobs_root)


def read_jobs_copy(jobs_root: Path) -> dict[str, dict[str, Any]]:
    """Independent snapshot for a caller that will mutate records.

    `read_jobs()` is optimized for readers: it reuses cached nested containers
    and only copies each record's top level. Any code that edits a record in
    place must go through this deep copy instead, or the shared cache would be
    modified without a corresponding file write.
    """
    with catalog_lock(jobs_root):
        return copy.deepcopy(read_jobs_unlocked(jobs_root))


def _audio_primary(
    record: dict[str, Any], records: dict[str, dict[str, Any]], *, validate_members: bool = True,
) -> dict[str, Any]:
    audio = record["audio"]
    primary = records.get(audio["primaryJobId"])
    _require(primary is not None and isinstance(primary.get("audio"), dict), "audio payload primary is missing")
    primary_audio = primary["audio"]
    _require(primary_audio["primaryJobId"] == primary["jobId"] and primary_audio["groupId"] == audio["groupId"]
             and primary["command"] == record["command"], "audio reference crosses groups")
    members = primary_audio["memberJobIds"]
    _require(audio["index"] < len(members) and members[audio["index"]] == record["jobId"], "audio membership/index mismatch")
    if validate_members:
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
