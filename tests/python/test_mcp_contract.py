"""工具面由 manifest 生成：数量、命令、模型清单、协议版本。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

import agent_contract  # noqa: E402
from shared import protocol_contract  # noqa: E402
from media_ops import capabilities  # noqa: E402


class ContractGenerationTests(unittest.TestCase):
    def test_media_capability_tools_are_generated_from_the_manifest(self) -> None:
        contract = agent_contract.build_contract()
        declared = {
            f"media.{modality}.{capability['id']}"
            for capability in capabilities.CAPABILITIES
            for modality in capability["modalities"]
        }
        generated = {tool["name"] for tool in contract["tools"] if tool["name"].startswith("media.")}
        self.assertTrue(declared <= generated, f"缺少工具：{sorted(declared - generated)}")

    def test_media_capability_tools_dispatch_through_the_protocol_entry(self) -> None:
        contract = agent_contract.build_contract()
        for tool in contract["tools"]:
            if tool["name"].startswith("media.") and tool.get("operation") in {"media_image", "media_workflow"}:
                with self.subTest(tool=tool["name"]):
                    self.assertEqual(tool["command"], "media_tool")

    def test_tool_model_list_follows_the_catalog(self) -> None:
        before = next(
            tool for tool in agent_contract._media_tools() if tool["name"].endswith(".upscale")
        )["models"]
        self.assertIn("media:real-esrgan-x4", before)

        # 模拟 catalog/能力里新增一个模型：工具模型清单必须自动包含它
        patched = [dict(capability) for capability in capabilities.CAPABILITIES]
        for capability in patched:
            if capability["id"] == "upscale":
                capability["modelIds"] = [*capability.get("modelIds", []), "media:test-model-x4"]
        with mock.patch.object(capabilities, "CAPABILITIES", patched):
            after = next(
                tool for tool in agent_contract._media_tools() if tool["name"].endswith(".upscale")
            )["models"]
        self.assertIn("media:test-model-x4", after)

    def test_contract_reports_the_mcp_schema_version(self) -> None:
        contract = agent_contract.build_contract()
        self.assertEqual(contract["mcpSchemaVersion"], protocol_contract.mcp_schema_version())

    def test_mismatched_schema_version_is_a_readable_error(self) -> None:
        broken = json_load = protocol_contract.load_protocol()
        broken = dict(broken)
        broken["mcpSchemaVersion"] = 99
        with mock.patch.object(protocol_contract, "load_protocol", return_value=broken):
            with self.assertRaises(agent_contract.ContractMismatchError) as context:
                agent_contract.build_contract()
            self.assertEqual(context.exception.code, "MCP_SCHEMA_MISMATCH")
            self.assertIn("mcpSchemaVersion", str(context.exception))
            with self.assertRaises(agent_contract.ContractMismatchError):
                agent_contract.find_tool("media.image.upscale")


class BridgeDiagnosticsTests(unittest.TestCase):
    def test_bridge_keeps_a_readable_tool_when_the_contract_is_incompatible(self) -> None:
        from agent_bridge import Bridge  # noqa: E402

        bridge = Bridge.__new__(Bridge)
        with mock.patch.object(
            agent_contract,
            "build_contract",
            side_effect=agent_contract.ContractMismatchError("MCP_SCHEMA_MISMATCH", "版本不匹配"),
        ):
            tools = bridge._contract_tools()
        self.assertEqual(len(tools), 1)
        self.assertIn("MCP_SCHEMA_MISMATCH", tools[0]["description"])


if __name__ == "__main__":
    unittest.main()
