"""Real smoke entry points are safe to import/help and require explicit assets.

Inference and dependency commands are mocked; only CLI --help/argument rejection
runs in subprocesses. No installed runtime, model or real profile is touched.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import media_smoke_env


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("MEDIAFORGE_", "PYMSS_")) and key not in {"HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME"}}


class MediaSmokeIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mediaforge-smoke-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.models = self.root / "test-models"
        self.models.mkdir()
        self.data = self.root / "isolated-data"
        self.python = Path(sys.executable).resolve()
        self.environment = mock.patch.dict(os.environ, clean_environment(), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.smoke = load_script("smoke-media-models")
        self.check = load_script("check-media")

    def arguments(self):
        return ["--python", str(self.python), "--model-dir", str(self.models), "--data-root", str(self.data)]

    def test_import_does_not_discover_profiles_or_probe_a_runtime(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("must not spawn during import")), \
             mock.patch.object(Path, "glob", side_effect=AssertionError("must not search profiles")):
            load_script("smoke-media-models")
            load_script("check-media")

    def test_real_help_and_missing_argument_paths_do_not_require_userprofile(self):
        for script in (SCRIPTS / "smoke-media-models.py", SCRIPTS / "check-media.py", ROOT / "tests/manual/advanced_workflow_real_smoke.py"):
            with self.subTest(script=script.name):
                result = subprocess.run([str(self.python), "-B", str(script), "--help"],
                                        env=clean_environment(), capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--data-root", result.stdout)
                result = subprocess.run([str(self.python), "-B", str(script)],
                                        env=clean_environment(), capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--python", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [self.models])

    def test_missing_models_or_data_are_rejected_before_verification(self):
        with mock.patch.object(self.smoke, "verify_models") as verify, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                self.smoke.main(["--python", str(self.python)])
            self.assertEqual(failure.exception.code, 2)
            verify.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_explicit_environment_aliases_remain_supported_without_implicit_discovery(self):
        with mock.patch.dict(os.environ, {"MEDIAFORGE_RUNTIME_PYTHON": str(self.python), "PYMSS_MODEL_DIR": str(self.models), "PYMSS_STUDIO_DATA_ROOT": str(self.data)}), \
             mock.patch.object(self.smoke, "verify_models", return_value=0) as verify:
            self.assertEqual(self.smoke.main([]), 0)
        self.assertEqual(verify.call_args.args[:2], (self.python, self.models))

    def test_all_process_path_aliases_and_library_caches_are_isolated(self):
        with mock.patch.dict(os.environ, {"MEDIAFORGE_BUNDLED_RUNTIME_ENVS_DIR": "unrelated-runtime", "PYMSS_STUDIO_DATA_ROOT": "unrelated-data", "HF_TOKEN": "unrelated-token"}):
            env = media_smoke_env.isolated_environment(self.data, self.python, self.models)
        for key in ("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT", "PYMSS_STUDIO_DATA_DIR"):
            self.assertEqual(env[key], str(self.data))
        for key in ("MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR", "PYMSS_STUDIO_MODEL_DIR"):
            self.assertEqual(env[key], str(self.models))
        for key in ("MEDIAFORGE_RUNTIME_ENVS_DIR", "PYMSS_STUDIO_RUNTIME_ENVS_DIR"):
            self.assertEqual(env[key], str(self.data / "runtime-envs"))
        for key in ("MEDIAFORGE_ACTIVE_RUNTIME_FILE", "PYMSS_STUDIO_ACTIVE_RUNTIME_FILE"):
            self.assertEqual(env[key], str(self.data / "runtime-envs/active-runtime.json"))
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "WEBVIEW2_USER_DATA_FOLDER", "HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "MODELSCOPE_CACHE", "TORCH_HOME", "TORCH_EXTENSIONS_DIR", "TEMP", "TMP", "TMPDIR"):
            self.assertTrue(Path(env[key]).is_relative_to(self.data), key)
        self.assertNotIn("MEDIAFORGE_BUNDLED_RUNTIME_ENVS_DIR", env)
        self.assertNotIn("HF_TOKEN", env)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_user_or_installed_profile_paths_are_rejected_without_creating_them(self):
        for path in (self.root / ".mediaforge/test", self.root / "Programs/MediaForge/data"):
            with self.subTest(path=path), mock.patch.object(self.smoke, "verify_models") as verify, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.smoke.main(["--python", str(self.python), "--model-dir", str(self.models), "--data-root", str(path)])
                verify.assert_not_called()
                self.assertFalse(path.exists())

    def test_default_outputs_are_temporary_and_explicit_outputs_are_preserved(self):
        observed = []
        def verify(_python, _models, outputs, _source, _env):
            outputs.mkdir(parents=True, exist_ok=True)
            (outputs / "fixture.txt").write_text("keep", encoding="utf-8")
            observed.append(outputs)
            return 0
        with mock.patch.object(self.smoke, "verify_models", side_effect=verify):
            self.assertEqual(self.smoke.main(self.arguments()), 0)
            self.assertFalse(observed[-1].exists())
            explicit = self.root / "preserved-outputs"
            self.assertEqual(self.smoke.main(self.arguments() + ["--output-dir", str(explicit)]), 0)
            self.assertEqual((explicit / "fixture.txt").read_text(encoding="utf-8"), "keep")

    def test_outputs_cannot_be_written_into_the_model_directory(self):
        with mock.patch.object(self.smoke, "verify_models") as verify, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.smoke.main(self.arguments() + ["--output-dir", str(self.models / "outputs")])
            verify.assert_not_called()

    def test_sample_is_synthetic_or_an_explicit_copy_and_never_scans_a_profile(self):
        target = self.smoke.sample_image(self.root / "samples")
        self.assertEqual(target.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", target.read_bytes()[16:24]), (256, 256))
        chosen = self.root / "chosen.jpg"
        chosen.write_bytes(b"explicit input fixture")
        copied = self.smoke.sample_image(self.root / "chosen-output", chosen)
        self.assertEqual(copied.read_bytes(), chosen.read_bytes())

    def test_worker_receives_explicit_models_no_download_and_isolated_env(self):
        env = media_smoke_env.isolated_environment(self.data, self.python, self.models)
        payload_paths = []
        def worker(command, **kwargs):
            self.assertEqual(command[0], str(self.python))
            path = Path(command[-1])
            payload_paths.append(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["modelDir"], str(self.models))
            self.assertEqual(payload["downloadMethod"], "never")
            self.assertEqual(kwargs["env"], env)
            sidecar = self.root / "detection.json"
            sidecar.write_text('{"boxes": []}', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, json.dumps({"type": "task.result", "payload": {"outputs": [{"path": str(sidecar)}]}}), "")
        with mock.patch.object(self.smoke.subprocess, "run", side_effect=worker):
            ok, _detail = self.smoke.run({"operation": "media_image"}, python=self.python, models=self.models, env=env)
        self.assertTrue(ok)
        self.assertTrue(all(not path.exists() for path in payload_paths))

    def test_payload_temp_file_is_removed_after_a_worker_timeout(self):
        env = media_smoke_env.isolated_environment(self.data, self.python, self.models)
        with mock.patch.object(self.smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.smoke.run({}, python=self.python, models=self.models, env=env)
        self.assertEqual(list((self.data / "tmp").glob("*.json")), [])

    def test_wrapper_deps_only_uses_a_temporary_profile_without_model_discovery(self):
        roots = []
        def command(arguments, **kwargs):
            self.assertEqual(arguments[0], str(self.python))
            self.assertEqual(Path(arguments[2]).name, "check-media-deps.py")
            root = Path(kwargs["env"]["MEDIAFORGE_DATA_ROOT"])
            roots.append(root)
            self.assertTrue(root.is_dir())
            return subprocess.CompletedProcess(arguments, 0)
        with mock.patch.object(self.check.subprocess, "run", side_effect=command), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.check.main(["--deps"]), 0)
        self.assertEqual(len(roots), 1)
        self.assertFalse(roots[0].exists())

    def test_wrapper_forwards_explicit_smoke_paths_and_keeps_requested_outputs(self):
        output = self.root / "outputs"
        seen = []
        def command(arguments, **kwargs):
            seen.append(arguments)
            self.assertEqual(kwargs["env"]["MEDIAFORGE_MODEL_DIR"], str(self.models))
            self.assertEqual(kwargs["env"]["MEDIAFORGE_DATA_ROOT"], str(self.data))
            return subprocess.CompletedProcess(arguments, 0)
        with mock.patch.object(self.check.subprocess, "run", side_effect=command), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.check.main(["--smoke", *self.arguments(), "--output-dir", str(output)]), 0)
        self.assertEqual(len(seen), 1)
        self.assertIn("--model-dir", seen[0])
        self.assertIn(str(output), seen[0])

    def test_advanced_workflow_uses_the_same_explicit_child_process_isolation(self):
        spec = importlib.util.spec_from_file_location("advanced_workflow_smoke", ROOT / "tests/manual/advanced_workflow_real_smoke.py")
        advanced = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(advanced)
        source = self.root / "input.wav"
        source.write_bytes(b"audio input fixture")
        output = self.root / "advanced-outputs"
        def worker(command, **kwargs):
            self.assertEqual(command[0], str(self.python))
            self.assertIn("infer_workflow", command)
            payload = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            self.assertEqual(payload["input"], str(source))
            self.assertEqual(payload["modelDir"], str(self.models))
            self.assertEqual(payload["downloadMethod"], "never")
            self.assertEqual(kwargs["env"]["MEDIAFORGE_DATA_ROOT"], str(self.data))
            outputs = []
            for stem in ("vocals", "instrumental"):
                target = output / f"{stem}.wav"
                target.write_bytes(b"output fixture")
                outputs.append({"stem": stem, "path": str(target)})
            event = {"type": "task.result", "payload": {"outputs": outputs}}
            return subprocess.CompletedProcess(command, 0, json.dumps(event), "")
        with mock.patch.object(advanced.subprocess, "run", side_effect=worker), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(advanced.main([*self.arguments(), "--input", str(source), "--output", str(output)]), 0)
        self.assertTrue((output / "vocals.wav").is_file())
        self.assertEqual(list((self.data / "tmp").iterdir()), [])

    def test_a_smoke_with_no_executable_catalog_models_cannot_report_success(self):
        root = self.root / "source-fixture"
        catalog = root / "python/media_ops/media_catalog.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text('{"models": [{"id": "not-supported", "status": "available", "task": "pending"}]}', encoding="utf-8")
        env = media_smoke_env.isolated_environment(self.data, self.python, self.models)
        with mock.patch.object(self.smoke, "ROOT", root), mock.patch.object(self.smoke, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.smoke.verify_models(self.python, self.models, self.root / "outputs", None, env), 1)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
