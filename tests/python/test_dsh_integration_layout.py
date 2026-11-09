from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


class DshIntegrationLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).parents[2] / "integrations" / "dsh-mediaforge"
        cls.launcher = (cls.root / "launcher.template.cmd").read_text(encoding="utf-8")
        cls.patch = (cls.root / "cordis.patch.yml").read_text(encoding="utf-8")
        cls.register = (cls.root / "register.ps1").read_text(encoding="utf-8")

    def test_launcher_uses_delayed_expansion_for_the_install_root(self) -> None:
        self.assertIn("EnableDelayedExpansion", self.launcher)
        self.assertIn("!MF_INSTALL_ROOT!\\data", self.launcher)
        self.assertIn("!MF_INSTALL_ROOT!\\models", self.launcher)
        self.assertIn("!MF_INSTALL_ROOT!\\runtime-envs", self.launcher)

    def test_shipped_launcher_matches_the_install_template(self) -> None:
        self.assertEqual(self.launcher, (self.root / 'mediaforge-mcp.cmd').read_text(encoding='utf-8'))

    @unittest.skipUnless(os.name == 'nt', 'Windows batch launcher')
    def test_explicit_data_root_is_not_replaced_with_installed_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix='mf-launcher-') as raw:
            root = Path(raw)
            app = root / 'install' / 'app'
            (app / 'python').mkdir(parents=True)
            (app / 'python' / 'agent_bridge.py').write_text(
                'import json, os; print(json.dumps({k: os.environ.get(k) for k in '
                '["MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_MODEL_DIR", "MEDIAFORGE_RUNTIME_ENVS_DIR"]}))', encoding='utf-8'
            )
            env = {key: value for key, value in os.environ.items() if key not in {
                'MEDIAFORGE_DATA_ROOT', 'MEDIAFORGE_DATA_DIR', 'PYMSS_STUDIO_DATA_ROOT',
                'MEDIAFORGE_MODEL_DIR', 'PYMSS_MODEL_DIR', 'MEDIAFORGE_RUNTIME_ENVS_DIR'
            }}
            env.update(MEDIAFORGE_HOME=str(app), MEDIAFORGE_PYTHON=sys.executable, MEDIAFORGE_DATA_ROOT=str(root / 'isolated' / 'data'))
            result = subprocess.run(['cmd', '/d', '/c', str(self.root / 'mediaforge-mcp.cmd')], env=env, capture_output=True, text=True, check=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actual = json.loads(result.stdout)
            self.assertEqual(actual['MEDIAFORGE_DATA_ROOT'], str(root / 'isolated' / 'data'))
            self.assertIsNone(actual['MEDIAFORGE_MODEL_DIR'])
            self.assertIsNone(actual['MEDIAFORGE_RUNTIME_ENVS_DIR'])

    @unittest.skipUnless(os.name == 'nt', 'Windows batch launcher')
    def test_legacy_model_override_is_preserved_without_a_data_override(self) -> None:
        with tempfile.TemporaryDirectory(prefix='mf-launcher-alias-') as raw:
            root = Path(raw)
            app = root / 'install' / 'app'
            (app / 'python').mkdir(parents=True)
            (app / 'python' / 'agent_bridge.py').write_text(
                'import json, os; print(json.dumps({k: os.environ.get(k) for k in '
                '["MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR"]}))', encoding='utf-8'
            )
            env = {key: value for key, value in os.environ.items() if key not in {
                'MEDIAFORGE_DATA_ROOT', 'MEDIAFORGE_DATA_DIR', 'PYMSS_STUDIO_DATA_ROOT',
                'MEDIAFORGE_MODEL_DIR', 'PYMSS_MODEL_DIR', 'MEDIAFORGE_RUNTIME_ENVS_DIR'
            }}
            weights = str(root / 'weights')
            env.update(MEDIAFORGE_HOME=str(app), MEDIAFORGE_PYTHON=sys.executable, PYMSS_MODEL_DIR=weights)
            result = subprocess.run(['cmd', '/d', '/c', str(self.root / 'mediaforge-mcp.cmd')], env=env, capture_output=True, text=True, check=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            actual = json.loads(result.stdout)
            self.assertEqual(actual['PYMSS_MODEL_DIR'], weights)
            self.assertIsNone(actual['MEDIAFORGE_MODEL_DIR'])

    def test_dsh_config_uses_integration_launcher_not_legacy_data_root(self) -> None:
        self.assertIn("integrations\\dsh-mediaforge\\mediaforge-mcp.cmd", self.patch)
        self.assertNotIn(".mediaforge\\bin", self.patch)

    def test_register_supports_explicit_paths_and_does_not_write_legacy_home(self) -> None:
        self.assertIn("[string]$AppRoot = ''", self.register)
        self.assertIn("[string]$LauncherPath = ''", self.register)
        self.assertNotIn(".mediaforge\\home.txt", self.register)
    def test_bundle_file_manifest_entries_exist(self) -> None:
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        for entry in package["files"]:
            if "*" in entry:
                continue
            self.assertTrue((self.root / entry).is_file(), f"bundle manifest lists a missing file: {entry}")

    def test_readme_documents_manual_upgrade_cleanup(self) -> None:
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("-File register.ps1", readme, "users no longer have the helper")
        self.assertIn("mediaforge MCP BEGIN", readme)
        self.assertIn("手动删除", readme)
        self.assertIn("只删 MediaForge 自己的行", readme)
        self.assertIn("其它行一律保留", readme)
        self.assertIn("不要动其它区块", readme)
        self.assertNotIn("删除从", readme, "deleting the whole block would remove the user's rows")

    def test_published_bundle_excludes_powershell_helpers(self) -> None:
        import json
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], "0.2.2")
        self.assertNotIn("register.ps1", package["files"])
        self.assertNotIn("setup.ps1", package["files"])
        self.assertNotIn("verify.ps1", package["files"])
        self.assertIn("mediaforge-mcp.cmd", package["files"])

    def test_icon_stays_below_the_dsh_metadata_limit(self) -> None:
        icon = self.root / "icon.svg"
        self.assertLess(icon.stat().st_size, 256 * 1024)
        self.assertNotIn("data:image", icon.read_text(encoding="utf-8"))





