"""开发 exe 的数据根必须由真实进程验证，且默认与 scripts/dev.ps1 一致。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DevelopmentDataRootSmokeTests(unittest.TestCase):
    def test_rust_default_matches_the_development_profile(self) -> None:
        storage = (ROOT / "src-tauri/src/storage.rs").read_text(encoding="utf-8")
        self.assertIn('DEVELOPMENT_DATA_ROOT_DIR_NAME: &str = ".dev-data"', storage)
        self.assertIn("project_root.join(DEVELOPMENT_DATA_ROOT_DIR_NAME)", storage)
        main = (ROOT / "src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn('"--smoke-print-data-root"', main)
        self.assertIn("SMOKE_DEVELOPMENT=", main)

    def test_smoke_clears_overrides_and_isolates_user_directories(self) -> None:
        script = (ROOT / "scripts/smoke-dev-data-root.ps1").read_text(encoding="utf-8")
        for name in ("MEDIAFORGE_DATA_ROOT", "PYMSS_STUDIO_DATA_ROOT", "USERPROFILE", "WEBVIEW2_USER_DATA_FOLDER"):
            self.assertIn(name, script)
        self.assertIn("'--smoke-print-data-root'", script)
        self.assertIn("Join-Path $projectRoot '.dev-data'", script)
        self.assertIn("SMOKE_DEVELOPMENT=true", script)
        self.assertIn("mediaforge-dev-data-root-", script)

    def test_ci_runs_the_real_target_executable(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn(
            "./scripts/smoke-dev-data-root.ps1 -AppExe 'src-tauri\\target\\release\\pymss-studio.exe'",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
