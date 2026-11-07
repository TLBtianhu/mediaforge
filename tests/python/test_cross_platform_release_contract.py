"""POSIX packaging fixtures; no native builds, runtimes, downloads or user profiles."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("posix_release", ROOT / "scripts/posix-release.py")
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
ENV_SPEC = importlib.util.spec_from_file_location("posix_release_smoke_env", ROOT / "scripts/media_smoke_env.py")
ENV = importlib.util.module_from_spec(ENV_SPEC)
ENV_SPEC.loader.exec_module(ENV)


def write_fixture(root: Path) -> None:
    files = {
        "python/worker.py": "# fixture worker\n", "python/core/__init__.py": "",
        "python/core/handler.py": "def run(payload): return 0\n",
        "python/shared/protocol_contract.py": "# fixture protocol\n", "python/surfaces/dispatch.py": "# dispatch\n",
        "python/runtime-manifest.json": json.dumps({"baseGeneration": 1, "manifestVersion": "fixture", "python": "3.12"}),
        "manifest/protocol.json": json.dumps({"domains": {"test": {"commands": {"run": {"handler": "core.handler:run"}}}}}),
        "manifest/runtime.json": '{"schemaVersion":1}', "manifest/future-contract.json": '{"keep":true}',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class PosixResourceFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mf-posix-release-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # These tests isolate staging/contract I/O from native bootstrap execution.
        patch = mock.patch.object(RELEASE, "check_runtime_version")
        patch.start()
        self.addCleanup(patch.stop)

    def test_whole_worker_and_manifest_are_usable_outside_the_checkout_without_dependencies(self):
        resources = self.root / "installed/Resources"
        RELEASE.stage_resources(ROOT, resources)
        environment = ENV.isolated_environment(self.root / "profile", Path(sys.executable))
        environment.pop("PYTHONPATH", None)
        for command in (
            [str(resources / "python/worker.py"), "health", "--payload", "{}"],
            ["-c", "from shared.protocol_contract import load_protocol; "
             "from media_ops.runtime_guard import load_manifest; "
             "assert load_protocol()['domains']; assert load_manifest()['runtimes']; print('contracts ok')"],
        ):
            if command[0] == "-c":
                command[1] = "import sys; sys.path.insert(0, sys.argv[1]); " + command[1]
                command.append(str(resources / "python"))
            result = subprocess.run([sys.executable, "-B", "-S", *command], cwd=self.root,
                                    env=environment, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if command[0] != "-c":
                event = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertEqual(event["type"], "health")
                self.assertTrue(event["payload"]["ok"])
        self.assertTrue((resources / "python/core/operation_kernel.py").is_file())
        self.assertTrue((resources / "python/shared/worker_protocol.py").is_file())
        self.assertTrue((resources / "python/surfaces/agent_contract.py").is_file())

    def test_staging_replaces_stale_files_keeps_all_contracts_and_excludes_generated_caches(self):
        source, resources = self.root / "source", self.root / "resources"
        write_fixture(source)
        for name in ("python/__pycache__/old.pyc", "python/core/debug.log", "python/run.jsonl", "python/.cache/large.bin"):
            file = source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"excluded")
        stale = resources / "python/removed_module.py"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale", encoding="utf-8")
        RELEASE.stage_resources(source, resources)
        self.assertFalse(stale.exists())
        self.assertEqual(json.loads((resources / "manifest/future-contract.json").read_text()), {"keep": True})
        self.assertFalse((resources / "python/__pycache__").exists())
        self.assertFalse((resources / "python/.cache").exists())
        self.assertFalse((resources / "python/core/debug.log").exists())
        self.assertTrue((source / "python/__pycache__/old.pyc").exists())
        self.assertFalse((resources / "runtime-envs").exists())

    def test_missing_protocol_handler_or_manifest_is_rejected_before_bundling(self):
        write_fixture(self.root)
        (self.root / "python/core/handler.py").unlink()
        with self.assertRaisesRegex(ValueError, "protocol handler module"):
            RELEASE.verify_resources(self.root)
        (self.root / "manifest/runtime.json").unlink()
        with self.assertRaisesRegex(ValueError, "manifest/runtime.json"):
            RELEASE.verify_resources(self.root)

    def test_staging_cannot_remove_its_source(self):
        write_fixture(self.root)
        with self.assertRaisesRegex(ValueError, "overwrite its source"):
            RELEASE.stage_resources(self.root, self.root)
        self.assertTrue((self.root / "python/worker.py").is_file())

    def test_tauri_override_maps_complete_code_contract_tools_and_bootstrap_at_resource_root(self):
        write_fixture(self.root)
        for platform in ("linux", "macos"):
            config = RELEASE.bundle_config(self.root, platform)["bundle"]
            self.assertEqual(set(config["resources"].values()), {"python", "manifest", "python-runtime", "bin", "resources/plugin-sdk"})
            self.assertTrue(all(Path(name).is_absolute() for name in config["resources"]))
            self.assertEqual("linux" in config, platform == "linux")


class MacReleaseFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mf-macos-contract-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patch = mock.patch.object(RELEASE, "check_runtime_version")
        patch.start()
        self.addCleanup(patch.stop)
        self.config = {"productName": "MediaForge", "identifier": "studio.mediaforge.desktop"}
        self.app = self.root / "MediaForge.app"
        self.resources = self.app / "Contents/Resources"
        write_fixture(self.resources)
        (self.app / "Contents/MacOS").mkdir()
        (self.app / "Contents/MacOS/pymss-studio").write_bytes(b"native executable fixture")
        self.info = {"CFBundleIdentifier": self.config["identifier"], "CFBundleExecutable": "pymss-studio", "CFBundleShortVersionString": "1.2.3"}
        self.write_info()
        active = self.resources / "python-runtime/runtime-envs/active-runtime.json"
        active.parent.mkdir(parents=True)
        active.write_text(json.dumps({"backend": "mlx", "source": "bundled", "pythonPath": "../bin/python3",
                                     "packageVersions": {"pymss": "2.1.12"}}), encoding="utf-8")
        python = self.resources / "python-runtime/bin/python3"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"bundled Python fixture")

    def write_info(self):
        with (self.app / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(self.info, stream)

    def test_exact_product_app_selected_and_real_cargo_executable_name_retained(self):
        (self.root / "Pymss Studio.app").mkdir()
        (self.root / "MediaForge test.app").mkdir()
        self.assertEqual(RELEASE.mac_app(self.root, self.config), self.app.resolve())

    def test_missing_real_app_never_falls_back_to_old_or_test_bundle(self):
        self.app.rename(self.root / "Pymss Studio.app")
        with self.assertRaises(FileNotFoundError):
            RELEASE.mac_app(self.root, self.config)

    def test_invalid_identifier_or_executable_cannot_be_packaged(self):
        for changed in ({"CFBundleIdentifier": "test.invalid"}, {"CFBundleExecutable": "../outside"}, {"CFBundleExecutable": "MediaForge-missing"}):
            with self.subTest(changed=changed):
                previous = dict(self.info)
                self.info.update(changed)
                self.write_info()
                with self.assertRaises(ValueError):
                    RELEASE.mac_app(self.root, self.config)
                self.info = previous

    def test_update_archive_and_sidecar_match_publisher_contract_and_keep_full_resources(self):
        output = self.root / "release/MediaForge-1.2.3-macos-arm64-update.app.tar.gz"
        RELEASE.mac_update(self.app, output, "1.2.3", self.config)
        with tarfile.open(output) as archive:
            self.assertEqual(archive.extractfile("MediaForge.app/Contents/Resources/manifest/future-contract.json").read(), b'{"keep":true}')
            self.assertIsNotNone(archive.getmember("MediaForge.app/Contents/Resources/python/shared/protocol_contract.py"))
            self.assertEqual({name.split("/")[0] for name in archive.getnames()}, {"MediaForge.app"})
        sidecar = json.loads(Path(str(output) + ".json").read_text())
        self.assertEqual(sidecar["version"], "1.2.3")
        self.assertGreater(sidecar["pymss_unpacked_size"], 0)
        self.assertEqual(sidecar["pymss_runtime_contract"], {"baseGeneration": 1, "manifestVersion": "fixture", "python": "3.12",
                                                         "backend": "mlx", "source": "bundled", "packageVersions": {"pymss": "2.1.12"}})
        Path(str(output) + ".sig").write_text("fixture signature", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("fixture_update_metadata", ROOT / "scripts/generate-update-metadata.py")
        metadata = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(metadata)
        generated = metadata.generate(output.parent, {"RELEASE_TAG": "v1.2.3", "RELEASE_MACOS_BUILD_RESULT": "success"})
        self.assertIn("stable-v1-macos-arm64-update.json", generated)
        channel = json.loads((output.parent / generated[0]).read_text())
        self.assertEqual(channel["pymss_runtime_contract"], sidecar["pymss_runtime_contract"])

    def test_mismatched_app_version_is_rejected_before_creating_archive(self):
        output = self.root / "never.tar.gz"
        with self.assertRaisesRegex(ValueError, "version"):
            RELEASE.mac_update(self.app, output, "9.9.9", self.config)
        self.assertFalse(output.exists())


class CrossPlatformWorkflowTests(unittest.TestCase):
    def test_workflows_use_complete_resource_contract_and_current_repository(self):
        for name in ("linux", "macos"):
            workflow = (ROOT / f".github/workflows/release-{name}.yml").read_text(encoding="utf-8")
            self.assertIn("posix-release.py stage src-tauri/resources", workflow)
            self.assertIn(f"--platform {name}", workflow)
            self.assertIn("github.repository == 'TLBtianhu/mediaforge'", workflow)
            self.assertNotIn("pymss-project/pymss-studio", workflow)
            self.assertNotIn("Pymss Studio.app", workflow)
            yaml.safe_load(workflow)
        macos = (ROOT / ".github/workflows/release-macos.yml").read_text(encoding="utf-8")
        self.assertIn("release/*-update.app.tar.gz.json", macos)
        self.assertIn("release/*-update.app.tar.gz", macos)
        self.assertIn("scripts/posix-release.py mac-update", macos)
        self.assertIn('source scripts/posix-release-isolation.sh "$PROBE_ROOT"', macos)
        self.assertIn("prepare-python-runtime.sh minimal", (ROOT / ".github/workflows/release-linux.yml").read_text())
        self.assertIn("prepare-python-runtime.sh mlx", macos)

    def test_bash_scripts_and_every_workflow_run_block_have_valid_syntax(self):
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash is unavailable for static syntax verification")
        scripts = [path.read_text(encoding="utf-8") for path in (ROOT / "scripts").glob("prepare-*.sh")]
        scripts += [(ROOT / "scripts/verify-linux-appimage.sh").read_text(), (ROOT / "scripts/posix-release-isolation.sh").read_text()]
        for name in ("linux", "macos"):
            data = yaml.safe_load((ROOT / f".github/workflows/release-{name}.yml").read_text())
            for job in data["jobs"].values():
                scripts += [step["run"] for step in job["steps"] if "run" in step]
        for script in scripts:
            script = re.sub(r"\$\{\{.*?\}\}", "fixture", script)
            result = subprocess.run([bash, "-n"], input=script, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
