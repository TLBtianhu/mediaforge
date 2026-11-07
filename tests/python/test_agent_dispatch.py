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
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from surfaces import agent_contract  # noqa: E402
from shared import worker_protocol  # noqa: E402


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
        self.assertIsNone(worker_protocol._EVENT_SINK.get())

    def test_sink_is_restored_after_the_command_raises(self) -> None:
        import worker

        def boom(command: str, payload: dict) -> int:
            raise RuntimeError("handler exploded")

        with mock.patch.object(worker, "run_command", side_effect=boom):
            with self.assertRaises(RuntimeError):
                agent_contract.dispatch("system.health", {})
        self.assertIsNone(worker_protocol._EVENT_SINK.get())

    def test_dispatch_restores_the_callers_sink_after_success_or_failure(self) -> None:
        import worker

        for raises in (False, True):
            with self.subTest(raises=raises):
                outer_events = []

                def outer_sink(kind, data, request_id, task_id):
                    outer_events.append({"type": kind, "payload": data})

                def fake_run(command, payload):
                    worker_protocol.emit("health", {"owner": "dispatch"})
                    if raises:
                        raise RuntimeError("handler exploded")
                    return 0

                with worker_protocol.bind_event_sink(outer_sink):
                    with mock.patch.object(worker, "run_command", side_effect=fake_run):
                        if raises:
                            with self.assertRaisesRegex(RuntimeError, "handler exploded"):
                                agent_contract.dispatch("system.health", {}, retry=False)
                        else:
                            outcome = agent_contract.dispatch("system.health", {}, retry=False)
                            self.assertEqual(outcome["result"]["payload"], {"owner": "dispatch"})
                    worker_protocol.emit("health", {"owner": "caller"})

                self.assertEqual(outer_events, [{"type": "health", "payload": {"owner": "caller"}}])
                self.assertIsNone(worker_protocol._EVENT_SINK.get())

    def test_nested_dispatch_restores_outer_capture_even_when_inner_raises(self) -> None:
        import worker

        for raises in (False, True):
            with self.subTest(raises=raises):
                inner_results = []
                calls = 0

                def fake_run(command, payload):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        worker_protocol.emit("health", {"owner": "outer-before"})
                        if raises:
                            with self.assertRaisesRegex(RuntimeError, "inner exploded"):
                                agent_contract.dispatch("system.health", {}, retry=False)
                        else:
                            inner_results.append(agent_contract.dispatch("system.health", {}, retry=False))
                        worker_protocol.emit("health", {"owner": "outer-after"})
                    else:
                        worker_protocol.emit("health", {"owner": "inner"})
                        if raises:
                            raise RuntimeError("inner exploded")
                    return 0

                with mock.patch.object(worker, "run_command", side_effect=fake_run):
                    outcome = agent_contract.dispatch("system.health", {}, retry=False)

                self.assertTrue(outcome["ok"])
                self.assertEqual([event["payload"]["owner"] for event in outcome["events"]], ["outer-before", "outer-after"])
                if not raises:
                    self.assertEqual([event["payload"]["owner"] for event in inner_results[0]["events"]], ["inner"])
                self.assertIsNone(worker_protocol._EVENT_SINK.get())

    def test_concurrent_dispatch_keeps_each_requests_events_when_another_finishes(self) -> None:
        """Keep B active while A emits and exits; global sinks steal A and drop B."""
        import worker

        for raises in (False, True):
            with self.subTest(first_raises=raises):
                first_started = threading.Event()
                second_started = threading.Event()
                first_finished = threading.Event()

                def fake_run(command, payload):
                    owner = payload["inputs"][0]
                    if owner == "first":
                        first_started.set()
                        if not second_started.wait(5):
                            raise RuntimeError("second dispatch did not start")
                        worker_protocol.emit("media_evaluation", {"owner": owner})
                        if raises:
                            raise RuntimeError("first exploded")
                    else:
                        second_started.set()
                        if not first_finished.wait(5):
                            raise RuntimeError("first dispatch did not finish")
                        worker_protocol.emit("media_evaluation", {"owner": owner})
                    return 0

                def run_first():
                    try:
                        return agent_contract.dispatch("media.evaluate", {"inputs": ["first"]}, retry=False)
                    finally:
                        first_finished.set()

                with (
                    mock.patch.object(worker, "run_command", side_effect=fake_run),
                    mock.patch.object(worker_protocol, "_write_protocol_message", side_effect=AssertionError("request event escaped to stdout")),
                    ThreadPoolExecutor(max_workers=2) as executor,
                ):
                    first = executor.submit(run_first)
                    self.assertTrue(first_started.wait(5), "first dispatch did not start")
                    second = executor.submit(agent_contract.dispatch, "media.evaluate", {"inputs": ["second"]}, retry=False)
                    if raises:
                        with self.assertRaisesRegex(RuntimeError, "first exploded"):
                            first.result(timeout=10)
                    else:
                        result = first.result(timeout=10)
                        self.assertTrue(result["ok"])
                        self.assertEqual(result["events"], [{"type": "media_evaluation", "payload": {"owner": "first"}}])
                    result = second.result(timeout=10)
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["events"], [{"type": "media_evaluation", "payload": {"owner": "second"}}])
                self.assertIsNone(worker_protocol._EVENT_SINK.get())

    def test_error_event_is_captured_not_printed(self) -> None:
        """错误也要被捕获成事件，而不是写到 stdout 里丢掉。"""
        result = agent_contract.dispatch("media.evaluate", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], 1)
        self.assertEqual(result["result"]["type"], "error")
        self.assertIn("inputs", result["result"]["payload"]["message"])

    def test_media_result_with_failures_is_not_ok(self) -> None:
        """媒体命令返回 0 但 failed[] 非空时不能算成功。"""
        import worker

        def fake_run(command: str, payload: dict) -> int:
            worker_protocol.emit(
                "task.result",
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


if __name__ == "__main__":
    unittest.main()
