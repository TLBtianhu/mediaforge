"""协议契约的守卫：manifest/protocol.json 与 catalog 的一致性。"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from shared import protocol_contract  # noqa: E402


class ProtocolManifestTests(unittest.TestCase):
    def test_schema_version_is_the_one_the_code_understands(self) -> None:
        data = protocol_contract.load_protocol()
        self.assertEqual(data["schemaVersion"], protocol_contract.SCHEMA_VERSION)
        self.assertIn("media", data["domains"])

    def test_every_declared_command_resolves_to_a_handler(self) -> None:
        data = protocol_contract.load_protocol()
        for domain_id, domain in data["domains"].items():
            for name, entry in domain["commands"].items():
                with self.subTest(command=f"{domain_id}.{name}"):
                    self.assertEqual(protocol_contract.handler_for(domain_id, name), entry["handler"])

    def test_unknown_command_is_rejected_with_a_code(self) -> None:
        with self.assertRaises(protocol_contract.ProtocolError) as context:
            protocol_contract.handler_for("media", "does-not-exist")
        self.assertEqual(context.exception.code, "PROTOCOL_UNKNOWN_COMMAND")

    def test_legacy_events_map_into_the_canonical_vocabulary(self) -> None:
        canonical = set(protocol_contract.canonical_events())
        self.assertIn("task.progress", canonical)
        for legacy in protocol_contract.declared_legacy_events():
            with self.subTest(event=legacy):
                self.assertIn(protocol_contract.canonical_event(legacy), canonical)

    def test_handlers_resolve_to_existing_module_functions(self) -> None:
        """handler 是 module:function；静态确认模块与函数都在，不 import（避免副作用）。"""
        from shared.protocol_contract import command_entries

        for command_id, entry in command_entries().items():
            with self.subTest(command=command_id):
                module_name, function_name = entry["handler"].split(":", 1)
                path = ROOT / "python" / Path(*module_name.split(".")).with_suffix(".py")
                self.assertTrue(path.is_file(), f"{module_name} 不存在")
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, rf"(?m)^def {re.escape(function_name)}\(", f"{function_name} 未定义")

    def test_every_command_has_a_unique_handler_entry(self) -> None:
        from shared.protocol_contract import command_entries

        entries = command_entries()
        self.assertGreaterEqual(len(entries), 40)
        for command_id, entry in entries.items():
            with self.subTest(command=command_id):
                self.assertIn(entry.get("proxy"), {"configured", "payload", "none"})


class CatalogInvariantTests(unittest.TestCase):
    def test_available_models_declare_assets_and_planned_ones_declare_a_blocker(self) -> None:
        catalog = json.loads((ROOT / "python" / "media_ops" / "media_catalog.json").read_text(encoding="utf-8"))
        for item in catalog["models"]:
            with self.subTest(model=item["id"]):
                if item.get("status") == "available":
                    for field in ("size", "url", "licenseSpdx"):
                        self.assertTrue(item.get(field), f"{field} 缺失")
                else:
                    self.assertTrue(item.get("blockedReason"), "未可用却没有写阻塞原因")

    def test_capabilities_only_reference_models_that_exist(self) -> None:
        capabilities = json.loads(
            (ROOT / "python" / "media_ops" / "capabilities.json").read_text(encoding="utf-8")
        )
        catalog = json.loads((ROOT / "python" / "media_ops" / "media_catalog.json").read_text(encoding="utf-8"))
        known = {f"media:{item['id']}" for item in catalog["models"]}
        for capability in capabilities["capabilities"]:
            for model_id in capability.get("modelIds") or []:
                with self.subTest(model=model_id):
                    self.assertIn(model_id, known)


if __name__ == "__main__":
    unittest.main()
