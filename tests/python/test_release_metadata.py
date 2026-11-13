"""Offline release-channel fixtures; no credentials, network or real packages."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_metadata", ROOT / "scripts/generate-update-metadata.py")
METADATA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METADATA)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mediaforge-release-metadata-")))

    def signed_asset(self, slug="windows-x64", version="1.2.3"):
        name = f"MediaForge-{version}-{slug}-update.zip"
        (self.root / name).write_bytes(b"tiny offline archive")
        (self.root / f"{name}.sig").write_text("fixture-signature", encoding="utf-8")
        return name

    def test_selected_windows_release_does_not_create_other_platform_channels(self):
        asset = self.signed_asset()
        result = METADATA.generate(self.root, {"RELEASE_TAG": "v1.2.3", "RELEASE_WINDOWS_BUILD_RESULT": "success", "RELEASE_MACOS_BUILD_RESULT": "skipped", "RELEASE_LINUX_BUILD_RESULT": "skipped"})
        self.assertEqual(set(result), {"stable-v1-windows-x64-update.json", "prerelease-v1-windows-x64-update.json"})
        data = json.loads((self.root / result[0]).read_text(encoding="utf-8"))
        self.assertTrue(data["platforms"]["windows-x86_64"]["url"].endswith("/" + asset))
        self.assertTrue(data["pymss_requires_manual_install"])

    def test_prerelease_tag_cannot_write_stable_even_when_flag_is_missing(self):
        self.signed_asset(version="1.2.3-rc.1")
        self.assertEqual(METADATA.generate(self.root, {"RELEASE_TAG": "v1.2.3-rc.1"}), ["prerelease-v1-windows-x64-update.json"])
        self.assertFalse((self.root / "stable-v1-windows-x64-update.json").exists())

    def test_build_metadata_is_encoded_in_links_and_does_not_imply_prerelease(self):
        self.signed_asset(version="1.2.3+build-test")
        METADATA.generate(self.root, {"RELEASE_TAG": "v1.2.3+build-test", "GITHUB_REPOSITORY": "owner/fork"})
        data = json.loads((self.root / "stable-v1-windows-x64-update.json").read_text(encoding="utf-8"))
        self.assertFalse(data["prerelease"])
        self.assertEqual(data["pymss_manual_install_url"], "https://github.com/owner/fork/releases/tag/v1.2.3%2Bbuild-test")
        self.assertIn("MediaForge-1.2.3%2Bbuild-test-windows", data["platforms"]["windows-x86_64"]["url"])

    def test_missing_successful_platform_fails_before_any_channel_is_written(self):
        self.signed_asset()
        with self.assertRaisesRegex(ValueError, "MACOS"):
            METADATA.generate(self.root, {"RELEASE_TAG": "v1.2.3", "RELEASE_WINDOWS_BUILD_RESULT": "success", "RELEASE_MACOS_BUILD_RESULT": "success"})
        self.assertEqual(list(self.root.glob("*-update.json")), [])
        self.assertFalse((self.root / "managed-update-metadata.txt").exists())

    def test_invalid_signature_on_second_platform_never_partially_replaces_channels(self):
        self.signed_asset()
        (self.root / "MediaForge-1.2.3-linux-x64.AppImage").touch()
        with self.assertRaises(FileNotFoundError):
            METADATA.generate(self.root, {"RELEASE_TAG": "v1.2.3"})
        self.assertEqual(list(self.root.glob("*-update.json")), [])

    def test_invalid_release_identity_is_rejected(self):
        self.signed_asset()
        for environment in ({"RELEASE_TAG": "v../outside"}, {"RELEASE_TAG": "v1.2.3", "GITHUB_REPOSITORY": "owner/repo/../other"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                METADATA.generate(self.root, environment)


if __name__ == "__main__":
    unittest.main()
