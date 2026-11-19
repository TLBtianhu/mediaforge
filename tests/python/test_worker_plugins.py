from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

if __package__:
    from . import _bootstrap as _worker_test_bootstrap
    from ._plugin_marketplace_fixture import ArchiveResponse, MarketplaceFixture
else:
    import _bootstrap as _worker_test_bootstrap
    from _plugin_marketplace_fixture import ArchiveResponse, MarketplaceFixture

from core import worker_operations
from core import worker_plugins as plugins


class PluginMarketplaceTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[2] / ".tmp" / "plugin-tests"
        work.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.environment = mock.patch.dict(os.environ, {
            "MEDIAFORGE_RUNTIME_ENVS_DIR": str(self.work / "runtimes"),
            "MEDIAFORGE_ACTIVE_RUNTIME_FILE": str(self.work / "active.json"),
            "PYMSS_PLUGINS_DIR": str(self.work / "manual"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        (self.work / "active.json").write_text('{"backend":"cpu"}')
        self.root = plugins._root()
        self.marketplace = MarketplaceFixture(self)
        self.reset_runtime()
        self.addCleanup(self.reset_runtime)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))

    @staticmethod
    def reset_runtime():
        import pymss.operations
        import pymss.plugins
        runtime = pymss.operations._DEFAULT_RUNTIME
        snapshot = getattr(runtime, "_studio_snapshot", None)
        if snapshot is not None:
            snapshot.cleanup()
        pymss.operations._DEFAULT_RUNTIME = None
        pymss.plugins.reset()

    def source(self, *, name="hello", version="1.0.0", broken=False, operation="example.hello", print_message=False):
        source = self.work / "sources" / name
        source.mkdir(parents=True, exist_ok=True)
        source.joinpath("pyproject.toml").write_text(
            f'[project]\nname="pymss-plugin-{name}"\nversion="{version}"\nrequires-python=">=3.10"\ndependencies=[]\n',
        )
        code = f'''def run(ctx, inputs, params):
    return {{"value": 42}}

def setup(plugin):
    plugin.register_operation(id={operation!r}, title="Hello", inputs={{}},
        params={{"type":"object", "properties":{{}}}},
        outputs={{"value":{{"type":"number"}}}}, run=run)
'''
        if broken:
            code += '    raise RuntimeError("setup failed deliberately")\n'
        if print_message:
            code = 'print("incidental plugin output")\n' + code
        source.joinpath("__init__.py").write_text(code)
        return source

    def manage(self, action, **payload):
        output = io.StringIO()
        with redirect_stdout(output):
            code = plugins.cmd_manage_plugin({"action": action, "taskId": "plugin_manage_test", **payload})
        events = [json.loads(line) for line in output.getvalue().splitlines() if line]
        return code, events

    def install(self, source):
        package = self.marketplace.pin(source)
        code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 0, events)
        self.assertEqual(events[-1]["type"], "plugin_manage_result")
        return events[-1]["payload"]

    def test_install_verified_marketplace_package_and_run_through_worker(self):
        snapshot = self.install(self.source(print_message=True))
        self.assertTrue(snapshot["installed"][0]["loaded"])
        self.assertEqual(snapshot["installed"][0]["operations"], ["example.hello"])
        output = io.StringIO()
        with redirect_stdout(output):
            code = worker_operations.cmd_run_operation({
                "operation": "example.hello", "inputs": {}, "params": {},
                "outputDir": str(self.work / "output"), "requestId": "hello-run",
            })
        self.assertEqual(code, 0, output.getvalue())
        event = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(event["payload"]["outputs"]["value"]["value"], 42)
        self.assertEqual(os.environ["PYMSS_PLUGINS_DIR"], str(self.work / "manual"))

    def test_successful_update_replaces_source_and_version(self):
        source = self.source()
        self.install(source)
        self.marketplace.pin(self.source(version="2.0.0"))
        code, events = self.manage("update", name="hello")
        self.assertEqual(code, 0, events)
        self.assertEqual(events[-1]["payload"]["installed"][0]["version"], "2.0.0")

    def test_failed_update_keeps_working_previous_version(self):
        source = self.source()
        self.install(source)
        previous = self.marketplace.packages["hello"]
        original = (self.root / "installed/hello/__init__.py").read_text()
        self.marketplace.pin(self.source(version="2.0.0", broken=True))
        code, events = self.manage("update", name="hello")
        self.assertEqual(code, 1)
        self.assertIn("setup failed deliberately", events[-1]["payload"]["message"])
        self.assertEqual((self.root / "installed/hello/__init__.py").read_text(), original)
        self.assertEqual(plugins._manifest(self.root / "installed/hello")["version"], "1.0.0")
        self.marketplace.packages["hello"] = previous
        self.assertTrue(plugins._inventory(self.root)["installed"][0]["loaded"])

    def test_failing_dependency_install_does_not_publish_source(self):
        package = self.marketplace.pin(self.source())
        with mock.patch.object(plugins, "_install_dependencies", side_effect=RuntimeError("dependency conflict")):
            code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "installed/hello").exists())
        self.assertIn("dependency conflict", events[-1]["payload"]["message"])

    def test_uninstall_removes_source_and_sidecar_without_touching_manual_plugins(self):
        self.install(self.source())
        deps = self.root / "installed/hello/.studio-deps"
        deps.mkdir()
        deps.joinpath("example.py").write_text("value=1")
        manual = self.work / "manual/keep"
        manual.mkdir(parents=True)
        manual.joinpath("__init__.py").write_text("")
        code, events = self.manage("uninstall", name="hello")
        self.assertEqual(code, 0, events)
        self.assertFalse(deps.parent.exists())
        self.assertTrue(manual.exists())

    def test_invalid_names_cannot_escape_installation_directory(self):
        for name in ["../hello", "/hello", "hello/../../", "", ".hidden", "a\\b"]:
            with self.subTest(name=name):
                code, events = self.manage("uninstall", name=name)
                self.assertEqual(code, 1)
                self.assertEqual(events[-1]["payload"]["code"], "PLUGIN_MANAGE_FAILED")

    def test_repair_uses_the_catalog_instead_of_the_installed_source_or_receipt(self):
        self.install(self.source())
        installed = self.root / "installed/hello"
        receipt = plugins._manifest(installed)
        receipt["url"] = "https://example.com/tampered.zip"
        installed.joinpath(".pymss-install.json").write_text(json.dumps(receipt))
        entry = installed / "__init__.py"
        approved = entry.read_bytes()
        marker = self.work / "unverified-repair-import"
        entry.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.marketplace.download.reset_mock()
        code, events = self.manage("repair", name="hello")
        self.assertEqual(code, 0, events)
        package = self.marketplace.packages["hello"]
        self.marketplace.download.assert_called_once_with(package)
        self.assertEqual(plugins._manifest(installed)["url"], package.repository_url)
        self.assertEqual(entry.read_bytes(), approved)
        self.assertFalse(marker.exists())

    def test_catalog_failure_still_returns_installed_plugins(self):
        self.install(self.source())
        output = io.StringIO()
        with mock.patch.object(plugins, "catalog", side_effect=ValueError("invalid bundled catalog")), redirect_stdout(output):
            code = plugins.cmd_plugin_marketplace({})
        snapshot = json.loads(output.getvalue())["payload"]
        self.assertEqual(code, 0)
        self.assertEqual(snapshot["catalogError"], "invalid bundled catalog")
        self.assertEqual(snapshot["installed"][0]["name"], "hello")

    def test_catalog_normalizes_search_metadata(self):
        self.marketplace.pin(self.source())
        output = io.StringIO()
        with redirect_stdout(output):
            code = plugins.cmd_plugin_marketplace({})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["payload"]["catalog"][0]["tags"], ["audio"])

    def test_recovery_restores_previous_version_after_interrupted_publication(self):
        self.install(self.source())
        backup = self.root / "backups/hello"
        backup.parent.mkdir(exist_ok=True)
        (self.root / "installed/hello").rename(backup)
        snapshot = plugins._inventory(self.root)
        self.assertTrue(snapshot["installed"][0]["loaded"])
        self.assertFalse(backup.exists())

    def test_duplicate_operation_rejected_without_breaking_existing_plugin(self):
        self.install(self.source())
        package = self.marketplace.pin(self.source(name="duplicate"))
        code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 1, events)
        self.assertFalse((self.root / "installed/duplicate").exists())
        self.assertTrue(plugins._inventory(self.root)["installed"][0]["loaded"])

    def test_builtin_operation_collision_rejected(self):
        self.source(operation="pymss.audio.adjust_volume")
        # Use an actual built-in id rather than assuming the DSP namespace.
        from pymss.operations import default_runtime
        builtin = default_runtime().list_operations()[0]["id"]
        package = self.marketplace.pin(self.source(operation=builtin))
        code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 1, events)
        self.assertFalse((self.root / "installed/hello").exists())

    def test_backend_and_interpreter_both_scope_plugin_installations(self):
        (self.work / "active.json").write_text('{"backend":"mlx"}')
        self.assertNotEqual(self.root, plugins._root())
        (self.work / "active.json").write_text('{"backend":"cpu"}')
        with mock.patch.object(sys, "executable", str(self.work / "another-venv/bin/python")):
            self.assertNotEqual(self.root, plugins._root())

    def test_probe_failure_is_visible_on_installed_row(self):
        self.install(self.source())
        with mock.patch.object(plugins, "_probe", side_effect=RuntimeError("plugin probe timed out")):
            snapshot = plugins._inventory(self.root)
        self.assertIsNone(snapshot["installed"][0]["loaded"])
        self.assertIn("timed out", snapshot["verificationError"])

    def test_update_check_compares_the_current_catalog_version_without_downloading(self):
        source = self.source()
        self.install(source)
        self.marketplace.pin(self.source(version="2.0.0"))
        self.marketplace.download.reset_mock()
        code, events = self.manage("check_updates")
        self.assertEqual(code, 0, events)
        update = events[-1]["payload"]["updates"]["hello"]
        self.assertFalse(update["up_to_date"])
        self.assertEqual(update["remote_version"], "2.0.0")
        self.marketplace.download.assert_not_called()

    def test_manual_plugin_is_visible_and_operation_collision_names_the_owner(self):
        manual = self.work / "manual/manual-hello"
        manual.parent.mkdir(parents=True)
        shutil.copytree(self.source(), manual)
        package = self.marketplace.pin(self.source(name="managed-hello"))
        code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 1, events)
        self.assertIn("manually installed plugin manual-hello", events[-1]["payload"]["message"])
        row = plugins._inventory(self.root)["installed"][0]
        self.assertEqual(row["name"], "manual-hello")
        self.assertFalse(row["managed"])
        self.assertTrue(row["loaded"])

    def test_recovery_cleans_abandoned_install_and_uninstall_transactions(self):
        for name in ["install-abandoned", "removed-old"]:
            directory = self.root / "transactions" / name
            directory.mkdir(parents=True)
            directory.joinpath("partial-file").write_text("left behind")
        plugins._recover(self.root)
        self.assertEqual(list(self.root.joinpath("transactions").iterdir()), [])

    def test_inventory_does_not_hold_exclusive_lock_while_probing(self):
        from core.worker_runtime_lock import runtime_lock
        self.install(self.source())
        def probe(*args, **kwargs):
            with runtime_lock(self.root / ".lock", timeout=0):
                return []
        with mock.patch.object(plugins, "_probe", side_effect=probe):
            self.assertIsNone(plugins._inventory(self.root)["verificationError"])

    def test_readers_do_not_wait_or_recover_a_foreign_live_transaction(self):
        import subprocess
        import time
        live = self.root / "transactions/install-live"
        live.mkdir(parents=True)
        live.joinpath("marker").write_text("active writer")
        script = "from pathlib import Path; import sys; from core.worker_runtime_lock import runtime_lock\nwith runtime_lock(Path(sys.argv[1])):\n print('locked', flush=True)\n sys.stdin.read(1)"
        environment = dict(os.environ, PYTHONPATH=str(Path(plugins.__file__).resolve().parents[1]))
        child = subprocess.Popen([sys.executable, "-u", "-c", script, str(self.root / ".lock")],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            started = time.monotonic()
            snapshot = plugins._inventory(self.root, verify=False)
            runtime = plugins.operation_runtime()
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(snapshot["installed"], [])
            self.assertGreaterEqual(len(runtime.list_operations()), 4)
            self.assertTrue(live.joinpath("marker").exists())
        finally:
            child.communicate(input="x", timeout=5)

    def test_read_only_recovery_failure_does_not_hide_installed_plugins(self):
        self.install(self.source())
        with mock.patch.object(plugins, "runtime_lock", side_effect=PermissionError("read-only")):
            snapshot = plugins._inventory(self.root)
            runtime = plugins.operation_runtime()
        self.assertTrue(snapshot["installed"][0]["loaded"])
        self.assertIn("example.hello", [item["id"] for item in runtime.list_operations()])

    def test_removed_catalog_entry_preserves_the_installed_files(self):
        self.install(self.source())
        package = self.marketplace.packages.pop("hello")
        installed = self.root / "installed/hello"
        original = installed.joinpath("__init__.py").read_bytes()
        receipt = plugins._manifest(installed)
        code, events = self.manage("update", name="hello")
        self.assertEqual(code, 1, events)
        self.assertEqual(installed.joinpath("__init__.py").read_bytes(), original)
        self.assertEqual(plugins._manifest(installed), receipt)
        self.marketplace.packages["hello"] = package
        self.assertTrue(plugins._inventory(self.root)["installed"][0]["loaded"])

    def test_probe_failure_preserves_manual_plugin_names_with_unknown_status(self):
        manual = self.work / "manual/hello"
        manual.parent.mkdir(parents=True)
        shutil.copytree(self.source(), manual)
        with mock.patch.object(plugins, "_probe", side_effect=RuntimeError("probe timeout")):
            snapshot = plugins._inventory(self.root)
        self.assertEqual(snapshot["installed"][0]["name"], "hello")
        self.assertFalse(snapshot["installed"][0]["managed"])
        self.assertIsNone(snapshot["installed"][0]["loaded"])

    def test_unapproved_sources_are_rejected_before_acquisition_or_code(self):
        from pymss.plugins import install as legacy_installer

        source = self.source()
        self.marketplace.pin(source)
        sources = (
            {"name": "unlisted"},
            {"target": str(source)},
            {"target": "https://example.com/foreign.git"},
            {"target": "ssh://git@example.com/repo.git@v1#plugins/hello"},
            {"name": "hello", "target": "https://example.com/foreign.git"},
            {"name": "hello", "url": "https://example.com/foreign.zip", "sha256": "a" * 64},
        )
        with mock.patch.dict(os.environ, {"PYMSS_PLUGINS_REGISTRY": "https://example.com/registry.json"}), \
             mock.patch.object(legacy_installer, "install", side_effect=AssertionError("unapproved acquisition")) as legacy, \
             mock.patch.object(plugins, "_install_dependencies") as install, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            for payload in sources:
                with self.subTest(payload=payload):
                    code, events = self.manage("install", **payload)
                    self.assertEqual(code, 1, events)
                    self.assertEqual(events[-1]["payload"]["code"], "PLUGIN_MANAGE_FAILED")
            self.marketplace.download.assert_not_called()
            legacy.assert_not_called()
            install.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def test_tampered_download_never_reaches_dependency_install_or_python_probe(self):
        package = self.marketplace.pin(self.source())
        data = self.marketplace.archives[package.archive_url]
        self.marketplace.archives[package.archive_url] = data[:-1] + bytes([data[-1] ^ 1])
        with mock.patch.object(plugins, "_install_dependencies") as install, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            code, events = self.manage("install", name=package.name)
            self.assertEqual(code, 1, events)
            self.assertEqual(events[-1]["payload"]["code"], "PLUGIN_MANAGE_FAILED")
            self.marketplace.download.assert_called_once_with(package)
            install.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def test_redirected_response_is_rejected_even_with_the_expected_archive_bytes(self):
        package = self.marketplace.pin(self.source())
        data = self.marketplace.archives[package.archive_url]
        self.marketplace.download.side_effect = lambda _: ArchiveResponse(data, "https://example.com/copied.zip")
        with mock.patch.object(plugins, "_install_dependencies") as install, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            code, events = self.manage("install", name=package.name)
            self.assertEqual(code, 1, events)
            install.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def test_matching_archive_hash_does_not_permit_path_traversal(self):
        package = self.marketplace.pin(self.source(), extra_members={"../escape.py": "raise SystemExit(11)"})
        with mock.patch.object(plugins, "_install_dependencies") as install, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            code, events = self.manage("install", name=package.name)
            self.assertEqual(code, 1, events)
            install.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def test_matching_archive_hash_does_not_bypass_plugin_tree_verification(self):
        package = self.marketplace.pin(self.source())
        self.marketplace.packages[package.name] = replace(package, tree_sha256="1" * 64)
        with mock.patch.object(plugins, "_install_dependencies") as install, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            code, events = self.manage("install", name=package.name)
            self.assertEqual(code, 1, events)
            install.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def assert_not_loaded(self, name, operation, marker):
        rows = plugins._inventory(self.root)["installed"]
        row = next(row for row in rows if row["name"] == name)
        self.assertIs(row["loaded"], False, row)
        self.assertTrue(row["error"], row)
        self.assertFalse(row.get("contributions"), row)
        runtime = plugins.operation_runtime()
        self.assertNotIn(operation, [item["id"] for item in runtime.list_operations()])
        self.assertFalse(marker.exists(), "unverified Python was loaded")

    def test_modified_installed_source_is_not_imported_by_queries_or_operations(self):
        self.install(self.source())
        self.install(self.source(name="intact", operation="example.intact"))
        marker = self.work / "tampered-source-loaded"
        entry = self.root / "installed/hello/__init__.py"
        entry.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + entry.read_text())
        self.assert_not_loaded("hello", "example.hello", marker)
        runtime = plugins.operation_runtime()
        self.assertIn("example.intact", [item["id"] for item in runtime.list_operations()])

    def test_unexpected_installed_sidecar_is_not_added_to_python_path(self):
        source = self.source()
        entry = source / "__init__.py"
        entry.write_text("try:\n    import unapproved_test_sidecar\nexcept ImportError:\n    pass\n" + entry.read_text())
        self.install(source)
        marker = self.work / "tampered-sidecar-loaded"
        deps = self.root / "installed/hello/.studio-deps"
        deps.mkdir(exist_ok=True)
        deps.joinpath("unapproved_test_sidecar.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.assert_not_loaded("hello", "example.hello", marker)

    def test_unverified_managed_files_cannot_be_loaded_via_manual_discovery(self):
        self.install(self.source())
        marker = self.work / "managed-source-loaded-as-manual"
        entry = self.root / "installed/hello/__init__.py"
        entry.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + entry.read_text())
        with mock.patch.dict(os.environ, {"PYMSS_PLUGINS_DIR": str(self.root / "installed")}):
            self.assert_not_loaded("hello", "example.hello", marker)

    def test_legacy_marketplace_receipt_is_preserved_without_loading_python(self):
        source = self.source()
        marker = self.work / "unverified-legacy-source-loaded"
        entry = source / "__init__.py"
        entry.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + entry.read_text())
        self.marketplace.pin(source)
        installed = self.root / "installed/hello"
        shutil.copytree(source, installed)
        receipt = installed / ".pymss-install.json"
        old = b'{"name":"hello","source":"url","url":"https://example.com/legacy.git"}'
        receipt.write_bytes(old)
        self.assert_not_loaded("hello", "example.hello", marker)
        self.assertEqual(receipt.read_bytes(), old)

    def test_source_without_entrypoint_is_not_installed(self):
        source = self.source()
        source.joinpath("__init__.py").unlink()
        package = self.marketplace.pin(source)
        code, events = self.manage("install", name=package.name)
        self.assertEqual(code, 1, events)
        self.assertFalse((self.root / "installed/hello").exists())

    def test_operation_runtime_can_be_queried_twice_in_same_process(self):
        self.install(self.source())
        first = plugins.operation_runtime()
        second = plugins.operation_runtime()
        self.assertIs(first, second)
        self.assertIn("example.hello", [item["id"] for item in second.list_operations()])

    def test_probe_uses_the_manual_directory_after_an_overlapping_bootstrap(self):
        probing = threading.Event()
        started_child = threading.Event()
        child_environments = []

        def probe():
            probing.set()
            return plugins._probe_snapshot(self.work / "private", self.root)

        def run(*args, env, **kwargs):
            child_environments.append(env["PYMSS_PLUGINS_DIR"])
            started_child.set()
            return mock.Mock(stdout=f"{plugins._PROBE_MARKER}[]\n", stderr="")

        with mock.patch.object(plugins.subprocess, "run", side_effect=run), ThreadPoolExecutor(max_workers=1) as pool:
            with plugins._plugin_dir(self.work / "other-bootstrap"):
                result = pool.submit(probe)
                self.assertTrue(probing.wait(timeout=5))
                self.assertFalse(started_child.wait(timeout=0.2))
            self.assertEqual(result.result(timeout=5), [])
        self.assertEqual(child_environments, [str(self.work / "manual")])

    def test_real_pip_installs_a_local_wheel_into_the_plugin_sidecar(self):
        source = self.source()
        wheelhouse = self.work / "wheelhouse"
        wheelhouse.mkdir()
        wheel = wheelhouse / "studio_market_test_dep-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("studio_market_test_dep/__init__.py", "VALUE = 123\n")
            archive.writestr("studio_market_test_dep-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: studio-market-test-dep\nVersion: 1.0.0\n")
            archive.writestr("studio_market_test_dep-1.0.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: unittest\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("studio_market_test_dep-1.0.0.dist-info/RECORD", "")
        source.joinpath("pyproject.toml").write_text(
            '[project]\nname="hello"\nversion="1.0.0"\ndependencies=["studio-market-test-dep==1.0.0"]\n',
        )
        entry = source / "__init__.py"
        entry.write_text("import studio_market_test_dep\n" + entry.read_text())
        run_process = plugins._run_process

        def run_offline(command, task_id, **kwargs):
            flags = command.index("install") + 1
            command = command[:flags] + ["--no-index", "--find-links", str(wheelhouse)] + command[flags:]
            run_process(command, task_id, **kwargs)

        with mock.patch.object(plugins, "_run_process", side_effect=run_offline):
            snapshot = self.install(source)
        self.assertTrue(snapshot["installed"][0]["loaded"])
        self.assertTrue((self.root / "installed/hello/.studio-deps/studio_market_test_dep").is_dir())
        import importlib.metadata
        with self.assertRaises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version("studio-market-test-dep")

    def test_direct_plugin_requirement_is_rejected_before_starting_pip(self):
        source = self.source()
        source.joinpath("pyproject.toml").write_text(
            '[project]\nversion="1.0.0"\ndependencies=["tiny-extra @ https://example.com/dependency.whl"]\n',
        )
        package = self.marketplace.pin(source)
        with mock.patch.object(plugins, "_run_process") as run_process, \
             mock.patch.object(plugins, "_probe_snapshot") as probe:
            code, events = self.manage("install", name=package.name)
            self.assertEqual(code, 1, events)
            run_process.assert_not_called()
            probe.assert_not_called()
        self.assertFalse((self.root / "installed/hello").exists())

    def test_dependency_resolution_freezes_host_and_installs_only_into_sidecar(self):
        source = self.source()
        source.joinpath("pyproject.toml").write_text('[project]\ndependencies=["tiny-extra>=1"]\n')
        import importlib.metadata
        fake_host = mock.Mock(metadata={"Name": "numpy"}, version="2.0.0")
        calls = []
        def run(command, task_id, **kwargs):
            calls.append(command)
            if "--report" in command:
                Path(command[command.index("--report") + 1]).write_text(json.dumps({"install": [
                    {"metadata": {"name": "tiny-extra", "version": "1.2.0"},
                     "download_info": {
                         "url": "https://files.pythonhosted.org/packages/tiny_extra-1.2.0-py3-none-any.whl",
                         "archive_info": {"hashes": {"sha256": "1" * 64}},
                     }},
                ]}))
        with mock.patch.object(importlib.metadata, "distributions", return_value=[fake_host]), \
             mock.patch.object(plugins, "_run_process", side_effect=run), redirect_stdout(io.StringIO()):
            plugins._install_dependencies(source, self.root, "plugin_manage_test", {"mirror": "pypi"})
        self.assertEqual(calls[0][:3], [sys.executable, "-m", "pip"])
        self.assertIn("--only-binary=:all:", calls[0])
        self.assertIn("--no-compile", calls[1])
        self.assertIn("numpy==2.0.0", source.parent.joinpath("constraints.txt").read_text())
        self.assertIn("--dry-run", calls[0])
        self.assertIn("--no-deps", calls[1])
        self.assertIn("--require-hashes", calls[1])
        requirements = Path(calls[1][calls[1].index("--requirement") + 1])
        self.assertEqual(requirements.read_text().strip(), f"tiny-extra==1.2.0 --hash=sha256:{'1' * 64}")
        self.assertEqual(calls[1][calls[1].index("--target") + 1], str(source / ".studio-deps"))

    def test_resolved_dependency_without_a_hash_is_not_installed(self):
        source = self.source()
        source.joinpath("pyproject.toml").write_text('[project]\ndependencies=["tiny-extra>=1"]\n')

        def resolve(command, task_id, **kwargs):
            Path(command[command.index("--report") + 1]).write_text(json.dumps({"install": [
                {"metadata": {"name": "tiny-extra", "version": "1.2.0"},
                 "download_info": {"url": "https://files.pythonhosted.org/packages/tiny_extra-1.2.0-py3-none-any.whl"}},
            ]}))

        with mock.patch.object(plugins, "_run_process", side_effect=resolve) as run_process, redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                plugins._install_dependencies(source, self.root, "plugin_manage_test", {"mirror": "pypi"})
            run_process.assert_called_once()
        self.assertFalse((source / ".studio-deps").exists())


if __name__ == "__main__":
    unittest.main()
