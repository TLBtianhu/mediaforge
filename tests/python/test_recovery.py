"""失败恢复建议：认得出的失败才给建议，认不出就别猜。

这组测试守两件事：
* 建议必须是**可执行**的（指向真实槽位 / 设备 / 动作 id）
* 认不出的文本不能凭空造建议（否则 agent 会被误导去做无用动作）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from shared import recovery  # noqa: E402
from app_core import interface as model_interface  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_cuda_oom_suggests_smaller_tile_and_cpu(self) -> None:
        hint = recovery.classify("RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["code"], "out_of_memory")
        self.assertTrue(hint["retryable"])
        actions = {action["action"] for action in hint["actions"]}
        self.assertIn("reduce_param", actions)
        self.assertIn("switch_device", actions)

    def test_missing_weights_suggests_download(self) -> None:
        hint = recovery.classify("model file not found: weights/vocals.pth")
        self.assertEqual(hint["code"], "missing_weights")
        self.assertEqual([action["action"] for action in hint["actions"]], ["download_model"])

    def test_bad_input_suggests_conversion(self) -> None:
        hint = recovery.classify("moov atom not found", "Invalid data found when processing input")
        self.assertEqual(hint["code"], "bad_input")
        self.assertEqual(hint["actions"][0]["action"], "convert_input")

    def test_cancellation_is_not_retryable(self) -> None:
        hint = recovery.classify("operation was cancelled")
        self.assertEqual(hint["code"], "cancelled")
        self.assertFalse(hint["retryable"])
        self.assertEqual(hint["actions"], [])

    def test_unknown_error_yields_no_hint(self) -> None:
        self.assertIsNone(recovery.classify("something odd happened in module xyz"))
        self.assertIsNone(recovery.classify(""))
        self.assertIsNone(recovery.classify(None))


class ActionVocabularyTests(unittest.TestCase):
    def test_every_action_id_is_declared(self) -> None:
        samples = [
            "CUDA out of memory",
            "no CUDA-capable device",
            "model file not found",
            "moov atom not found",
            "No space left on device",
            "ffmpeg not found",
        ]
        for message in samples:
            hint = recovery.classify(message)
            with self.subTest(message=message):
                self.assertIsNotNone(hint, message)
                for action in hint["actions"]:
                    self.assertIn(action["action"], recovery.ACTIONS)
                    self.assertTrue(action.get("why"), f"{action} 缺少理由")

    def test_suggested_slots_exist_in_the_vocabulary(self) -> None:
        """建议里提到的槽位必须是真实存在的，否则 agent 照做也会失败。"""
        vocabulary = set(model_interface.SLOT_DEFAULTS)
        for message in ("CUDA out of memory",):
            for action in recovery.classify(message)["actions"]:
                slot = action.get("slot")
                if slot:
                    self.assertIn(slot, vocabulary)


class EmitErrorTests(unittest.TestCase):
    def test_error_event_carries_recovery(self) -> None:
        from shared.worker_protocol import emit_error, set_event_sink

        events: list[dict] = []
        set_event_sink(lambda event_type, data, request_id, task_id: events.append({"type": event_type, "payload": data}))
        try:
            emit_error("INFER_FAILED", "CUDA out of memory. Tried to allocate 512 MiB")
        finally:
            set_event_sink(None)
        payload = events[-1]["payload"]
        self.assertEqual(payload["code"], "INFER_FAILED")
        self.assertIn("recovery", payload)
        self.assertEqual(payload["recovery"]["code"], "out_of_memory")

    def test_plain_error_has_no_recovery_field(self) -> None:
        from shared.worker_protocol import emit_error, set_event_sink

        events: list[dict] = []
        set_event_sink(lambda event_type, data, request_id, task_id: events.append({"type": event_type, "payload": data}))
        try:
            emit_error("SOMETHING_ODD", "unexpected value in module xyz")
        finally:
            set_event_sink(None)
        self.assertNotIn("recovery", events[-1]["payload"])


class MediaFailureShapeTests(unittest.TestCase):
    def test_failed_entries_carry_path_error_and_recovery(self) -> None:
        from media_ops import failures

        entry = failures.describe("song.wav", RuntimeError("CUDA out of memory"))
        self.assertEqual(entry["path"], "song.wav")
        self.assertIn("CUDA out of memory", entry["error"])
        self.assertEqual(entry["recovery"]["code"], "out_of_memory")

    def test_unknown_failure_keeps_shape_without_recovery(self) -> None:
        from media_ops import failures

        entry = failures.describe("song.wav", ValueError("odd"))
        self.assertEqual(set(entry), {"path", "error"})


class ContractTests(unittest.TestCase):
    def test_contract_lists_the_recovery_actions(self) -> None:
        from surfaces import agent_contract

        contract = agent_contract.build_contract()
        self.assertEqual(set(contract["recoveryActions"]), set(recovery.ACTIONS))


if __name__ == "__main__":
    unittest.main()
