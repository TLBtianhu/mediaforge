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
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "tools":
            source = ast.unparse(node)
            return set(re.findall(r"'name': '([a-z_]+)'", source))
    raise AssertionError("Bridge.tools() not found")


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
            "feedback_submit",
            "agent_contract",
            "evaluate_media",
        }
        self.assertEqual(declared_tools(), expected)

    def test_bridge_class_exposes_both_methods(self) -> None:
        self.assertTrue(hasattr(agent_bridge.Bridge, "tools"))
        self.assertTrue(hasattr(agent_bridge.Bridge, "call"))


if __name__ == "__main__":
    unittest.main()
