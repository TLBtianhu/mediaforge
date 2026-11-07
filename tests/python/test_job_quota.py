"""Shared compute admission, durable waiting and lifetime lease boundaries."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import gc
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import weakref

from . import _bootstrap as _worker_test_bootstrap
from ._job_fixtures import audio_group, job_record, write_record
from core import job_quota, job_store
from core.operation_kernel import OperationKernel, _atomic_write
from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock
from shared.worker_subprocess import hidden_subprocess_kwargs


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mediaforge-quota-")
        self.root = Path(self.temporary.name)
        self.jobs = self.root / "agent-jobs"
        self.kernels = []
        self.guards = []
        self.gates = []

    def tearDown(self):
        for event in self.gates:
            event.set()
        for kernel in self.kernels:
            kernel.close()
        for kernel in self.kernels:
            self.wait_for(lambda: all(job._lease is None for job in kernel._jobs.values()), timeout=5)
        for guard in reversed(self.guards):
            guard.__exit__(None, None, None)
        self.temporary.cleanup()

    def wait_for(self, predicate, *, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("timed out waiting for isolated job fixture")
            threading.Event().wait(0.01)

    def kernel(self):
        kernel = OperationKernel(self.root)
        self.kernels.append(kernel)
        return kernel

    def settings(self, limit):
        path = self.root / "settings" / "app.json"
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix(".writing")
        temporary.write_text(json.dumps({"maxConcurrentSeparations": limit}), encoding="utf-8")
        temporary.replace(path)

    def hold_slot(self, index=0):
        with job_store.catalog_lock(self.jobs):
            guard = runtime_lock(self.jobs / ".quota" / f"global-{index}.lock", timeout=0)
            guard.__enter__()
        self.guards.append(guard)
        return guard

    def release_slot(self, guard):
        self.guards.remove(guard)
        guard.__exit__(None, None, None)

    def blocked_worker(self):
        started = []
        gates = {}
        lock = threading.Lock()

        def worker(kernel, job):
            gate = threading.Event()
            with lock:
                self.gates.append(gate)
                gates[job.job_id] = gate
                started.append(job.job_id)
            if not gate.wait(5):
                raise TimeoutError("isolated fixture was not released")
            job.update(status="completed", result={"outputs": []}, fraction=1)
            kernel._persist(job, force=True)

        return worker, started, gates

    def test_shared_settings_contract_preserves_every_invalid_file(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "jobs" / "quota-contract.json"
        settings = self.root / "settings" / "app.json"
        settings.parent.mkdir()
        for case in json.loads(fixture.read_text(encoding="utf-8"))["settingsCases"]:
            with self.subTest(case=case["name"]):
                settings.unlink(missing_ok=True)
                if not case.get("fileMissing"):
                    settings.write_bytes(case["raw"].encode("utf-8"))
                before = settings.read_bytes() if settings.exists() else None
                if case.get("invalid"):
                    with self.assertRaises(job_store.JobStoreError):
                        job_quota.read_limit(self.root)
                else:
                    self.assertEqual(job_quota.read_limit(self.root), case["expected"])
                self.assertEqual(settings.read_bytes() if settings.exists() else None, before)

    def test_only_manifest_compute_commands_reserve_capacity(self):
        contract = json.loads(job_quota.CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(job_quota.MAX_COMPUTE_WORKERS, 4)
        for command in contract["commands"]:
            self.assertTrue(job_quota.applies(command))
        for command in ("model_download", "health", "runtime_info", "list_models", "media_capabilities", "fixture"):
            self.assertFalse(job_quota.applies(command))

    def test_reparse_settings_are_rejected_before_reading(self):
        path = self.root / "settings" / "app.json"
        path.parent.mkdir()
        original = b'{"maxConcurrentSeparations": 4}'
        path.write_bytes(original)
        metadata = mock.Mock(st_mode=0, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=metadata):
            with self.assertRaisesRegex(job_store.JobStoreError, "reparse"):
                job_quota.read_limit(self.root)
        self.assertEqual(path.read_bytes(), original)

    def test_shrink_counts_busy_slots_above_new_limit_and_release_is_idempotent(self):
        high = self.hold_slot(3)
        with job_store.catalog_lock(self.jobs):
            self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            low = job_quota.try_acquire(self.jobs, 2)
            self.assertEqual(low.index, 0)
            self.assertIsNone(job_quota.try_acquire(self.jobs, 2))
            low.release()
            low.release()
        self.release_slot(high)
        with job_store.catalog_lock(self.jobs):
            again = job_quota.try_acquire(self.jobs, 1)
            self.assertEqual(again.index, 0)
            again.release()
        self.assertEqual(len(list((self.jobs / ".quota").glob("global-*.lock"))), 4)

    def test_windows_sharing_conflict_is_busy_but_acl_denial_releases_prior_probes(self):
        for winerror in (32, 33, 5):
            with self.subTest(winerror=winerror):
                released = []
                error = PermissionError("isolated Windows open failure")
                error.winerror = winerror

                @contextmanager
                def fake_lock(path, **kwargs):
                    index = int(path.stem.rsplit("-", 1)[1])
                    if index == 1:
                        raise error
                    try:
                        yield True
                    finally:
                        released.append(index)

                with mock.patch.object(job_quota, "runtime_lock", side_effect=fake_lock):
                    if winerror == 5:
                        with self.assertRaises(PermissionError) as raised:
                            job_quota.try_acquire(self.jobs, 1)
                        self.assertIs(raised.exception, error)
                        self.assertEqual(released, [0])
                    else:
                        self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
                        self.assertCountEqual(released, [0, 2, 3])

    @unittest.skipUnless(os.name == "nt", "Windows CRT/Win32 error classification")
    def test_crt_permission_error_probes_native_sharing_without_swallowing_acl_or_missing(self):
        original = PermissionError(errno.EACCES, "isolated CRT error", str(self.jobs / ".quota" / "global-0.lock"))
        self.assertIsNone(getattr(original, "winerror", None))
        with mock.patch.object(job_quota, "_probe_windows_open_conflict", return_value=True) as probe:
            self.assertTrue(job_quota.is_busy(original))
            probe.assert_called_once_with(original.filename)
        for code in (5, 2, 3):
            import ctypes
            native = ctypes.WinError(code)
            with self.subTest(winerror=code), \
                 mock.patch.object(job_quota, "_probe_windows_open_conflict", side_effect=native):
                with self.assertRaises(OSError) as raised:
                    job_quota.is_busy(original)
                self.assertIs(raised.exception, native)
        explicit_acl = PermissionError("isolated ACL error")
        explicit_acl.winerror = 5
        with mock.patch.object(job_quota, "_probe_windows_open_conflict") as probe:
            self.assertFalse(job_quota.is_busy(explicit_acl))
            probe.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows exclusive CreateFileW handle")
    def test_real_native_exclusive_handle_is_busy_then_released_after_child_exit(self):
        path = self.jobs / ".quota" / "global-3.lock"
        path.parent.mkdir(parents=True)
        path.touch()
        source = """
