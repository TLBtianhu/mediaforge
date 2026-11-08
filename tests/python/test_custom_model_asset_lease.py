"""Current custom-model writers share the app asset lease, without loading models."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from core import worker_custom_models as custom
from core.user_model_registry import read_registry
from core.operation_kernel import Job, OperationKernel
from shared.asset_references import AssetReferencesBusy, asset_reference_lock
from shared import worker_paths
from shared.worker_subprocess import hidden_subprocess_kwargs
import worker


class CustomModelAssetLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mediaforge-custom-registry-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = worker_paths.user_models_file(self.root)
        self.registry.parent.mkdir()
        self.weights = self.root / "weights.wav"  # Import verification may be disabled; suffix is not ownership.
        self.weights.write_bytes(b"isolated fixture")
        self.env = mock.patch.dict(os.environ, {
            "MEDIAFORGE_DATA_ROOT": str(self.root),
            "PYMSS_USER_MODELS": str(self.registry),
            "MEDIAFORGE_MODEL_DIR": str(self.root / "models"),
            "PYMSS_STUDIO_CUSTOM_MODEL_META": str(self.registry.parent / "custom-models-meta.json"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def held(self):
        with self.assertRaises(AssetReferencesBusy):
            with asset_reference_lock(self.root, timeout=0):
                self.fail("another asset operation entered the writer's critical section")

    def released(self):
        with asset_reference_lock(self.root, timeout=0):
            pass
        self.assertEqual((self.root / ".asset-references.lock").stat().st_size, 0)

    @staticmethod
    def entry(path):
        return types.SimpleNamespace(name="fixture", model_type="demucs", model_path=str(path), config_path=None, aliases=())

    def invoke(self, command, payload):
        output = io.StringIO()
        with redirect_stdout(output):
            code = command(payload)
        return code, [json.loads(line) for line in output.getvalue().splitlines() if line]

    def test_import_holds_lease_during_verification_registration_and_sidecar_commit(self):
        stages = []
        def verify(*args):
            self.held();stages.append("verify")
        def register(*args, **kwargs):
            self.held();stages.append("register");return self.entry(args[2])
        remember = custom._remember_import
        def commit(*args):
            self.held();stages.append("sidecar");return remember(*args)
        with mock.patch.dict(sys.modules, {"pymss": mock.Mock(), "pymss.model_registry": mock.Mock(register_model=register)}), \
                mock.patch.object(custom, "_verify_model_loads", side_effect=verify), \
                mock.patch.object(custom, "_remember_import", side_effect=commit):
            code, _ = self.invoke(custom.cmd_import_custom_model, {"name":"fixture", "modelType":"demucs", "modelPath":str(self.weights)})
        self.assertEqual(code, 0)
        self.assertEqual(stages, ["verify", "register", "sidecar"])
        self.released()

    def test_import_copy_failure_rollback_finishes_before_releasing_asset_lease(self):
        restore = custom._restore_managed_dir
        rollback = []
        def fail(*args, **kwargs):
            self.held();raise ValueError("synthetic registration failure")
        def restore_while_locked(*args):
            self.held();rollback.append(True);return restore(*args)
        with mock.patch.dict(sys.modules, {"pymss":mock.Mock(), "pymss.model_registry":mock.Mock(register_model=fail)}), \
                mock.patch.object(custom, "_restore_managed_dir", side_effect=restore_while_locked):
            code, _ = self.invoke(custom.cmd_import_custom_model, {"name":"fixture", "modelType":"demucs", "modelPath":str(self.weights),
                                                                     "importMode":"copy", "verify":False})
        self.assertEqual(code, 1)
        self.assertEqual(rollback, [True])
        self.assertFalse((self.root / "models/custom/fixture").exists())
        self.released()

    def test_relink_reads_existing_and_rewrites_registry_under_the_same_lease(self):
        def existing(name):
            self.held();return self.entry(self.weights)
        def register(*args, **kwargs):
            self.held();return self.entry(args[2])
        with mock.patch.dict(sys.modules, {"pymss":mock.Mock(),
                "pymss.user_models":mock.Mock(get_user_model_entry=existing),
                "pymss.model_registry":mock.Mock(register_model=register)}):
            code, _ = self.invoke(custom.cmd_relink_custom_model, {"name":"fixture", "modelPath":str(self.weights)})
        self.assertEqual(code, 0)
        self.released()

    def test_remap_snapshot_rewrites_and_sidecar_commit_share_one_lease(self):
        old = self.root / "old"
        def entries():
            self.held();return [self.entry(old / "weights.wav")]
        def register(*args, **kwargs):
            self.held();return self.entry(args[2])
        def sidecar(*args):
            self.held()
        with mock.patch.dict(sys.modules, {"pymss":mock.Mock(), "pymss.user_models":mock.Mock(list_user_models=entries),
                "pymss.model_registry":mock.Mock(register_model=register)}), mock.patch.object(custom, "_write_sidecar", side_effect=sidecar):
            code, events = self.invoke(custom.cmd_remap_custom_model_paths, {"fromRoot":str(old), "toRoot":str(self.root / "new")})
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]["payload"]["count"], 1)
        self.released()

    def test_unregister_and_managed_file_cleanup_keep_lease_through_sidecar_commit(self):
        directory = self.root / "models/custom/fixture"
        directory.mkdir(parents=True)
        (directory / "weights.wav").write_bytes(b"own copy")
        custom._write_sidecar({"fixture":{"importMode":"copy", "managedDir":str(directory)}})
        def unregister(*args):
            self.held();return self.entry(directory / "weights.wav")
        original = custom._forget_import
        def forget(*args):
            self.held();self.assertFalse(directory.exists());return original(*args)
        with mock.patch.dict(sys.modules, {"pymss":mock.Mock(), "pymss.model_registry":mock.Mock(unregister_model=unregister)}), \
                mock.patch.object(custom, "_forget_import", side_effect=forget):
            code, _ = self.invoke(custom.cmd_unregister_custom_model, {"name":"fixture", "deleteFiles":True})
        self.assertEqual(code, 0)
        self.released()

    def test_every_writer_rejects_invalid_existing_registry_without_replacing_bytes(self):
        valid_entry = {"name":"fixture", "model_type":"demucs", "model_path":str(self.weights)}
        cases = ["{broken", '\ufeff{"version":1,"models":[]}', '{"version":1,"version":1,"models":[]}',
                 '{"version":1,"models":null}', '{"version":0,"models":[]}', '{"version":2,"models":[]}',
                 '{"models":[]}', '{"version":true,"models":[]}', '{"version":1,"models":[null]}',
                 '{"version":1,"models":[],"extra":NaN}', '{"version":1,"models":[],"extra":1e999}',
                 '{"version":1,"models":[],"extra":"\\ud800"}']
        for patch in ({"model_path":"relative.wav"}, {"config_path":"relative.yaml"}, {"model_path":""},
                      {"model_path":"C:/file\0.wav"}, {"model_path":"\ufeffC:/file.wav"}, {"model_path":"\ud800"},
                      {"name":""}, {"model_type":""}, {"config_path":False}):
            cases.append(json.dumps({"version":1,"models":[{**valid_entry, **patch}]}))
        writers = (custom.cmd_import_custom_model, custom.cmd_relink_custom_model,
                   custom.cmd_remap_custom_model_paths, custom.cmd_unregister_custom_model)
        for raw in cases:
            for command in writers:
                with self.subTest(raw=repr(raw), command=command.__name__):
                    original = raw.encode("utf-8")
                    self.registry.write_bytes(original)
                    with mock.patch.dict(sys.modules, {"pymss":None, "pymss.model_registry":None}):
                        code, events = self.invoke(command, {})
                    self.assertEqual(code, 1)
                    self.assertEqual(events[-1]["payload"]["code"], "CUSTOM_MODEL_REGISTRY_UNAVAILABLE")
                    self.assertEqual(self.registry.read_bytes(), original)
                    self.released()

    def test_current_registry_accepts_optional_config_and_preserves_unavailable_absolute_references(self):
        path = str(self.root / "missing.wav")
        for config in [None, "", str(self.root / "missing.yaml")]:
            self.registry.write_text(json.dumps({"version":1,"models":[{"name":"fixture", "model_type":"demucs", "model_path":path,
                                                                         "config_path":config}]}), encoding="utf-8")
            read_registry(self.registry)

    def test_root_mismatch_and_missing_explicit_registry_fail_before_work(self):
        for configured in [None, str(self.root / "wrong.json")]:
            with mock.patch.dict(os.environ):
                if configured is None:
                    os.environ.pop("PYMSS_USER_MODELS", None)
                else:
                    os.environ["PYMSS_USER_MODELS"] = configured
                code, events = self.invoke(custom.cmd_import_custom_model, {})
            self.assertEqual(code, 1)
            self.assertEqual(events[-1]["payload"]["code"], "CUSTOM_MODEL_REGISTRY_UNAVAILABLE")
        self.assertFalse((self.root / ".asset-references.lock").exists())

    def test_preimported_pymss_bound_to_a_different_root_is_rejected(self):
        module = types.ModuleType("pymss.user_models")
        module.DEFAULT_USER_MODELS_PATH = self.root / "wrong.json"
        module.clear_user_model_caches = mock.Mock()
        with mock.patch.dict(sys.modules, {"pymss":types.ModuleType("pymss"), "pymss.user_models":module}):
            code, events = self.invoke(custom.cmd_unregister_custom_model, {"name":"fixture"})
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["payload"]["code"], "CUSTOM_MODEL_REGISTRY_UNAVAILABLE")
        module.clear_user_model_caches.assert_not_called()
        self.released()

    def test_cli_main_configures_registry_before_dispatch_without_inline_env_mutation(self):
        observed = []
        with mock.patch.dict(os.environ, {"PYMSS_USER_MODELS":str(self.root / "foreign.json")}), \
                mock.patch.object(worker, "_check_python_runtime", return_value=True), \
                mock.patch.object(worker, "dispatch_run", side_effect=lambda *args: observed.append(os.environ["PYMSS_USER_MODELS"]) or 0), \
                mock.patch.object(worker, "protocol_logging", return_value=__import__("contextlib").nullcontext()), \
                mock.patch.object(worker, "debug_log"):
            self.assertEqual(worker.main(["worker.py", "health"]), 0)
        self.assertEqual(observed, [str(self.registry)])
        # Inline run_command deliberately does not rewrite a concurrent caller's env.
        with mock.patch.dict(os.environ, {"PYMSS_USER_MODELS":"unchanged"}), mock.patch.object(worker,"dispatch_run",return_value=0):
            worker.run_command("health", {})
            self.assertEqual(os.environ["PYMSS_USER_MODELS"], "unchanged")

    def test_kernel_child_environment_overrides_an_inherited_foreign_registry(self):
        kernel = OperationKernel.__new__(OperationKernel)
        kernel.data_root = self.root
        kernel.python_path = sys.executable
        kernel._persist = mock.Mock()
        process = mock.Mock(stdout=io.StringIO('{"type":"custom_model_import_finished","payload":{"name":"fixture"}}\n'))
        process.wait.return_value = 0
        job = Job("fixture", "import_custom_model", {}, self.root / "bridge-jobs/fixture")
        with mock.patch.dict(os.environ, {"PYMSS_USER_MODELS":"foreign"}), \
                mock.patch("core.operation_kernel.subprocess.Popen", return_value=process) as spawn, \
                mock.patch("core.operation_kernel.threading.Thread"):
            kernel._run_worker(job)
        self.assertEqual(spawn.call_args.kwargs["env"]["PYMSS_USER_MODELS"], str(self.registry))
        self.assertEqual(job.status, "completed")
        self.assertFalse((job.workdir / "payload.json").exists())

    def test_real_worker_import_holds_cross_process_lease_until_registration_and_sidecar_finish(self):
        script = r'''import sys,time,types
from pathlib import Path
assert sys.version_info[:2] == (3,12)
sys.path.insert(0,sys.argv[1])
from core import worker_custom_models as custom
root=Path(sys.argv[2]);weights=Path(sys.argv[3])
package=types.ModuleType('pymss');registry=types.ModuleType('pymss.model_registry')
def register(name,model_type,path,**kwargs):
    (root/'ready').write_text('held',encoding='utf-8')
    deadline=time.monotonic()+10
    while not (root/'release').exists():
        if time.monotonic()>deadline:raise RuntimeError('fixture gate timeout')
        time.sleep(.01)
    return types.SimpleNamespace(name=name,model_type=model_type,model_path=path,config_path=None)
registry.register_model=register
sys.modules['pymss']=package;sys.modules['pymss.model_registry']=registry
raise SystemExit(custom.cmd_import_custom_model({'name':'fixture','modelType':'demucs','modelPath':str(weights),'verify':False}))
'''
        project = Path(__file__).resolve().parents[2]
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", script, str(project / "python"), str(self.root), str(self.weights)],
                                 cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 **hidden_subprocess_kwargs())
        try:
            deadline = time.monotonic() + 10
            while not (self.root / "ready").exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((self.root / "ready").is_file(), "owned worker did not reach registration")
            self.held()
            self.assertIsNone(child.poll())
            (self.root / "release").write_text("release", encoding="utf-8")
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout.splitlines()[-1])["type"], "custom_model_import_finished")
            self.assertEqual(custom.sidecar_entry("fixture")["importMode"], "reference")
            self.released()
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
            for pipe in (child.stdout, child.stderr):
                if pipe is not None:
                    pipe.close()


if __name__ == "__main__":
    unittest.main()
