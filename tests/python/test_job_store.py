"""Schema-2 storage, queued audio and reference-group lifecycle boundaries."""
from __future__ import annotations

import json
import io
import os
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from ._job_fixtures import audio_group, job_record, write_record
from core.operation_kernel import OperationKernel
from core import failure_stats
from core import job_store


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mediaforge-job-schema-")))
        self.jobs = self.root / "agent-jobs"
        self.jobs.mkdir()

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.jobs.glob("*.json")}

    def write_group(self, **kwargs):
        records = audio_group(**kwargs)
        for record in records:
            write_record(self.jobs, record)
        return records

    def external_cancel(self, job):
        path = self.jobs / f"{job.job_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(status="cancelled", message="Rust cancelled", fraction=0.75,
                      finishedAt=time.time(), updatedAt=time.time(), result={"outputs": [{"path": "partial.wav"}]},
                      logs=[{"message": "preserved external log"}])
        record["runtime"]["stage"] = "cancelled"
        path.write_text(json.dumps(record, indent=4), encoding="utf-8")
        return path, record, path.read_bytes()

    def test_disk_terminal_wins_cached_get_and_still_wakes_the_cancel_watcher(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"):
            job = kernel.start("fixture", {"requestId": "external_cancel"})
        try:
            job.update(status="running")
            kernel._persist(job, force=True)
            path, record, before = self.external_cancel(job)
            self.assertIs(kernel.get(job.job_id), job)
            self.assertEqual(job.status, "cancelled")
            self.assertEqual(job.result, record["result"])
            self.assertEqual(job.logs, record["logs"])
            self.assertTrue(job.cancel_event.is_set())
            process = mock.Mock()
            process.poll.return_value = None
            kernel._watch_cancel(job, process)
            process.terminate.assert_called_once()
            self.assertEqual(path.read_bytes(), before)
        finally:
            job._lease.__exit__(None, None, None)
            job._lease = None

    def test_external_terminal_is_adopted_instead_of_failing_late_progress_or_result(self):
        for result in (False, True):
            with self.subTest(late_result=result):
                kernel = OperationKernel(self.root)
                with mock.patch("core.operation_kernel.threading.Thread.start"):
                    job = kernel.start("fixture", {"requestId": f"late_{result}"})
                try:
                    job.update(status="running")
                    kernel._persist(job, force=True)
                    path, record, before = self.external_cancel(job)
                    if result:
                        job.update(status="completed", result={"outputs": [{"path": "wrong.wav"}]})
                    else:
                        job.update(message="late progress", fraction=0.95)
                    kernel._persist(job, force=True)
                    self.assertEqual(job.status, "cancelled")
                    self.assertEqual(job.persisted_status, "cancelled")
                    self.assertEqual(job.result, record["result"])
                    self.assertEqual(job.logs, record["logs"])
                    self.assertEqual(job.updated_at, record["updatedAt"])
                    kernel._consume(job, {"type": "task.result", "payload": {"outputs": [{"path": "later.wav"}]}})
                    self.assertIs(kernel.get(job.job_id), job)
                    self.assertEqual(path.read_bytes(), before)
                finally:
                    job._lease.__exit__(None, None, None)
                    job._lease = None

    def test_terminal_with_changed_owner_or_creation_is_still_rejected(self):
        for field in ("owner", "createdAt"):
            with self.subTest(field=field):
                kernel = OperationKernel(self.root)
                with mock.patch("core.operation_kernel.threading.Thread.start"):
                    job = kernel.start("fixture", {"requestId": f"changed_{field}"})
                try:
                    path, record, _ = self.external_cancel(job)
                    if field == "owner":
                        record["owner"]["kernelId"] = "different owner"
                    else:
                        record["createdAt"] -= 1.0
                    path.write_text(json.dumps(record), encoding="utf-8")
                    before = path.read_bytes()
                    with self.assertRaises(job_store.JobStoreError):
                        kernel._persist(job, force=True)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(job.status, "queued")
                finally:
                    job._lease.__exit__(None, None, None)
                    job._lease = None

    def test_worker_transport_payload_is_cleaned_after_wait_or_spawn_failure_only(self):
        for mode in ("complete", "spawn_failure", "event_failure", "existing_transport"):
            with self.subTest(mode=mode):
                kernel = OperationKernel(self.root)
                with mock.patch("core.operation_kernel.threading.Thread.start"):
                    job = kernel.start("fixture", {"requestId": f"transport_{mode}", "graph": {"one": "request"}})
                note = job.workdir / "user-artifact.txt"
                note.write_bytes(b"preserve unrelated workspace output")
                payload = job.workdir / "payload.json"
                if mode == "existing_transport":
                    payload.write_bytes(b"pre-existing transport is not owned by this invocation")
                process = mock.Mock(stdout=io.StringIO('{"type":"task.result","payload":{"outputs":[]}}\n'))
                process.poll.return_value = None
                def waited(*args, **kwargs):
                    self.assertTrue(payload.exists(), "transport removed before its child was reaped")
                    return 0
                process.wait.side_effect = waited
                def spawn(*args, **kwargs):
                    self.assertTrue(payload.exists())
                    if mode == "spawn_failure":
                        raise OSError("fixture spawn failed")
                    return process
                with mock.patch("core.operation_kernel.subprocess.Popen", side_effect=spawn) as popen, \
                     mock.patch("core.operation_kernel.threading.Thread"):
                    if mode == "event_failure":
                        with mock.patch.object(kernel, "_consume", side_effect=ValueError("fixture event failed")):
                            kernel._run(job)
                    else:
                        kernel._run(job)
                self.assertIsNone(job._lease)
                self.assertEqual(note.read_bytes(), b"preserve unrelated workspace output")
                self.assertEqual(job.status, "completed" if mode == "complete" else "failed")
                if mode == "existing_transport":
                    popen.assert_not_called()
                    self.assertEqual(payload.read_bytes(), b"pre-existing transport is not owned by this invocation")
                else:
                    self.assertFalse(payload.exists())
                    self.assertTrue((job.workdir / "events.jsonl").exists())
                    self.assertTrue((job.workdir / "stderr.log").exists())
                    if mode != "spawn_failure":
                        process.wait.assert_called_once()
                        self.assertTrue(process.stdout.closed)
                if mode == "event_failure":
                    process.terminate.assert_called_once()

    def test_failed_cleanup_holds_owner_and_payload_until_explicit_cancel_reaps(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"), \
             mock.patch("core.operation_kernel.time.time", return_value=time.time() - 100 * 86400):
            job = kernel.start("fixture", {"requestId": "cleanup_retry"})
        lease = job._lease
        process = mock.Mock(stdout=io.StringIO('{"type":"task.progress","payload":{"fraction":0.5}}\n'))
        process.poll.return_value = None
        process.terminate.side_effect = [OSError("terminate denied"), OSError("terminate still denied"), None]
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), subprocess.TimeoutExpired("fixture", 5), 0]
        process.kill.side_effect = [OSError("kill denied"), OSError("kill still denied")]
        payload = job.workdir / "payload.json"
        try:
            with mock.patch("core.operation_kernel.subprocess.Popen", return_value=process), \
                 mock.patch("core.operation_kernel.threading.Thread"), \
                 mock.patch.object(kernel, "_consume", side_effect=ValueError("fixture consumer failed")):
                kernel._run(job)
            self.assertEqual(job.status, "failed")
            self.assertTrue(job._cleanup_pending)
            self.assertFalse(job._process_reaped)
            self.assertIs(job._lease, lease)
            self.assertTrue(payload.exists(), "unreaped worker's transport must stay available")
            before = self.snapshot()
            observer = OperationKernel(self.root)
            self.assertEqual(observer.pruned, 0, "a terminal record with a live owner lease must not be pruned")
            self.assertEqual(self.snapshot(), before)
            retry = kernel.cancel(job.job_id)
            self.assertFalse(retry["ok"])
            self.assertTrue(retry["cleanupPending"])
            self.assertIn("kill still denied", retry["error"])
            self.assertIs(job._lease, lease)
            self.assertTrue(payload.exists())
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(kernel.cancel(job.job_id), {"ok": True, "cancelled": False, "cleanupPending": False})
            self.assertTrue(job._process_reaped)
            self.assertFalse(job._cleanup_pending)
            self.assertIsNone(job._lease)
            self.assertFalse(payload.exists())
            self.assertEqual(self.snapshot(), before, "cleanup must not rewrite the existing terminal")
            self.assertTrue(all(call.kwargs == {"timeout": 5} for call in process.wait.call_args_list))
            self.assertEqual(observer._prune_history(), 1, "retention may proceed only after confirmed reap")
        finally:
            process.terminate.side_effect = None
            process.kill.side_effect = None
            process.wait.side_effect = None
            process.wait.return_value = 0
            kernel._cleanup_process(job)
            kernel._release_job_lease(job)

    def test_terminal_runner_in_normal_shutdown_is_not_cleaned_or_released_by_cancel(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"):
            job = kernel.start("fixture", {"requestId": "normal_shutdown"})
        process = mock.Mock(stdout=io.StringIO())
        process.poll.return_value = 0
        process.wait.return_value = 0
        job.process = process
        job._process_reaped = False
        lease = job._lease
        try:
            job.update(status="completed", result={"outputs": []})
            kernel._persist(job, force=True)
            self.assertFalse(job._cleanup_pending)
            with mock.patch.object(kernel, "_cleanup_process", wraps=kernel._cleanup_process) as cleanup:
                self.assertEqual(kernel.cancel(job.job_id), {"ok": True, "cancelled": False})
                cleanup.assert_not_called()
            process.wait.assert_not_called()
            process.terminate.assert_not_called()
            process.kill.assert_not_called()
            self.assertIs(job._lease, lease)
        finally:
            kernel._cleanup_process(job)
            kernel._release_job_lease(job)

    def test_entire_catalog_is_validated_before_recovery_or_pruning(self):
        stale = write_record(self.jobs, job_record("a_stale", status="running"))
        old = write_record(self.jobs, job_record("b_old", createdAt=time.time() - 100 * 86400))
        broken = self.jobs / "z_broken.json"
        broken.write_bytes(b'{"schemaVersion": 999}')
        before = self.snapshot()
        with self.assertRaises(job_store.JobStoreError):
            OperationKernel(self.root)
        self.assertEqual(before, self.snapshot())
        self.assertTrue(stale.exists() and old.exists())

    def test_start_never_overwrites_existing_terminal_old_corrupt_or_future_jobs(self):
        kernel = OperationKernel(self.root)
        path = self.jobs / "collision.json"
        for raw in (json.dumps(job_record("collision")).encode(), b'{"jobId":"collision"}',
                    b'{broken', b'{"schemaVersion": 99,"jobId":"collision"}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with mock.patch("core.operation_kernel.threading.Thread.start") as start, \
                     self.assertRaises(job_store.JobStoreError):
                    kernel.start("fixture", {"requestId": "collision"})
                start.assert_not_called()
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse((self.root / "bridge-jobs/collision").exists())

    def test_active_memory_cannot_hide_or_overwrite_a_corrupt_authoritative_record(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"):
            job = kernel.start("fixture", {"requestId": "active"})
        try:
            path = self.jobs / "active.json"
            path.write_bytes(b'{broken')
            with self.assertRaises(job_store.JobStoreError):
                kernel.get("active")
            job.update(message="new progress")
            with self.assertRaises(job_store.JobStoreError):
                kernel._persist(job, force=True)
            self.assertEqual(path.read_bytes(), b'{broken')
        finally:
            job._lease.__exit__(None, None, None)
            job._lease = None

    def test_ownerless_audio_queue_survives_recovery_and_retention(self):
        self.write_group(createdAt=time.time() - 100 * 86400)
        before = self.snapshot()
        kernel = OperationKernel(self.root)
        self.assertEqual((kernel.recovered, kernel.pruned), (0, 0))
        self.assertEqual(self.snapshot(), before)

    def test_single_queued_cancel_preserves_sibling_and_primary_payload_anchor(self):
        first, second = self.write_group()
        kernel = OperationKernel(self.root)
        self.assertEqual(kernel.cancel(first["jobId"]), {"ok": True, "cancelled": True})
        records = job_store.read_jobs(self.jobs)
        self.assertEqual(records[first["jobId"]]["status"], "cancelled")
        self.assertEqual(records[second["jobId"]]["status"], "queued")
        resolved = job_store.resolve_payload(records[second["jobId"]], self.jobs)
        self.assertEqual(resolved["output"], first["payload"]["output"])
        self.assertEqual(resolved["tasks"], [{**second["audio"]["request"], "taskId": second["jobId"]}])
        self.assertFalse(list(self.jobs.glob("*.cancel")))

    def test_manual_audio_history_is_not_subject_to_kernel_ttl_or_count(self):
        kernel = OperationKernel(self.root)
        first, second = self.write_group(status="completed", createdAt=time.time() - 100 * 86400)
        second.update(status="queued", finishedAt=None)
        write_record(self.jobs, second)
        with mock.patch("core.operation_kernel._retention_limits", return_value=(30, 1)):
            self.assertEqual(kernel._prune_history(), 0)
            second.update(status="completed", finishedAt=time.time())
            write_record(self.jobs, second)
            write_record(self.jobs, job_record("recent_agent"))
            before = self.snapshot()
            self.assertEqual(kernel._prune_history(), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(job_store.read_jobs(self.jobs)), 3)

    def test_recent_child_protects_an_old_primary_reference(self):
        kernel = OperationKernel(self.root)
        first, second = self.write_group(status="completed", createdAt=time.time() - 100 * 86400)
        second["createdAt"] = time.time()
        write_record(self.jobs, second)
        with mock.patch("core.operation_kernel._retention_limits", return_value=(30, 0)):
            self.assertEqual(kernel._prune_history(), 0)
        self.assertEqual(len(job_store.read_jobs(self.jobs)), 2)

    def test_agent_retention_rename_failure_preserves_the_original_record(self):
        kernel = OperationKernel(self.root)
        write_record(self.jobs, job_record("old_agent", createdAt=time.time() - 100 * 86400))
        before = self.snapshot()
        rename = Path.rename
        calls = 0
        def fail_second(path, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("fixture rename failure")
            return rename(path, target)
        with mock.patch("core.operation_kernel._retention_limits", return_value=(30, 0)), \
             mock.patch.object(Path, "rename", fail_second), self.assertRaises(PermissionError):
            kernel._prune_history()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.jobs.glob("*.removing")))

    def test_incomplete_removal_is_explicit_instead_of_empty(self):
        (self.jobs / ".old.nonce.removing").write_bytes(b"preserved temporary transaction")
        with self.assertRaisesRegex(job_store.JobStoreError, "incomplete"):
            job_store.read_jobs(self.jobs)

    def test_reference_cannot_change_the_primary_command(self):
        first, child = self.write_group()
        child["command"] = "infer_workflow"
        write_record(self.jobs, child)
        before = self.snapshot()
        with self.assertRaises(job_store.JobStoreError):
            job_store.read_jobs(self.jobs)
        self.assertEqual(before, self.snapshot())

    def test_explicit_inference_params_version_is_current_integer_only(self):
        primary, _ = audio_group()
        self.assertIs(job_store.validate_record(primary), primary)
        primary["payload"]["inferenceParamsVersion"] = 3
        self.assertIs(job_store.validate_record(primary), primary)
        for version in (2, 4, 3.0, "3", True, None):
            with self.subTest(version=version):
                primary["payload"]["inferenceParamsVersion"] = version
                with self.assertRaisesRegex(job_store.JobStoreError, "inferenceParamsVersion"):
                    job_store.validate_record(primary)

    def test_bom_and_duplicate_json_keys_are_rejected_without_repair(self):
        raw = json.dumps(job_record("raw")).encode("utf-8")
        path = self.jobs / "raw.json"
        for invalid in (b"\xef\xbb\xbf" + raw, raw[:-1] + b', "schemaVersion": 2}'):
            with self.subTest(raw=invalid):
                path.write_bytes(invalid)
                with self.assertRaises(job_store.JobStoreError):
                    job_store.read_jobs(self.jobs)
                self.assertEqual(path.read_bytes(), invalid)

    def test_stats_validate_bad_records_outside_the_display_limit(self):
        write_record(self.jobs, job_record("recent", status="failed", error="OOM"))
        (self.jobs / "old.json").write_bytes(b'{"schemaVersion": 1}')
        before = self.snapshot()
        with self.assertRaises(job_store.JobStoreError):
            failure_stats.collect(self.root, limit=1)
        self.assertEqual(before, self.snapshot())

    def test_catalog_reader_cannot_observe_a_half_created_group(self):
        first, second = audio_group()
        partial = threading.Event()
        publish = threading.Event()
        read_started = threading.Event()
        finished = threading.Event()
        errors = []
        result = []
        def writer():
            try:
                with job_store.catalog_lock(self.jobs):
                    write_record(self.jobs, first)
                    partial.set()
                    if not publish.wait(3):
                        raise RuntimeError("fixture publication timeout")
                    write_record(self.jobs, second)
            except BaseException as error:
                errors.append(error)
        def reader():
            try:
                read_started.set()
                result.append(job_store.read_jobs(self.jobs))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()
        worker = threading.Thread(target=writer)
        observer = threading.Thread(target=reader)
        worker.start()
        self.assertTrue(partial.wait(3))
        observer.start()
        self.assertTrue(read_started.wait(3))
        try:
            self.assertFalse(finished.wait(0.1))
        finally:
            publish.set()
            worker.join(3)
            observer.join(3)
        self.assertFalse(worker.is_alive() or observer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(set(result[0]), {first["jobId"], second["jobId"]})

    def test_audio_retry_keeps_batch_root_and_uses_new_global_event_ids(self):
        import agent_bridge
        first, second = self.write_group(status="failed")
        first["payload"]["device"] = "cuda"
        second["result"] = {"recovery": {"retryable": True, "actions": [{"action": "switch_device", "to": "cpu"}]}}
        write_record(self.jobs, first)
        write_record(self.jobs, second)
        before = self.snapshot()
        bridge = agent_bridge.Bridge(None, 0, self.root)
        started = mock.Mock(job_id="audio_2-r1")
        started.snapshot.return_value = {"status": "queued"}
        with mock.patch.object(bridge.jobs, "start", return_value=started) as start:
            self.assertTrue(bridge.job_retry("audio_2")["ok"])
        payload = start.call_args.args[1]
        self.assertEqual(payload["device"], "cpu")
        self.assertEqual(payload["output"], first["payload"]["output"])
        self.assertEqual(payload["tasks"][0]["output"], second["audio"]["request"]["output"])
        self.assertEqual((payload["requestId"], payload["taskId"], payload["tasks"][0]["taskId"]), ("audio_2-r1",) * 3)
        self.assertEqual(self.snapshot(), before)

    def test_bridge_failure_stats_uses_its_explicit_root(self):
        import agent_bridge
        write_record(self.jobs, job_record("failed", status="failed", error="CUDA out of memory"))
        other = self.root / "other"
        with mock.patch.dict(os.environ, {"MEDIAFORGE_DATA_ROOT": str(other), "MEDIAFORGE_DATA_DIR": str(other)}):
            bridge = agent_bridge.Bridge(None, 0, self.root)
            result = bridge.call("system.failure_stats", {})
        self.assertEqual(result["failed"], 1)

    def test_real_persistence_updates_updated_at_without_changing_creation(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"), \
             mock.patch("core.operation_kernel.time.time", return_value=1000.0):
            job = kernel.start("fixture", {"requestId": "clock"})
        try:
            with mock.patch("core.operation_kernel.time.time", return_value=2000.0):
                job.update(message="actual progress")
                kernel._persist(job, force=True)
            record = job_store.read_jobs(self.jobs)["clock"]
            self.assertEqual((record["createdAt"], record["updatedAt"]), (1000.0, 2000.0))
            before = self.snapshot()
            with self.assertRaises(job_store.JobStoreError):
                job.update(created_at=999.0)
            self.assertEqual(before, self.snapshot())
        finally:
            job._lease.__exit__(None, None, None)
            job._lease = None

    def test_logs_node_progress_and_partial_outputs_survive_failure(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"):
            job = kernel.start("fixture", {"requestId": "events"})
        try:
            kernel._consume(job, {"type": "task.log", "payload": {"record": {"id": "log-1", "message": "model log"}}})
            kernel._consume(job, {"type": "workflow_execution", "payload": {
                "version": 1, "event": "node_progress", "nodeId": "node-a", "nodeProgress": 0.5,
            }})
            job.result = {"outputs": [{"path": "partial.wav"}]}
            kernel._consume(job, {"type": "error", "payload": {"message": "decoder failed", "recovery": {"retryable": False}}})
            saved = job_store.read_jobs(self.jobs)[job.job_id]
            self.assertEqual(saved["result"]["outputs"], [{"path": "partial.wav"}])
            self.assertEqual(saved["runtime"]["nodes"]["node-a"]["status"], "failed")
            self.assertEqual(saved["runtime"]["nodes"]["node-a"]["progress"], 50.0)
            self.assertEqual(saved["logs"][0]["message"], "model log")
            self.assertIsNotNone(saved["finishedAt"])
        finally:
            job._lease.__exit__(None, None, None)
            job._lease = None

    def test_result_outputs_are_the_only_persisted_file_inventory(self):
        kernel = OperationKernel(self.root)
        with mock.patch("core.operation_kernel.threading.Thread.start"):
            job = kernel.start("fixture", {"requestId": "outputs"})
        try:
            kernel._consume(job, {"type": "task.result", "payload": {"files": ["out.wav"], "outputs": [{"path": "out.wav"}]}})
            result = job_store.read_jobs(self.jobs)[job.job_id]["result"]
            self.assertEqual(result["outputs"], [{"path": "out.wav"}])
            self.assertNotIn("files", result)
        finally:
            job._lease.__exit__(None, None, None)
            job._lease = None


if __name__ == "__main__":
    unittest.main()
