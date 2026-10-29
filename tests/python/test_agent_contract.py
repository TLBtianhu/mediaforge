"""Agent 契约：每个可执行功能都有一条机器可读描述，且覆盖率不许有洞。

这组测试防的是"新增了功能但 agent 用不上"和"契约悄悄过期"：
覆盖率不是靠人维护的清单，而是从 `worker.py` 源码里数出来的命令表，
所以任何新命令没登记，这里会直接红。
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

REQUIRED_FIELDS = {
    "name",
    "title",
    "description",
    "category",
    "command",
    "approval",
    "cost",
    "longRunning",
    "inputSchema",
    "available",
}


class ContractShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = agent_contract.build_contract()
        cls.tools = cls.contract["tools"]
        cls.by_name = {tool["name"]: tool for tool in cls.tools}

    def test_version_is_declared(self) -> None:
        self.assertEqual(self.contract["version"], agent_contract.CONTRACT_VERSION)

    def test_every_tool_is_well_formed(self) -> None:
        for tool in self.tools:
            missing = REQUIRED_FIELDS - set(tool)
            self.assertEqual(missing, set(), f"{tool.get('name')} 缺字段 {missing}")
            self.assertIn(tool["approval"], agent_contract.APPROVAL_LEVELS, tool["name"])
            self.assertIn(tool["cost"], agent_contract.COST_LEVELS, tool["name"])
            self.assertIsInstance(tool["inputSchema"], dict)
            self.assertEqual(tool["inputSchema"].get("type"), "object", tool["name"])
            self.assertTrue(tool["description"].strip(), tool["name"])

    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in self.tools]
        self.assertEqual(len(names), len(set(names)))

    def test_every_tool_points_at_a_real_worker_command(self) -> None:
        commands = set(agent_contract.worker_commands())
        for tool in self.tools:
            self.assertIn(tool["command"], commands, f"{tool['name']} 指向了不存在的命令")


class CoverageTests(unittest.TestCase):
    def test_hard_coded_worker_commands_are_all_covered(self) -> None:
        audit = agent_contract.audit_contract()
        self.assertEqual(audit["missing"], [], f"这些命令没有对应的工具：{audit['missing']}")

    def test_internal_commands_are_declared_with_a_reason(self) -> None:
        audit = agent_contract.audit_contract()
        self.assertEqual(audit["staleInternal"], [], "登记的 internal 命令已经不存在了")
        for name, reason in audit["internal"].items():
            self.assertTrue(reason.strip(), f"{name} 的理由是空的")

    def test_a_newly_added_worker_command_is_reported_missing(self) -> None:
        """模拟"有人加了命令但没登记"——审计必须抓到。"""
        with mock.patch.object(
            agent_contract, "worker_commands", return_value=["brand_new_command", "health"]
        ):
            audit = agent_contract.audit_contract()
        self.assertIn("brand_new_command", audit["missing"])


class ResultContractTests(unittest.TestCase):
    """`resultEvent` 是输出契约：调用方据此判断拿到的是不是想要的结果。"""

    def test_result_events_are_declared_for_known_tools(self) -> None:
        tools = {tool["name"]: tool for tool in agent_contract.build_contract()["tools"]}
        self.assertEqual(tools["system.health"]["resultEvent"], "health")
        self.assertEqual(tools["system.contract"]["resultEvent"], "agent_contract")
        self.assertEqual(tools["media.image.upscale"]["resultEvent"], "audio_tool_result")

    def test_declared_result_event_matches_what_dispatch_returns(self) -> None:
        import agent_contract as contract

        for name in ("system.health", "system.contract", "model.list"):
            tool = contract.find_tool(name)
            outcome = contract.dispatch(name, {})
            self.assertEqual(
                outcome["result"]["type"], tool["resultEvent"], f"{name} 的输出契约与实际不一致"
            )


class ApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.by_name = {tool["name"]: tool for tool in agent_contract.build_contract()["tools"]}

    def test_dangerous_tools_require_explicit_confirmation(self) -> None:
        for name in ("model.download", "model.delete", "runtime.install", "runtime.delete", "plugin.manage"):
            self.assertEqual(self.by_name[name]["approval"], "explicit", name)

    def test_read_only_tools_need_no_confirmation(self) -> None:
        for name in ("model.list", "system.health", "system.env", "media.evaluate", "system.contract"):
            self.assertEqual(self.by_name[name]["approval"], "none", name)

    def test_long_running_tools_are_marked(self) -> None:
        for name in ("model.download", "runtime.install", "audio.separate"):
            self.assertTrue(self.by_name[name]["longRunning"], name)
        self.assertFalse(self.by_name["model.list"]["longRunning"])


class MediaToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools = {tool["name"]: tool for tool in agent_contract.build_contract()["tools"]}

    def _capability_tools(self) -> dict[str, dict]:
        """按 operation 认能力工具，别按名字前缀——`media.metadata` 也是 media.*。"""
        return {
            name: tool
            for name, tool in self.tools.items()
            if tool["operation"] in {"media_image", "media_workflow"}
        }

    def test_media_tools_follow_the_capability_manifest(self) -> None:
        from media_ops import capabilities

        expected = {
            f"media.{modality}.{item['id']}"
            for item in capabilities.CAPABILITIES
            for modality in item["modalities"]
        }
        self.assertEqual(set(self._capability_tools()), expected)

    def test_unimplemented_capabilities_are_flagged_not_hidden(self) -> None:
        from media_ops import capabilities

        implemented = capabilities.adapter_capability_ids()
        for name, tool in self._capability_tools().items():
            capability_id = name.rsplit(".", 1)[1]
            self.assertEqual(
                tool["available"], capability_id in implemented, f"{name} 的 available 标记不准确"
            )

    def test_media_tools_declare_an_evaluation_hook(self) -> None:
        self.assertEqual(self.tools["media.image.upscale"]["evaluate"], "media.evaluate")
        self.assertTrue(self.tools["media.evaluate"]["evaluate"] is None)


if __name__ == "__main__":
    unittest.main()
