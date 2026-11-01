"""versions.json 的形状与"运行时描述符来自唯一真源"的契约。"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location("make_versions", ROOT / "scripts" / "make-versions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_versions"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VersionsManifestTests(unittest.TestCase):
    def test_document_carries_the_two_live_axes(self) -> None:
        doc = load_script().build()
        self.assertEqual(doc["schemaVersion"], 1)
        self.assertIn("version", doc["app"])
        self.assertIn("assetId", doc["runtime"])
        # catalog 轴已删除：catalog 随应用发布，没有独立更新通道
        self.assertNotIn("catalog", doc)

    def test_runtime_descriptor_matches_the_manifest(self) -> None:
        doc = load_script().build()
        manifest = json.loads((ROOT / "manifest" / "runtime.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["runtimes"] if item["assetId"] == doc["runtime"]["assetId"])
        self.assertEqual(doc["runtime"]["sha256"], entry["sha256"])
        self.assertEqual(doc["runtime"]["size"], entry["size"])
        self.assertEqual(doc["runtime"]["urls"], entry["urls"])

    def test_runtime_descriptor_is_complete_enough_to_download(self) -> None:
        runtime = load_script().build()["runtime"]
        self.assertEqual(len(runtime["sha256"]), 64)
        self.assertGreater(runtime["size"], 0)
        self.assertTrue(all(url.startswith("https://") for url in runtime["urls"]))
    def test_tauri_signature_envelope_becomes_minisign_text(self) -> None:
        signature = (
            "untrusted comment: signature from tauri secret key\n"
            "c2lnbmF0dXJl\n"
            "trusted comment: timestamp:1\tfile:versions.json\n"
            "Z2xvYmFsLXNpZ25hdHVyZQ==\n"
        )
        envelope = base64.b64encode(signature.encode("utf-8")).decode("ascii")
        self.assertEqual(load_script().decode_tauri_signature(envelope), signature)

    def test_invalid_signature_envelope_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            load_script().decode_tauri_signature("not-base64")


if __name__ == "__main__":
    unittest.main()
