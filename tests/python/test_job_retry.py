"""长任务（作业台账）的重试计划：异步任务只能事后判断能不能再试。

这组测试覆盖 `plan_retry` 的每种分支——它决定了 `job_retry` 会怎么做，
包括"什么时候拒绝"。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import retry_policy  # noqa: E402

OOM_HINT = {
    "code": "out_of_memory",
    "retryable": True,
    "actions": [
        {"action": "reduce_param", "slot": "tile", "to": 256},
        {"action": "switch_device", "to": "cpu"},
    ],
}
FAILED_RESULT = {"failed": [{"path": "a.png", "error": "CUDA out of memory", "recovery": OOM_HINT}]}
PAYLOAD = {
    "operation": "media_image",
    "device": "cuda",
    "steps": [{"capability": "upscale", "params": {"scale": 2}}],
}


class PlanRetryTests(unittest.TestCase):
    def test_recoverable_failure_yields_adjusted_payload(self) -> None:
        plan = retry_policy.plan_retry(PAYLOAD, FAILED_RESULT)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["payload"]["device"], "cpu")
        self.assertEqual(plan["payload"]["steps"][0]["params"]["tile"], 256)
        self.assertEqual(plan["actions"], ["reduce_param", "switch_device"])

    def test_original_payload_is_not_mutated(self) -> None:
        before = {**PAYLOAD, "steps": [{"capability": "upscale", "params": {"scale": 2}}]}
        retry_policy.plan_retry(PAYLOAD, FAILED_RESULT)
        self.assertEqual(PAYLOAD["device"], before["device"])
        self.assertNotIn("tile", PAYLOAD["steps"][0]["params"])

    def test_budget_exhaustion_refuses(self) -> None:
        plan = retry_policy.plan_retry(PAYLOAD, FAILED_RESULT, attempts_used=1)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "retry budget exhausted")

    def test_explicit_budget_overrides_the_environment(self) -> None:
        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "0"}, clear=False):
            self.assertFalse(retry_policy.plan_retry(PAYLOAD, FAILED_RESULT)["ok"])
            self.assertTrue(retry_policy.plan_retry(PAYLOAD, FAILED_RESULT, budget=2)["ok"])

    def test_non_retryable_failure_refuses(self) -> None:
        result = {"recovery": {"code": "cancelled", "retryable": False, "actions": []}}
        plan = retry_policy.plan_retry(PAYLOAD, result)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "failure is not retryable")

    def test_consent_requiring_action_refuses(self) -> None:
        result = {"recovery": {"code": "missing_weights", "retryable": True, "actions": [{"action": "download_model"}]}}
        plan = retry_policy.plan_retry(PAYLOAD, result)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "no automatically applicable action")

    def test_unrecognised_failure_refuses(self) -> None:
        plan = retry_policy.plan_retry(PAYLOAD, {"outputs": []})
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "no recovery hint")

    def test_no_effective_change_refuses(self) -> None:
        already_cpu = {**PAYLOAD, "device": "cpu", "steps": [{"capability": "upscale", "params": {"scale": 2, "tile": 256}}]}
        plan = retry_policy.plan_retry(already_cpu, FAILED_RESULT)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["error"], "adjustment produced no change")


class JobRetryToolTests(unittest.TestCase):
    """桥接层的 job_retry：工具表/分派/预算三件事。"""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        import agent_bridge

        cls.root = tempfile.mkdtemp(prefix="mf-jobretry-")
        cls.bridge = agent_bridge.Bridge(token=None, port=0, data_root=Path(cls.root))

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls.root, ignore_errors=True)

    def test_tool_is_declared_and_dispatchable(self) -> None:
        names = {tool["name"] for tool in self.bridge.tools()}
        self.assertIn("job_retry", names)

    def _failed_job(self, job_id: str = "job_x"):
        job = mock.Mock()
        job.job_id = job_id
        job.command = "audio_tools"
        job.snapshot.return_value = {
            "jobId": job_id,
            "command": "audio_tools",
            "status": "failed",
            "payload": PAYLOAD,
            "result": FAILED_RESULT,
            "error": "CUDA out of memory",
        }
        return job

    def test_unknown_job_is_refused(self) -> None:
        with mock.patch.object(self.bridge.jobs, "get", return_value=None):
            result = self.bridge.job_retry("nope")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown job")

    def test_successful_retry_starts_a_new_job_with_adjusted_payload(self) -> None:
        failed = self._failed_job("job_1")
        started = mock.Mock()
        started.job_id = "job_1-r1"
        started.snapshot.return_value = {"jobId": "job_1-r1", "status": "running"}
        with mock.patch.object(self.bridge.jobs, "get", return_value=failed), \
             mock.patch.object(self.bridge.jobs, "start", return_value=started) as start:
            result = self.bridge.job_retry("job_1")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["jobId"], "job_1-r1")
        self.assertEqual(result["retriedFrom"], "job_1")
        command, payload = start.call_args[0]
        self.assertEqual(command, "audio_tools")
        self.assertEqual(payload["device"], "cpu")
        self.assertEqual(payload["requestId"], "job_1-r1")

    def test_second_retry_is_refused_by_budget(self) -> None:
        failed = self._failed_job("job_2")
        started = mock.Mock()
        started.job_id = "job_2-r1"
        started.snapshot.return_value = {"jobId": "job_2-r1", "status": "running"}
        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "1"}, clear=False):
            with mock.patch.object(self.bridge.jobs, "get", return_value=failed), \
                 mock.patch.object(self.bridge.jobs, "start", return_value=started) as start:
                first = self.bridge.job_retry("job_2")
                second = self.bridge.job_retry("job_2")
        self.assertTrue(first["ok"], first)
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "retry budget exhausted")
        start.assert_called_once()

    def test_job_that_did_not_fail_is_refused(self) -> None:
        job = mock.Mock()
        job.snapshot.return_value = {"status": "completed", "payload": PAYLOAD, "result": {}}
        with mock.patch.object(self.bridge.jobs, "get", return_value=job):
            result = self.bridge.job_retry("job_ok")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "job did not fail")


if __name__ == "__main__":
    unittest.main()
