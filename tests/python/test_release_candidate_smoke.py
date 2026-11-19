"""Candidate installer identity, environment and cleanup use disposable fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("candidate_smoke", ROOT / "scripts/smoke-release-candidate.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class CandidateSmokeTests(unittest.TestCase):
    def setUp(self):
        nonce = uuid.uuid4().hex
        self.work = Path(tempfile.gettempdir()).absolute() / f"MediaForge-candidate-{nonce}"
        self.work.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.work) if self.work.exists() else None)
        self.report = self.work.parent / f"MediaForge-candidate-report-{nonce}.json"
        self.addCleanup(lambda: self.report.unlink(missing_ok=True))
        self.identity = {"nonce": nonce, "workRoot": str(self.work), "installDir": str(self.work / "install"),
                         "appId": "{" + str(uuid.uuid4()).upper() + "}", "appName": "MediaForge Candidate Test fixture",
                         "registryKey": rf"Software\MediaForgeCandidateTests\{nonce}"}
        (self.work / SMOKE.OWNER_FILE).write_text(json.dumps(self.identity), encoding="utf-8")

    def test_real_installer_source_has_only_test_identity_and_no_shortcuts_or_run(self):
        source = (ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8-sig")
        test = SMOKE.test_installer_source(source, self.identity, "A")
        self.assertNotIn(SMOKE.PRODUCTION_APP_ID, test)
        self.assertIn(self.identity["registryKey"], test)
        self.assertIn(f'AppId={{{self.identity["appId"]}', test)
        self.assertIn("CloseApplications=no", test)
        self.assertNotIn("{autoprograms}", test)
        self.assertNotIn("{autodesktop}", test)
        self.assertNotIn("Filename:", test)
        self.assertIn('Source: "{#SourceDir}\\*"', test)
        self.assertIn("LZMADictionarySize=65536", test)

    def test_source_identity_drift_fails_closed(self):
        source = (ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8-sig")
        with self.assertRaisesRegex(ValueError, "AppId declaration changed"):
            SMOKE.test_installer_source(source.replace(SMOKE.PRODUCTION_APP_ID, "another-app-id"), self.identity, "A")

    def test_tainted_paths_and_signing_secrets_are_removed_before_setting_temp_roots(self):
        tainted = {"MEDIAFORGE_DATA_ROOT": "C:/user-profile", "MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR": "old",
                   "PYMSS_STUDIO_PYTHON": "C:/installed/python.exe", "PYMSS_OTHER_PATH": "C:/user-data",
                   "TAURI_SIGNING_PRIVATE_KEY": "secret", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "secret",
                   "VERSIONS_SIGNING_PRIVATE_KEY": "secret", "SIGNPATH_API_TOKEN": "secret",
                   "HF_TOKEN": "secret", "PYTHONPATH": "C:/external", "PYTHONHOME": "C:/external"}
        with mock.patch.dict(os.environ, tainted, clear=True):
            env = SMOKE.isolated_environment(self.work)
        for key in ("MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR", "PYMSS_OTHER_PATH", "TAURI_SIGNING_PRIVATE_KEY",
                    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "HF_TOKEN", "PYTHONPATH", "PYTHONHOME"):
            self.assertNotIn(key, env)
        self.assertNotIn("VERSIONS_SIGNING_PRIVATE_KEY", env)
        self.assertNotIn("SIGNPATH_API_TOKEN", env)
        for key in ("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT", "PYMSS_STUDIO_DATA_DIR"):
            self.assertEqual(Path(env[key]), self.work / "install/data")
        self.assertEqual(Path(env["MEDIAFORGE_MODEL_DIR"]), self.work / "install/models")
        self.assertEqual(Path(env["MEDIAFORGE_RUNTIME_ENVS_DIR"]), self.work / "install/runtime-envs")
        self.assertEqual(Path(env["WEBVIEW2_USER_DATA_FOLDER"]), self.work / "webview")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_help_performs_no_external_process_or_stage_probe(self):
        with mock.patch.object(SMOKE.subprocess, "run") as run, mock.patch.object(SMOKE, "execute") as execute:
            with self.assertRaises(SystemExit) as stopped:
                SMOKE.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        run.assert_not_called()
        execute.assert_not_called()

    def test_assets_are_valid_preset_config_and_exactly_four_kib_of_fake_model(self):
        install = Path(self.identity["installDir"])
        snapshot = SMOKE.seed_assets(install, self.identity["nonce"])
        self.assertEqual(snapshot, SMOKE.asset_snapshot(install))
        self.assertEqual(len(snapshot), 4)
        self.assertEqual((install / "models/candidate-fixture/preservation-only.bin").stat().st_size, 4096)
        config = json.loads((install / "data/settings/media-workflows.json").read_text())
        self.assertEqual(config["version"], 1)
        self.assertEqual(config["presets"][0]["definition"]["modality"], "image")
        self.assertEqual(config["presets"][0]["definition"]["steps"][0]["bindings"], {"upscaler": "media:real-esrgan-x2"})

    def test_owned_work_rejects_altered_marker_and_production_identity(self):
        self.assertEqual(SMOKE.owned_work(self.identity), self.work)
        with self.assertRaisesRegex(ValueError, "marker mismatch"):
            SMOKE.owned_work(dict(self.identity, installDir=str(self.work / "other")))
        forbidden = dict(self.identity, appId=SMOKE.PRODUCTION_APP_ID)
        (self.work / SMOKE.OWNER_FILE).write_text(json.dumps(forbidden), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "AppId is not isolated"):
            SMOKE.owned_work(forbidden)

    def test_cleanup_refuses_running_candidate_without_stopping_or_uninstalling_it(self):
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[123]) as active, mock.patch.object(SMOKE, "run_hidden") as run:
            with self.assertRaisesRegex(RuntimeError, "close candidate processes"):
                SMOKE.cleanup(self.report, self.identity)
        active.assert_called_once_with(self.work)
        run.assert_not_called()
        self.assertTrue(self.work.is_dir())

    def test_failed_runs_cannot_automatically_uninstall_or_clean_a_possible_process_tree(self):
        with mock.patch.object(SMOKE, "running_test_processes") as active, mock.patch.object(SMOKE, "run_hidden") as run:
            with self.assertRaisesRegex(ValueError, "failed candidate runs require inspection"):
                SMOKE.cleanup(self.report, dict(self.identity, status="failed"))
        active.assert_not_called()
        run.assert_not_called()
        self.assertTrue(self.work.is_dir())

    def test_cleanup_detects_asset_changes_and_retains_the_work_root(self):
        install = Path(self.identity["installDir"])
        SMOKE.seed_assets(install, self.identity["nonce"])
        (install / "app").mkdir()
        (install / "app/MediaForge.exe").write_bytes(b"app fixture")
        uninstaller = install / "unins000.exe"
        uninstaller.write_bytes(b"uninstaller fixture")
        report = dict(self.identity, installedVersion="0.0.32", uninstallerSha256=SMOKE.digest(uninstaller))
        def fake_uninstall(*args, **kwargs):
            shutil.rmtree(install / "app")
            uninstaller.unlink()
            (install / "models/candidate-fixture/preservation-only.bin").write_bytes(b"changed")
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[]), mock.patch.object(SMOKE, "verify_identity"), mock.patch.object(SMOKE, "run_hidden", side_effect=fake_uninstall):
            with self.assertRaisesRegex(RuntimeError, "changed preserved"):
                SMOKE.cleanup(self.report, report)
        self.assertTrue(self.work.is_dir())

    def test_successful_cleanup_preserves_assets_before_removing_only_owned_temp_root(self):
        install = Path(self.identity["installDir"])
        preserved = SMOKE.seed_assets(install, self.identity["nonce"])
        (install / "app").mkdir()
        (install / "app/MediaForge.exe").write_bytes(b"app fixture")
        uninstaller = install / "unins000.exe"
        uninstaller.write_bytes(b"uninstaller fixture")
        report = dict(self.identity, installedVersion="0.0.32", uninstallerSha256=SMOKE.digest(uninstaller))
        def fake_uninstall(*args, **kwargs):
            shutil.rmtree(install / "app")
            uninstaller.unlink()
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[]), mock.patch.object(SMOKE, "verify_identity"), mock.patch.object(SMOKE, "registry_values", return_value=None), mock.patch.object(SMOKE, "run_hidden", side_effect=fake_uninstall):
            SMOKE.cleanup(self.report, report)
        self.assertFalse(self.work.exists())
        evidence = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "cleaned")
        self.assertEqual(evidence["uninstallPreservedAssets"], preserved)

    def test_uninstall_process_wait_polls_until_the_second_phase_exits(self):
        with mock.patch.object(SMOKE, "running_test_processes", side_effect=[[321], []]) as active, \
             mock.patch.object(SMOKE.time, "sleep") as sleep:
            self.assertEqual(SMOKE.wait_test_processes(self.work), [])
        self.assertEqual(active.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_uninstall_process_wait_returns_live_pids_at_the_deadline(self):
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[321]), \
             mock.patch.object(SMOKE.time, "monotonic", side_effect=[0, 31]), \
             mock.patch.object(SMOKE.time, "sleep") as sleep:
            self.assertEqual(SMOKE.wait_test_processes(self.work), [321])
        sleep.assert_not_called()

    def test_cleanup_recovers_a_verified_uninstall_without_running_another_uninstaller(self):
        install = Path(self.identity["installDir"])
        preserved = SMOKE.seed_assets(install, self.identity["nonce"])
        report = dict(self.identity, installedVersion="0.0.32", uninstallerSha256="a" * 64,
                      status="failed-retained", uninstallPreservedAssets=preserved)
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[]), \
             mock.patch.object(SMOKE, "registry_values", return_value=None), \
             mock.patch.object(SMOKE, "verify_identity") as verify, mock.patch.object(SMOKE, "run_hidden") as run:
            SMOKE.cleanup(self.report, report)
        verify.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.work.exists())
        self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["status"], "cleaned")

    def test_temp_removal_failure_does_not_report_cleanup_as_completed(self):
        report = dict(self.identity)
        (self.work / "locked").mkdir()
        with mock.patch.object(SMOKE, "running_test_processes", return_value=[]), mock.patch.object(SMOKE.shutil, "rmtree", side_effect=PermissionError("fixture locked")):
            with self.assertRaises(PermissionError):
                SMOKE.cleanup(self.report, report)
        self.assertTrue(self.work.is_dir())
        evidence = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "uninstalled-pending-temp-cleanup")
        self.assertNotIn("cleanedAt", evidence)
        self.assertTrue((self.work / SMOKE.OWNER_FILE).is_file())

    @unittest.skipUnless(os.name == "nt", "Windows readonly shell directory attributes")
    def test_only_owned_readonly_shell_directory_is_retried_without_acl_or_process_changes(self):
        directory = self.work / "profile/AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
        directory.mkdir(parents=True)
        (directory / "fixture.txt").write_bytes(b"temporary test file")
        os.chmod(directory, stat.S_IREAD)
        self.assertTrue(directory.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
        with mock.patch.object(SMOKE, "running_test_processes") as active, mock.patch.object(SMOKE, "run_hidden") as run:
            SMOKE.remove_owned_work(self.identity)
        active.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.work.exists())


if __name__ == "__main__":
    unittest.main()
