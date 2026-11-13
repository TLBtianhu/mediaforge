"""Current single-job fixtures; intentionally no migration or schema repair."""
from __future__ import annotations

import json
import time
from pathlib import Path


def job_record(job_id: str, *, status="completed", command="audio_tools", source="agent", **fields):
    now = time.time()
    record = {
        "schemaVersion": 2, "jobId": job_id, "command": command, "source": source, "status": status,
        "fraction": 1.0 if status == "completed" else 0.0, "message": status, "error": None, "result": None,
        "createdAt": now, "updatedAt": now, "startedAt": now if status == "running" else None,
        "finishedAt": now if status in {"completed", "failed", "cancelled", "interrupted"} else None,
        "owner": {"kind": "operation-kernel", "pid": 12345, "kernelId": "fixture"} if status == "running" else None,
        "payload": {}, "runtime": {"stage": None, "progress": None, "nodes": {}, "order": []}, "logs": [],
    }
    record.update(fields)
    return record


def audio_group(primary="audio_1", child="audio_2", *, status="queued", **fields):
    first = job_record(primary, status=status, command="infer", source="manual", **fields)
    first.update(
        payload={"model": "fixture-model", "output": "batch-output", "inferenceParams": {"tile": 128}},
        presentation={},
        audio={"groupId": primary, "primaryJobId": primary, "index": 0, "memberJobIds": [primary, child],
               "request": {"input": "first.wav", "output": "batch-output/first", "inputIndex": 1}},
    )
    second = job_record(child, status=status, command="infer", source="manual", **fields)
    second.pop("payload")
    second.update(payloadRef=primary, audio={"groupId": primary, "primaryJobId": primary, "index": 1,
                  "request": {"input": "second.wav", "output": "batch-output/second", "inputIndex": 2}})
    return first, second


def write_record(jobs: Path, record):
    jobs.mkdir(parents=True, exist_ok=True)
    path = jobs / f"{record['jobId']}.json"
    path.write_text(json.dumps(record, allow_nan=False), encoding="utf-8")
    return path
