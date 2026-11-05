"""安装根目录与分类卸载契约。"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallerLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8")

    def test_program_and_integration_files_use_the_single_root_layout(self) -> None:
        self.assertIn('DestDir: "{app}\\app"', self.script)
        self.assertIn('DestDir: "{app}\\integrations"', self.script)
        for directory in ("data", "models", "runtime-envs"):
            self.assertIn(f'Name: "{{app}}\\{directory}"', self.script)
        self.assertIn('ValueData: "{app}\\app"', self.script)
        self.assertIn("DisableDirPage=no", self.script)

    def test_dsh_bridge_is_optional_but_never_registered_by_the_installer(self) -> None:
        self.assertIn('Name: "dshbridge"', self.script)
        self.assertNotIn("Flags: checked", self.script)
        run_section = re.search(r"\[Run\](.*?)(?=\n\[|\Z)", self.script, re.S)
        self.assertIsNotNone(run_section)
        self.assertNotIn("register.ps1", run_section.group(1))
        self.assertIn("由 DSH 插件管理器加载", run_section.group(1))

    def test_resource_cleanup_is_opt_in_and_exactly_scoped(self) -> None:
        self.assertIn("CurUninstallStepChanged", self.script)
        for relative in ("runtime-envs", "models", "data"):
            self.assertIn(f"RemoveIfExists(ExpandConstant('{{app}}\\{relative}'))", self.script)
        self.assertIn("RuntimeCheck: TNewCheckBox", self.script)
        self.assertIn("ModelsCheck: TNewCheckBox", self.script)
        self.assertIn("DataCheck: TNewCheckBox", self.script)
        self.assertIn("RuntimeCheck.Checked := False", self.script)
        self.assertIn("ModelsCheck.Checked := False", self.script)
        self.assertIn("DataCheck.Checked := False", self.script)
        self.assertIn("ChooseCleanupOptions", self.script)
        self.assertIn("InitializeUninstall", self.script)
        self.assertIn("Form.ShowModal() = mrOk", self.script)
        self.assertIn("RemoveRuntime := RuntimeCheck.Checked", self.script)
        self.assertIn("RemoveModels := ModelsCheck.Checked", self.script)
        self.assertIn("RemoveData := DataCheck.Checked", self.script)
        self.assertNotIn("RepairVenvConfig", self.script)
        self.assertNotIn("RepairBundledRuntimeEnvs", self.script)
        self.assertNotIn("pyvenv.cfg", self.script)
        self.assertNotIn("CleanupOptions.Checked", self.script)


if __name__ == "__main__":
    unittest.main()
