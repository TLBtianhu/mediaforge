"""MCP imports use the durable executor and registry queries keep request scope."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from agent_bridge import Bridge
from core import worker_models, worker_custom_models
from shared import worker_paths, worker_protocol
from shared.asset_references import asset_reference_lock, AssetReferencesBusy
from surfaces import agent_contract
import worker


def model_row(name, **overrides):
    row = {
        "name": name, "aliases": [], "modelType": "demucs", "architecture": "demucs",
        "supported": True, "unsupportedReason": "", "category": "user",
        "categoryCn": "user", "primaryCategory": "user", "primaryCategoryCn": "user",
        "secondaryCategory": "", "secondaryCategoryCn": "", "targetStem": "",
        "configInstruments": "", "configTargetInstrument": "", "classificationConfidence": "",
        "classificationBasis": "", "sizeBytes": 0, "sha256": "", "downloaded": False,
        "missingPaths": [], "modelPath": "", "configPath": None, "auxiliaryPaths": [],
    }
    row.update(overrides)
    return row


class ModelRegistryBridgeScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mediaforge-model-bridge-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def bridge(self, name):
        bridge = Bridge.__new__(Bridge)
        bridge.data_root = self.root / name
        bridge.model_root = bridge.data_root / "models"
        bridge.jobs = mock.Mock()
        return bridge

    def registry(self, bridge, name):
        path = worker_paths.user_models_file(bridge.data_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        weights = bridge.data_root / f"{name}.wav"
        weights.write_bytes(b"fixture")
        path.write_text(json.dumps({"version":1,"models":[{"name":name,"model_type":"demucs","model_path":str(weights)}]}),encoding="utf-8")
        return path

    @staticmethod
    def fake_job():
        job = mock.Mock()
        job.job_id = "fixture_job"
        job.snapshot.return_value = {"jobId":"fixture_job","status":"queued"}
        return job

    def test_import_contract_has_real_handler_arguments_and_requires_durable_execution(self):
        tool = agent_contract.find_tool("model.import_custom")
        self.assertTrue(tool["longRunning"])
        self.assertEqual(set(tool["inputSchema"]["required"]), {"name","modelType","modelPath"})
        self.assertNotIn("paths",tool["inputSchema"]["properties"])
        self.assertNotIn("modelDir",tool["inputSchema"]["properties"])
        with mock.patch.object(worker,"run_command") as inline:
            with self.assertRaisesRegex(ValueError,"job ledger"):
                agent_contract.dispatch("model.import_custom", {"name":"test","modelType":"demucs","modelPath":"test.wav"})
        inline.assert_not_called()

    def test_public_bridge_import_submits_exact_current_payload_and_rejects_old_or_internal_arguments(self):
        bridge = self.bridge("profile")
        bridge.jobs.start.return_value = self.fake_job()
        payload = {"name":"fixture","modelType":"demucs","modelPath":str(self.root / "source.wav"),
                   "importMode":"reference","verify":False,"force":False,"aliases":["alias"]}
        result = bridge.call("model.import_custom", {**payload,"confirmExplicit":True})
        self.assertTrue(result["ok"],result)
        bridge.jobs.start.assert_called_once_with("import_custom_model",payload)
        for arguments in [{"paths":["source.wav"]},{**payload,"dataRoot":str(self.root / "foreign")},{**payload,"modelDir":"foreign"}]:
            bridge.jobs.start.reset_mock()
            self.assertFalse(bridge.call("model.import_custom",{**arguments,"confirmExplicit":True})["ok"])
            bridge.jobs.start.assert_not_called()

    def test_inline_model_lists_have_independent_roots_in_parallel_without_env_changes(self):
        bridges = [self.bridge("first"),self.bridge("second")]
        for bridge,name in zip(bridges,("one","two")):
            self.registry(bridge,name)
        ambient = self.root / "ambient.json"
        ambient.write_bytes(b"must not be read or replaced")
        original = worker_models._load_registered_user_models
        barrier = threading.Barrier(2)
        def synchronized_read():
            barrier.wait(timeout=5)
            return original()
        with mock.patch.dict(os.environ,{"PYMSS_USER_MODELS":str(ambient),"MEDIAFORGE_DATA_ROOT":str(self.root / "ambient")}), \
                mock.patch.object(worker_models,"load_model_catalog",return_value={}), \
                mock.patch.object(worker_models,"list_catalog_models",return_value=[]), \
                mock.patch.object(worker_models,"model_to_dict",side_effect=lambda entry,*args:model_row(entry.name, category="user", categoryCn="user")), \
                mock.patch.object(worker_models,"_load_registered_user_models",side_effect=synchronized_read):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda bridge:bridge.call("model.list",{}),bridges))
            self.assertEqual(os.environ["PYMSS_USER_MODELS"],str(ambient))
        self.assertEqual([[row["name"] for row in result["models"]] for result in outcomes],[["one"],["two"]])
        self.assertTrue(all(result["ok"] for result in outcomes))
        self.assertIsNone(worker_models._REGISTRY_SCOPE.get())
        self.assertEqual(ambient.read_bytes(),b"must not be read or replaced")

    def test_real_mcp_alias_lists_custom_entries_from_its_root_and_preserves_catalog_download_semantics(self):
        bridges = [self.bridge("first"),self.bridge("second")]
        for bridge,name in zip(bridges,("one","two")):
            self.registry(bridge,name)
        catalog = {"models":[{"id":"media:catalog","label":"catalog","modalities":["image"],"task":"upscale","size":3,
                               "downloaded":False,"implemented":True}],"modelDir":"configured","environment":{},"count":1,"downloadedCount":0}
        ambient = self.root / "ambient.json"
        ambient.write_bytes(b"must remain untouched")
        with mock.patch.dict(os.environ,{"PYMSS_USER_MODELS":str(ambient)}), mock.patch("agent_bridge.model_hub.list_models",return_value=catalog):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda bridge:bridge.call("models_list",{}),bridges))
            self.assertEqual(os.environ["PYMSS_USER_MODELS"],str(ambient))
        for result,name in zip(results,("one","two")):
            self.assertEqual(result["count"],2)
            self.assertEqual(result["downloadedCount"],1)
            self.assertEqual(result["models"][0]["id"],"media:catalog")
            entry = result["models"][1]
            self.assertEqual(entry["id"],f"audio:{name}")
            self.assertEqual(entry["source"],"user")
            self.assertFalse(entry["downloadable"])
            self.assertTrue(entry["downloaded"])
        self.assertEqual(ambient.read_bytes(),b"must remain untouched")

    def test_model_list_rejects_a_caller_supplied_internal_root(self):
        bridge = self.bridge("profile")
        outcome = bridge.call("model.list",{"dataRoot":str(self.root / "foreign")})
        self.assertFalse(outcome["ok"])
        self.assertFalse((self.root / "foreign").exists())

    def test_dispatch_restores_scope_after_exception(self):
        with mock.patch.object(worker,"run_command",side_effect=RuntimeError("fixture")):
            with self.assertRaisesRegex(RuntimeError,"fixture"):
                agent_contract.dispatch("model.list",{},data_root=str(self.root))
        self.assertIsNone(worker_models._REGISTRY_SCOPE.get())

    def test_import_and_both_query_entrypoints_share_the_same_canonical_registry(self):
        bridge = self.bridge("profile")
        bridge.data_root.mkdir()
        weights = bridge.data_root / "source.wav"
        weights.write_bytes(b"fixture")
        registry = worker_paths.user_models_file(bridge.data_root)
        def register(name,model_type,path,**kwargs):
            registry.parent.mkdir(parents=True,exist_ok=True)
            registry.write_text(json.dumps({"version":1,"models":[{"name":name,"model_type":model_type,"model_path":path}]}),encoding="utf-8")
            return types.SimpleNamespace(name=name,model_type=model_type,model_path=path,config_path=None)
        def submit(command,payload):
            self.assertEqual(command,"import_custom_model")
            with worker_protocol.bind_event_sink(lambda *args:None):
                self.assertEqual(worker_custom_models.cmd_import_custom_model(payload),0)
            return self.fake_job()
        bridge.jobs.start.side_effect = submit
        payload = {"name":"new_model","modelType":"demucs","modelPath":str(weights),"verify":False}
        with mock.patch.dict(os.environ,{"MEDIAFORGE_DATA_ROOT":str(bridge.data_root),"PYMSS_USER_MODELS":str(registry)}), \
                mock.patch.dict("sys.modules",{"pymss":mock.Mock(),"pymss.model_registry":mock.Mock(register_model=register)}):
            self.assertTrue(bridge.call("model.import_custom",{**payload,"confirmExplicit":True})["ok"])
        with mock.patch.object(worker_models,"load_model_catalog",return_value={}), \
                mock.patch.object(worker_models,"list_catalog_models",return_value=[]), \
                mock.patch.object(worker_models,"model_to_dict",side_effect=lambda entry,*args:model_row(entry.name, category="user", categoryCn="user")):
            listed = bridge.call("model.list",{})
        self.assertEqual([entry["name"] for entry in listed["models"]],["new_model"])
        with mock.patch("agent_bridge.model_hub.list_models",return_value={"models":[],"modelDir":"configured","environment":{}}):
            listed = bridge.call("models_list",{})
        self.assertEqual([entry["id"] for entry in listed["models"]],["audio:new_model"])

    def test_bad_or_unknown_registry_is_an_error_for_both_queries_and_bytes_are_unchanged(self):
        bridge = self.bridge("profile")
        registry = self.registry(bridge,"fixture")
        for raw in ['{broken','{"version":2,"models":[]}','{"version":1,"models":null}',
                    '{"version":1,"models":[],"extra":1e400}','{"version":1,"version":1,"models":[]}']:
            registry.write_text(raw,encoding="utf-8")
            with mock.patch.object(worker_models,"load_model_catalog",return_value={}), \
                    mock.patch.object(worker_models,"list_catalog_models",return_value=[]):
                result = bridge.call("model.list",{})
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"],"MODEL_REGISTRY_UNAVAILABLE")
            self.assertNotIn("models",result)
            with mock.patch("agent_bridge.model_hub.list_models",return_value={"models":[],"modelDir":"configured","environment":{}}):
                with self.assertRaises(ValueError):
                    bridge.call("models_list",{})
            self.assertEqual(registry.read_text(encoding="utf-8"),raw)

    def test_scoped_read_holds_asset_lease_and_scopes_sidecar_metadata(self):
        bridge = self.bridge("profile")
        registry = self.registry(bridge,"fixture")
        registry.with_name("custom-models-meta.json").write_text(json.dumps({"version":1,"models":{"fixture":{"importMode":"copy"}}}),encoding="utf-8")
        original = worker_models.read_registry
        def read_while_locked(path):
            with self.assertRaises(AssetReferencesBusy):
                with asset_reference_lock(bridge.data_root,timeout=0):
                    self.fail("registry reader released its lease too soon")
            return original(path)
        with mock.patch.object(worker_models,"read_registry",side_effect=read_while_locked), worker_models.user_model_registry_scope(bridge.data_root):
            entries = worker_models._load_registered_user_models()
            self.assertEqual(worker_models._user_model_import_mode(entries[0]),"copy")


if __name__ == "__main__":
    unittest.main()
