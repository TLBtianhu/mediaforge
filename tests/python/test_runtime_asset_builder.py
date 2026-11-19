"""Runtime asset builds cannot overwrite older assets or archive build data."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runtime_asset_builder", ROOT / "scripts/build-runtime-asset.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class RuntimeAssetBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mediaforge-asset-builder-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.asset_id = "runtime-cuda-py312-2026.11.7"
        self.stage = self.root / ".dev-data/runtime-envs" / self.asset_id
        self.patch = mock.patch.object(BUILDER, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_stage_paths_cannot_escape_the_owned_development_runtime_root(self):
        for path in (self.root, self.root / "runtime", self.stage / "nested", self.root.parent / self.asset_id):
            with self.subTest(path=path), self.assertRaises(ValueError):
                BUILDER.checked_stage(self.asset_id, path)
        self.assertEqual(BUILDER.checked_stage(self.asset_id, self.stage), self.stage)
        with self.assertRaises(ValueError):
            BUILDER.checked_stage("../../runtime", self.stage)

    def test_wrong_python_or_venv_is_rejected_before_creating_any_output(self):
        for version, prefix, base, target in (("3.11", "host", "host", "win-amd64"),
                                              ("3.12", "venv", "host", "win-amd64"),
                                              ("3.12", "host", "host", "win-arm64")):
            with self.subTest(version=version, prefix=prefix, target=target):
                with mock.patch.object(BUILDER, "python_info", return_value={
                        "version": version, "prefix": prefix, "basePrefix": base, "platform": target}):
                    with self.assertRaises(ValueError):
                        BUILDER.copy_base(Path("host/python.exe"), self.stage, "3.12")
                self.assertFalse(self.stage.exists())

    def test_archive_contains_runtime_files_once_and_excludes_build_profile_and_caches(self):
        for relative in ("Scripts/python.exe", "Scripts/python312.dll", "Lib/site-packages/example.py",
                         ".build-data/models/private-model.bin", ".build-data/cache/private-token",
                         ".runtime-build.json", ".asset-id", "Lib/__pycache__/cached.pyc"):
            file = self.stage / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"fixture")
        descriptor = BUILDER.package(self.stage, self.asset_id, "cuda", {"torch": "2.7.1+cu128"})
        with zipfile.ZipFile(self.root / f"release/runtime/{self.asset_id}.zip") as archive:
            self.assertEqual(set(archive.namelist()), {
                "Scripts/python.exe", "Scripts/python312.dll", "Lib/site-packages/example.py"})
        marker = json.loads((self.stage / ".asset-id").read_text(encoding="utf-8"))
        self.assertEqual(marker["schemaVersion"], 2)
        self.assertEqual(marker["python"], "3.12")
        self.assertEqual(marker["sha256"], descriptor["sha256"])
        self.assertEqual(descriptor["python"], "3.12")
        self.assertEqual((descriptor["platform"], descriptor["arch"]), ("windows", "x86_64"))
        self.assertEqual(set(descriptor), {"assetId", "backend", "platform", "arch", "python", "sha256", "size", "urls"})
        self.assertEqual(len(descriptor["sha256"]), 64)

    def test_existing_archive_is_preserved_instead_of_replaced(self):
        archive = self.root / f"release/runtime/{self.asset_id}.zip"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"previous verified artifact")
        with self.assertRaises(FileExistsError):
            BUILDER.package(self.stage, self.asset_id, "cuda", {"torch": "2.7.1+cu128"})
        self.assertEqual(archive.read_bytes(), b"previous verified artifact")

    def test_failed_archive_does_not_publish_a_partial_zip_or_leave_a_temp_copy(self):
        self.stage.mkdir(parents=True)
        (self.stage / "file").write_bytes(b"fixture")
        with mock.patch.object(BUILDER.zipfile.ZipFile, "write", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                BUILDER.package(self.stage, self.asset_id, "cuda", {"torch": "2.7.1+cu128"})
        self.assertEqual(list((self.root / "release/runtime").iterdir()), [])

    def test_windows_reparse_attributes_are_rejected_without_following_the_target(self):
        info = mock.Mock(st_mode=0, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=info):
            with self.assertRaises(ValueError):
                BUILDER.checked_stage(self.asset_id, self.stage)

    def test_build_environment_does_not_inherit_user_paths_or_signing_credentials(self):
        with mock.patch.dict(os.environ, {"PYTHONHOME": "user", "PYTHONPATH": "external",
                "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "fixture-password", "HF_TOKEN": "fixture-token",
                "MEDIAFORGE_MODEL_DIR": "user-models", "PIP_INDEX_URL": "fixture-credential-url"}, clear=True):
            env = BUILDER.isolated_environment(self.stage)
        for key in ("PYTHONHOME", "PYTHONPATH", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "HF_TOKEN"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PIP_INDEX_URL"], "https://pypi.org/simple")
        self.assertEqual(Path(env["MEDIAFORGE_MODEL_DIR"]), self.stage / ".build-data/models")
        self.assertEqual(env["PIP_NO_CACHE_DIR"], "1")
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "MPLCONFIGDIR"):
            self.assertTrue(Path(env[key]).is_relative_to(self.stage / ".build-data"), key)

    def test_replaced_interpreter_is_rejected_before_installing_any_packages(self):
        self.stage.mkdir(parents=True)
        (self.stage / BUILDER.OWNER).write_text('{"schemaVersion":1,"python":"3.12"}', encoding="utf-8")
        with mock.patch.object(BUILDER, "python_info", return_value={
                "version": "3.11", "prefix": str(self.stage), "basePrefix": str(self.stage)}), \
                mock.patch.object(BUILDER, "run") as installer:
            with self.assertRaises(ValueError):
                BUILDER.install(self.stage, "cuda", {"python": "3.12"})
        installer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
