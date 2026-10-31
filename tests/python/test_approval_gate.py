"""审批等级必须被执行，而不只是写在契约里。

`explicit` 代表下载、安装、删除这类不可轻易撤销的动作。以前它只是工具描述里的
一个字段——agent 直接调就会执行。现在桥接层要求显式确认：

    {"ok": false, "requiresConfirmation": true, "approval": "explicit"}

调用方（agent 或内嵌 loop）看到这个就应当去问用户，拿到同意后带
`confirmExplicit: true` 再调一次。
"""
from __future__ import annotations

import sys
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import agent_bridge  # noqa: E402


class ApprovalGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = tempfile.mkdtemp(prefix="mf-approval-")
        cls.bridge = agent_bridge.Bridge(token=None, port=0, data_root=Path(cls.root))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_explicit_tool_is_refused_without_confirmation(self) -> None:
        result = self.bridge.call("model.delete", {"model": "media:lama"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["requiresConfirmation"])
        self.assertEqual(result["approval"], "explicit")
        self.assertIn("confirmExplicit", result["error"])

    def test_confirmation_flag_reaches_the_executor_and_is_not_passed_to_the_worker(self) -> None:
        from surfaces import agent_contract

        captured: dict = {}

        def fake_dispatch(tool_name: str, payload: dict):
            captured["tool"] = tool_name
            captured["payload"] = payload
            return {"ok": True, "result": {"type": "model_deleted", "payload": {}}}

        with mock.patch.object(agent_contract, "dispatch", side_effect=fake_dispatch):
            result = self.bridge.call("model.delete", {"model": "media:lama", "confirmExplicit": True})

        self.assertTrue(result["ok"], result)
        self.assertNotIn("confirmExplicit", captured["payload"], "确认标记不该传给 worker")
        self.assertEqual(captured["payload"]["model"], "media:lama")

    def test_confirmation_does_not_leak_into_arguments_validation(self) -> None:
        from surfaces import agent_contract

        problems = agent_contract.validate_arguments("model.delete", {"model": "media:lama"})
        self.assertEqual(problems, [])

    def test_read_only_and_confirm_tools_need_no_flag(self) -> None:
        for name, arguments in (
            ("system.health", {}),
            ("media.evaluate", {"inputs": ["C:/nope.png"]}),
        ):
            result = self.bridge.call(name, arguments)
            with self.subTest(tool=name):
                self.assertNotIn("requiresConfirmation", result)

    def test_legacy_bridge_tools_are_untouched(self) -> None:
        """models_download 这类历史工具保持原行为，避免打断既有调用方。"""
        legacy = {tool["name"] for tool in self.bridge._bridge_tools()}
        self.assertIn("models_download", legacy)
        with mock.patch.object(self.bridge, "download_model", return_value={"ok": True}) as download:
            result = self.bridge.call("models_download", {"modelId": "media:lama"})
        self.assertTrue(result["ok"])
        download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
