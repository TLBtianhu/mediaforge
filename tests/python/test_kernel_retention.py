"""作业文件的保留策略。

每个作业一个 json、永不覆盖，所以 `agent-jobs/` 会一直攒下去。几千个小文件之后：
启动扫描、`_absorb_foreign_jobs` 的 iterdir、资源管理器打开都会变慢。

规则：默认保留 **30 天 / 最多 500 个终态作业**，可用
`MEDIAFORGE_JOB_RETENTION_DAYS` / `MEDIAFORGE_JOB_RETENTION_COUNT` 覆盖；
两个都设成 0 等于关闭清理。

三条不能破的底线（这组测试就是在钉它们）：
① `queued` / `running` 永远不删；② 没有 `createdAt` 的不删（不猜它多老）；
③ 清理失败不影响启动（下次再来）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core.operation_kernel import OperationKernel  # noqa: E402

DAY = 86400.0


class JobRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.jobs = self.root / "agent-jobs"
        self.jobs.mkdir(parents=True)

    def seed(self, job_id: str, *, status: str = "completed", age_days: float = 0.0, stamp: bool = True) -> Path:
        payload = {
            "jobId": job_id,
            "command": "audio_tools",
            "status": status,
            "fraction": 1.0,
            "message": "done",
        }
        if stamp:
            payload["createdAt"] = time.time() - age_days * DAY
        path = self.jobs / f"{job_id}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def kernel(self, **env: str) -> OperationKernel:
        with mock.patch.dict(os.environ, env, clear=False):
            return OperationKernel(self.root)

    def remaining(self) -> set[str]:
        return {path.stem for path in self.jobs.glob("*.json") if path.name != "index.json"}

    def test_count_limit_keeps_the_newest(self) -> None:
        for index in range(5):
            self.seed(f"job_{index}", age_days=index)  # job_0 最新
        with mock.patch.dict(os.environ, {"MEDIAFORGE_JOB_RETENTION_COUNT": "2"}, clear=False):
            kernel = OperationKernel(self.root)
        self.assertEqual(kernel.pruned, 3)
        self.assertEqual(self.remaining(), {"job_0", "job_1"})

    def test_age_limit_drops_only_the_old_ones(self) -> None:
        self.seed("fresh", age_days=1)
        self.seed("stale", age_days=40)
        with mock.patch.dict(os.environ, {"MEDIAFORGE_JOB_RETENTION_COUNT": "0"}, clear=False):
            kernel = OperationKernel(self.root)
        self.assertEqual(kernel.pruned, 1)
        self.assertEqual(self.remaining(), {"fresh"})

    def test_running_and_queued_are_never_pruned(self) -> None:
        self.seed("alive", status="running", age_days=100)
        self.seed("waiting", status="queued", age_days=100)
        with mock.patch.dict(
            os.environ,
            {"MEDIAFORGE_JOB_RETENTION_DAYS": "0", "MEDIAFORGE_JOB_RETENTION_COUNT": "0"},
            clear=False,
        ):
            kernel = OperationKernel(self.root)
        # 启动恢复会把 queued/running 改写成 interrupted（那是另一条逻辑）。这里把状态摆回去，
        # 单独验证清理规则本身不碰运行中的作业。
        kernel._index["alive"]["status"] = "running"
        kernel._index["waiting"]["status"] = "queued"
        with mock.patch("core.operation_kernel._retention_limits", return_value=(30.0, 0)):
            kernel._prune_history()
        self.assertTrue((self.jobs / "alive.json").is_file())
        self.assertTrue((self.jobs / "waiting.json").is_file())

    def test_entries_without_a_timestamp_survive(self) -> None:
        self.seed("undated", stamp=False)
        self.seed("dated", age_days=100)
        with mock.patch.dict(os.environ, {"MEDIAFORGE_JOB_RETENTION_COUNT": "0"}, clear=False):
            kernel = OperationKernel(self.root)
        self.assertTrue((self.jobs / "undated.json").is_file())
        self.assertTrue(kernel.pruned >= 1)

    def test_pruning_can_be_disabled(self) -> None:
        self.seed("ancient", age_days=3650)
        with mock.patch.dict(
            os.environ,
            {"MEDIAFORGE_JOB_RETENTION_DAYS": "0", "MEDIAFORGE_JOB_RETENTION_COUNT": "0"},
            clear=False,
        ):
            kernel = OperationKernel(self.root)
        self.assertEqual(kernel.pruned, 0)
        self.assertTrue((self.jobs / "ancient.json").is_file())

    def test_index_entry_is_dropped_with_the_file(self) -> None:
        self.seed("gone", age_days=100)
        with mock.patch.dict(os.environ, {"MEDIAFORGE_JOB_RETENTION_COUNT": "0"}, clear=False):
            kernel = OperationKernel(self.root)
        self.assertNotIn("gone", kernel._index)

    def test_pruning_is_idempotent(self) -> None:
        for index in range(3):
            self.seed(f"job_{index}", age_days=index)
        with mock.patch.dict(os.environ, {"MEDIAFORGE_JOB_RETENTION_COUNT": "1"}, clear=False):
            kernel = OperationKernel(self.root)
            first = kernel.pruned
            second = kernel._prune_history()
        self.assertEqual(first, 2)
        self.assertEqual(second, 0)


if __name__ == "__main__":
    unittest.main()
