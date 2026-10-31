"""自动纠正：失败 → 建议 → 改参数再试一次，有预算。

三件事各有一条测试：
* 预算用尽/部分成功/不可重试 时**不**重试，并说明原因
* 可自动执行的动作（reduce_param / switch_device）真的改了参数
* dispatch 端到端：第一次 OOM、第二次成功，参数被纠正过
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import retry_policy  # noqa: E402
from shared import worker_protocol  # noqa: E402

OOM_HINT = {
    "code": "out_of_memory",
    "retryable": True,
    "summary": "显存/内存不足",
    "actions": [
        {"action": "reduce_param", "slot": "tile", "to": 256, "why": "降低峰值"},
        {"action": "switch_device", "to": "cpu", "why": "不受显存限制"},
    ],
}
OOM_MESSAGE = "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB"


def media_arguments() -> dict:
    return {
        "modality": "image",
        "inputs": ["a.png"],
        "outputDir": "out",
        "steps": [{"capability": "upscale", "bindings": {"upscaler": "media:real-esrgan-x2"}, "params": {"scale": 2}}],
    }


class BudgetTests(unittest.TestCase):
    def test_default_budget_is_small(self) -> None:
        self.assertGreaterEqual(retry_policy.DEFAULT_BUDGET, 1)
        self.assertLessEqual(retry_policy.DEFAULT_BUDGET, 3)

    def test_environment_can_disable_or_cap_retries(self) -> None:
        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "0"}, clear=False):
            self.assertEqual(retry_policy.budget_from_env(), 0)
        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "99"}, clear=False):
            self.assertLessEqual(retry_policy.budget_from_env(), 5)
        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "nonsense"}, clear=False):
            self.assertEqual(retry_policy.budget_from_env(), retry_policy.DEFAULT_BUDGET)


class ApplicableActionTests(unittest.TestCase):
    def test_only_safe_actions_are_selected(self) -> None:
        hint = {
            "actions": [
                {"action": "reduce_param", "slot": "tile", "to": 0},
                {"action": "download_model"},
                {"action": "switch_device", "to": "cpu"},
            ]
        }
        actions = retry_policy.applicable_actions(hint)
        self.assertEqual([action["action"] for action in actions], ["reduce_param", "switch_device"])

    def test_reduce_param_without_slot_is_dropped(self) -> None:
        self.assertEqual(retry_policy.applicable_actions({"actions": [{"action": "reduce_param"}]}), [])

    def test_arguments_are_adjusted_for_steps_and_device(self) -> None:
        adjusted = retry_policy.adjust_arguments(media_arguments(), retry_policy.applicable_actions(OOM_HINT))
        self.assertIsNotNone(adjusted)
        self.assertEqual(adjusted["device"], "cpu")
        self.assertEqual(adjusted["steps"][0]["params"]["scale"], 2)
        self.assertEqual(adjusted["steps"][0]["params"]["tile"], 256)

    def test_no_change_yields_none(self) -> None:
        actions = [{"action": "switch_device", "to": "cpu"}]
        arguments = {"modality": "image", "device": "cpu"}
        self.assertIsNone(retry_policy.adjust_arguments(arguments, actions))


class BlockedReasonTests(unittest.TestCase):
    def test_reasons_are_explicit(self) -> None:
        self.assertEqual(retry_policy.retry_blocked_reason(1, 0, None), "no recovery hint")
        self.assertEqual(
            retry_policy.retry_blocked_reason(1, 0, {"retryable": False, "actions": []}),
            "failure is not retryable",
        )
        self.assertEqual(
            retry_policy.retry_blocked_reason(1, 3, OOM_HINT),
            "partial success; not retrying the whole batch",
        )
        self.assertEqual(retry_policy.retry_blocked_reason(0, 0, OOM_HINT), "retry budget exhausted")
        self.assertEqual(
            retry_policy.retry_blocked_reason(1, 0, {"retryable": True, "actions": [{"action": "download_model"}]}),
            "no automatically applicable action",
        )
        self.assertIsNone(retry_policy.retry_blocked_reason(1, 0, OOM_HINT))


class DispatchRetryTests(unittest.TestCase):
    def test_dispatch_corrects_an_oom_and_succeeds(self) -> None:
        import agent_contract
        import worker

        calls: list[dict] = []

        def fake_run(command: str, payload: dict) -> int:
            calls.append(json.loads(json.dumps(payload)))
            if len(calls) == 1:
                worker_protocol.emit_error("INFER_FAILED", OOM_MESSAGE)
                return 1
            worker_protocol.emit("audio_tool_result", {"operation": "media_image", "completed": 1, "outputs": [{"path": "out/a.png"}]})
            return 0

        with mock.patch.object(worker, "run_command", side_effect=fake_run):
            outcome = agent_contract.dispatch("media.image.upscale", media_arguments())

        self.assertEqual(len(calls), 2, "应该自动重试一次")
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["recoveryAttempts"][0]["code"], "out_of_memory")
        self.assertEqual(calls[1]["device"], "cpu")
        self.assertEqual(calls[1]["steps"][0]["params"]["tile"], 256)

    def test_budget_zero_prevents_retry_and_explains(self) -> None:
        import agent_contract
        import worker

        calls: list[dict] = []

        def fake_run(command: str, payload: dict) -> int:
            calls.append(dict(payload))
            worker_protocol.emit_error("INFER_FAILED", OOM_MESSAGE)
            return 1

        with mock.patch.dict("os.environ", {"MEDIAFORGE_RETRY_BUDGET": "0"}, clear=False):
            with mock.patch.object(worker, "run_command", side_effect=fake_run):
                outcome = agent_contract.dispatch("media.image.upscale", media_arguments())

        self.assertEqual(len(calls), 1)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["retryBlocked"], "retry budget exhausted")

    def test_partial_success_is_not_retried(self) -> None:
        import agent_contract
        import worker

        calls: list[dict] = []

        def fake_run(command: str, payload: dict) -> int:
            calls.append(dict(payload))
            worker_protocol.emit(
                "audio_tool_result",
                {
                    "operation": "media_image",
                    "completed": 1,
                    "outputs": [{"path": "out/ok.png"}],
                    "failed": [{"path": "out/bad.png", "error": OOM_MESSAGE, "recovery": OOM_HINT}],
                },
            )
            return 0

        with mock.patch.object(worker, "run_command", side_effect=fake_run):
            outcome = agent_contract.dispatch("media.image.upscale", media_arguments())

        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome["retryBlocked"], "partial success; not retrying the whole batch")


class JobStatusRecoveryTests(unittest.TestCase):
    def test_recovery_is_extracted_from_a_failed_job_result(self) -> None:
        self.assertEqual(
            retry_policy.recovery_from_result({"failed": [{"error": "x", "recovery": OOM_HINT}]}),
            OOM_HINT,
        )
        self.assertEqual(retry_policy.recovery_from_result({"recovery": OOM_HINT}), OOM_HINT)
        self.assertIsNone(retry_policy.recovery_from_result({"outputs": []}))
        self.assertIsNone(retry_policy.recovery_from_result(None))


if __name__ == "__main__":
    unittest.main()
