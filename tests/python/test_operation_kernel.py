"""OperationKernel 的落盘/恢复/取消契约。

这些行为在 2026-10-29 之前完全没有测试覆盖，而它们正好是最容易悄悄退化的地方：
- 落盘频率（改回逐事件全量扫描，界面照样能用，只是十分钟视频要多扫一万八千次目录）
- 重启恢复（不恢复的话，磁盘永远显示 running，而接口返回 unknown job）
- 取消通道（少盯一条路径，UI 点取消对 agent 起的作业就无效）
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core.operation_kernel import Job, OperationKernel  # noqa: E402
from core.worker_runtime_lock import RuntimeLockBusy  # noqa: E402
from core.job_store import JobStoreError
from ._job_fixtures import job_record


def owned_job(*args, **kwargs):
    job = Job(*args, **kwargs)
    job.owner = {"kind": "operation-kernel", "kernelId": "fixture", "pid": 12345}
    return job


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RecoveryTests(unittest.TestCase):
    def test_stale_running_job_becomes_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            stale = job_record("job_stale", status="running")
            (jobs / "job_stale.json").write_text(json.dumps(stale), encoding="utf-8")

            kernel = OperationKernel(root)

            self.assertEqual(kernel.recovered, 1)
            saved = read_json(jobs / "job_stale.json")
            self.assertEqual(saved["status"], "interrupted")
            self.assertIn("finishedAt", saved)
            self.assertTrue(saved["message"])
            self.assertEqual(kernel.get("job_stale").snapshot()["status"], "interrupted")
            self.assertFalse((jobs / "index.json").exists())
            self.assertFalse((root / "agent-workspace.json").exists())

    def test_finished_jobs_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            done = job_record("job_done")
            (jobs / "job_done.json").write_text(json.dumps(done), encoding="utf-8")

            kernel = OperationKernel(root)

            self.assertEqual(kernel.recovered, 0)
            self.assertEqual(read_json(jobs / "job_done.json")["status"], "completed")

    def test_corrupt_job_file_blocks_startup_and_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir(parents=True)
            (jobs / "job_broken.json").write_text("{ not json", encoding="utf-8")
            (jobs / "index.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(JobStoreError):
                OperationKernel(root)
            self.assertEqual((jobs / "job_broken.json").read_text(encoding="utf-8"), "{ not json")
            self.assertEqual((jobs / "index.json").read_text(encoding="utf-8"), "{}")

    def test_other_kernel_in_same_process_does_not_interrupt_an_owned_job(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = OperationKernel(root)
            with mock.patch("core.operation_kernel.threading.Thread.start"):
                job = owner.start("fixture", {"requestId": "job_live"})
            try:
                observer = OperationKernel(root)
                self.assertEqual(observer.recovered, 0)
                self.assertEqual(observer.get(job.job_id).snapshot()["status"], "queued")
                self.assertEqual(observer.count(), 0)  # Observing does not own/run it.
                with mock.patch("core.operation_kernel.threading.Thread.start"):
                    with self.assertRaises(JobStoreError):
                        observer.start("fixture", {"requestId": "job_live"})
            finally:
                job._lease.__exit__(None, None, None)
                job._lease = None

    def test_live_process_is_preserved_then_recovered_after_owner_exit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = """
import sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[2])
from core.operation_kernel import OperationKernel
kernel = OperationKernel(Path(sys.argv[1]))
with mock.patch('core.operation_kernel.threading.Thread.start'):
    job = kernel.start('fixture', {'requestId': 'job_remote'})
