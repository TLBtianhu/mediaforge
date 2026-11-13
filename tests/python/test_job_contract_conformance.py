"""The desktop and Python readers consume the same current job contract fixtures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

if __package__:
    from . import _bootstrap as _worker_test_bootstrap
else:
    import _bootstrap as _worker_test_bootstrap

from core import job_store


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/jobs/current-contract.json"


class JobContractConformanceTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.records = {value["jobId"]: value for value in fixture["records"]}
        self.invalid = fixture["invalidRecords"]
        temporary = tempfile.TemporaryDirectory(prefix="job-contract-")
        self.addCleanup(temporary.cleanup)
        self.jobs = Path(temporary.name) / "agent-jobs"
        self.jobs.mkdir()

    def write_records(self):
        for job_id, value in self.records.items():
            (self.jobs / f"{job_id}.json").write_text(json.dumps(value), encoding="utf-8")

    def bytes_on_disk(self):
        return {path.name: path.read_bytes() for path in self.jobs.glob("*.json")}

    def test_shared_valid_records_and_invalid_patches(self):
        for job_id, value in self.records.items():
            with self.subTest(valid=job_id):
                self.assertEqual(job_store.validate_record(copy.deepcopy(value), job_id), value)
        for case in self.invalid:
            value = copy.deepcopy(self.records[case["base"]])
            for key in case.get("remove", []):
                value.pop(key, None)
            value.update(case.get("set", {}))
            with self.subTest(invalid=case["name"]):
                with self.assertRaises(job_store.JobStoreError):
                    job_store.validate_record(value, case["base"])

    def test_batch_requests_resolve_without_duplicating_or_mutating_storage(self):
        self.write_records()
        before = self.bytes_on_disk()
        loaded = job_store.read_jobs(self.jobs)
        self.assertEqual(loaded, self.records)
        self.assertEqual(sum(data.count(b"ONE_GRAPH_ONLY") for data in before.values()), 1)
        primary = loaded["audio-primary"]
        child = loaded["audio-child"]
        self.assertNotIn("payload", child)
        self.assertNotIn("presentation", child)
        self.assertNotIn("tasks", primary["payload"])
        for job in (primary, child):
            with self.subTest(job=job["jobId"]):
                resolved = job_store.resolve_payload(job, self.jobs)
                self.assertEqual(resolved["output"], primary["payload"]["output"],
                                 "the batch root must not become the leaf output directory")
                self.assertEqual(resolved["taskId"], job["jobId"])
                self.assertEqual(resolved["tasks"], [{**job["audio"]["request"], "taskId": job["jobId"]}])
                self.assertEqual(resolved["workflow"], primary["payload"]["workflow"])
        self.assertEqual(self.bytes_on_disk(), before, "materialized requests must stay in memory")

    def test_broken_reference_fails_without_rewriting_any_record(self):
        self.records["audio-child"]["payloadRef"] = "missing-primary"
        self.write_records()
        before = self.bytes_on_disk()
        with self.assertRaises(job_store.JobStoreError):
            job_store.read_jobs(self.jobs)
        self.assertEqual(self.bytes_on_disk(), before)

    def test_missing_member_is_not_silently_omitted(self):
        self.records.pop("audio-child")
        self.write_records()
        before = self.bytes_on_disk()
        with self.assertRaises(job_store.JobStoreError):
            job_store.read_jobs(self.jobs)
        self.assertEqual(self.bytes_on_disk(), before)

    def test_old_broken_and_future_records_keep_their_original_bytes(self):
        path = self.jobs / "old-record.json"
        for raw in (b'{"jobId":"old-record","status":"completed"}', b'{"schemaVersion":',
                    b'{"schemaVersion":99,"jobId":"old-record"}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaises(job_store.JobStoreError):
                    job_store.read_job(path)
                self.assertEqual(path.read_bytes(), raw)
        self.assertIsNone(job_store.read_job(self.jobs / "missing.json"))


if __name__ == "__main__":
    unittest.main()
