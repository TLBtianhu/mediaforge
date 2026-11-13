"""Exercise the real executor, supervision, pipes and cross-process admission."""
from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap

from core import job_quota, job_store, operation_kernel
from core.worker_runtime_lock import RuntimeLockBusy
from shared.worker_subprocess import hidden_subprocess_kwargs


FIXTURE = Path(__file__).with_name("_kernel_process_fixture.py").resolve()
JOB_ID = "executor_lifecycle"


class ProcessProbe:
    """Observe fixture exit without reaping the executor's child on its behalf."""

    def __init__(self, pid: int, *, can_terminate: bool = False):
        self.pid = pid
        self.handle = None
        if os.name == "nt":
            import _winapi

            synchronize = 0x00100000
            terminate = 0x0001 if can_terminate else 0
            self.handle = _winapi.OpenProcess(synchronize | terminate, False, pid)

    def exited(self) -> bool:
        if self.handle is not None:
            import _winapi

            return _winapi.WaitForSingleObject(self.handle, 0) == _winapi.WAIT_OBJECT_0
        if sys.platform.startswith("linux"):
            try:
                process = Path(f"/proc/{self.pid}/stat").read_text(encoding="utf-8")
            except FileNotFoundError:
                return True
            return process.rpartition(")")[2].strip().startswith(("Z", "X"))
        # An already terminated orphan may still be visible as a zombie.
        result = subprocess.run(["/bin/ps", "-p", str(self.pid), "-o", "stat="],
                                capture_output=True, text=True, timeout=3,
                                **hidden_subprocess_kwargs())
        return result.returncode != 0 or result.stdout.strip().startswith(("Z", "X"))

    def kill(self) -> None:
        if self.handle is not None:
            import _winapi

            _winapi.TerminateProcess(self.handle, 9)
        else:
            os.kill(self.pid, signal.SIGKILL)

    def reaped(self) -> bool:
        if sys.platform.startswith("linux"):
            return not Path(f"/proc/{self.pid}").exists()
        return self.exited()

    def close(self) -> None:
        if self.handle is not None:
            import _winapi

            _winapi.CloseHandle(self.handle)
            self.handle = None


class ExecutorProcessLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        test_parent = FIXTURE.parents[2] / ".tmp"
        test_parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=test_parent, prefix="kernel-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.probes: list[ProcessProbe] = []
        self.kernel: operation_kernel.OperationKernel | None = None
        self.job: operation_kernel.Job | None = None
        self.owner: subprocess.Popen | None = None
        self.addCleanup(self._finish_fixtures)

        data_paths = {
            "MEDIAFORGE_DATA_ROOT": self.root,
            "MEDIAFORGE_DATA_DIR": self.root,
            "MEDIAFORGE_RUNTIME_ENVS_DIR": self.root / "runtime-envs",
            "MEDIAFORGE_MODEL_DIR": self.root / "models",
            "WEBVIEW2_USER_DATA_FOLDER": self.root / "webview",
        }
        self.enterContext(mock.patch.dict(os.environ, {key: str(value) for key, value in data_paths.items()}))

    def _finish_fixtures(self) -> None:
        # On a failed assertion unblock this test's own processes and close only
        # their captured handles. Descendants also have an independent timeout.
        for name in ("allow-reap", "stop-fixtures", "finish-worker"):
            (self.root / name).touch()
        try:
            if self.kernel is not None:
                self.kernel.close()
            if self.job is not None and self.job.process is not None:
                process = self.job.process
                if not self._until(lambda: self.job._lease is None, timeout=10):
                    self.kernel._cleanup_process(self.job)
                    self.kernel._release_job_lease(self.job)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            if self.owner is not None:
                try:
                    self.owner.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.owner.kill()
                    self.owner.wait(timeout=5)
            self._until(lambda: all(probe.exited() for probe in self.probes), timeout=6)
        finally:
            for probe in self.probes:
                probe.close()

    @staticmethod
    def _until(predicate, *, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.015)
        return True

    def _wait_for(self, predicate, message: str) -> None:
        self.assertTrue(self._until(predicate), message)

    def _probe(self, pid: int, *, can_terminate: bool = False) -> ProcessProbe:
        probe = ProcessProbe(pid, can_terminate=can_terminate)
        self.probes.append(probe)
        return probe

    def _tree(self) -> list[ProcessProbe]:
        ready = self.root / "tree.json"
        self._wait_for(ready.is_file, "worker and its two descendant fixtures never became ready")
        pids = json.loads(ready.read_text(encoding="utf-8"))
        self.assertEqual(len(set(pids)), 3)
        probes = [self._probe(pid) for pid in pids]
        self.assertTrue(all(not probe.exited() for probe in probes))
        return probes

    def _start(self, mode: str) -> list[ProcessProbe]:
        self.enterContext(mock.patch.object(operation_kernel, "WORKER", FIXTURE))
        self.enterContext(mock.patch.object(operation_kernel, "SUPERVISOR", FIXTURE))
        self.kernel = operation_kernel.OperationKernel(self.root, sys.executable)
        self.job = self.kernel.start("run_operation", {
            "requestId": JOB_ID, "fixtureRoot": str(self.root), "fixtureMode": mode,
        })
        return self._tree()

    def _quota_is_available(self) -> bool:
        jobs_root = self.root / "agent-jobs"
        with job_store.catalog_lock(jobs_root):
            lease = job_quota.try_acquire(jobs_root, job_quota.read_limit(self.root))
        if lease is None:
            return False
        lease.release()
        return True

    @contextmanager
    def _observe_release(self, tree: list[ProcessProbe]):
        quota, process = self.job._quota_lease, self.job.process
        self.assertIsNotNone(quota)
        self.assertIsNotNone(process)
        streams = [pipe for pipe in (process.stdin, process.stdout, process.stderr) if pipe is not None]
        self.assertGreaterEqual(len(streams), 2)
        release = job_quota.QuotaLease.release
        observed = []

        def record(lease):
            try:
                if lease is quota:
                    observed.append((all(probe.reaped() for probe in tree),
                                     all(stream.closed for stream in streams),
                                     process.returncode is not None))
            finally:
                release(lease)

        with mock.patch.object(job_quota.QuotaLease, "release", record):
            yield
            self._wait_for(lambda: bool(observed) and self.job._lease is None and self.job._quota_lease is None,
                           "execution never released the owner and compute leases")
        self.assertEqual(observed, [(True, True, True)], "quota released before process tree and pipes were reaped")

    def _assert_finished(self, expected: str) -> None:
        self.assertTrue(self.job._process_reaped)
        self.assertTrue(self._quota_is_available())
        self.assertFalse((self.job.workdir / "payload.json").exists())
        self.assertIn("grandchild diagnostics", (self.job.workdir / "stderr.log").read_text(encoding="utf-8"))
        stored = job_store.read_jobs(self.kernel.jobs_root)[JOB_ID]
        self.assertEqual(stored["status"], expected)

    def test_normal_result_waits_for_descendants_and_closes_pipes_before_releasing_quota(self) -> None:
        tree = self._start("normal")
        self._wait_for(lambda: self.job.status == "completed", "terminal worker result was not delivered")
        self.assertFalse(self._quota_is_available())
        with self._observe_release(tree):
            (self.root / "finish-worker").touch()
            self._wait_for((self.root / "cleanup-started").is_file, "root exit did not trigger tree cleanup")
            self.assertTrue(tree[0].exited())
            self.assertTrue(all(not descendant.exited() for descendant in tree[1:]))
            self.assertFalse(self._quota_is_available())
            (self.root / "allow-reap").touch()
        self._assert_finished("completed")
        self.assertEqual(self.job.result, {"outputs": {"value": 42}})

    def test_cancelling_reaps_the_tree_before_releasing_quota(self) -> None:
        tree = self._start("cancel")
        self.assertFalse(self._quota_is_available())
        requested = Future()

        def cancel() -> None:
            try:
                requested.set_result(self.kernel.cancel(JOB_ID))
            except BaseException as error:
                requested.set_exception(error)

        with self._observe_release(tree):
            thread = threading.Thread(target=cancel, daemon=True)
            thread.start()
            self._wait_for((self.root / "cleanup-started").is_file, "cancellation did not trigger tree cleanup")
            self.assertTrue(all(not probe.exited() for probe in tree))
            self.assertFalse(self._quota_is_available())
            (self.root / "allow-reap").touch()
        self.assertTrue(requested.result(timeout=10)["cancelled"])
        self._assert_finished("cancelled")

    def test_hard_parent_exit_preserves_fences_until_the_tree_is_reaped(self) -> None:
        with (self.root / "owner.stdout.log").open("w", encoding="utf-8") as stdout, \
             (self.root / "owner.stderr.log").open("w", encoding="utf-8") as stderr:
            self.owner = subprocess.Popen(
                [sys.executable, "-B", str(FIXTURE), "--test-owner", str(self.root)],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **hidden_subprocess_kwargs(),
            )
        tree = self._tree()
        owner_file = self.root / "owner.json"
        self._wait_for(owner_file.is_file, "owner PID was not reported")
        pids = json.loads(owner_file.read_text(encoding="utf-8"))
        owner = self._probe(pids["owner"], can_terminate=True)
        supervisor = self._probe(pids["supervisor"])
        self.assertFalse(self._quota_is_available())
        owner.kill()
        self._wait_for(owner.exited, "fixture executor did not exit abnormally")
        self.assertNotEqual(self.owner.wait(timeout=10), 0)
        self._wait_for((self.root / "cleanup-started").is_file, "parent EOF did not trigger supervised cleanup")
        self.assertTrue(all(not probe.exited() for probe in tree))
        self.assertFalse(self._quota_is_available(), "another executor could claim quota while the old tree was alive")
        self.kernel = operation_kernel.OperationKernel(self.root)
        self.assertEqual(self.kernel.recovered, 0)
        self.assertEqual(self.kernel.get(JOB_ID).status, "running")
        with self.assertRaises(RuntimeLockBusy):
            with job_store.owner_lock(self.kernel.jobs_root, JOB_ID):
                self.fail("another executor could take the live tree's owner lease")

        (self.root / "allow-reap").touch()
        self._wait_for(self._quota_is_available, "quota remained occupied after the parent and process tree exited")
        self.assertTrue(all(probe.reaped() for probe in tree), "quota became available with an unreaped worker or descendant")
        self._wait_for(supervisor.exited, "the supervisor kept its pipe handles open after the executor and worker tree exited")
        self.assertEqual(self.kernel.get(JOB_ID).status, "interrupted")
        self.assertEqual(job_store.read_jobs(self.kernel.jobs_root)[JOB_ID]["status"], "interrupted")
