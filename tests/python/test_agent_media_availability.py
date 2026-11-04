from __future__ import annotations

import unittest
from unittest import mock

from media_ops import capabilities, runtime_guard
from surfaces import agent_contract


class AgentMediaAvailabilityTest(unittest.TestCase):
    def test_mcp_availability_requires_adapter_and_runtime_support(self) -> None:
        with mock.patch.object(capabilities, "adapter_capability_ids", return_value={"upscale", "watermark_removal"}), \
             mock.patch.object(capabilities, "default_bindings", return_value={}), \
             mock.patch.object(runtime_guard, "describe", return_value={"capabilities": ["watermark_removal"]}):
            tools = agent_contract._media_tools()
        upscale = next(tool for tool in tools if tool["name"] == "media.image.upscale")
        watermark = next(tool for tool in tools if tool["name"] == "media.image.watermark_removal")
        self.assertFalse(upscale["available"])
        self.assertTrue(watermark["available"])


if __name__ == "__main__":
    unittest.main()