import ctypes
from ctypes import wintypes
import sys
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
create = kernel.CreateFileW
create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
create.restype = wintypes.HANDLE
close = kernel.CloseHandle
close.argtypes = (wintypes.HANDLE,)
close.restype = wintypes.BOOL
handle = create(sys.argv[1], 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
if handle == ctypes.c_void_p(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
try:
    print('ready', flush=True)
    sys.stdin.readline()
finally:
    close(handle)
"""
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", source, str(path)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **hidden_subprocess_kwargs(),
        )
        ready = threading.Event()
        lines = []
        def handshake():
            lines.append(process.stdout.readline().strip())
            ready.set()
        reader = threading.Thread(target=handshake, daemon=True)
        reader.start()
        try:
            self.assertTrue(ready.wait(5), "isolated native child handshake timed out")
            self.assertEqual(lines, ["ready"])
            with self.assertRaises(PermissionError) as caught:
                path.open("a+b")
            self.assertTrue(job_quota.is_busy(caught.exception))
            with job_store.catalog_lock(self.jobs):
                self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            process.communicate("exit\n", timeout=5)
            self.assertEqual(process.returncode, 0)
            with job_store.catalog_lock(self.jobs):
                lease = job_quota.try_acquire(self.jobs, 1)
                self.assertIsNotNone(lease)
                lease.release()
            # A CRT failure can race with the native holder's release.
            self.assertTrue(job_quota.is_busy(caught.exception))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            reader.join(timeout=1)

    def test_real_hidden_process_slot_excludes_parent_until_process_exit(self):
        source = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from core import job_store
from core.worker_runtime_lock import runtime_lock
jobs = Path(sys.argv[1])
with job_store.catalog_lock(jobs):
    guard = runtime_lock(jobs / '.quota' / 'global-3.lock', timeout=0)
    guard.__enter__()
try:
    print('ready', flush=True)
    sys.stdin.readline()
finally:
    guard.__exit__(None, None, None)
"""
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", source, str(self.jobs), str(job_quota.CONTRACT_PATH.parent.parent / "python")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **hidden_subprocess_kwargs(),
        )
        ready = threading.Event()
        lines = []
        def handshake():
            lines.append(process.stdout.readline().strip())
            ready.set()
        reader = threading.Thread(target=handshake, daemon=True)
        reader.start()
        try:
            self.assertTrue(ready.wait(5), "isolated child handshake timed out")
            self.assertEqual(lines, ["ready"])
            with job_store.catalog_lock(self.jobs):
                self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            process.communicate("exit\n", timeout=5)
            self.assertEqual(process.returncode, 0)
            with job_store.catalog_lock(self.jobs):
                lease = job_quota.try_acquire(self.jobs, 1)
                self.assertIsNotNone(lease)
                lease.release()
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            reader.join(timeout=1)

    def test_waiting_compute_job_has_no_owner_workdir_or_spawn_and_cancel_is_final(self):
        held = self.hold_slot()
        kernel = self.kernel()
        with mock.patch.object(OperationKernel, "_run_worker") as worker:
            job = kernel.start("run_operation", {"requestId": "waiting", "graph": {"fixture": True}})
            kernel._dispatch_once()
            record = job_store.read_jobs(self.jobs)[job.job_id]
            self.assertEqual(record["status"], "queued")
            self.assertIsNone(record["owner"])
            self.assertIsNone(record["startedAt"])
            self.assertFalse(job.workdir.exists())
            self.assertIsNone(job._quota_lease)
            self.assertEqual(kernel.cancel(job.job_id), {"ok": True, "cancelled": True})
            self.release_slot(held)
            kernel._dispatch_once()
            self.assertEqual(kernel.get(job.job_id).status, "cancelled")
            worker.assert_not_called()
            self.assertFalse(job.workdir.exists())

    def test_two_kernels_restore_agent_queue_once_and_leave_manual_audio_queued(self):
        held = self.hold_slot()
        write_record(self.jobs, job_record("restored", command="run_operation", status="queued"))
        manual = audio_group()
        for record in manual:
            write_record(self.jobs, record)
        before = [(self.jobs / f"{record['jobId']}.json").read_bytes() for record in manual]
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            first, second = self.kernel(), self.kernel()
            self.release_slot(held)
            self.wait_for(lambda: len(started) == 1)
            first._dispatch_once()
            second._dispatch_once()
            self.assertEqual(started, ["restored"])
            active = job_store.read_jobs(self.jobs)["restored"]
            self.assertIn(active["owner"]["kernelId"], {first._owner_id, second._owner_id})
            self.assertEqual(active["status"], "running")
            self.assertIsNotNone(active["startedAt"])
            self.assertEqual([(self.jobs / f"{record['jobId']}.json").read_bytes() for record in manual], before)
            gates["restored"].set()
            self.wait_for(lambda: first.get("restored").status == "completed")

    def test_shared_limit_serializes_two_kernels_then_frees_capacity(self):
        first, second = self.kernel(), self.kernel()
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            a = first.start("audio_tools", {"requestId": "first"})
            self.wait_for(lambda: started == ["first"])
            b = second.start("run_operation", {"requestId": "second"})
            second._dispatch_once()
            self.assertEqual(second.get(b.job_id).status, "queued")
            self.assertEqual(started, ["first"])
            gates[a.job_id].set()
            self.wait_for(lambda: len(started) == 2)
            self.assertEqual(started, ["first", "second"])
            self.assertIsNone(a._quota_lease)
            gates[b.job_id].set()
            self.wait_for(lambda: second.get(b.job_id).status == "completed")

    def test_noncompute_download_bypasses_occupied_compute_quota(self):
        self.hold_slot()
        kernel = self.kernel()
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            job = kernel.start("model_download", {"requestId": "download"})
            self.wait_for(lambda: started == [job.job_id])
            self.assertIsNone(job._quota_lease)
            self.assertIsNotNone(job._lease)
            gates[job.job_id].set()
            self.wait_for(lambda: job.status == "completed")

    def test_setting_increase_allows_second_live_compute_without_restarting(self):
        first, second = self.kernel(), self.kernel()
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            a = first.start("audio_tools", {"requestId": "first"})
            self.wait_for(lambda: started == [a.job_id])
            b = second.start("run_operation", {"requestId": "second"})
            second._dispatch_once()
            self.assertEqual(second.get(b.job_id).status, "queued")
            self.settings(2)
            self.wait_for(lambda: len(started) == 2)
            records = job_store.read_jobs(self.jobs)
            self.assertEqual([records[key]["status"] for key in (a.job_id, b.job_id)], ["running", "running"])
            for gate in gates.values():
                gate.set()
            self.wait_for(lambda: all(first.get(key).status == "completed" for key in (a.job_id, b.job_id)))

    def test_bridge_wait_reads_remote_claim_and_reports_queued_on_timeout(self):
        import agent_bridge

        first = self.kernel()
        with mock.patch.object(first, "_ensure_dispatcher"):
            original = first.start("run_operation", {"requestId": "other_kernel"})
        bridge = agent_bridge.Bridge.__new__(agent_bridge.Bridge)
        bridge.jobs = first
        self.assertEqual(bridge._wait(original, 0)["status"], "queued")
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            second = self.kernel()
            self.wait_for(lambda: bool(started))
            gates[original.job_id].set()
            result = bridge._wait(original, 2)
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(first._jobs[original.job_id].status, "queued", "the origin's stale in-memory view is not authoritative")
            self.assertEqual(result["owner"]["kernelId"], second._owner_id)

    def test_invalid_settings_reject_new_compute_job_without_mutating_original(self):
        kernel = self.kernel()
        path = self.root / "settings" / "app.json"
        path.parent.mkdir()
        original = b'{"maxConcurrentSeparations": false}'
        path.write_bytes(original)
        with self.assertRaises(job_store.JobStoreError):
            kernel.start("run_operation", {"requestId": "invalid_settings"})
        self.assertEqual(job_store.read_jobs(self.jobs), {})
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / "bridge-jobs").exists())

    def test_restarted_queue_with_bad_settings_preserves_pending_record(self):
        write_record(self.jobs, job_record("restored", status="queued", command="run_operation"))
        original = (self.jobs / "restored.json").read_bytes()
        self.settings(0)
        with self.assertRaises(job_store.JobStoreError):
            OperationKernel(self.root)
        self.assertEqual((self.jobs / "restored.json").read_bytes(), original)

    def test_close_stops_dispatcher_but_does_not_release_running_capacity(self):
        kernel = self.kernel()
        worker, started, gates = self.blocked_worker()
        with mock.patch.object(OperationKernel, "_run_worker", autospec=True, side_effect=worker):
            job = kernel.start("run_operation", {"requestId": "active"})
            self.wait_for(lambda: bool(started))
            thread = kernel._pump_thread
            kernel._ensure_dispatcher()
            self.assertIs(kernel._pump_thread, thread)
            kernel.close()
            self.assertFalse(thread.is_alive())
            with job_store.catalog_lock(self.jobs):
                self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            self.assertIsNotNone(job._lease)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                kernel.start("run_operation", {})
            gates[job.job_id].set()
            self.wait_for(lambda: job._lease is None)
            with job_store.catalog_lock(self.jobs):
                lease = job_quota.try_acquire(self.jobs, 1)
                self.assertIsNotNone(lease)
                lease.release()

    def test_waiting_dispatcher_does_not_keep_kernel_alive_or_recreate_root(self):
        self.hold_slot()
        kernel = self.kernel()
        kernel.start("run_operation", {"requestId": "unowned"})
        thread = kernel._pump_thread
        stop = kernel._pump_stop
        reference = weakref.ref(kernel)
        self.kernels.remove(kernel)
        del kernel
        gc.collect()
        self.wait_for(lambda: reference() is None)
        self.assertTrue(stop.is_set())
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_claim_write_failure_releases_owner_and_quota_without_replacing_queue(self):
        kernel = self.kernel()
        with mock.patch.object(kernel, "_ensure_dispatcher"):
            job = kernel.start("run_operation", {"requestId": "write_failure"})
        original = (self.jobs / f"{job.job_id}.json").read_bytes()
        with mock.patch("core.operation_kernel._atomic_write", side_effect=OSError("isolated write denied")):
            with self.assertRaises(OSError):
                kernel._try_claim_next()
        self.assertEqual((self.jobs / f"{job.job_id}.json").read_bytes(), original)
        with runtime_lock(kernel._lease_path(job.job_id), timeout=0):
            pass
        with job_store.catalog_lock(self.jobs):
            lease = job_quota.try_acquire(self.jobs, 1)
            self.assertIsNotNone(lease)
            lease.release()

    def test_spawn_failure_finishes_job_and_releases_both_leases(self):
        kernel = self.kernel()
        with mock.patch.object(kernel, "_ensure_dispatcher"):
            queued = kernel.start("run_operation", {"requestId": "spawn_failure"})
        job = kernel._try_claim_next()
        self.assertIs(job, queued)
        with mock.patch("core.operation_kernel.subprocess.Popen", side_effect=OSError("isolated spawn failed")):
            kernel._run(job)
        self.assertEqual(job.status, "failed")
        self.assertIsNone(job._lease)
        self.assertIsNone(job._quota_lease)
        self.assertFalse((job.workdir / "payload.json").exists())

    def test_unreaped_terminal_worker_holds_capacity_until_explicit_cleanup_succeeds(self):
        kernel = self.kernel()
        with mock.patch.object(kernel, "_ensure_dispatcher"):
            queued = kernel.start("run_operation", {"requestId": "cleanup_pending"})
        job = kernel._try_claim_next()
        self.assertIs(job, queued)
        process = mock.Mock(stdout=io.StringIO('{"type":"task.progress","payload":{"fraction":0.5}}\n'))
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        process.kill.side_effect = OSError("isolated kill denied")
        try:
            with mock.patch("core.operation_kernel.subprocess.Popen", return_value=process), \
                 mock.patch("core.operation_kernel.threading.Thread"), \
                 mock.patch.object(kernel, "_consume", side_effect=ValueError("isolated callback failed")):
                kernel._run(job)
            self.assertTrue(job._cleanup_pending)
            self.assertIsNotNone(job._lease)
            self.assertIsNotNone(job._quota_lease)
            self.assertTrue((job.workdir / "payload.json").exists())
            with job_store.catalog_lock(self.jobs):
                self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            self.assertEqual(kernel.cancel(job.job_id)["cleanupPending"], False)
            self.assertIsNone(job._lease)
            self.assertIsNone(job._quota_lease)
            self.assertFalse((job.workdir / "payload.json").exists())
            with job_store.catalog_lock(self.jobs):
                lease = job_quota.try_acquire(self.jobs, 1)
                self.assertIsNotNone(lease)
                lease.release()
        finally:
            process.kill.side_effect = None
            process.wait.side_effect = None
            process.wait.return_value = 0
            kernel._cleanup_process(job)
            kernel._release_job_lease(job)

    def recoverable_process(self, job_id, mode):
        kernel = self.kernel()
        with mock.patch.object(kernel, "_ensure_dispatcher"):
            queued = kernel.start("run_operation", {"requestId": job_id})
        job = kernel._try_claim_next()
        self.assertIs(job, queued)
        job.result = {"outputs": [{"path": "partial.wav"}]}
        kernel._persist(job, force=True)
        diagnostic = {"id": "recoverable-diagnostic", "message": "retrying decoder", "nodeId": "node-a"}
        events = [
            {"type": "error", "payload": {"recoverable": True, "message": "retrying decoder", "logRecord": diagnostic}},
            {"type": "task.progress", "payload": {"fraction": 0.75}},
            {"type": "task.result", "payload": {"outputs": [{"path": "finished.wav"}]}},
        ]
        process = mock.Mock(stdout=io.StringIO("\n".join(json.dumps(event) for event in events)))
        process.poll.return_value = None
        consumed = kernel._consume
        observations = []

        def observe(current, event):
            consumed(current, event)
            if event["type"] == "error":
                record = job_store.read_jobs(self.jobs)[job_id]
                self.assertEqual(record["status"], "running")
                self.assertIsNone(record["error"])
                self.assertIsNone(record["finishedAt"])
                self.assertEqual(record["result"], {"outputs": [{"path": "partial.wav"}]})
                self.assertEqual(record["logs"][0]["id"], diagnostic["id"])
                self.assertEqual(record["logs"][0]["event"], "error")
                self.assertEqual(record["logs"][0]["nodeId"], "node-a")
                observations.append(record)
                if mode == "cancel":
                    self.assertTrue(kernel.cancel(job_id)["cancelled"])
                elif mode == "watcher":
                    job.cancel_event.set()
                    kernel._watch_cancel(job, process)
                if mode != "complete":
                    self.assertEqual(job.status, "cancelled")
                    process.terminate.assert_called_once()
                with job_store.catalog_lock(self.jobs):
                    self.assertIsNone(job_quota.try_acquire(self.jobs, 1))

        def waited(*args, **kwargs):
            self.assertIsNotNone(job._lease)
            self.assertIsNotNone(job._quota_lease)
            self.assertTrue((job.workdir / "payload.json").exists())
            self.assertFalse(job._process_reaped)
            self.assertEqual(job.status, "completed" if mode == "complete" else "cancelled")
            with job_store.catalog_lock(self.jobs):
                self.assertIsNone(job_quota.try_acquire(self.jobs, 1))
            return 0

        process.wait.side_effect = waited
        with mock.patch("core.operation_kernel.subprocess.Popen", return_value=process), \
             mock.patch("core.operation_kernel.threading.Thread"), \
             mock.patch.object(kernel, "_consume", side_effect=observe):
            kernel._run(job)
        self.assertEqual(len(observations), 1)
        record = job_store.read_jobs(self.jobs)[job_id]
        self.assertEqual(record["status"], "completed" if mode == "complete" else "cancelled")
        self.assertIsNone(record["error"])
        self.assertIsNotNone(record["finishedAt"])
        self.assertEqual(record["result"]["outputs"][0]["path"], "finished.wav" if mode == "complete" else "partial.wav")
        self.assertEqual(record["logs"][0]["id"], diagnostic["id"])
        self.assertTrue(job._process_reaped)
        self.assertIsNone(job._lease)
        self.assertIsNone(job._quota_lease)
        self.assertTrue(process.stdout.closed)
        process.wait.assert_called_once()
        with job_store.catalog_lock(self.jobs):
            lease = job_quota.try_acquire(self.jobs, 1)
            self.assertIsNotNone(lease)
            lease.release()

    def test_recoverable_error_continues_to_result_with_durable_diagnostic_and_reap_scoped_quota(self):
        self.recoverable_process("recover_complete", "complete")

    def test_recoverable_error_remains_cancellable_until_process_is_reaped(self):
        self.recoverable_process("recover_cancel", "cancel")

    def test_recoverable_error_does_not_disable_the_cancel_watcher(self):
        self.recoverable_process("recover_watcher", "watcher")

    def test_only_boolean_true_marks_an_error_recoverable(self):
        kernel = self.kernel()
        for value in (False, "true", 1, None):
            with self.subTest(recoverable=value):
                with mock.patch.object(kernel, "_ensure_dispatcher"):
                    kernel.start("run_operation", {"requestId": f"fatal_{type(value).__name__}"})
                job = kernel._try_claim_next()
                try:
                    kernel._consume(job, {"type": "error", "payload": {"message": "fatal error", "recoverable": value}})
                    self.assertEqual(job.status, "failed")
                    self.assertEqual(job.error, "fatal error")
                    self.assertIsNotNone(job.finished_at)
                finally:
                    kernel._release_job_lease(job)

    def test_catalog_reader_serializes_with_real_atomic_snapshot_writer_for_ten_rounds(self):
        path = write_record(self.jobs, job_record("atomic_reader", status="running"))
        for index in range(10):
            with self.subTest(round=index):
                writer_entered = threading.Event()
                reader_entered = threading.Event()
                publish = threading.Event()
                reader_done = threading.Event()
                errors, observed = [], []
                replacement = job_record("atomic_reader", status="running", message=f"publication-{index}")

                def writer():
                    try:
                        with job_store.write_lock(self.jobs, "atomic_reader"):
                            writer_entered.set()
                            if not publish.wait(2):
                                raise TimeoutError("isolated writer was not released")
                            _atomic_write(path, json.dumps(replacement))
                    except BaseException as error:
                        errors.append(error)

                def reader():
                    try:
                        with job_store.catalog_lock(self.jobs):
                            reader_entered.set()
                            observed.append(job_store.read_jobs_unlocked(self.jobs)["atomic_reader"])
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        reader_done.set()

                write_thread = threading.Thread(target=writer, daemon=True)
                read_thread = threading.Thread(target=reader, daemon=True)
                write_thread.start()
                try:
                    self.assertTrue(writer_entered.wait(2))
                    read_thread.start()
                    self.assertTrue(reader_entered.wait(2))
                    self.assertFalse(reader_done.wait(0.02), "catalog-only readers must wait for the snapshot writer")
                finally:
                    publish.set()
                    write_thread.join(timeout=3)
                    if read_thread.ident is not None:
                        read_thread.join(timeout=3)
                self.assertFalse(write_thread.is_alive())
                self.assertFalse(read_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(observed, [replacement])


if __name__ == "__main__":
    unittest.main()
