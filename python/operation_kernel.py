"""Shared Operation Kernel for MediaForge jobs.

This module owns the job lifecycle, persistence, and the shared
`agent-jobs/index.json` + `agent-workspace.json` surface. It is intentionally
independent of the MCP transport and the UI so both callers can share one
implementation.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "worker.py"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class Job:
    def __init__(self, job_id: str, command: str, payload: dict[str, Any], workdir: Path):
        self.job_id = job_id
        self.command = command
        self.payload = payload
        self.workdir = workdir
        self.status = "queued"
        self.fraction = 0.0
        self.message = "queued"
        self.result: Any = None
        self.error: str | None = None
        self.created_at = time.time()
        self.finished_at: float | None = None
        self.process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "jobId": self.job_id,
                "command": self.command,
                "source": str(self.payload.get("source") or "agent"),
                "status": self.status,
                "fraction": round(float(self.fraction), 4),
                "message": self.message,
                "result": self.result,
                "error": self.error,
                "createdAt": self.created_at,
                "finishedAt": self.finished_at,
                "payload": self.payload,
            }

    def update(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)


class OperationKernel:
    def __init__(self, data_root: Path, python_path: str | None = None):
        self.data_root = data_root
        self.python_path = python_path
        self.jobs_root = data_root / "agent-jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()

    def _persist(self, job: Job) -> None:
        snapshot = job.snapshot()
        with self._persist_lock:
            path = self.jobs_root / f"{job.job_id}.json"
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(_json_text(snapshot), encoding="utf-8")
            temporary.replace(path)
            entries = []
            for candidate in sorted(self.jobs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                if candidate.name == "index.json" or candidate.name.endswith(".json.tmp"):
                    continue
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    entries.append(
                        {
                            "jobId": value.get("jobId"),
                            "command": value.get("command"),
                            "source": value.get("source") or "agent",
                            "status": value.get("status"),
                            "fraction": value.get("fraction"),
                            "message": value.get("message"),
                            "createdAt": value.get("createdAt"),
                            "finishedAt": value.get("finishedAt"),
                            "payload": value.get("payload"),
                            "result": value.get("result"),
                            "error": value.get("error"),
                        }
                    )
            index = {"version": 1, "updatedAt": time.time(), "jobs": entries[:100]}
            index_path = self.jobs_root / "index.json"
            temporary = index_path.with_suffix(".json.tmp")
            temporary.write_text(_json_text(index), encoding="utf-8")
            temporary.replace(index_path)
            workspace = {"version": 1, "updatedAt": index["updatedAt"], "jobs": entries[:100]}
            workspace_path = self.data_root / "agent-workspace.json"
            temporary = workspace_path.with_suffix(".json.tmp")
            temporary.write_text(_json_text(workspace), encoding="utf-8")
            temporary.replace(workspace_path)

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, command: str, payload: dict[str, Any]) -> Job:
        job_id = str(payload.get("requestId") or "").strip() or f"job_{uuid.uuid4().hex[:12]}"
        workdir = self.data_root / "bridge-jobs" / job_id
        workdir.mkdir(parents=True, exist_ok=True)
        job = Job(job_id, command, payload, workdir)
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"mediaforge-{job_id}").start()
        return job

    def _run(self, job: Job) -> None:
        payload_path = job.workdir / "payload.json"
        payload_path.write_text(_json_text(job.payload), encoding="utf-8")
        stdout_path = job.workdir / "events.jsonl"
        stderr_path = job.workdir / "stderr.log"
        job.update(status="running", message="starting worker")
        self._persist(job)
        try:
            with open(stdout_path, "w", encoding="utf-8") as stdout, open(stderr_path, "w", encoding="utf-8") as stderr:
                process = subprocess.Popen(
                    [self.python_path or sys.executable, str(WORKER), job.command, "--payload", str(payload_path)],
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                job.process = process
                assert process.stdout is not None
                threading.Thread(target=self._watch_cancel, args=(job, process), daemon=True).start()
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    stdout.write(line + "\n")
                    stdout.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._consume(job, event)
                code = process.wait()
            job.update(finished_at=time.time())
            if job.status not in {"completed", "failed", "cancelled"}:
                job.update(status="completed" if code == 0 else "failed", message="worker exited")
                if code != 0:
                    job.update(error=f"worker exited with code {code}")
            if job.status == "completed":
                job.update(fraction=1.0)
            self._persist(job)
        except Exception as exc:  # noqa: BLE001
            job.update(status="failed", error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
            self._persist(job)

    def _watch_cancel(self, job: Job, process: subprocess.Popen[str]) -> None:
        cancel_path = job.workdir / "cancel"
        while process.poll() is None:
            if cancel_path.exists():
                job.update(status="cancelled", message="cancelled by user")
                self._persist(job)
                process.terminate()
                return
            time.sleep(0.2)

    def _consume(self, job: Job, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if kind == "audio_tool_progress":
            job.update(
                fraction=float(payload.get("fraction") or 0),
                message=str(payload.get("current") or payload.get("message") or "running"),
            )
        elif kind in {"audio_tool_result", "media_image_result", "media_video_result"}:
            failed = payload.get("failed") if isinstance(payload.get("failed"), list) else []
            if failed:
                first = failed[0] if isinstance(failed[0], dict) else {"error": str(failed[0])}
                job.update(
                    status="failed",
                    result=payload,
                    message="completed with failures",
                    error=str(first.get("error") or first.get("message") or "operation failed"),
                )
            else:
                job.update(status="completed", result=payload, message="completed", fraction=1.0)
        elif kind == "error":
            job.update(status="failed", error=str(payload.get("message") or payload.get("code") or "worker error"))
        self._persist(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job: {job_id}"}
        process = job.process
        if process and process.poll() is None:
            process.terminate()
            job.update(status="cancelled", message="cancelled by client")
            self._persist(job)
            return {"ok": True, "cancelled": True}
        return {"ok": True, "cancelled": job.status == "cancelled"}
