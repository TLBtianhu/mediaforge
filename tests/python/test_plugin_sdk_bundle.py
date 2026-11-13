from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class PluginSdkBundleTests(unittest.TestCase):
    def resource_config(self, platform):
        with tempfile.TemporaryDirectory(prefix="mf-sdk-bundle-fixture-") as directory:
            resources = Path(directory) / "resources"
            helper = ROOT / "scripts/posix-release.py"
            # Native interpreter validation has its own ABI tests. This fixture
            # exercises the real stage/config entry point for SDK placement.
            cli = "import runpy,sys; from unittest import mock; m=runpy.run_path(sys.argv[1]); " \
                  "sys.argv=sys.argv[1:]; " \
                  "m['main'].__globals__['check_runtime_version']=mock.Mock(); m['main']()"
            result = subprocess.run([sys.executable, "-B", "-c", cli, str(helper), "stage", str(resources)],
                                    capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, "-B", "-c", cli, str(helper), "config", str(resources), "--platform", platform],
                                    capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["bundle"]["resources"], resources

    def test_macos_release_maps_the_sdk_to_the_protocol_resource_directory(self):
        workflow = (ROOT / ".github/workflows/release-macos.yml").read_text(encoding="utf-8")
        step = workflow.split("- name: Build Tauri app bundle", 1)[1].split("- name:", 1)[0]
        self.assertIn("scripts/posix-release.py config src-tauri/resources --platform macos", step)
        resources, staged = self.resource_config("macos")
        sdk = ROOT / "src-tauri/resources/plugin-sdk"
        self.assertEqual(resources[str((staged / "plugin-sdk").resolve())], "resources/plugin-sdk")
        for filename in ["pymss-sdk.js", "pymss-sdk.css"]:
            self.assertGreater((sdk / filename).stat().st_size, 0)

    def test_macos_release_checks_both_sdk_files_in_the_finished_bundle(self):
        workflow = (ROOT / ".github/workflows/release-macos.yml").read_text(encoding="utf-8")
        verification = workflow.split("- name: Verify bundled macOS resources", 1)[1].split("- name:", 1)[0]
        for filename in ["pymss-sdk.js", "pymss-sdk.css"]:
            self.assertIn(f'test -s "$RES/resources/plugin-sdk/{filename}"', verification)

    def test_linux_release_maps_the_sdk_and_checks_the_finished_appimage(self):
        workflow = (ROOT / ".github/workflows/release-linux.yml").read_text(encoding="utf-8")
        step = workflow.split("- name: Build AppImage", 1)[1].split("- name:", 1)[0]
        self.assertIn("scripts/posix-release.py config src-tauri/resources --platform linux", step)
        resources, staged = self.resource_config("linux")
        self.assertEqual(resources[str((staged / "plugin-sdk").resolve())], "resources/plugin-sdk")
        verification = (ROOT / "scripts/verify-linux-appimage.sh").read_text(encoding="utf-8")
        for filename in ["pymss-sdk.js", "pymss-sdk.css"]:
            self.assertIn(f'test -s "$RESOURCES/resources/plugin-sdk/{filename}"', verification)

    def test_every_windows_portable_variant_stages_the_sdk(self):
        workflow = (ROOT / ".github/workflows/release-windows.yml").read_text(encoding="utf-8")
        stages = re.findall(r"(?ms)^      - name: Stage[^\n]*\n(.*?)(?=^      - |\Z)", workflow)
        self.assertGreaterEqual(len(stages), 4)
        for stage in stages:
            self.assertIn("./scripts/stage-plugin-sdk.ps1 -StagePath $stage", stage)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell staging")
    def test_windows_sdk_staging_copies_the_actual_files(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "Portable app"
            result = subprocess.run([
                powershell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "scripts/stage-plugin-sdk.ps1"),
                "-StagePath", str(stage),
            ], capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            for filename in ["pymss-sdk.js", "pymss-sdk.css"]:
                source = ROOT / "src-tauri/resources/plugin-sdk" / filename
                self.assertEqual((stage / "python/plugin-sdk" / filename).read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
