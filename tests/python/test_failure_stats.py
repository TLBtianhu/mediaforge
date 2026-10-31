"""失败模式统计：按可恢复性归类台账里的失败。

真实台账通常很干净（都成功），所以这里用合成作业文件覆盖各种失败形态。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core import failure_stats  # noqa: E402


def write_job(root: Path, job_id: str, *, status: str, command: str, error: str | None = None) -> None:
    jobs = root / "agent-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    record = {"jobId": job_id, "command": command, "status": status, "error": error}
    (jobs / f"{job_id}.json").write_text(json.dumps(record), encoding="utf-8")


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-stats-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_empty_ledger_reports_zero(self) -> None:
        stats = failure_stats.collect(self.root)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["recommendations"], [])

    def test_failures_are_grouped_by_recoverable_code(self) -> None:
        write_job(self.root, "a", status="failed", command="audio_tools", error="CUDA out of memory. Tried to allocate 2 GiB")
        write_job(self.root, "b", status="failed", command="audio_tools", error="CUDA out of memory. Tried to allocate 4 GiB")
        write_job(self.root, "c", status="failed", command="infer", error="model file not found: weights/x.pth")
        write_job(self.root, "d", status="completed", command="infer")

        stats = failure_stats.collect(self.root)
        self.assertEqual(stats["scanned"], 4)
        self.assertEqual(stats["failed"], 3)
        self.assertEqual(stats["byCode"]["out_of_memory"], 2)
        self.assertEqual(stats["byCode"]["missing_weights"], 1)
        self.assertEqual(stats["byCommand"], {"audio_tools": 2, "infer": 1})
        # 两条可恢复（OOM + 缺权重），一条也一样可恢复，所以是 3/3
        self.assertEqual(stats["recoverable"], 3)
        self.assertEqual(stats["recoverableShare"], 1.0)

    def test_unknown_failure_is_bucketed_as_unclassified(self) -> None:
        write_job(self.root, "x", status="failed", command="infer", error="something odd in module xyz")
        stats = failure_stats.collect(self.root)
        self.assertEqual(stats["byCode"], {"unclassified": 1})
        self.assertEqual(stats["recoverable"], 0)
        self.assertTrue(any("无法归类" in note for note in stats["recommendations"]))

    def test_top_issues_merge_variants_of_the_same_message(self) -> None:
        write_job(self.root, "p1", status="failed", command="audio_tools", error="No such file or directory: 'C:\\a\\song1.wav'")
        write_job(self.root, "p2", status="failed", command="audio_tools", error="No such file or directory: 'C:\\b\\song2.wav'")
        stats = failure_stats.collect(self.root)
        self.assertEqual(len(stats["topIssues"]), 1, stats["topIssues"])
        self.assertEqual(stats["topIssues"][0]["count"], 2)
        self.assertEqual(stats["topIssues"][0]["code"], "input_missing")

    def test_index_file_and_broken_files_are_ignored(self) -> None:
        write_job(self.root, "ok", status="failed", command="infer", error="CUDA out of memory")
        jobs = self.root / "agent-jobs"
        (jobs / "index.json").write_text(json.dumps({"jobs": []}), encoding="utf-8")
        (jobs / "broken.json").write_text("{not json", encoding="utf-8")
        stats = failure_stats.collect(self.root)
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["failed"], 1)

    def test_limit_keeps_the_most_recent_files(self) -> None:
        for index in range(5):
            write_job(self.root, f"j{index}", status="failed", command="infer", error="CUDA out of memory")
        stats = failure_stats.collect(self.root, limit=2)
        self.assertEqual(stats["scanned"], 2)


class NormalizeTests(unittest.TestCase):
    def test_paths_and_numbers_are_erased(self) -> None:
        text = failure_stats.normalize_message(r"failed on C:\Music\song 42.wav after 12.5s")
        self.assertIn("<path>", text)
        self.assertNotIn("Music", text)
        self.assertNotIn("12.5", text)

    def test_relative_paths_are_erased_too(self) -> None:
        text = failure_stats.normalize_message("cannot open output/tmp/out_003.png")
        self.assertIn("<path>", text)

    def test_long_messages_are_truncated(self) -> None:
        self.assertLessEqual(len(failure_stats.normalize_message("x" * 500)), 120)


class RecommendationTests(unittest.TestCase):
    def test_dominant_code_gets_a_concrete_note(self) -> None:
        notes = failure_stats.recommendations({"out_of_memory": 8, "bad_input": 2}, 10, [])
        self.assertTrue(any("显存" in note for note in notes), notes)

    def test_scattered_failures_point_at_top_issues(self) -> None:
        notes = failure_stats.recommendations({"bad_input": 1, "missing_weights": 1}, 8, [{"code": "bad_input"}])
        self.assertTrue(notes)
        self.assertIn("topIssues", notes[0])

    def test_no_failures_means_no_notes(self) -> None:
        self.assertEqual(failure_stats.recommendations({}, 0, []), [])


class ContractTests(unittest.TestCase):
    def test_tool_is_registered_and_covered(self) -> None:
        from surfaces import agent_contract

        contract = agent_contract.build_contract()
        names = {tool["name"] for tool in contract["tools"]}
        self.assertIn("system.failure_stats", names)
        self.assertEqual(contract["audit"]["missing"], [])


if __name__ == "__main__":
    unittest.main()
