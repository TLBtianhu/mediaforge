"""便携冒烟必须等待启动条件并保留失败现场，不能依赖固定启动耗时。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PortableSmokeTests(unittest.TestCase):
    def test_portable_smoke_polls_for_its_data_root_and_captures_logs(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("[int]$LaunchSeconds = 30", script)
        self.assertIn("AddSeconds($LaunchSeconds)", script)
        self.assertIn("until ((Test-Path $portableSettings)", script)
        self.assertIn("-RedirectStandardOutput $stdout", script)
        self.assertIn("-RedirectStandardError $stderr", script)
        self.assertNotIn("Start-Sleep -Seconds $LaunchSeconds", script)
        self.assertIn("Assert-TempWorkDir", script)

    def test_portable_smoke_isolates_and_restores_webview_profile(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("$env:WEBVIEW2_USER_DATA_FOLDER = Join-Path $work 'webview2'", script)
        self.assertIn("$previousWebView = [Environment]::GetEnvironmentVariable('WEBVIEW2_USER_DATA_FOLDER')", script)
        self.assertIn("Remove-Item Env:WEBVIEW2_USER_DATA_FOLDER", script)


if __name__ == "__main__":
    unittest.main()