@unittest.skipUnless(os.name == "nt", "Windows PowerShell migration tool")
class DshRegisterMigrationTests(unittest.TestCase):
    """register.ps1 不再是安装器：默认只报告，-RemoveLegacyBlock 才写文件。"""

    LEGACY = (
        "- id: keep-me\n"
        "  name: keep\n"
        "# === mediaforge MCP BEGIN ===\n"
        "- insert:\n"
        "    - id: mediaforge-mcp\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "- id: mediaforge-mcp\n"
        "  disabled: true\n"
        "- id: web\n"
        "  name: \"@deepseek-ai/dsh-web\"\n"
        "  config:\n"
        "    searchProvider: deepseek-search-api\n"
        "# === mediaforge MCP END ===\n"
        "- id: after-block\n"
        "  name: after\n"
    )

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dsh-register-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))
        self.patch = self.write_patch("desktop")
        self.script = Path(__file__).parents[2] / "integrations" / "dsh-mediaforge" / "register.ps1"

    def write_patch(self, profile: str) -> Path:
        directory = self.root / "profiles" / profile
        directory.mkdir(parents=True)
        path = directory / "cordis.patch.yml"
        path.write_text(self.LEGACY, encoding="utf-8")
        return path

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
             "-DshHome", str(self.root), "-AppRoot", str(self.root / "none"), *args],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_default_run_never_writes_the_profile_patch(self) -> None:
        before = self.patch.read_bytes()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.patch.read_bytes(), before, "default run must not touch the file")

    def test_remove_requires_an_explicit_profile(self) -> None:
        before = self.patch.read_bytes()
        result = self.run_script("-RemoveLegacyBlock")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Profile", result.stderr)
        self.assertEqual(self.patch.read_bytes(), before, "a refused run must not touch the file")

    def test_remove_only_touches_the_requested_profile(self) -> None:
        web = self.write_patch("web")
        web_before = web.read_bytes()
        result = self.run_script("-Profile", "desktop", "-RemoveLegacyBlock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("mediaforge MCP BEGIN", self.patch.read_text(encoding="utf-8"))
        self.assertEqual(web.read_bytes(), web_before, "another profile must stay untouched")

    def test_remove_legacy_block_keeps_foreign_rows(self) -> None:
        result = self.run_script("-Profile", "desktop", "-RemoveLegacyBlock")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.patch.read_text(encoding="utf-8")
        self.assertIn("- id: keep-me", text)
        self.assertIn("- id: web", text)
        self.assertIn("- id: after-block", text)
        self.assertNotIn("mediaforge MCP BEGIN", text)
        self.assertNotIn("mediaforge-mcp", text)


if __name__ == "__main__":
    unittest.main()
