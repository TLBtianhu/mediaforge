"""便携冒烟必须等待启动条件并保留失败现场，不能依赖固定启动耗时。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PortableSmokeTests(unittest.TestCase):
    def test_portable_smoke_bounds_gui_wait_and_captures_logs(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("[int]$LaunchSeconds = 30", script)
        self.assertIn("Start-Sleep -Seconds ([Math]::Min(3, $LaunchSeconds))", script)
        self.assertNotIn("until ((Test-Path $portableSettings)", script)
        self.assertIn("-RedirectStandardOutput $stdout", script)
        self.assertIn("-RedirectStandardError $stderr", script)
        self.assertIn("Assert-TempWorkDir", script)

    def test_portable_smoke_isolates_and_restores_webview_profile(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("$webviewRoot = Join-Path $work 'webview2'", script)
        self.assertIn("$env:WEBVIEW2_USER_DATA_FOLDER = $webviewRoot", script)
        self.assertIn("$previousWebView = [Environment]::GetEnvironmentVariable('WEBVIEW2_USER_DATA_FOLDER')", script)
        self.assertIn("Remove-Item Env:WEBVIEW2_USER_DATA_FOLDER", script)

    def test_portable_smoke_stops_only_its_webview_children_before_cleanup(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("Name = 'msedgewebview2.exe'", script)
        self.assertIn("$_.CommandLine.Contains($webviewRoot)", script)
        self.assertIn("WebView2 child processes did not exit within 10 seconds", script)

    def test_data_root_probe_is_separate_from_gui_startup(self) -> None:
        script = (ROOT / "scripts/smoke-portable.ps1").read_text(encoding="utf-8")
        self.assertIn("-ArgumentList '--smoke-print-data-root'", script)
        self.assertIn("真实进程数据根初始化", script)
        self.assertIn("便携形态被识别", script)


if __name__ == "__main__":
    unittest.main()
