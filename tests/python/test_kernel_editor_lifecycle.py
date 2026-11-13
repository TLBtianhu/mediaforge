"""Canonical editor events, durable results and execution leases in isolated fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
import agent_bridge
from core import job_quota, job_store
from core.operation_kernel import Job, OperationKernel
from core.worker_runtime_lock import RuntimeLockBusy


FAKE_WORKER = r'''
import json,sys,time
from pathlib import Path
assert sys.version_info[:2] == (3,12), 'real Python 3.12 is required'
payload=json.loads(Path(sys.argv[sys.argv.index('--payload')+1]).read_text(encoding='utf-8'))
root=Path(payload['fixtureRoot']).resolve()
gate=Path(payload['fixtureRelease']).resolve()
assert gate.parent==root
mode=payload['fixtureMode']
if mode=='completed':
    output=root/'fixture_mix.wav'
    output.write_bytes(b'fixture, not real audio')
    event={'type':'editor_mix_exported','payload':{'path':str(output),'duration':1.25,
        'sampleRate':48000,'channels':2,'format':'wav','peakProtectionApplied':True,'peakAdjustmentDb':-1.5}}
elif mode=='cancelled':
    event={'type':'task.cancelled','payload':{'message':'Cancelled'}}
else:
    event={'type':'error','payload':{'code':'EDITOR_EXPORT_FAILED','message':'synthetic export failure'}}
print(json.dumps(event),flush=True)
deadline=time.monotonic()+15
while not gate.exists():
    if time.monotonic()>deadline:raise SystemExit(9)
    time.sleep(.01)
raise SystemExit(7 if mode=='failed' else 0)
'''


class EditorKernelLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mediaforge-kernel-editor-")
        self.root = Path(self.temporary.name)
        self.kernels = []
        self.release_files = []

    def tearDown(self):
        # Only these finite, synthetic workers are ours; release them before removing fixtures.
        for gate in self.release_files:
            gate.write_text("release", encoding="utf-8")
        for kernel in self.kernels:
            kernel.close()
        for kernel in self.kernels:
            for job in kernel._jobs.values():
                deadline = time.monotonic() + 8
                while (job._lease is not None or job._quota_lease is not None) and time.monotonic() < deadline:
                    time.sleep(.01)
                if job._lease is not None:
                    kernel._cleanup_process(job)
                    kernel._release_job_lease(job)
                self.assertIsNone(job._lease, "owned fixture worker was not reaped")
                self.assertIsNone(job._quota_lease)
        self.temporary.cleanup()

    def kernel(self):
        kernel = OperationKernel(self.root, sys.executable)
        self.kernels.append(kernel)
        return kernel

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("isolated editor worker did not reach the expected boundary")
            time.sleep(.01)

    def job(self, kernel, command="export_editor_mix"):
        job = Job("editor_fixture", command, {"project": {"version": 4, "sources": [], "tracks": []}},
                  self.root / "bridge-jobs/editor_fixture")
        job.owner = {"kind": "operation-kernel", "kernelId": kernel._owner_id, "pid": 12345}
        job.update(status="running")
        kernel._persist(job, force=True)
        return job

    @staticmethod
    def bridge(kernel):
        bridge = agent_bridge.Bridge.__new__(agent_bridge.Bridge)
        bridge.jobs = kernel
        return bridge

    def test_export_result_is_complete_queryable_after_restart_and_has_one_path_source(self):
        kernel = self.kernel()
        job = self.job(kernel)
        expected = {"path": str(self.root / "mix.wav"), "duration": 2.5, "sampleRate": 48000,
                    "channels": 2, "format": "wav", "peakProtectionApplied": True, "peakAdjustmentDb": -2.0}
        payload = copy.deepcopy(expected)
        kernel._consume(job, {"type": "editor_mix_exported", "payload": payload})
        payload["path"] = "changed outside the journal"
        restored = self.kernel()
        outcome = self.bridge(restored)._wait(job, 0)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(outcome["fraction"], 1)
        self.assertEqual(outcome["result"], expected)
        self.assertNotIn("outputs", outcome["result"])
        self.assertNotIn("files", outcome["result"])
        self.assertEqual(set(path.name for path in kernel.jobs_root.glob("*.json")), {job.job_id + ".json"})

    def test_worker_cancel_preserves_known_partial_outputs_and_bridge_reports_not_ok(self):
        kernel = self.kernel()
        job = self.job(kernel, "infer_workflow")
        partial = {"outputs": [{"path": str(self.root / "partial.wav"), "stem": "Vocals"}],
                   "nodeValues": [{"nodeId": "1", "value": "kept"}]}
        job.result = copy.deepcopy(partial)
        kernel._persist(job, force=True)
        kernel._consume(job, {"type": "task.cancelled", "payload": {"message": "Stopped by worker", "outputs": []}})
        outcome = self.bridge(kernel)._wait(job, .1)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["status"], "cancelled")
        self.assertEqual(outcome["result"], {**partial, "message": "Stopped by worker"})
        self.assertEqual(outcome["message"], "Stopped by worker")
        self.assertIsNotNone(outcome["finishedAt"])

    def test_recoverable_error_can_be_followed_by_the_canonical_export_result(self):
        kernel = self.kernel()
        job = self.job(kernel)
        kernel._consume(job, {"type": "error", "payload": {"message": "retrying", "recoverable": True}})
        self.assertEqual(job.status, "running")
        self.assertEqual(job.logs[-1]["message"], "retrying")
        expected = {"path": str(self.root / "mix.wav"), "channels": 2}
        kernel._consume(job, {"type": "editor_mix_exported", "payload": expected})
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.result, expected)
        self.assertIsNone(job.error)

    def test_adopting_external_cancellation_still_signals_the_owned_watcher(self):
        kernel = self.kernel()
        job = self.job(kernel)
        self.assertFalse(job.cancel_event.is_set())
        external = job.snapshot()
        external.update(status="cancelled", message="external client", finishedAt=time.time())
        job.adopt_terminal(external)
        self.assertTrue(job.cancel_event.is_set())
        self.assertEqual(job.status, "cancelled")

    def test_late_export_cancel_and_error_events_never_reverse_a_terminal(self):
        for terminal in ("editor_mix_exported", "task.cancelled", "error"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory(dir=self.root) as raw:
                kernel = OperationKernel(Path(raw))
                try:
                    job = self.job(kernel)
                    payload = {"path": str(Path(raw) / "out.wav"), "message": "first terminal"}
                    kernel._consume(job, {"type": terminal, "payload": payload})
                    before = job.snapshot()
                    disk_before = (kernel.jobs_root / f"{job.job_id}.json").read_bytes()
                    for late in ("editor_mix_exported", "task.cancelled", "error"):
                        kernel._consume(job, {"type": late, "payload": {"path": "late.wav", "message": "late"}})
                    self.assertEqual(job.snapshot(), before)
                    self.assertEqual((kernel.jobs_root / f"{job.job_id}.json").read_bytes(), disk_before)
                finally:
                    kernel.close()

    def test_fatal_export_failure_preserves_payload_and_bridge_reports_failed(self):
        kernel = self.kernel()
        job = self.job(kernel)
        expected = {"code": "EDITOR_EXPORT_FAILED", "message": "synthetic failure", "detail": "fixture detail"}
        kernel._consume(job, {"type": "error", "payload": expected})
        outcome = self.bridge(kernel)._wait(job, 0)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], "synthetic failure")
        self.assertEqual(outcome["result"], expected)

    def run_hidden_worker(self, mode):
        self.assertEqual(sys.version_info[:2], (3, 12))
        kernel = self.kernel()
        worker = self.root / "fake_worker.py"
        worker.write_text(FAKE_WORKER, encoding="utf-8")
        gate = self.root / f"release-{mode}"
        self.release_files.append(gate)
        with mock.patch("core.operation_kernel.WORKER", worker):
            job = kernel.start("export_editor_mix", {"requestId": f"editor_{mode}", "fixtureMode": mode,
                "fixtureRoot": str(self.root), "fixtureRelease": str(gate), "project": {"sources": [], "tracks": []}})
            self.wait_for(lambda: job.status == mode and kernel.get(job.job_id).status == mode)
            process = job.process
            self.assertIsNotNone(process)
            self.assertIsNone(process.poll())
            self.assertFalse(job._process_reaped)
            self.assertIsNotNone(job._lease)
            self.assertIsNotNone(job._quota_lease)
            with self.assertRaises(RuntimeLockBusy):
                with job_store.owner_lock(kernel.jobs_root, job.job_id):
                    pass
            with job_store.catalog_lock(kernel.jobs_root):
                self.assertIsNone(job_quota.try_acquire(kernel.jobs_root, 1))
            outcome = self.bridge(kernel)._wait(job, 0)
            self.assertEqual(outcome["status"], mode)
            self.assertEqual(outcome["ok"], mode == "completed")
            if mode == "completed":
                self.assertEqual(outcome["result"]["path"], str(self.root / "fixture_mix.wav"))
                self.assertNotIn("outputs", outcome["result"])
                self.assertEqual((self.root / "fixture_mix.wav").read_bytes(), b"fixture, not real audio")
            elif mode == "cancelled":
                self.assertEqual(outcome["result"], {"message": "Cancelled"})
                self.assertFalse((self.root / "fixture_mix.wav").exists())
                self.assertFalse(job.cancel_event.is_set(), "a worker's own terminal must not terminate its cleanup")
            else:
                self.assertEqual(outcome["result"]["code"], "EDITOR_EXPORT_FAILED")
            gate.write_text("release", encoding="utf-8")
            self.wait_for(lambda: job._lease is None and job._quota_lease is None)
        self.assertTrue(job._process_reaped)
        self.assertIsNone(job._quota_lease)
        self.assertIsNone(job.process)
        self.assertEqual(process.returncode, 7 if mode == "failed" else 0)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertEqual(kernel.get(job.job_id).status, mode, "exit code must not replace the canonical event")
        self.assertFalse((job.workdir / "payload.json").exists())
        with job_store.catalog_lock(kernel.jobs_root):
            lease = job_quota.try_acquire(kernel.jobs_root, 1)
            self.assertIsNotNone(lease)
            lease.release()

    def test_real_hidden_export_worker_keeps_both_leases_until_reaped(self):
        self.run_hidden_worker("completed")

    def test_real_hidden_cancel_worker_exit_zero_stays_cancelled_until_and_after_reap(self):
        self.run_hidden_worker("cancelled")

    def test_real_hidden_failed_worker_retains_failure_and_releases_leases_after_reap(self):
        self.run_hidden_worker("failed")


if __name__ == "__main__":
    unittest.main()
