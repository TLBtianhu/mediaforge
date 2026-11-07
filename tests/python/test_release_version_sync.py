"""Version synchronization uses a tiny checkout and never invokes a build."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is required")
class ReleaseVersionSyncTests(unittest.TestCase):
    def fixture(self, root):
        (root / "scripts").mkdir()
        (root / "src-tauri").mkdir()
        shutil.copyfile(ROOT / "scripts/sync-build-version.ps1", root / "scripts/sync-build-version.ps1")
        (root / "package.json").write_text('{\n  "version": "0.0.31"\n}\n')
        (root / "src-tauri/tauri.conf.json").write_text('{\n  "version": "0.0.31"\n}\n')
        (root / "src-tauri/Cargo.toml").write_text('[package]\nname = "pymss-studio"\nversion = "0.0.31"\n')
        (root / "src-tauri/Cargo.lock").write_text('version = 4\n\n[[package]]\nname = "pymss-studio"\nversion = "0.0.31"\n\n[[package]]\nname = "serde"\nversion = "1.0.0"\n')

    def run_sync(self, root, version):
        env = {key: value for key, value in os.environ.items() if not key.startswith("TAURI_SIGNING_")}
        return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/sync-build-version.ps1"), "-Version", version], cwd=root, env=env, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_all_four_files_change_without_updating_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            result = self.run_sync(root, "v0.0.32")
            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in ("package.json", "src-tauri/tauri.conf.json"):
                self.assertEqual(json.loads((root / relative).read_text())["version"], "0.0.32")
            self.assertIn('version = "0.0.32"', (root / "src-tauri/Cargo.toml").read_text())
            lock = (root / "src-tauri/Cargo.lock").read_text()
            self.assertIn('name = "pymss-studio"\nversion = "0.0.32"', lock)
            self.assertIn('name = "serde"\nversion = "1.0.0"', lock)

    def test_invalid_or_unsafe_version_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            paths = [root / relative for relative in ("package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml", "src-tauri/Cargo.lock")]
            before = [path.read_bytes() for path in paths]
            for version in ("../0.0.32", "01.2.3", "1.2.3\nother"):
                self.assertNotEqual(self.run_sync(root, version).returncode, 0)
                self.assertEqual([path.read_bytes() for path in paths], before)

    def test_missing_lock_identity_fails_before_any_file_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            (root / "src-tauri/Cargo.lock").write_text('version = 4\n')
            before = (root / "package.json").read_bytes()
            self.assertNotEqual(self.run_sync(root, "0.0.32").returncode, 0)
            self.assertEqual((root / "package.json").read_bytes(), before)
