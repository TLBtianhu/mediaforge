from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("check_architecture", ROOT / "scripts" / "check-architecture.py")
architecture = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(architecture)


class EventCoverageTests(unittest.TestCase):
    def check(self, root: Path, source: str, directory: str) -> list[str]:
        (root / directory).mkdir()
        (root / directory / "fixture.py").write_text(source, encoding="utf-8")
        manifest = architecture.load_manifest()
        previous = architecture.PY, architecture.ROOT
        architecture.PY = root
        architecture.ROOT = root
        try:
            return architecture.check_event_coverage(manifest)
        finally:
            architecture.PY, architecture.ROOT = previous

    def test_dotted_and_multiline_events_are_checked_outside_the_media_engine(self) -> None:
        with tempfile.TemporaryDirectory(prefix="architecture-events-") as raw:
            problems = self.check(
                Path(raw),
                "from shared.worker_protocol import emit\n"
                "emit(\n    'task.not_declared',\n    {},\n)\n"
                "emit('command_result', {})\n",
                "core",
            )
            self.assertEqual(len(problems), 1)
            self.assertIn("task.not_declared", problems[0])
            self.assertIn(f"{Path('core') / 'fixture.py'}:2", problems[0])

    def test_current_dotted_events_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="architecture-events-") as raw:
            problems = self.check(
                Path(raw),
                "from shared.worker_protocol import emit\nemit('task.progress', {})\nemit('task.result', {})\n",
                "audio_tools",
            )
            self.assertEqual(problems, [])


class RustEventLiteralTests(unittest.TestCase):
    def check(self, root: Path, source: str, manifest: dict | None = None) -> list[str]:
        source_dir = root / "src-tauri" / "src"
        source_dir.mkdir(parents=True)
        (source_dir / "fixture.rs").write_text(source, encoding="utf-8")
        effective = manifest if manifest is not None else architecture.load_manifest()
        previous_root, previous_rust = architecture.ROOT, architecture.RUST_SRC
        architecture.ROOT = root
        architecture.RUST_SRC = source_dir
        try:
            return architecture.check_rust_event_literals(effective)
        finally:
            architecture.ROOT, architecture.RUST_SRC = previous_root, previous_rust

    def test_undeclared_rust_event_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="architecture-rust-") as raw:
            problems = self.check(Path(raw), 'fn f() { let kind = "task.not_declared"; }\n')
            self.assertEqual(len(problems), 1)
            self.assertIn("task.not_declared", problems[0])

    def test_canonical_rust_events_pass_and_comments_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="architecture-rust-") as raw:
            problems = self.check(
                Path(raw),
                '// historical: "task.done" must not be emitted\n'
                'fn f() { let kind = "task.progress"; let other = "asset.done"; }\n'
                'fn g() { session_log::append(app, "WARN", "task.journal_failed", vec![]); }\n',
            )
            self.assertEqual(problems, [])

    def test_terminal_events_must_be_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="architecture-rust-") as raw:
            manifest = {
                "events": {"canonical": ["task.result"], "terminal": ["task.result", "task.ghost"]}
            }
            problems = self.check(Path(raw), "fn f() {}\n", manifest)
            self.assertTrue(any("task.ghost" in problem for problem in problems))


