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


if __name__ == "__main__":
    unittest.main()
