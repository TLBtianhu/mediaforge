from pathlib import Path
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
