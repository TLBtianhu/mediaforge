"""Shared job lifecycle backed only by agent-jobs/<jobId>.json.

Each owner holds a lifetime lease and atomically writes its job snapshot.
Progress writes are throttled; status changes and terminal payloads are durable
immediately. Readers query job files directly, so restarted MCP clients and
other processes see the same records without secondary indexes or workspaces.
"""
from __future__ import annotations

import json
import copy
from contextlib import ExitStack
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock
from shared.worker_subprocess import hidden_subprocess_kwargs
from core import job_store

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker.py"

TERMINAL_STATES = job_store.TERMINAL_STATES
JOB_FILE_SECONDS = 0.2
CANCEL_POLL_SECONDS = 1.0
# 作业文件保留策略（每个作业一个 json，永不覆盖，所以会一直攒）。
# Startup and queue listing scan these records; retain 30 days / 500 terminal jobs.
RETENTION_DAYS = 30.0
RETENTION_COUNT = 500


def _retention_limits() -> tuple[float, int]:
    """读保留窗口；`MEDIAFORGE_JOB_RETENTION_DAYS=0` 且 `..._COUNT=0` 等于关闭清理。"""
    try:
        days = float(os.environ.get("MEDIAFORGE_JOB_RETENTION_DAYS") or RETENTION_DAYS)
    except ValueError:
        days = RETENTION_DAYS
    try:
        count = int(os.environ.get("MEDIAFORGE_JOB_RETENTION_COUNT") or RETENTION_COUNT)
    except ValueError:
        count = RETENTION_COUNT
    return max(0.0, days), max(0, count)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _atomic_write(path: Path, text: str) -> None:
    # Writers publish a whole job snapshot with one atomic replacement.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_job_id(job_id: str) -> bool:
    return job_store.valid_job_id(job_id)


def _read_job(path: Path) -> dict[str, Any] | None:
    return job_store.read_job(path)


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
        self.updated_at = self.created_at
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.source = "agent"
        self.runtime: dict[str, Any] = {"stage": None, "progress": None, "nodes": {}, "order": []}
        self.logs: list[dict[str, Any]] = []
        self.process: subprocess.Popen[str] | None = None
        self._process_reaped = True
        self._cleanup_pending = False
        self._cleanup_error: str | None = None
        self._cleanup_lock = threading.Lock()
        self._payload_path: Path | None = None
        self.cancel_event = threading.Event()
        self._lock = threading.RLock()
        # 节流用的记账；只在 OperationKernel._persist 的锁内读写
        self.persisted_at = 0.0
        self.persisted_status = ""
        self.owner: dict[str, Any] | None = None
        self._lease: Any = None
        self._stored: dict[str, Any] = {}

    @classmethod
    def from_snapshot(cls, value: dict[str, Any], workdir: Path) -> Job:
        job_store.validate_record(value)
        value = copy.deepcopy(value)
        payload = value.get("payload")
        job = cls(str(value["jobId"]), str(value.get("command") or ""), payload, workdir)
        job._stored = dict(value)
        job.status = value["status"]
        job.fraction = value["fraction"]
        job.message = value["message"]
        job.result = value.get("result")
        job.error = value.get("error")
        job.created_at = value["createdAt"]
        job.updated_at = value["updatedAt"]
        job.started_at = value["startedAt"]
        job.finished_at = value["finishedAt"]
        job.source = value["source"]
        job.owner = value["owner"]
        job.runtime = value["runtime"]
        job.logs = value["logs"]
        return job

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._stored,
                "schemaVersion": job_store.SCHEMA_VERSION,
                "jobId": self.job_id,
                "command": self.command,
                "source": self.source,
                "status": self.status,
                "fraction": round(float(self.fraction), 4),
                "message": self.message,
                "result": self.result,
                "error": self.error,
                "createdAt": self.created_at,
                "updatedAt": self.updated_at,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                **({"payload": self.payload} if "payloadRef" not in self._stored else {}),
                "owner": self.owner,
                "runtime": copy.deepcopy(self.runtime),
                "logs": copy.deepcopy(self.logs),
                "cancelTaskId": self._stored.get("cancelTaskId", self.job_id),
            }

    def adopt_terminal(self, value: dict[str, Any]) -> None:
        """Synchronize a terminal published by the same owner without writing it back."""
        job_store.validate_record(value)
        with self._lock:
            if value["owner"] != self.owner or value["createdAt"] != self.created_at:
                raise job_store.JobStoreError("job ownership or creation changed")
            if value["status"] not in TERMINAL_STATES:
                raise job_store.JobStoreError("expected an authoritative terminal job")
            value = copy.deepcopy(value)
            self._stored = value
            self.command = value["command"]
            self.source = value["source"]
            self.payload = value.get("payload")
            self.status = value["status"]
            self.fraction = value["fraction"]
            self.message = value["message"]
            self.result = value["result"]
            self.error = value["error"]
            self.updated_at = value["updatedAt"]
            self.started_at = value["startedAt"]
            self.finished_at = value["finishedAt"]
            self.runtime = value["runtime"]
            self.logs = value["logs"]
            self.persisted_status = value["status"]
            if self.status == "cancelled":
                self.cancel_event.set()

    def update(self, **fields: Any) -> bool:
        with self._lock:
            if self.status in TERMINAL_STATES:
                return False
            if "created_at" in fields and fields["created_at"] != self.created_at:
                raise job_store.JobStoreError("createdAt is immutable")
            next_status = fields.get("status", self.status)
            if next_status == "running" and self.started_at is None:
                self.started_at = time.time()
            if next_status in TERMINAL_STATES and self.finished_at is None and "finished_at" not in fields:
                self.finished_at = time.time()
            if next_status in TERMINAL_STATES:
                self.runtime["stage"] = next_status
                for node in self.runtime["nodes"].values():
                    if node.get("status") == "running" and next_status in {"failed", "cancelled", "interrupted"}:
                        node["status"] = "cancelled" if next_status == "cancelled" else "failed"
            for key, value in fields.items():
                setattr(self, key, value)
            return True


