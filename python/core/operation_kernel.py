"""Shared job lifecycle backed only by agent-jobs/<jobId>.json.

Each owner holds a lifetime lease and atomically writes its job snapshot.
Progress writes are throttled; status changes and terminal payloads are durable
immediately. Readers query job files directly, so restarted MCP clients and
other processes see the same records without secondary indexes or workspaces.
"""
from __future__ import annotations

import json
import os
import re
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

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker.py"

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
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
    return json.dumps(value, ensure_ascii=False, indent=2)


def _atomic_write(path: Path, text: str) -> None:
    # Writers publish a whole job snapshot with one atomic replacement.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        # 这些文件可能被 PowerShell 或别的工具写过，BOM 会让 json.loads 直接抛
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _valid_job_id(job_id: str) -> bool:
    device = job_id.split(".", 1)[0].upper()
    return (
        job_id.casefold() != "index"
        and device not in {"CON", "PRN", "AUX", "NUL"}
        and not re.fullmatch(r"(?:COM|LPT)[1-9]", device)
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", job_id))
    )


def _read_job(path: Path) -> dict[str, Any] | None:
    value = _read_json(path)
    if not isinstance(value, dict):
        return None
    job_id = value.get("jobId")
    if not isinstance(job_id, str) or not _valid_job_id(job_id) or path.stem != job_id:
        return None
    return value


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
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        # 节流用的记账；只在 OperationKernel._persist 的锁内读写
        self.persisted_at = 0.0
        self.persisted_status = ""
        self.owner: dict[str, Any] | None = None
        self._lease: Any = None
        self._stored: dict[str, Any] = {}

    @classmethod
    def from_snapshot(cls, value: dict[str, Any], workdir: Path) -> Job:
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
        job = cls(str(value["jobId"]), str(value.get("command") or ""), payload, workdir)
        job._stored = dict(value)
        job.status = str(value.get("status") or "queued")
        job.fraction = float(value.get("fraction") or 0)
        job.message = str(value.get("message") or "")
        job.result = value.get("result")
        job.error = value.get("error")
        job.created_at = value.get("createdAt")
        job.finished_at = value.get("finishedAt")
        job.owner = value.get("owner") if isinstance(value.get("owner"), dict) else None
        return job

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._stored,
                "jobId": self.job_id,
                "command": self.command,
                "source": str(self._stored.get("source") or self.payload.get("source") or "agent"),
                "status": self.status,
                "fraction": round(float(self.fraction), 4),
                "message": self.message,
                "result": self.result,
                "error": self.error,
                "createdAt": self.created_at,
                "finishedAt": self.finished_at,
                "payload": self.payload,
                **({"owner": self.owner} if self.owner else {}),
            }

    def update(self, **fields: Any) -> bool:
        with self._lock:
            if self.status in TERMINAL_STATES and fields.get("status", self.status) != self.status:
                return False
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
        """Recover abandoned agent jobs, never a job owned by a live kernel/UI."""
        recovered = 0
        for path in self.jobs_root.glob("*.json"):
            value = _read_job(path)
            if value is None:
                continue
            job_id = value["jobId"]
            if value.get("status") in {"queued", "running"} and (
                value.get("source") != "manual" or isinstance(value.get("owner"), dict)
            ):
                try:
                    with runtime_lock(self._lease_path(job_id), timeout=0):
                        # The owner may have finished while we were opening its lock.
                        value = _read_job(path)
                        if value is None:
                            continue
                        if value.get("status") in {"queued", "running"}:
                            value["status"] = "interrupted"
                            value["message"] = "进程重启时被中断（未完成）"
                            value["error"] = value.get("error") or "kernel restarted before the job finished"
                            value["finishedAt"] = value.get("finishedAt") or time.time()
                            _atomic_write(path, _json_text(value))
                            recovered += 1
                except (RuntimeLockBusy, OSError):
                    # Busy means another kernel still owns this job. An inaccessible
                    # lock is also insufficient evidence that its owner has died.
                    pass
        if recovered:
            print(f"[kernel] 恢复 {recovered} 个被中断的作业（queued/running -> interrupted）", file=sys.stderr)
        return recovered

    def _prune_history(self) -> int:
        """清理过期的终态作业文件（保留策略见 `_retention_limits`）。

        只删**自己的** `agent-jobs/<id>.json`，且只删终态；`queued`/`running` 永远不碰，
        没写 `createdAt` 的也不碰（没有时间戳就不该猜它多老）。
        """
        days, count = _retention_limits()
        if days <= 0 and count <= 0:
            return 0
        cutoff = (time.time() - days * 86400.0) if days > 0 else None
        dated: list[tuple[float, str]] = []
        for path in self.jobs_root.glob("*.json"):
            snapshot = _read_job(path)
            if snapshot is None or str(snapshot.get("status") or "") not in TERMINAL_STATES:
                continue
            try:
                created = float(snapshot.get("createdAt") or 0.0)
            except (TypeError, ValueError):
                continue
            if created > 0:
                dated.append((created, str(snapshot["jobId"])))
        dated.sort(reverse=True)

        removed = 0
        for rank, (created, job_id) in enumerate(dated):
            too_old = cutoff is not None and created < cutoff
            too_many = count > 0 and rank >= count
            if not (too_old or too_many):
                continue
            try:
                with runtime_lock(self._lease_path(job_id), timeout=0):
                    path = self.jobs_root / f"{job_id}.json"
                    latest = _read_job(path)
                    # A different owner may have reused the ID since the startup scan.
                    # Never delete an active job or apply an old retention rank to it.
                    if (
                        not isinstance(latest, dict)
                        or latest.get("status") not in TERMINAL_STATES
                        or float(latest.get("createdAt") or 0.0) != created
                    ):
                        continue
                    path.unlink(missing_ok=True)
            except (RuntimeLockBusy, OSError, TypeError, ValueError):
                continue
            removed += 1
        if removed:
            print(
                f"[kernel] 清理 {removed} 个过期作业文件（保留 {days:g} 天 / 最多 {count} 个）",
                file=sys.stderr,
            )
        return removed

    def _write_job_file(self, snapshot: dict[str, Any]) -> None:
        if not _valid_job_id(str(snapshot["jobId"])):
            raise ValueError("invalid job id")
        path = self.jobs_root / f"{snapshot['jobId']}.json"
        _atomic_write(path, _json_text(snapshot))

    def _persist(self, job: Job, *, force: bool = False) -> None:
        snapshot = job.snapshot()
        job_id = str(snapshot["jobId"])
        terminal = snapshot.get("status") in TERMINAL_STATES
        with self._persist_lock:
            now = time.monotonic()
            should_write = (
                force
                or terminal
                or snapshot.get("status") != job.persisted_status
                or (now - job.persisted_at) >= JOB_FILE_SECONDS
            )
            if should_write:
                try:
                    self._write_job_file(snapshot)
                    job.persisted_at = now
                    job.persisted_status = str(snapshot.get("status") or "")
                except OSError as exc:
                    print(f"[kernel] 作业落盘失败 {job_id}: {exc}", file=sys.stderr)

    # ── 查询 ────────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            local = self._jobs.get(job_id)
        if local is not None and local.status not in TERMINAL_STATES:
            return local
        if not _valid_job_id(job_id):
            return None
        value = _read_job(self.jobs_root / f"{job_id}.json")
        if not isinstance(value, dict) or value.get("jobId") != job_id:
            return None
        try:
            return Job.from_snapshot(value, self.data_root / "bridge-jobs" / job_id)
        except (TypeError, ValueError):
            return None

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def start(self, command: str, payload: dict[str, Any]) -> Job:
        job_id = str(payload.get("requestId") or "").strip() or f"job_{uuid.uuid4().hex[:12]}"
        if not _valid_job_id(job_id):
            raise ValueError("invalid job id")
        lease = runtime_lock(self._lease_path(job_id), timeout=0)
        lease.__enter__()
        workdir = self.data_root / "bridge-jobs" / job_id
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            job = Job(job_id, command, payload, workdir)
            job.owner = {"kernelId": self._owner_id, "pid": os.getpid()}
            job._lease = lease
            # A reused terminal request ID must not inherit an old cancel request.
            for path in self._cancel_paths(job):
                path.unlink(missing_ok=True)
            with self._lock:
                self._jobs[job_id] = job
            self._persist(job, force=True)
            threading.Thread(target=self._run, args=(job,), daemon=True, name=f"mediaforge-{job_id}").start()
        except BaseException:
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
            if job._lease is not None:
                job._lease.__exit__(None, None, None)
                job._lease = None

    def _run_worker(self, job: Job) -> None:
        payload_path = job.workdir / "payload.json"
        payload_path.write_text(_json_text(job.payload), encoding="utf-8")
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
                    **hidden_subprocess_kwargs(),
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
            if job.status not in {"completed", "failed", "cancelled", "interrupted"}:
                job.update(status="completed" if code == 0 else "failed", message="worker exited")
                if code != 0:
                    job.update(error=f"worker exited with code {code}")
            if job.status == "completed":
                job.update(fraction=1.0)
            self._persist(job, force=True)
        except Exception as exc:  # noqa: BLE001
            job.update(status="failed", error=f"{exc}\n{traceback.format_exc()}", finished_at=time.time())
            self._persist(job, force=True)

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
        if not job.update(status="cancelled", message="cancelled by user"):
            return
        self._persist(job, force=True)
        try:
            process.terminate()
        except OSError:
            pass

    def _consume(self, job: Job, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if job.status in TERMINAL_STATES:
            return  # Late worker output cannot reverse cancellation/failure.
        if kind in {"task.progress", "asset.progress"}:
            fraction = payload.get("fraction")
            if fraction is None and kind == "asset.progress":
                fraction = float(payload.get("progress") or 0) / 100.0
            job.update(
                fraction=float(fraction or 0),
                message=str(payload.get("current") or payload.get("message") or "running"),
            )
        elif kind in {"task.result", "asset.done"}:
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
            # 保留错误载荷：job_status 要把 recovery 建议一并透出给调用方
            job.update(
                status="failed",
                result=payload,
                error=str(payload.get("message") or payload.get("code") or "worker error"),
            )
        self._persist(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            return {"ok": False, "error": f"unknown job: {job_id}"}
        if job.status in TERMINAL_STATES:
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
