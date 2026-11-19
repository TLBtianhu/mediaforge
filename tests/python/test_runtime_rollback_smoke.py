"""小资产运行时回滚冒烟的入口与覆盖契约。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RuntimeRollbackSmokeTests(unittest.TestCase):
    def test_cli_accepts_complete_local_descriptors_and_a_ready_activation(self) -> None:
        source = (ROOT / "src-tauri/src/runtime_assets.rs").read_text(encoding="utf-8")
        self.assertIn("MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR", source)
        self.assertIn("pub fn smoke_activate", source)
        self.assertIn("runtime_asset_install(app.clone(), None)", source)
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
        self.assertIn("python = '3.12'", script)
        self.assertIn("backend = 'cuda'", script)
        self.assertIn("platform = 'windows'", script)
        self.assertIn("arch = 'x86_64'", script)
        self.assertNotIn("backend = 'fixture'", script)

    def test_fixture_satisfies_real_probe_schema_without_production_bypass(self) -> None:
        source = (ROOT / "scripts/make-fixture-runtime.ps1").read_text(encoding="utf-8")
        for required in ("python312.dll", "Lib/encodings/__init__.py", "Lib/site-packages", "torchBackend", "basePrefix"):
            self.assertIn(required, source)
        for key in ("platform", "arch"):
            self.assertIn(r'\"' + key + r'\"', source)
        installer = (ROOT / "src-tauri/src/runtime_assets.rs").read_text(encoding="utf-8")
        self.assertNotIn("repair_embedded_python_from", installer)
        self.assertNotIn('backend == "fixture"', installer)

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

    def test_ci_prepares_real_minimal_bootstrap_before_the_full_suite(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        prepare = "./scripts/prepare-python-runtime.ps1 -Minimal -Python '${{ steps.python.outputs.python-path }}'"
        self.assertIn(prepare, workflow)
        self.assertLess(workflow.index(prepare), workflow.index("- name: Run all tests"))


if __name__ == "__main__":
    unittest.main()