class OperationKernel:
    def __init__(self, data_root: Path, python_path: str | None = None):
        self.data_root = Path(data_root)
        self.python_path = python_path
        self.jobs_root = self.data_root / "agent-jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._owner_id = uuid.uuid4().hex
        self.recovered = self._recover_from_disk()
        self.pruned = self._prune_history()

    # ── 落盘 ────────────────────────────────────────────────────────────────

    def _recover_from_disk(self) -> int:
        """Validate the full catalog before recovering genuinely abandoned owners."""
        recovered = 0
        with job_store.catalog_lock(self.jobs_root):
            records = job_store.read_jobs_unlocked(self.jobs_root)
            for job_id, value in records.items():
                if value["status"] not in {"queued", "running"} or value["owner"] is None:
                    continue
                lease = runtime_lock(self._lease_path(job_id), timeout=0)
                try:
                    lease.__enter__()
                except (RuntimeLockBusy, OSError):
                    continue  # An inaccessible/busy lease is not evidence of death.
                try:
                    with job_store.write_lock(self.jobs_root, job_id):
                        path = self.jobs_root / f"{job_id}.json"
                        value = _read_job(path)
                        if value is not None and value["owner"] is not None and value["status"] in {"queued", "running"}:
                            value["status"] = "interrupted"
                            value["message"] = "进程重启时被中断（未完成）"
                            value["error"] = value.get("error") or "kernel restarted before the job finished"
                            value["finishedAt"] = time.time()
                            value["updatedAt"] = time.time()
                            job_store.validate_record(value)
                            _atomic_write(path, _json_text(value))
                            recovered += 1
                finally:
                    lease.__exit__(None, None, None)
        if recovered:
            print(f"[kernel] 恢复 {recovered} 个被中断的作业（queued/running -> interrupted）", file=sys.stderr)
        return recovered

    def _prune_history(self) -> int:
        """Remove only complete terminal groups, preserving every payload reference."""
        days, count = _retention_limits()
        cutoff = (time.time() - days * 86400.0) if days > 0 else None
        removed = 0
        with job_store.catalog_lock(self.jobs_root):
            records = job_store.read_jobs_unlocked(self.jobs_root)
            if days <= 0 and count <= 0:
                return 0
            groups: dict[str, list[dict[str, Any]]] = {}
            for value in records.values():
                group_id = value.get("audio", {}).get("primaryJobId", value["jobId"])
                groups.setdefault(group_id, []).append(value)
            dated = sorted((
                (max(value["createdAt"] for value in members), group_id, members)
                for group_id, members in groups.items()
                if all(value["status"] in TERMINAL_STATES and value["source"] == "agent" for value in members)
            ), key=lambda item: (item[0], item[1]), reverse=True)
            for rank, (created, _, members) in enumerate(dated):
                if not ((cutoff is not None and created < cutoff) or (count > 0 and rank >= count)):
                    continue
                try:
                    with ExitStack() as stack:
                        ids = sorted(value["jobId"] for value in members)
                        for job_id in ids:
                            stack.enter_context(runtime_lock(self._lease_path(job_id), timeout=0))
                        for job_id in ids:
                            stack.enter_context(job_store.write_lock(self.jobs_root, job_id))
                        latest = [_read_job(self.jobs_root / f"{job_id}.json") for job_id in ids]
                        if any(value is None or value["status"] not in TERMINAL_STATES for value in latest):
                            continue
                        moved: list[tuple[Path, Path]] = []
                        try:
                            for job_id in ids:
                                path = self.jobs_root / f"{job_id}.json"
                                temporary = self.jobs_root / f".{job_id}.{uuid.uuid4().hex}.removing"
                                path.rename(temporary)
                                moved.append((path, temporary))
                        except OSError:
                            for path, temporary in reversed(moved):
                                temporary.rename(path)
                            raise
                        for _, temporary in moved:
                            temporary.unlink()
                        removed += len(ids)
                except RuntimeLockBusy:
                        continue
        if removed:
            print(
                f"[kernel] 清理 {removed} 个过期作业文件（保留 {days:g} 天 / 最多 {count} 个）",
                file=sys.stderr,
            )
        return removed

    def _write_job_file(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        job_store.validate_record(snapshot)
        path = self.jobs_root / f"{snapshot['jobId']}.json"
        with job_store.write_lock(self.jobs_root, snapshot["jobId"]):
            previous = _read_job(path)
            if previous is not None:
                if previous["createdAt"] != snapshot["createdAt"]:
                    raise job_store.JobStoreError("createdAt is immutable")
                if previous["owner"] != snapshot["owner"]:
                    raise job_store.JobStoreError("job owner changed")
                if previous["status"] in TERMINAL_STATES:
                    return previous
            snapshot["updatedAt"] = time.time()
            _atomic_write(path, _json_text(snapshot))
            return snapshot

    def _persist(self, job: Job, *, force: bool = False, catalog_held: bool = False) -> None:
        snapshot = job.snapshot()
        job_id = str(snapshot["jobId"])
        terminal = snapshot.get("status") in TERMINAL_STATES
        published = None
        with ExitStack() as stack:
            if not job.persisted_status and not catalog_held:
                stack.enter_context(job_store.catalog_lock(self.jobs_root))
                if job_id in job_store.read_jobs_unlocked(self.jobs_root):
                    raise job_store.JobStoreError(f"job id already exists: {job_id}")
            with self._persist_lock:
                now = time.monotonic()
                should_write = (
                    force
                    or terminal
                    or snapshot.get("status") != job.persisted_status
                    or (now - job.persisted_at) >= JOB_FILE_SECONDS
                )
                if should_write:
                    published = self._write_job_file(snapshot)
        if published is not None:
            # Never take a job's lock while holding the persistence lock: event
            # consumption takes those locks in the opposite order.
            with job._lock:
                if published["status"] in TERMINAL_STATES:
                    job.adopt_terminal(published)
                elif job.status not in TERMINAL_STATES:
                    job.updated_at = published["updatedAt"]
                    job.persisted_status = published["status"]
                job.persisted_at = now

    # ── 查询 ────────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def get(self, job_id: str) -> Job | None:
        if not _valid_job_id(job_id):
            return None
        records = job_store.read_jobs(self.jobs_root)
        value = records.get(job_id)
        if value is None:
            return None
        with self._lock:
            local = self._jobs.get(job_id)
        if local is not None and value["status"] in TERMINAL_STATES and value["owner"] == local.owner and value["createdAt"] == local.created_at:
            local.adopt_terminal(value)
            return local
        if local is not None and local.status not in TERMINAL_STATES and value["owner"] == local.owner:
            return local
        return Job.from_snapshot(value, self.data_root / "bridge-jobs" / job_id)

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def start(self, command: str, payload: dict[str, Any]) -> Job:
        if not isinstance(payload, dict) or not isinstance(command, str) or not command.strip():
            raise job_store.JobStoreError("invalid job request")
        job_id = str(payload.get("requestId") or "").strip() or f"job_{uuid.uuid4().hex[:12]}"
        if not _valid_job_id(job_id):
            raise ValueError("invalid job id")
        with job_store.catalog_lock(self.jobs_root):
            records = job_store.read_jobs_unlocked(self.jobs_root)
            if job_id in records:
                raise job_store.JobStoreError(f"job id already exists: {job_id}")
            lease = runtime_lock(self._lease_path(job_id), timeout=0)
            lease.__enter__()
            workdir = self.data_root / "bridge-jobs" / job_id
            try:
                workdir.mkdir(parents=True, exist_ok=True)
                job = Job(job_id, command, copy.deepcopy(payload), workdir)
                job.owner = {"kind": "operation-kernel", "kernelId": self._owner_id, "pid": os.getpid()}
                job._lease = lease
                for path in self._cancel_paths(job):
                    path.unlink(missing_ok=True)
                self._persist(job, force=True, catalog_held=True)
                with self._lock:
                    self._jobs[job_id] = job
                threading.Thread(target=self._run, args=(job,), daemon=True, name=f"mediaforge-{job_id}").start()
            except BaseException:
                with self._lock:
                    self._jobs.pop(job_id, None)
                lease.__exit__(*sys.exc_info())
                raise
        return job

    def _lease_path(self, job_id: str) -> Path:
        # Never unlink lock files: all processes must lock the same inode.
        return self.jobs_root / ".owners" / f"{job_id}.lock"

    def _run(self, job: Job) -> None:
        try:
            self._run_worker(job)
        except Exception as exc:  # Preparation can fail before the worker is spawned.
            job.update(status="failed", error=str(exc), finished_at=time.time())
            self._persist(job, force=True)
        finally:
            if job.process is None or job._process_reaped:
                self._release_job_lease(job)
            else:
                # The kernel's job registry retains this Job and its lease. A
                # later explicit cancel retries cleanup; no unowned daemon does it.
                job._cleanup_pending = True
                job._cleanup_error = job._cleanup_error or "worker exit has not been confirmed"
                print(f"[kernel] owner retained for cleanup {job.job_id}: {job._cleanup_error}", file=sys.stderr)

    @staticmethod
    def _release_job_lease(job: Job) -> None:
        with job._lock:
            if job.process is not None and not job._process_reaped:
                return
            if job._lease is not None:
                job._lease.__exit__(None, None, None)
                job._lease = None

    def _cleanup_process(self, job: Job) -> bool:
        """One bounded cleanup attempt; retain ownership until wait confirms exit."""
        if not job._cleanup_lock.acquire(blocking=False):
            return False
        try:
            process = job.process
            if process is not None and not job._process_reaped:
                try:
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except OSError:
                            pass  # wait/kill can still confirm an already exiting child.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    job._process_reaped = True
                except (OSError, subprocess.SubprocessError) as error:
                    job._cleanup_pending = True
                    job._cleanup_error = str(error)
                    print(f"[kernel] worker cleanup failed {job.job_id}: {error}", file=sys.stderr)
                    return False
                finally:
                    if process.stdout is not None:
                        try:
                            process.stdout.close()
                        except OSError:
                            pass
            elif process is not None and process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
            job._cleanup_pending = False
            job._cleanup_error = None
            if job._payload_path is not None:
                try:
                    job._payload_path.unlink(missing_ok=True)
                    job._payload_path = None
                except OSError as error:
                    print(f"[kernel] payload cleanup failed {job.job_id}: {error}", file=sys.stderr)
            return True
        finally:
            job._cleanup_lock.release()

    def _run_worker(self, job: Job) -> None:
        payload_path = job.workdir / "payload.json"
        process = None
        try:
            # Never replace or remove a pre-existing workspace file. This precise
            # transport is ours only after exclusive creation succeeds.
            with payload_path.open("x", encoding="utf-8") as payload_file:
                job._payload_path = payload_path
                payload_file.write(_json_text(job.payload))
            stdout_path = job.workdir / "events.jsonl"
            stderr_path = job.workdir / "stderr.log"
            job.update(status="running", message="starting worker")
            self._persist(job, force=True)
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
                        env={**os.environ, "MEDIAFORGE_DATA_ROOT": str(self.data_root), "MEDIAFORGE_DATA_DIR": str(self.data_root)},
                        **hidden_subprocess_kwargs(),
                    )
                    job.process = process
                    job._process_reaped = False
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
                    job._process_reaped = True
                job.update(finished_at=time.time())
                if job.status not in TERMINAL_STATES:
                    job.update(status="completed" if code == 0 else "failed", message="worker exited",
                               error=f"worker exited with code {code}" if code else None,
                               fraction=1.0 if code == 0 else job.fraction)
                self._persist(job, force=True)
            except Exception as exc:  # noqa: BLE001
                job.update(status="failed", error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
                self._persist(job, force=True)
        finally:
            self._cleanup_process(job)

    def _cancel_paths(self, job: Job) -> tuple[Path, Path]:
        # 两套取消通道：本进程约定的 workdir/cancel，和 Rust 侧写的 agent-jobs/<id>.cancel
        return (job.workdir / "cancel", self.jobs_root / f"{job.job_id}.cancel")

    def _watch_cancel(self, job: Job, process: subprocess.Popen[str]) -> None:
        ticks = 0
        while process.poll() is None:
            # 同进程取消：Event 立刻醒，不用等文件
            if job.cancel_event.wait(JOB_FILE_SECONDS):
                break
            ticks += 1
            # 跨进程取消：文件兜底，1 秒摸一次就够了
            if ticks % max(1, int(CANCEL_POLL_SECONDS / JOB_FILE_SECONDS)) == 0:
                if any(path.exists() for path in self._cancel_paths(job)):
                    break
        else:
            return
        changed = job.update(status="cancelled", message="cancelled by user")
        if not changed and job.status != "cancelled":
            return
        if changed:
            self._persist(job, force=True)
        try:
            process.terminate()
        except OSError:
            pass

    def _consume(self, job: Job, event: dict[str, Any]) -> None:
        with job._lock:
            self._consume_locked(job, event)

    def _consume_locked(self, job: Job, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if job.status in TERMINAL_STATES:
            return  # Late worker output cannot reverse cancellation/failure.
        if kind in {"task.log", "worker_log"}:
            record = copy.deepcopy(payload.get("record") if isinstance(payload.get("record"), dict) else payload)
            record.setdefault("taskId", job.job_id)
            record.setdefault("timestamp", event.get("timestamp") or time.time())
            record.setdefault("event", kind)
            if not record.get("id") or not any(row.get("id") == record["id"] for row in job.logs):
                job.logs.append(record)
                del job.logs[:-1200]
        if isinstance(payload.get("stage"), str):
            job.runtime["stage"] = payload["stage"]
        if kind in {"task.progress", "task.stage", "workflow_execution"}:
            job.runtime["progress"] = copy.deepcopy(payload)
        if kind == "task.started":
            job.runtime["stage"] = "preparing"
        if kind == "workflow_execution" and payload.get("version") == 1:
            node_id = payload.get("nodeId")
            status = {"node_started": "running", "node_progress": "running", "node_completed": "done",
                      "node_failed": "failed", "node_cancelled": "cancelled", "node_skipped": "skipped"}.get(payload.get("event"))
            if isinstance(node_id, str) and node_id.strip() and status:
                fraction = payload.get("nodeProgress", 1.0 if status == "done" else 0.0)
                if not job_store._number(fraction):
                    fraction = 1.0 if status == "done" else 0.0
                if node_id not in job.runtime["nodes"]:
                    job.runtime["order"].append(node_id)
                job.runtime["nodes"][node_id] = {"status": status, "progress": min(1.0, max(0.0, fraction)) * 100,
                                                "message": str(payload.get("message") or "")}
                job.runtime["stage"] = "separating"
        result = copy.deepcopy(payload)
        if "outputs" in result:
            result.pop("files", None)
        if kind in {"task.progress", "asset.progress"}:
            fraction = payload.get("fraction")
            if fraction is None and kind == "asset.progress":
                fraction = float(payload.get("progress") or 0) / 100.0
            job.update(
                fraction=min(1.0, max(0.0, float(fraction or 0))),
                message=str(payload.get("current") or payload.get("message") or "running"),
            )
        elif kind in {"task.result", "asset.done"}:
            failed = payload.get("failed") if isinstance(payload.get("failed"), list) else []
            if failed:
                first = failed[0] if isinstance(failed[0], dict) else {"error": str(failed[0])}
                job.update(
                    status="failed",
                    result=result,
                    message="completed with failures",
                    error=str(first.get("error") or first.get("message") or "operation failed"),
                )
            else:
                job.update(status="completed", result=result, message="completed", fraction=1.0)
        elif kind == "error":
            # 保留错误载荷：job_status 要把 recovery 建议一并透出给调用方
            previous = copy.deepcopy(job.result) if isinstance(job.result, dict) else {}
            old_outputs = previous.get("outputs")
            previous.update(result)
            if old_outputs and not result.get("outputs"):
                previous["outputs"] = old_outputs
            job.update(
                status="failed",
                result=previous,
                error=str(payload.get("message") or payload.get("code") or "worker error"),
            )
        self._persist(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        if _valid_job_id(job_id):
            with job_store.catalog_lock(self.jobs_root):
                records = job_store.read_jobs_unlocked(self.jobs_root)
                value = records.get(job_id)
                if value is not None and value["status"] == "queued" and value["owner"] is None:
                    with job_store.write_lock(self.jobs_root, job_id):
                        latest = _read_job(self.jobs_root / f"{job_id}.json")
                        if latest is not None and latest["status"] == "queued" and latest["owner"] is None:
                            latest.update(status="cancelled", message="cancelled by client", finishedAt=time.time(), updatedAt=time.time())
                            job_store.validate_record(latest)
                            _atomic_write(self.jobs_root / f"{job_id}.json", _json_text(latest))
                            return {"ok": True, "cancelled": True}
        job = self.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job: {job_id}"}
        if job.status in TERMINAL_STATES:
            with self._lock:
                owned = self._jobs.get(job_id) is job
            if owned and job._cleanup_pending:
                if self._cleanup_process(job):
                    self._release_job_lease(job)
                    return {"ok": True, "cancelled": job.status == "cancelled", "cleanupPending": False}
                return {"ok": False, "cancelled": job.status == "cancelled", "cleanupPending": True,
                        "error": job._cleanup_error or "worker cleanup is already in progress"}
            return {"ok": True, "cancelled": job.status == "cancelled"}
        with self._lock:
            local = self._jobs.get(job_id) is job
        if not local:
            # A detached disk snapshot has no watcher/Event. Signal its real owner.
            (self.jobs_root / f"{job_id}.cancel").touch()
            return {"ok": True, "cancelled": False, "requested": True}
        job.cancel_event.set()
        process = job.process
        if process and process.poll() is None:
            if not job.update(status="cancelled", message="cancelled by client"):
                return {"ok": True, "cancelled": job.status == "cancelled"}
            try:
                process.terminate()
            except OSError:
                pass
            self._persist(job, force=True)
            return {"ok": True, "cancelled": True}
        return {"ok": True, "cancelled": job.status == "cancelled"}
