"""内嵌直调：同一个工具，进程内执行，不需要端口。

外部 agent 走 MCP → bridge → 作业台账；内嵌 loop 走 `dispatch()` → 同一个 worker 命令。
两边用的是同一份契约里的 `command` + `operation`，所以"内部/外部"只差一次跨进程。

这组测试钉住四件事：
① 只读工具能在进程内跑通并拿到终止事件；
② 长任务被**拒绝**（必须走作业台账，否则内嵌调用会卡住且无法取消）；
③ `operation` 会被自动注入，调用方不必重复填；
④ 无论成功还是抛异常，事件 sink 都必须复原（否则后续 emit 会凭空消失）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import agent_contract  # noqa: E402
import worker_protocol  # noqa: E402


class DispatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        worker_protocol.set_event_sink(None)

    def test_read_only_tools_run_in_process(self) -> None:
        for tool_name, expected_type in (
            ("system.health", "health"),
            ("system.contract", "agent_contract"),
            ("model.list", "models"),
        ):
            result = agent_contract.dispatch(tool_name, {})
            self.assertEqual(result["code"], 0, tool_name)
            self.assertTrue(result["ok"], tool_name)
            self.assertEqual(result["result"]["type"], expected_type, tool_name)

    def test_long_running_tools_are_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            agent_contract.dispatch("model.download", {"modelId": "media:lama"})
        self.assertIn("job ledger", str(caught.exception))

    def test_unknown_tool_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            agent_contract.dispatch("does.not.exist")

    def test_operation_is_injected_from_the_contract(self) -> None:
        seen: dict = {}

        def fake_run(command: str, payload: dict) -> int:
            seen["command"] = command
            seen["payload"] = payload
            worker_protocol.emit("fake_result", {"ok": True})
            return 0

        import worker

        with mock.patch.object(worker, "run_command", side_effect=fake_run):
            result = agent_contract.dispatch("audio.convert", {"inputs": ["a.wav"]})

        self.assertEqual(seen["command"], "audio_tools")
        self.assertEqual(seen["payload"]["operation"], "convert")
        self.assertEqual(seen["payload"]["inputs"], ["a.wav"])
        self.assertTrue(result["ok"])

    def test_sink_is_restored_after_success(self) -> None:
        agent_contract.dispatch("system.health", {})
        self.assertIsNone(worker_protocol._EVENT_SINK)

    def test_sink_is_restored_after_the_command_raises(self) -> None:
        import worker

        def boom(command: str, payload: dict) -> int:
            raise RuntimeError("handler exploded")

        with mock.patch.object(worker, "run_command", side_effect=boom):
            with self.assertRaises(RuntimeError):
                agent_contract.dispatch("system.health", {})
        self.assertIsNone(worker_protocol._EVENT_SINK)

    def test_error_event_is_captured_not_printed(self) -> None:
        """错误也要被捕获成事件，而不是写到 stdout 里丢掉。"""
        result = agent_contract.dispatch("media.evaluate", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], 1)
        self.assertEqual(result["result"]["type"], "error")
        self.assertIn("inputs", result["result"]["payload"]["message"])


if __name__ == "__main__":
    unittest.main()

    def test_media_result_with_failures_is_not_ok(self) -> None:
        """媒体命令返回 0 但 failed[] 非空时不能算成功。"""
        import worker

        def fake_run(command: str, payload: dict) -> int:
            worker_protocol.emit(
                "audio_tool_result",
                {"outputs": [], "completed": 0, "failed": [{"path": "a.png", "error": "no watermark"}]},
            )
            return 0

        with mock.patch.object(worker, "run_command", side_effect=fake_run):
            outcome = agent_contract.dispatch("media.image.upscale", {
                "modality": "image",
                "inputs": ["a.png"],
                "outputDir": "out",
                "steps": [{"capability": "upscale", "bindings": {"upscaler": "media:real-esrgan-x2"}}],
            })
        self.assertFalse(outcome["ok"])
