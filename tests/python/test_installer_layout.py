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
        self.assertIn("AddCheckBox('同时删除独立运行时（runtime-envs）', '', 0, True, False, True, True, nil)", self.script)
        self.assertIn("AddCheckBox('同时删除模型资源（models）', '', 0, True, False, True, True, nil)", self.script)
        self.assertIn("AddCheckBox('同时删除配置、日志和任务历史（data）', '', 0, True, False, True, True, nil)", self.script)


if __name__ == "__main__":
    unittest.main()