print('ready', flush=True)
sys.stdin.readline()
"""
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", script, str(root), str(PYTHON_DIR)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                live = OperationKernel(root)
                self.assertEqual(live.recovered, 0)
                self.assertEqual(live.get("job_remote").snapshot()["status"], "queued")
                process.communicate("exit\n", timeout=10)
                self.assertEqual(process.returncode, 0)
                restarted = OperationKernel(root)
                self.assertEqual(restarted.recovered, 1)
                self.assertEqual(restarted.get("job_remote").snapshot()["status"], "interrupted")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)

    def test_manual_task_has_no_kernel_owner_and_is_not_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            jobs = root / "agent-jobs"
            jobs.mkdir()
            snapshot = job_record("manual_1", source="manual", status="queued", owner=None)
            (jobs / "manual_1.json").write_text(json.dumps(snapshot), encoding="utf-8")
            kernel = OperationKernel(root)
            self.assertEqual(kernel.recovered, 0)
            self.assertEqual(kernel.get("manual_1").snapshot()["status"], "queued")

    def test_retention_cannot_delete_a_job_while_its_owner_still_holds_the_lease(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = OperationKernel(root)
            with mock.patch("core.operation_kernel.threading.Thread.start"), \
                 mock.patch("core.operation_kernel.time.time", return_value=time.time() - 100 * 86400):
                job = owner.start("fixture", {"requestId": "job_live"})
            try:
                # A terminal event may be visible before the worker/thread has exited.
                job.update(status="completed")
                owner._persist(job, force=True)
                observer = OperationKernel(root)
                self.assertEqual(observer.pruned, 0)
                self.assertIsNotNone(observer.get(job.job_id))
            finally:
                job._lease.__exit__(None, None, None)
                job._lease = None
            observer = OperationKernel(root)
            self.assertEqual(observer.pruned, 1)
            self.assertIsNone(observer.get(job.job_id))
            self.assertIsNone(owner.get(job.job_id))
            self.assertFalse((root / "agent-jobs/index.json").exists())

    def test_completed_payload_is_queryable_after_restart_without_running_a_worker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            job = owned_job("historical", "run_operation", {"operation": "test"}, root / "bridge-jobs" / "historical")
            job.update(status="completed", result={"outputs": {"value": 42}}, finished_at=123.0)
            kernel._persist(job, force=True)
            restarted = OperationKernel(root)
            with mock.patch("core.operation_kernel.subprocess.Popen") as spawn:
                restored = restarted.get("historical")
            self.assertEqual(restored.snapshot()["result"], {"outputs": {"value": 42}})
            self.assertEqual(restored.snapshot()["finishedAt"], 123.0)
            self.assertEqual(restarted.count(), 0)
            spawn.assert_not_called()

    def test_bridge_job_status_and_retry_can_read_a_historical_failure(self) -> None:
        import agent_bridge

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            payload = {"operation": "media_image", "device": "cuda", "steps": [{"capability": "upscale", "params": {}}]}
            result = {"recovery": {"retryable": True, "actions": [{"action": "switch_device", "to": "cpu"}]}}
            job = owned_job("historical_failure", "audio_tools", payload, root / "bridge-jobs" / "historical_failure")
            job.update(status="failed", result=result, error="CUDA out of memory")
            kernel._persist(job, force=True)
            bridge = agent_bridge.Bridge(token=None, port=0, data_root=root)
            status = bridge.call("job_status", {"jobId": job.job_id})
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["result"], result)
            self.assertTrue(status["recovery"]["retryable"])
            retried = mock.Mock(job_id="historical_failure-r1")
            retried.snapshot.return_value = {"status": "queued"}
            with mock.patch.object(bridge.jobs, "start", return_value=retried) as start:
                outcome = bridge.job_retry(job.job_id)
            self.assertTrue(outcome["ok"], outcome)
            self.assertEqual(start.call_args.args[1]["device"], "cpu")


class PersistTests(unittest.TestCase):
    def _kernel_with_job(self, root: Path, job_id: str = "job_progress") -> tuple[OperationKernel, Job]:
        kernel = OperationKernel(root)
        job = owned_job(job_id, "audio_tools", {}, root / "bridge-jobs" / job_id)
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
                return original(snapshot)

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
            self.assertEqual(kernel.get("job_progress").snapshot()["status"], "completed")
            self.assertFalse((root / "agent-jobs/index.json").exists())

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


class SingleFileHistoryTests(unittest.TestCase):
    def test_foreign_job_changes_and_deletions_are_read_directly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            path = kernel.jobs_root / "manual_1.json"
            snapshot = job_record("manual_1", source="manual", status="running", owner={"kind": "tauri-worker", "pid": 12345})
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            self.assertEqual(kernel.get("manual_1").snapshot()["status"], "running")
            snapshot.update(status="completed", result={"outputs": ["done.wav"]}, finishedAt=time.time())
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            self.assertEqual(kernel.get("manual_1").snapshot()["result"], snapshot["result"])
            path.unlink()
            self.assertIsNone(kernel.get("manual_1"))

    def test_persist_never_scans_history_or_writes_duplicate_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            legacy_index = kernel.jobs_root / "index.json"
            legacy_workspace = root / "agent-workspace.json"
            legacy_workspace.write_bytes(b"leave previous workspace untouched")
            job = owned_job("new_job", "audio_tools", {}, root / "bridge-jobs/new_job")
            kernel._persist(job, force=True)
            legacy_index.write_bytes(b"leave previous index untouched")
            with mock.patch.object(Path, "glob", side_effect=AssertionError("progress must not scan history")):
                kernel._persist(job, force=True)
                job.update(status="completed", result={"outputs": ["done.wav"]})
                kernel._persist(job)
            with self.assertRaises(JobStoreError):
                kernel.get("new_job")
            self.assertEqual(read_json(kernel.jobs_root / "new_job.json")["status"], "completed")
            self.assertEqual(legacy_index.read_bytes(), b"leave previous index untouched")
            self.assertEqual(legacy_workspace.read_bytes(), b"leave previous workspace untouched")

    def test_existing_manual_job_is_not_rewritten_by_another_jobs_progress(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            manual = kernel.jobs_root / "manual_1.json"
            value = job_record("manual_1", source="manual", command="ui_task")
            manual.write_text(json.dumps(value), encoding="utf-8")
            before = manual.read_bytes()
            job = owned_job("job_a", "audio_tools", {}, root / "bridge-jobs/job_a")
            kernel._persist(job, force=True)
            self.assertEqual(manual.read_bytes(), before)
            self.assertEqual(kernel.get("manual_1").snapshot()["source"], "manual")
            self.assertFalse((kernel.jobs_root / "index.json").exists())
            self.assertFalse((root / "agent-workspace.json").exists())

    def test_reserved_index_id_cannot_be_started(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            for invalid in ("index", "INDEX", "CON", "nul.json", "COM1", "LPT9.log", "../escape"):
                with self.subTest(job_id=invalid), self.assertRaisesRegex(ValueError, "invalid job id"):
                    kernel.start("fixture", {"requestId": invalid})


class CancelTests(unittest.TestCase):
    def test_cancel_foreign_job_signals_owner_and_waits_for_owner_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            path = kernel.jobs_root / "foreign.json"
            path.write_text(json.dumps(job_record("foreign", status="running")), encoding="utf-8")
            result = kernel.cancel("foreign")
            self.assertTrue(result["requested"])
            self.assertFalse(result["cancelled"])
            self.assertTrue((kernel.jobs_root / "foreign.cancel").is_file())
            self.assertEqual(read_json(path)["status"], "running")

    def test_cancel_historical_terminal_does_not_create_a_cancel_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            path = kernel.jobs_root / "done.json"
            path.write_text(json.dumps(job_record("done")), encoding="utf-8")
            self.assertEqual(kernel.cancel("done"), {"ok": True, "cancelled": False})
            self.assertFalse((kernel.jobs_root / "done.cancel").exists())

    def test_cancel_paths_cover_both_channels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            job = owned_job("job_x", "audio_tools", {}, root / "bridge-jobs" / "job_x")
            paths = kernel._cancel_paths(job)
            self.assertIn(job.workdir / "cancel", paths)
            self.assertIn(root / "agent-jobs" / "job_x.cancel", paths)

    def test_cancel_sets_event_for_the_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            job = owned_job("job_y", "audio_tools", {}, root / "bridge-jobs" / "job_y")
            kernel._persist(job, force=True)
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
        job = owned_job("job_c", "audio_tools", {"source": "manual"}, Path("."))
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
        self.assertEqual(snapshot["source"], "agent")  # Payload data cannot change execution ownership.
        self.assertEqual(snapshot["schemaVersion"], 2)


class EventTests(unittest.TestCase):
    def test_operation_and_asset_events_preserve_their_terminal_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kernel = OperationKernel(root)
            for terminal, result in (
                ("task.result", {"operation": "test", "outputs": {"audio": {"path": "result.wav"}}}),
                ("asset.done", {"modelId": "media:test", "downloaded": ["model.bin"], "skipped": []}),
            ):
                with self.subTest(terminal=terminal):
                    job = owned_job(terminal.replace(".", "_"), "fixture", {}, root / "bridge-jobs" / terminal)
                    job.update(status="running")
                    progress = "task.progress" if terminal == "task.result" else "asset.progress"
                    kernel._consume(job, {"type": progress, "payload": {"fraction": 0.5, "message": "half"}})
                    self.assertEqual(job.fraction, 0.5)
                    kernel._consume(job, {"type": terminal, "payload": result})
                    self.assertEqual(job.status, "completed")
                    self.assertEqual(job.result, result)
                    self.assertEqual(read_json(kernel.jobs_root / f"{job.job_id}.json")["result"], result)
                    kernel._consume(job, {"type": progress, "payload": {"fraction": 0.1}})
                    self.assertEqual(job.fraction, 1.0)

    def test_asset_progress_percent_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            job = owned_job("download", "model_download", {}, Path(raw))
            kernel._consume(job, {"type": "asset.progress", "payload": {"progress": 25}})
            self.assertEqual(job.fraction, 0.25)

    def test_late_success_does_not_reverse_cancellation_or_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            for status in ("cancelled", "failed"):
                job = owned_job(status, "fixture", {}, Path(raw))
                job.update(status=status, error="original error")
                kernel._consume(job, {"type": "task.result", "payload": {"outputs": {}}})
                self.assertEqual(job.status, status)
                self.assertEqual(job.error, "original error")


if __name__ == "__main__":
    unittest.main()


class WorkerTerminalContractTests(unittest.TestCase):
    def test_command_specific_result_events_come_from_the_protocol(self) -> None:
        from core.operation_kernel import _result_events

        expected = {
            "import_custom_model": "custom_model_import_finished",
            "install_runtime": "runtime_install_finished",
            "delete_runtime": "runtime_deleted",
            "manage_optional_package": "optional_package_status",
            "manage_plugin": "plugin_manage_result",
            "run_operation": "task.result",
            "delete_model": "model_delete_done",
            "cleanup_model_residual_files": "model_residual_cleanup_done",
            "list_nodes": "nodes_list",
        }
        for command, event in expected.items():
            self.assertEqual(_result_events().get(command), event)

    def test_declared_management_result_is_persisted_as_the_job_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            job = owned_job("install_result", "install_runtime", {}, Path(raw))
            job.update(status="running")
            result = {"backend": "cpu", "state": {"pythonPath": "fixture"}}
            kernel._consume(job, {"type": "runtime_install_finished", "payload": result})
            snapshot = kernel.get(job.job_id).snapshot()
            self.assertEqual(snapshot["status"], "completed")
            self.assertEqual(snapshot["result"], result)

    def test_custom_model_delete_terminal_event_completes_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            kernel = OperationKernel(Path(raw))
            job = owned_job("delete_result", "delete_model", {}, Path(raw))
            job.update(status="running")
            kernel._consume(job, {"type": "model_delete_done", "payload": {"model": "fixture"}})
            self.assertEqual(kernel.get(job.job_id).snapshot()["status"], "completed")

    def _run_fake_worker(self, source: str) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            worker = root / "worker.py"
            worker.write_text(source, encoding="utf-8")
            with mock.patch("core.operation_kernel.WORKER", worker):
                kernel = OperationKernel(root, sys.executable)
                try:
                    job = kernel.start("fixture", {"requestId": "fixture_exit"})
                    deadline = time.monotonic() + 5
                    while (not job._process_reaped or job._lease is not None) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(job._process_reaped, "fake worker did not exit")
                    self.assertIsNone(job._lease, "fake worker retained its owner lease")
                    return kernel.get(job.job_id).snapshot()
                finally:
                    kernel.close()
                    for job in kernel._jobs.values():
                        kernel._cleanup_process(job)
                        kernel._release_job_lease(job)

    def test_invalid_output_and_successful_exit_without_result_are_failures(self) -> None:
        invalid = self._run_fake_worker("print('not-json', flush=True)\n")
        self.assertEqual(invalid["status"], "failed")
        self.assertIsNone(invalid["result"])
        self.assertIn("Invalid worker event", invalid["error"])
        empty = self._run_fake_worker("")
        self.assertEqual(empty["status"], "failed")
        self.assertIsNone(empty["result"])
        self.assertEqual(empty["error"], "worker exited without a result")