class ExecutionManifestTests(unittest.TestCase):
    def manifest(self, worker: list[str], native: list[str], **result_jobs: list[str]) -> dict:
        execution: dict = {"bootstrap": {"worker": worker, "native": native}}
        if result_jobs:
            execution["resultJobs"] = result_jobs
        return {
            "domains": {"runtime": {"commands": {"info": {"command": "runtime_info"}}}},
            "execution": execution,
        }

    def test_worker_entries_must_exist(self) -> None:
        problems = architecture.check_execution_manifest(
            self.manifest(["runtime_info", "runtime_ghost"], ["update_runtime_core"])
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("runtime_ghost", problems[0])

    def test_valid_execution_manifest_passes(self) -> None:
        self.assertEqual(
            architecture.check_execution_manifest(
                self.manifest(["runtime_info"], ["update_runtime_core"])
            ),
            [],
        )

    def test_result_commands_must_exist(self) -> None:
        problems = architecture.check_execution_manifest(
            self.manifest(["runtime_info"], ["update_runtime_core"], commands=["runtime_ghost"])
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("runtime_ghost", problems[0])

    def test_result_operations_must_be_routed(self) -> None:
        problems = architecture.check_execution_manifest(
            self.manifest(
                ["runtime_info"], ["update_runtime_core"],
                commands=["runtime_info"], operations=["media_ghost"],
            )
        )
        self.assertTrue(any("media_ghost" in problem for problem in problems))

    def test_valid_result_jobs_pass(self) -> None:
        self.assertEqual(
            architecture.check_execution_manifest(
                self.manifest(
                    ["runtime_info"], ["update_runtime_core"],
                    commands=["runtime_info"], operations=["media_image"],
                )
            ),
            [],
        )

    def test_output_scope_operations_must_exist(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["execution"]["outputScope"] = {"operations": ["convert", "not_a_tool"]}
        problems = architecture.check_execution_manifest(manifest)
        self.assertTrue(any("not_a_tool" in problem for problem in problems))

    def test_output_scope_and_background_commands_must_exist(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["execution"]["outputScope"] = {"commands": ["ghost_scope"]}
        manifest["execution"]["backgroundResult"] = ["ghost_background"]
        problems = architecture.check_execution_manifest(manifest)
        self.assertTrue(any("ghost_scope" in problem for problem in problems))
        self.assertTrue(any("ghost_background" in problem for problem in problems))

    def test_valid_output_scope_and_background_pass(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["execution"]["outputScope"] = {
            "commands": ["runtime_info"], "operations": ["convert"],
        }
        manifest["execution"]["backgroundResult"] = ["runtime_info"]
        self.assertEqual(architecture.check_execution_manifest(manifest), [])

    def test_bootstrap_query_and_update_sets_must_be_known(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["execution"]["bootstrap"]["withoutRuntime"] = ["ghost_query"]
        manifest["execution"]["bootstrap"]["updatesRuntime"] = ["update_runtime_core", "ghost_update"]
        problems = architecture.check_execution_manifest(manifest)
        self.assertTrue(any("ghost_query" in problem for problem in problems))
        self.assertTrue(any("ghost_update" in problem for problem in problems))

    def test_valid_bootstrap_query_and_update_sets_pass(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["execution"]["bootstrap"]["withoutRuntime"] = ["runtime_info"]
        manifest["execution"]["bootstrap"]["updatesRuntime"] = ["update_runtime_core"]
        self.assertEqual(architecture.check_execution_manifest(manifest), [])

    def test_terminal_events_must_be_real(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["events"] = {"canonical": ["task.result"]}
        manifest["execution"]["terminalEvents"] = {
            "runtime_info": ["error", "task.result", "ghost_event", "task.ghost"],
        }
        problems = architecture.check_execution_manifest(manifest)
        self.assertTrue(any("ghost_event" in problem for problem in problems))
        self.assertTrue(any("task.ghost" in problem for problem in problems))

    def test_result_event_must_appear_in_its_terminal_list(self) -> None:
        manifest = self.manifest(["runtime_info"], ["update_runtime_core"])
        manifest["domains"]["runtime"]["commands"]["info"]["resultEvent"] = "runtime_install_finished"
        manifest["execution"]["terminalEvents"] = {"runtime_info": ["error"]}
        problems = architecture.check_execution_manifest(manifest)
        self.assertTrue(any("runtime_install_finished" in problem for problem in problems))

    def test_worker_and_native_must_not_overlap(self) -> None:
        problems = architecture.check_execution_manifest(
            self.manifest(["runtime_info"], ["runtime_info"])
        )
        self.assertTrue(any("重叠" in problem for problem in problems))


class WorkerEventProjectionTests(unittest.TestCase):
    def test_generated_file_matches_the_manifest(self) -> None:
        self.assertEqual(architecture.check_worker_event_projection(architecture.load_manifest()), [])

    def test_drift_is_reported(self) -> None:
        manifest = architecture.load_manifest()
        manifest["events"]["canonical"] = [*manifest["events"]["canonical"], "task.ghost"]
        problems = architecture.check_worker_event_projection(manifest)
        self.assertEqual(len(problems), 1)
        self.assertIn("sync_worker_events", problems[0])


if __name__ == "__main__":
    unittest.main()
