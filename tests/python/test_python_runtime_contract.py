"""Version gates must reject drift before replacing any prepared directory."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runtime_contract_posix", ROOT / "scripts/posix-release.py")
POSIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POSIX)
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


class PythonRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mf-python312-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {name: os.environ[name] for name in os.environ if not any(part in name.upper() for part in ("KEY", "PASSWORD", "TOKEN", "SECRET"))}
        for name in ("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT", "PYMSS_STUDIO_DATA_DIR",
                     "MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR", "MEDIAFORGE_RUNTIME_ENVS_DIR", "WEBVIEW2_USER_DATA_FOLDER"):
            self.env[name] = str(self.root / "data" / name)

    def source(self):
        source = self.root / "source"
        (source / "scripts").mkdir(parents=True)
        (source / "python").mkdir()
        (source / "python/runtime-manifest.json").write_text('{"python":"3.12","bootstrap":{}}')
        for name in ("prepare-python-runtime.ps1", "prepare-python-runtime.sh", "release-signing.ps1"):
            shutil.copyfile(ROOT / "scripts" / name, source / "scripts" / name)
        (source / "python-runtime").mkdir()
        (source / "python-runtime/keep.txt").write_text("existing bootstrap")
        return source

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "PowerShell preflight")
    def test_windows_prepare_rejects_311_before_removing_existing_bootstrap(self):
        source = self.source()
        with (source / "scripts/release-signing.ps1").open("a", encoding="utf-8") as stream:
            stream.write("\nfunction Get-ReleasePythonVersion { param($PythonPath) '3.11' }\n")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(source / "scripts/prepare-python-runtime.ps1"),
                                 "-Minimal", "-Python", sys.executable], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.12 release contract", result.stderr)
        self.assertEqual((source / "python-runtime/keep.txt").read_text(), "existing bootstrap")

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "PowerShell strict parameters")
    def test_removed_venv_preparation_parameters_are_not_accepted(self):
        source = self.source()
        for parameter in ("-InitialBackend", "-RewriteRuntimeEnvConfigs", "-TemplateRuntimeEnvConfigs"):
            result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(source / "scripts/prepare-python-runtime.ps1"),
                                     "-Minimal", parameter, "cpu"], env=self.env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("NamedParameterNotFound", result.stderr)
        self.assertEqual((source / "python-runtime/keep.txt").read_text(), "existing bootstrap")

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "PowerShell fresh-only preparation")
    def test_preparation_rejects_existing_and_unrelated_destinations_without_cleanup(self):
        source = self.source()
        with (source / "scripts/release-signing.ps1").open("a", encoding="utf-8") as stream:
            stream.write("\nfunction Get-ReleasePythonVersion { param($PythonPath) '3.12' }\n")
        for destination in ("python-runtime", "other-assets", "release/runtime-envs"):
            result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(source / "scripts/prepare-python-runtime.ps1"),
                                     "-Minimal", "-Python", sys.executable, "-RuntimeDir", destination],
                                    env=self.env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr, "fresh destination|exact release/bootstrap")
        self.assertEqual((source / "python-runtime/keep.txt").read_text(), "existing bootstrap")

    def test_shell_wrong_standalone_version_is_rejected_before_runtime_deletion(self):
        git = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git) if os.name == "nt" and git.is_file() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash is unavailable")
        source = self.source()
        env = dict(self.env, PYTHON_BIN=Path(sys.executable).as_posix(), PBS_PYTHON_VERSION="3.11.9",
                   RUNTIME_DIR=(source / "python-runtime").as_posix())
        result = subprocess.run([bash, (source / "scripts/prepare-python-runtime.sh").as_posix(), "minimal"],
                                cwd=source, env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.12 release contract", result.stderr)
        self.assertEqual((source / "python-runtime/keep.txt").read_text(), "existing bootstrap")

    def test_posix_stage_drift_is_rejected_before_deleting_existing_code(self):
        source = self.source()
        (source / "python-runtime/bin").mkdir()
        (source / "python-runtime/bin/python3").write_bytes(b"mock interpreter")
        destination = self.root / "resources"
        (destination / "python").mkdir(parents=True)
        (destination / "python/keep.py").write_text("existing code")
        for actual in ({"python":"3.11","selfContained":True}, {"python":"3.12","selfContained":False}):
            with mock.patch.object(POSIX.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=json.dumps(actual))):
                with self.assertRaisesRegex(ValueError, "self-contained Python 3.12"):
                    POSIX.stage_resources(source, destination)
            self.assertEqual((destination / "python/keep.py").read_text(), "existing code")

    def test_posix_version_probe_accepts_only_real_json_312_self_contained_result(self):
        source = self.source()
        (source / "python-runtime/bin").mkdir()
        (source / "python-runtime/bin/python3").write_bytes(b"mock interpreter")
        with mock.patch.object(POSIX.subprocess, "run", return_value=mock.Mock(returncode=0, stdout='{"python":"3.12","selfContained":true}')):
            POSIX.check_runtime_version(source)


if __name__ == "__main__":
    unittest.main()
