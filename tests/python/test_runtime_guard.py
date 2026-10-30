"""运行时清单与能力门的契约测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from media_ops import runtime_guard  # noqa: E402


class RuntimeManifestTests(unittest.TestCase):
    def test_manifest_declares_the_asset_the_binary_pins(self) -> None:
        data = runtime_guard.load_manifest()
        self.assertEqual(data["schemaVersion"], runtime_guard.SCHEMA_VERSION)
        rust = (ROOT / "src-tauri" / "src" / "runtime_assets.rs").read_text(encoding="utf-8")
        pinned = rust.split('pub const ASSET_ID: &str = "', 1)[1].split('"', 1)[0]
        ids = [item["assetId"] for item in data["runtimes"]]
        self.assertIn(pinned, ids, "runtime.json 与 Rust 里钉死的资产必须一致")

    def test_every_declared_capability_exists_in_the_capability_manifest(self) -> None:
        declared = {item["id"] for item in json.loads(
            (ROOT / "python" / "media_ops" / "capabilities.json").read_text(encoding="utf-8")
        )["capabilities"]}
        for runtime in runtime_guard.load_manifest()["runtimes"]:
            for capability in runtime.get("capabilities") or []:
                with self.subTest(capability=capability):
                    self.assertIn(capability, declared)


class CapabilityGateTests(unittest.TestCase):
    def test_describe_falls_back_when_there_is_no_pointer(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            runtime_guard, "data_root", return_value=Path(tempfile.gettempdir()) / "mediaforge-no-such-root"
        ):
            described = runtime_guard.describe()
        self.assertEqual(described["assetId"], runtime_guard.load_manifest()["fallbackAssetId"])
        self.assertEqual(described["source"], "fallback")
        self.assertIn("upscale", described["capabilities"])

    def test_unknown_capability_is_reported_missing(self) -> None:
        self.assertEqual(runtime_guard.missing_capabilities(["upscale"]), [])
        self.assertEqual(runtime_guard.missing_capabilities(["teleport"]), ["teleport"])

    def test_pointer_selects_a_declared_asset_by_id(self) -> None:
        data = runtime_guard.load_manifest()
        asset = data["runtimes"][0]["assetId"]
        with mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_ASSET": asset}):
            described = runtime_guard.describe()
        self.assertEqual(described["assetId"], asset)
        self.assertEqual(described["source"], "pointer")


if __name__ == "__main__":
    unittest.main()
