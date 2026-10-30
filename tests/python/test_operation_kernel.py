"""OperationKernel 的落盘/恢复/取消契约。

这些行为在 2026-10-29 之前完全没有测试覆盖，而它们正好是最容易悄悄退化的地方：
- 落盘频率（改回逐事件全量扫描，界面照样能用，只是十分钟视频要多扫一万八千次目录）
- 重启恢复（不恢复的话，磁盘永远显示 running，而接口返回 unknown job）
- 取消通道（少盯一条路径，UI 点取消对 agent 起的作业就无效）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from operation_kernel import Job, OperationKernel  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RecoveryTests(unittest.TestCase):
    def test_stale_running_job_becomes_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            stale = {"jobId": "job_stale", "command": "audio_tools", "status": "running", "createdAt": time.time(), "payload": {}}
            (jobs / "job_stale.json").write_text(json.dumps(stale), encoding="utf-8")

            kernel = OperationKernel(root)

            self.assertEqual(kernel.recovered, 1)
            saved = read_json(jobs / "job_stale.json")
            self.assertEqual(saved["status"], "interrupted")
            self.assertIn("finishedAt", saved)
            self.assertTrue(saved["message"])
            index = read_json(jobs / "index.json")
            self.assertEqual([entry["jobId"] for entry in index["jobs"]], ["job_stale"])
            self.assertEqual(index["jobs"][0]["status"], "interrupted")
            self.assertTrue((root / "agent-workspace.json").is_file())

    def test_finished_jobs_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            done = {"jobId": "job_done", "command": "audio_tools", "status": "completed", "createdAt": time.time(), "payload": {}}
            (jobs / "job_done.json").write_text(json.dumps(done), encoding="utf-8")

            kernel = OperationKernel(root)

            self.assertEqual(kernel.recovered, 0)
            self.assertEqual(read_json(jobs / "job_done.json")["status"], "completed")

    def test_corrupt_job_file_does_not_block_startup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            (jobs / "job_broken.json").write_text("{ not json", encoding="utf-8")
            (jobs / "index.json").write_text("{}", encoding="utf-8")

            kernel = OperationKernel(root)

            self.assertEqual(kernel.recovered, 0)
            self.assertEqual(kernel.count(), 0)


class PersistTests(unittest.TestCase):
    def _kernel_with_job(self, root: Path, job_id: str = "job_progress") -> tuple[OperationKernel, Job]:
        kernel = OperationKernel(root)
        job = Job(job_id, "audio_tools", {}, root / "bridge-jobs" / job_id)
        job.workdir.mkdir(parents=True, exist_ok=True)
        return kernel, job

    def test_progress_persist_is_throttled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel, job = self._kernel_with_job(root)
            writes = {"count": 0}
            original = kernel._write_job_file

            def counting(snapshot):
                writes["count"] += 1
                original(snapshot)

            kernel._write_job_file = counting  # type: ignore[method-assign]
            for step in range(120):
                job.update(fraction=step / 120.0, message=f"frame {step}")
                kernel._persist(job)

            # 逐帧全量落盘的话这里会是 120；节流后必须显著更少
            self.assertGreaterEqual(writes["count"], 1)
            self.assertLess(writes["count"], 40)

    def test_terminal_state_is_never_lost_to_throttling(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel, job = self._kernel_with_job(root)
            job.update(status="running", message="working")
            kernel._persist(job, force=True)
            job.update(status="completed", fraction=1.0, result={"outputs": []})
            kernel._persist(job)

            saved = read_json(root / "agent-jobs" / "job_progress.json")
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["fraction"], 1.0)
            self.assertEqual(saved["result"], {"outputs": []})
            index = read_json(root / "agent-jobs" / "index.json")
            self.assertEqual(index["jobs"][0]["status"], "completed")

    def test_status_change_is_written_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel, job = self._kernel_with_job(root, "job_status_change")
            job.update(status="queued")
            kernel._persist(job, force=True)
            job.update(status="running", message="starting worker")
            kernel._persist(job)  # 没有 force，但状态变了必须落盘
            saved = read_json(root / "agent-jobs" / "job_status_change.json")
            self.assertEqual(saved["status"], "running")


class IndexTests(unittest.TestCase):
    def test_flush_keeps_job_files_written_by_another_writer(self) -> None:
        """Rust 的 journal_job 也会写 agent-jobs/*.json，索引不能把它们挤掉。"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            manual = root / "agent-jobs" / "manual_1.json"
            manual.write_text(
                json.dumps({"jobId": "manual_1", "command": "ui_task", "status": "completed", "createdAt": time.time()}),
                encoding="utf-8",
            )
            job = Job("job_a", "audio_tools", {}, root / "bridge-jobs" / "job_a")
            job.workdir.mkdir(parents=True, exist_ok=True)
            kernel._persist(job, force=True)

            index = read_json(root / "agent-jobs" / "index.json")
            self.assertEqual({entry["jobId"] for entry in index["jobs"]}, {"job_a", "manual_1"})

    def test_index_is_capped_and_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            for order in range(105):
                job = Job(f"job_{order:03d}", "audio_tools", {}, root / "bridge-jobs" / f"job_{order:03d}")
                job.workdir.mkdir(parents=True, exist_ok=True)
                job.update(created_at=float(order))
                kernel._persist(job, force=True)

            index = read_json(root / "agent-jobs" / "index.json")
            self.assertEqual(len(index["jobs"]), 100)
            self.assertEqual(index["jobs"][0]["jobId"], "job_104")


class CancelTests(unittest.TestCase):
    def test_cancel_paths_cover_both_channels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            job = Job("job_x", "audio_tools", {}, root / "bridge-jobs" / "job_x")
            paths = kernel._cancel_paths(job)
            self.assertIn(job.workdir / "cancel", paths)
            self.assertIn(root / "agent-jobs" / "job_x.cancel", paths)

    def test_cancel_sets_event_for_the_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            job = Job("job_y", "audio_tools", {}, root / "bridge-jobs" / "job_y")
            with kernel._lock:
                kernel._jobs["job_y"] = job

            result = kernel.cancel("job_y")

            self.assertTrue(result["ok"])
            self.assertTrue(job.cancel_event.is_set())

    def test_cancel_unknown_job_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            result = kernel.cancel("does_not_exist")
            self.assertFalse(result["ok"])
            self.assertIn("unknown job", result["error"])


class SnapshotContractTests(unittest.TestCase):
    def test_snapshot_keeps_the_fields_index_and_mcp_rely_on(self) -> None:
        job = Job("job_c", "audio_tools", {"source": "manual"}, Path("."))
        snapshot = job.snapshot()
        for key in (
            "jobId",
            "command",
            "source",
            "status",
            "fraction",
            "message",
            "result",
            "error",
            "createdAt",
            "finishedAt",
            "payload",
        ):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["source"], "manual")


if __name__ == "__main__":
    unittest.main()
