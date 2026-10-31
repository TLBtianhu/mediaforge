"""MCP 桥接的工具表必须和 `call()` 的分派对齐。

2026-10-29 出过一次事故：往 `tools()` 里插两个新工具时，替换锚点把 `feedback_submit`
整块吞掉了 —— 工具从列表里消失，但 `call()` 里的分支还在。用户看不到、agent 调不到，
而测试全绿。这组测试就是钉这个：**列表里有的必须能调，能调的必须在列表里**。
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import agent_bridge  # noqa: E402

SOURCE = (PYTHON_DIR / "agent_bridge.py").read_text(encoding="utf-8")


def declared_tools() -> set[str]:
    """手写工具表（`_bridge_tools`）；契约生成的那部分由 BridgeContractSurfaceTests 覆盖。"""
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bridge_tools":
            source = ast.unparse(node)
            return set(re.findall(r"'name': '([a-z_]+)'", source))
    raise AssertionError("Bridge._bridge_tools() not found")


def dispatched_tools() -> set[str]:
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "call":
            source = ast.unparse(node)
            return set(re.findall(r"name == '([a-z_]+)'", source))
    raise AssertionError("Bridge.call() not found")


class BridgeToolCoverageTests(unittest.TestCase):
    def test_every_declared_tool_is_dispatchable(self) -> None:
        declared = declared_tools()
        dispatched = dispatched_tools()
        self.assertEqual(declared - dispatched, set(), "这些工具在列表里但没有分派分支")

    def test_every_dispatched_tool_is_declared(self) -> None:
        declared = declared_tools()
        dispatched = dispatched_tools()
        self.assertEqual(dispatched - declared, set(), "这些工具有分派分支但没出现在列表里")

    def test_the_known_tool_set_is_intact(self) -> None:
        """钉住清单：少一个就说明替换/合并时被误删（feedback_submit 就这么丢过一次）。"""
        expected = {
            "mediaforge_status",
            "models_list",
            "models_download",
            "capabilities_list",
            "probe",
            "convert",
            "pipeline_plan",
            "pipeline_start",
            "workflow_start",
            "job_status",
            "job_cancel",
            "job_retry",
            "feedback_submit",
            "agent_contract",
            "evaluate_media",
        }
        self.assertEqual(declared_tools(), expected)

    def test_bridge_class_exposes_both_methods(self) -> None:
        self.assertTrue(hasattr(agent_bridge.Bridge, "tools"))
        self.assertTrue(hasattr(agent_bridge.Bridge, "call"))
        self.assertTrue(hasattr(agent_bridge.Bridge, "_bridge_tools"))
        self.assertTrue(hasattr(agent_bridge.Bridge, "_contract_tools"))


class BridgeContractSurfaceTests(unittest.TestCase):
    """bridge 的工具表 = 手写历史工具 + 契约里尚未上表的功能。"""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        import agent_bridge

        cls.root = tempfile.mkdtemp(prefix="mf-bridge-")
        cls.bridge = agent_bridge.Bridge(token=None, port=0, data_root=Path(cls.root))

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls.root, ignore_errors=True)

    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in self.bridge.tools()]
        self.assertEqual(len(names), len(set(names)))

    def test_every_contract_tool_without_an_alias_is_exposed(self) -> None:
        from surfaces import agent_contract

        expected = {
            tool["name"]
            for tool in agent_contract.build_contract()["tools"]
            if not tool.get("bridgeTool")
        }
        exposed = {tool["name"] for tool in self.bridge.tools()}
        missing = expected - exposed
        self.assertEqual(missing, set(), f"这些契约功能没有出现在 MCP 工具表里：{missing}")

    def test_aliased_tools_are_not_advertised_twice(self) -> None:
        exposed = {tool["name"] for tool in self.bridge.tools()}
        # 这些功能已经有了历史名字（models_list / probe …），不该再用契约名重复上表
        for name in ("model.list", "model.download", "media.metadata"):
            self.assertNotIn(name, exposed)

    def test_contract_only_tools_are_callable_in_process(self) -> None:
        result = self.bridge.call("system.health", {})
        self.assertTrue(result["ok"], result)
        self.assertIn("workerVersion", result)

    def test_long_running_contract_tools_go_through_the_job_ledger(self) -> None:
        from unittest import mock

        fake_job = mock.Mock()
        fake_job.job_id = "job_test"
        fake_job.snapshot.return_value = {"jobId": "job_test", "status": "queued"}
        with mock.patch.object(self.bridge.jobs, "start", return_value=fake_job) as start:
            result = self.bridge.call("audio.separate", {"model": "bs_roformer", "inputs": ["a.wav"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["jobId"], "job_test")
        start.assert_called_once()
        command, payload = start.call_args[0]
        self.assertEqual(command, "infer")
        self.assertEqual(payload["model"], "bs_roformer")

    def test_legacy_surface_switch_hides_contract_tools(self) -> None:
        from unittest import mock

        with mock.patch.dict("os.environ", {"MEDIAFORGE_MCP_SURFACE": "legacy"}, clear=False):
            names = {tool["name"] for tool in self.bridge.tools()}
        self.assertEqual(names, declared_tools())

    def test_mcp_round_trip_over_jsonrpc(self) -> None:
        """端到端：initialize → tools/list → tools/call，全程走 MCP 协议形状。"""
        init = self.bridge.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "mediaforge")

        listed = self.bridge.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("system.health", names)
        self.assertIn("agent_contract", names)

        called = self.bridge.rpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "system.health", "arguments": {}},
            }
        )
        self.assertFalse(called["result"]["isError"])
        self.assertIn("workerVersion", called["result"]["structuredContent"])

    def test_mcp_error_is_reported_not_raised(self) -> None:
        failed = self.bridge.rpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "no.such.tool", "arguments": {}},
            }
        )
        self.assertTrue(failed["result"]["isError"])

    def test_unknown_tool_still_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.bridge.call("definitely.not.a.tool", {})

    def test_aliased_tool_can_still_be_called_by_contract_name(self) -> None:
        """别名只是"不重复上表"，不代表不能调——按契约名调也要能用。"""
        result = self.bridge.call("media.metadata", {"inputs": []})
        self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()
