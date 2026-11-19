"""Crash recovery for the current job catalog and its transient deletion files."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from ._job_fixtures import audio_group, job_record, write_record
from core import job_store
from core.operation_kernel import OperationKernel
from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock


class JobRemovalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mediaforge-job-remove-")))
        self.jobs = self.root / "agent-jobs"
        self.jobs.mkdir()
        self.pending = self.jobs / ".removal-pending"
        self.committed = self.jobs / ".removal-committed"

    def test_pending_rollback_restores_exact_record_bytes_and_audio_closure(self):
        primary, child = audio_group(status="completed")
        first = write_record(self.jobs, primary)
        second = write_record(self.jobs, child)
        before = first.read_bytes(), second.read_bytes()
        self.pending.mkdir()
        first.rename(self.pending / first.name)
        self.assertEqual(job_store.read_jobs(self.jobs), {row["jobId"]: row for row in (primary, child)})
        self.assertEqual((first.read_bytes(), second.read_bytes()), before)
        self.assertFalse(self.pending.exists())
        self.assertEqual(set(job_store.read_jobs(self.jobs)), {"audio_1", "audio_2"})

    def test_committed_cleanup_resumes_after_part_of_an_audio_group_was_unlinked(self):
        _, child = audio_group(status="completed")
        write_record(self.committed, child)
        output = self.root / "finished.wav"
        output.write_bytes(b"keep media")
        unrelated = job_record("unrelated", result={"path": str(output)})
        write_record(self.jobs, unrelated)
        self.assertEqual(job_store.read_jobs(self.jobs), {"unrelated": unrelated})
        self.assertFalse(self.committed.exists())
        self.assertEqual(output.read_bytes(), b"keep media")
        self.assertEqual(job_store.read_jobs(self.jobs), {"unrelated": unrelated})

    def test_retention_uses_commit_directory_and_resumes_cleanup_on_next_read(self):
        kernel = OperationKernel(self.root)
        self.addCleanup(kernel.close)
        path = write_record(self.jobs, job_record("expired", createdAt=time.time() - 100 * 86400))
        other = job_record("expired-second", createdAt=time.time() - 101 * 86400)
        write_record(self.jobs, other)
        unlink = Path.unlink

        def fail_cleanup(file, *args, **kwargs):
            if file.parent == self.committed:
                raise PermissionError("fixture cleanup failure")
            return unlink(file, *args, **kwargs)

        with mock.patch("core.operation_kernel._retention_limits", return_value=(30, 0)), \
             mock.patch.object(Path, "unlink", fail_cleanup):
            self.assertEqual(kernel._prune_history(), 1)
        self.assertFalse(path.exists())
        self.assertTrue((self.committed / path.name).is_file())
        self.assertEqual(job_store.read_jobs(self.jobs), {"expired-second": other})
        self.assertFalse(self.committed.exists())

    def test_legacy_flat_rows_are_restored_for_both_writers(self):
        primary, child = audio_group(status="completed")
        agent = job_record("old.agent")
        files = {
            write_record(self.jobs, primary): ".audio_1.812-99123.removing",
            write_record(self.jobs, child): ".audio_2.812-99123.removing",
            write_record(self.jobs, agent): ".old.agent.0123456789abcdef0123456789abcdef.removing",
        }
        before = {path: path.read_bytes() for path in files}
        for path, staged in files.items():
            path.rename(self.jobs / staged)
        self.assertEqual(job_store.read_jobs(self.jobs), {row["jobId"]: row for row in (primary, child, agent)})
        self.assertEqual({path: path.read_bytes() for path in files}, before)
        self.assertFalse(list(self.jobs.glob("*.removing")))

    def test_legacy_flat_audio_cleanup_finishes_when_only_the_prefix_is_missing(self):
        _, child = audio_group(status="completed")
        primary, _ = audio_group(child="audio_0", status="completed")
        unrelated = job_record("unrelated")
        write_record(self.jobs, unrelated)
        for row in (primary, child):
            with self.subTest(survivor=row["jobId"]):
                source = write_record(self.jobs, row)
                source.rename(self.jobs / f".{row['jobId']}.812-99123.removing")
                self.assertEqual(job_store.read_jobs(self.jobs), {"unrelated": unrelated})
                self.assertFalse(list(self.jobs.glob("*.removing")))
                self.assertFalse(source.exists())

    def test_legacy_flat_audio_cleanup_preserves_an_impossible_missing_suffix(self):
        primary, _ = audio_group(status="completed")
        staged = write_record(self.jobs, primary).rename(self.jobs / ".audio_1.812-99123.removing")
        original = staged.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "original cleanup order"):
            job_store.read_jobs(self.jobs)
        self.assertEqual(staged.read_bytes(), original)

    def test_legacy_partial_group_with_live_member_is_preserved(self):
        primary, child = audio_group(status="completed")
        primary["audio"]["memberJobIds"].append("lost_member")
        staged = write_record(self.jobs, primary).rename(self.jobs / ".audio_1.812-99123.removing")
        live = write_record(self.jobs, child)
        before = staged.read_bytes(), live.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "another group member"):
            job_store.read_jobs(self.jobs)
        self.assertEqual((staged.read_bytes(), live.read_bytes()), before)
        self.assertFalse((self.jobs / "audio_1.json").exists())

    def test_legacy_partial_group_with_conflicting_transaction_nonces_is_preserved(self):
        _, child = audio_group(status="completed")
        _, another = audio_group(child="audio_3", status="completed")
        another["audio"]["index"] = 2
        first = write_record(self.jobs, child).rename(self.jobs / ".audio_2.812-99123.removing")
        second = write_record(self.jobs, another).rename(self.jobs / ".audio_3.812-99124.removing")
        before = first.read_bytes(), second.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "ambiguous incomplete audio removal"):
            job_store.read_jobs(self.jobs)
        self.assertEqual((first.read_bytes(), second.read_bytes()), before)

    def test_pending_group_with_a_missing_member_cannot_be_dropped(self):
        primary, _ = audio_group(status="completed")
        staged = write_record(self.pending, primary)
        before = staged.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "audio member job is missing"):
            job_store.read_jobs(self.jobs)
        self.assertEqual(staged.read_bytes(), before)
        self.assertFalse((self.jobs / staged.name).exists())

    def test_recovery_preflights_every_file_and_preserves_conflicts(self):
        good = write_record(self.pending, job_record("a_good"))
        bad = self.pending / "z_bad.json"
        bad.write_bytes(b"malformed")
        before = good.read_bytes(), bad.read_bytes()
        with self.assertRaises(job_store.JobStoreError):
            job_store.read_jobs(self.jobs)
        self.assertEqual((good.read_bytes(), bad.read_bytes()), before)
        self.assertFalse((self.jobs / good.name).exists())
        bad.unlink()
        live = write_record(self.jobs, job_record("a_good", message="keep live"))
        before = good.read_bytes(), live.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "collides"):
            job_store.read_jobs(self.jobs)
        self.assertEqual((good.read_bytes(), live.read_bytes()), before)

    def test_recovery_rejects_linked_staging_directory(self):
        outside = write_record(self.root / "outside", job_record("outside"))
        try:
            if os.name == "nt":
                from _winapi import CreateJunction
                CreateJunction(str(outside.parent), str(self.pending))
            else:
                self.pending.symlink_to(outside.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory link unavailable: {error}")
        before = outside.read_bytes()
        with self.assertRaisesRegex(job_store.JobStoreError, "invalid job removal path"):
            job_store.read_jobs(self.jobs)
        self.assertTrue(self.pending.exists())
        self.assertEqual(outside.read_bytes(), before)
        self.assertFalse((self.jobs / outside.name).exists())

    def test_recovery_does_not_modify_rows_while_an_owner_lease_is_held(self):
        staged = write_record(self.pending, job_record("leased"))
        with runtime_lock(self.jobs / ".owners" / "leased.lock", timeout=0):
            with self.assertRaises(RuntimeLockBusy):
                job_store.read_jobs(self.jobs)
            self.assertTrue(staged.exists())
            self.assertFalse((self.jobs / staged.name).exists())
        self.assertIn("leased", job_store.read_jobs(self.jobs))
        self.assertFalse(staged.exists())


if __name__ == "__main__":
    unittest.main()
