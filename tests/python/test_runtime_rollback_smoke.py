"""小资产运行时回滚冒烟的入口与覆盖契约。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RuntimeRollbackSmokeTests(unittest.TestCase):
    def test_cli_accepts_complete_local_descriptors_and_a_ready_activation(self) -> None:
        source = (ROOT / "src-tauri/src/runtime_assets.rs").read_text(encoding="utf-8")
        self.assertIn("MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR", source)
        self.assertIn("pub fn smoke_activate", source)
        self.assertIn("runtime_asset_install(app.clone())", source)
        self.assertGreaterEqual(source.count("write_active_pointer(&envs, &descriptor)"), 2)
        main = (ROOT / "src-tauri/src/main.rs").read_text(encoding="utf-8")
        self.assertIn('"--smoke-activate-runtime"', main)

    def test_script_exercises_install_switch_and_rollback_without_external_network(self) -> None:
        script = (ROOT / "scripts/smoke-runtime-rollback.ps1").read_text(encoding="utf-8")
        for asset in ("runtime-a", "runtime-b"):
            self.assertIn(asset, script)
        self.assertIn("--smoke-install-runtime", script)
        self.assertIn("--smoke-activate-runtime", script)
        self.assertIn("激活指针回滚到 A", script)
        self.assertIn("回滚后 B 仍保留", script)
        self.assertIn("http://127.0.0.1:$Port", script)

    def test_script_captures_child_process_output_for_ci_failures(self) -> None:
        script = (ROOT / "scripts/smoke-runtime-rollback.ps1").read_text(encoding="utf-8")
        self.assertIn("-RedirectStandardOutput $stdout", script)
        self.assertIn("-RedirectStandardError $stderr", script)
        self.assertIn("$($Descriptor.assetId)-$($Mode.TrimStart('-'))", script)
        self.assertIn("Get-Content $log -Raw", script)

    def test_ci_builds_the_real_cli_and_runs_the_rollback_smoke(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("cargo build --release --manifest-path src-tauri/Cargo.toml", workflow)
        self.assertIn("fetch-third-party.ps1 -ToolName aria2", workflow)
        self.assertIn("smoke-runtime-rollback.ps1", workflow)
        fetch = (ROOT / "scripts/fetch-third-party.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($ToolName -and $name -ne $ToolName) { continue }", fetch)


if __name__ == "__main__":
    unittest.main()
