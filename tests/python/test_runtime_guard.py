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
from shared import runtime_platform  # noqa: E402


class RuntimeManifestTests(unittest.TestCase):
    def test_manifest_rejects_old_schema_and_missing_asset_target(self) -> None:
        manifest = runtime_guard.load_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            with mock.patch.object(runtime_guard, "manifest_path", return_value=path):
                path.write_text(json.dumps({**manifest, "schemaVersion": 2}), encoding="utf-8")
                with self.assertRaises(runtime_guard.RuntimeManifestError) as old_schema:
                    runtime_guard.load_manifest()
                self.assertEqual(old_schema.exception.code, "RUNTIME_SCHEMA_MISMATCH")
                manifest["runtimes"][0].pop("arch")
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(runtime_guard.RuntimeManifestError) as missing_arch:
                    runtime_guard.load_manifest()
                self.assertEqual(missing_arch.exception.code, "RUNTIME_MANIFEST_INVALID")

    def test_manifest_is_the_single_source_for_the_pinned_asset(self) -> None:
        data = runtime_guard.load_manifest()
        self.assertEqual(data["schemaVersion"], runtime_guard.SCHEMA_VERSION)
        ids = [item["assetId"] for item in data["runtimes"]]
        self.assertIn(data["fallbackAssetId"], ids, "fallbackAssetId 必须指向清单里的一项")
        # P5 起 Rust 不再写死资产常量，而是直接 include 这份清单。
        rust = (ROOT / "src-tauri" / "src" / "runtime_assets.rs").read_text(encoding="utf-8")
        self.assertIn('include_str!("../../manifest/runtime.json")', rust)
        self.assertNotIn("pub const ASSET_SHA256", rust, "资产信息应只在 manifest 里")

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
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(runtime_guard, "active_asset_id", return_value=None), \
             mock.patch.object(runtime_guard, "host_target", return_value=("windows", "x86_64")):
            described = runtime_guard.describe()
        self.assertEqual(described["assetId"], runtime_guard.load_manifest()["fallbackAssetId"])
        self.assertEqual(described["source"], "fallback")
        self.assertIn("upscale", described["capabilities"])

    def test_unknown_capability_is_reported_missing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(runtime_guard, "active_asset_id", return_value=None), \
             mock.patch.object(runtime_guard, "host_target", return_value=("windows", "x86_64")):
            self.assertEqual(runtime_guard.missing_capabilities(["upscale"]), [])
            self.assertEqual(runtime_guard.missing_capabilities(["teleport"]), ["teleport"])

    def test_pointer_selects_a_declared_asset_by_id(self) -> None:
        data = runtime_guard.load_manifest()
        asset = data["runtimes"][0]["assetId"]
        with mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_ASSET": asset}), \
             mock.patch.object(runtime_guard, "host_target", return_value=("windows", "x86_64")):
            described = runtime_guard.describe()
        self.assertEqual(described["assetId"], asset)
        self.assertEqual(described["source"], "pointer")

    def test_foreign_fallback_does_not_mask_local_capabilities(self) -> None:
        for target in (("linux", "x86_64"), ("macos", "aarch64"), ("windows", "aarch64")):
            with self.subTest(target=target), mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(runtime_guard, "active_asset_id", return_value=None), \
                 mock.patch.object(runtime_guard, "host_target", return_value=target), \
                 mock.patch("media_ops.capabilities.adapter_capability_ids", return_value={"upscale"}):
                described = runtime_guard.describe()
                self.assertIsNone(described["assetId"])
                self.assertEqual(described["source"], "local")
                self.assertEqual(described["capabilities"], ["upscale"])
                self.assertEqual((described["platform"], described["arch"]), target)

    def test_explicit_dynamic_or_newer_asset_cannot_select_the_catalog_fallback(self) -> None:
        for asset in ("", "runtime-newer-than-app"):
            with self.subTest(asset=asset), mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_ASSET": asset}), \
                 mock.patch.object(runtime_guard, "host_target", return_value=("windows", "x86_64")), \
                 mock.patch("media_ops.capabilities.adapter_capability_ids", return_value={"upscale"}):
                described = runtime_guard.describe()
                self.assertEqual(described["assetId"], asset or None)
                self.assertEqual(described["source"], "local")
                self.assertEqual(described["capabilities"], ["upscale"])

    def test_explicit_foreign_asset_does_not_supply_the_local_backend(self) -> None:
        foreign_asset = runtime_guard.load_manifest()["fallbackAssetId"]
        with mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_ASSET": foreign_asset}), \
             mock.patch.object(runtime_guard, "host_target", return_value=("linux", "x86_64")), \
             mock.patch("media_ops.capabilities.adapter_capability_ids", return_value={"upscale"}):
            described = runtime_guard.describe()
        self.assertEqual(described["source"], "local")
        self.assertIsNone(described["backend"])
        self.assertEqual(described["capabilities"], ["upscale"])

    def test_minimal_bootstrap_can_describe_no_local_inference_capabilities(self) -> None:
        with mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_ASSET": ""}), \
             mock.patch.object(runtime_guard, "host_target", return_value=("linux", "x86_64")), \
             mock.patch("media_ops.capabilities.adapter_capability_ids", side_effect=ModuleNotFoundError("numpy")):
            self.assertEqual(runtime_guard.describe()["capabilities"], [])
            self.assertEqual(runtime_guard.missing_capabilities([]), [])

    def test_windows_architecture_uses_interpreter_target_under_emulation(self) -> None:
        with mock.patch.object(runtime_guard.sys, "platform", "win32"), \
             mock.patch.object(runtime_platform.sysconfig, "get_platform", return_value="win-amd64"), \
             mock.patch.object(runtime_platform.platform, "machine", return_value="ARM64"):
            self.assertEqual(runtime_guard.host_target(), ("windows", "x86_64"))


if __name__ == "__main__":
    unittest.main()
