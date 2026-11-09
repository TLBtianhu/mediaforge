"""Command payload/result contracts: protocol is the single source."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.command_schemas import (  # noqa: E402
    ResultContractError,
    result_event_validator,
    validate_payload,
    validate_result,
)
from shared.worker_protocol import bind_event_validator, emit  # noqa: E402


class CommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads((ROOT / "manifest" / "protocol.json").read_text(encoding="utf-8"))
        cls.system = cls.protocol["domains"]["system"]["commands"]
        cls.graph = cls.protocol["domains"]["graph"]["commands"]
        cls.audio = cls.protocol["domains"]["audio"]["commands"]
        cls.media = cls.protocol["domains"]["media"]["commands"]
        cls.model = cls.protocol["domains"]["model"]["commands"]
        cls.runtime = cls.protocol["domains"]["runtime"]["commands"]
        cls.plugin = cls.protocol["domains"]["plugin"]["commands"]
        cls.workflow = cls.protocol["domains"]["workflow"]["commands"]

    def test_every_system_command_declares_payload_and_result(self) -> None:
        for name, entry in self.system.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_every_graph_command_declares_payload_and_result(self) -> None:
        for name, entry in self.graph.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_graph_payload_types(self) -> None:
        self.assertEqual(validate_payload("list_nodes", {}), [])
        self.assertEqual(validate_payload("describe_operation", {"operation": "separate"}), [])
        self.assertTrue(validate_payload("describe_operation", {}))
        self.assertTrue(validate_payload("run_operation", {"operation": "separate", "params": "bad"}))

    def test_every_audio_command_declares_payload_and_result(self) -> None:
        for name, entry in self.audio.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_audio_payload_types(self) -> None:
        self.assertEqual(validate_payload("audio_metadata", {"path": "a.wav"}), [])
        self.assertTrue(validate_payload("audio_metadata", {}))
        self.assertTrue(validate_payload("waveform_peaks", {"path": "a.wav", "resolution": "high"}))
        self.assertTrue(validate_payload("export_editor_mix", {"project": "bad"}))

    def test_every_media_command_declares_payload_and_result(self) -> None:
        for name, entry in self.media.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_media_payload_types(self) -> None:
        self.assertEqual(validate_payload("media_capabilities", {}), [])
        self.assertTrue(validate_payload("media_capabilities", {"modelDir": 1}))
        self.assertTrue(validate_payload("media_precheck", {"inputs": "a.wav"}))
        self.assertTrue(validate_payload("media_library", {"limit": "ten"}))
        self.assertTrue(validate_payload("media_tool", {"operation": 1}))

    def test_every_model_command_declares_payload_and_result(self) -> None:
        for name, entry in self.model.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_model_payload_types(self) -> None:
        self.assertEqual(validate_payload("list_models", {"modelDir": None}), [])
        self.assertTrue(validate_payload("list_models", {"category": 1}))
        self.assertTrue(validate_payload("model_download", {"modelId": 1}))
        self.assertTrue(validate_payload("debug_catalog_save", {"catalog": "bad"}))

    def test_every_runtime_command_declares_payload_and_result(self) -> None:
        for name, entry in self.runtime.items():
            with self.subTest(name=name):
                self.assertIsInstance(entry.get("payload"), dict)
                self.assertIsInstance(entry.get("result"), dict)

    def test_runtime_payload_types(self) -> None:
        self.assertEqual(validate_payload("runtime_info", {}), [])
        self.assertTrue(validate_payload("install_runtime", {"backend": 1}))
        self.assertTrue(validate_payload("optional_package_status", {"package": 1}))

    def test_every_protocol_command_declares_payload_and_result(self) -> None:
        for domain_id, domain in self.protocol["domains"].items():
            for name, entry in domain["commands"].items():
                with self.subTest(domain=domain_id, name=name):
                    self.assertIsInstance(entry.get("payload"), dict)
                    self.assertIsInstance(entry.get("result"), dict)

    def test_plugin_and_workflow_payload_types(self) -> None:
        self.assertEqual(validate_payload("plugin_marketplace", {}), [])
        self.assertTrue(validate_payload("manage_plugin", {"action": 1}))
        self.assertTrue(validate_payload("infer", {"deviceIds": "0"}))
        self.assertTrue(validate_payload("infer_workflow", {"tasks": "bad"}))

    def test_every_command_declares_a_result_event(self) -> None:
        for domain_id, domain in self.protocol["domains"].items():
            for name, entry in domain["commands"].items():
                with self.subTest(domain=domain_id, name=name):
                    self.assertIsInstance(entry.get("resultSchemaEvent"), str)

    def test_selected_task_results_are_typed(self) -> None:
        self.assertEqual(validate_result("model_download", {"modelId": "m", "downloaded": [], "skipped": []}), [])
        self.assertTrue(validate_result("model_download", {"modelId": "m", "downloaded": "bad"}))
        self.assertEqual(validate_result("install_runtime", {"backend": "cuda", "state": {}, "logPath": "x"}), [])
        self.assertTrue(validate_result("install_runtime", {"backend": "cuda", "state": "bad", "logPath": "x"}))
        self.assertEqual(validate_result("infer", {"files": [], "outputs": [], "outputDir": "x"}), [])
        self.assertTrue(validate_result("infer", {"files": "bad", "outputs": []}))

    def test_media_tool_result_is_a_branch_union(self) -> None:
        self.assertEqual(validate_result("media_tool", {
            "operation": "media_probe", "probes": [], "probe": {},
        }), [])
        self.assertEqual(validate_result("media_tool", {
            "operation": "media_image", "outputs": [], "failed": [], "skipped": [],
            "total": 0, "completed": 0, "warnings": [],
        }), [])
        self.assertTrue(validate_result("media_tool", {
            "operation": "media_probe", "probes": [],
        }))

    def test_plugin_snapshot_result_is_typed(self) -> None:
        valid = {"installed": [], "pluginsDir": "plugins", "runtime": {}}
        self.assertEqual(validate_result("plugin_marketplace", valid), [])
        self.assertEqual(validate_result("manage_plugin", valid), [])
        self.assertTrue(validate_result("manage_plugin", {"installed": "bad", "pluginsDir": "p", "runtime": {}}))

    def test_library_asset_result_item_is_typed(self) -> None:
        asset = {
            "id": 1, "path": "a.wav", "name": "a.wav", "kind": "audio",
            "format": "wav", "size": 10, "duration": 1.0, "width": 0,
            "height": 0, "fps": 0.0, "thumbnail": "", "tags": "",
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
        }
        self.assertEqual(validate_result("media_library", {"operation": "list", "ok": True, "assets": [asset]}), [])
        broken = dict(asset); broken.pop("id")
        self.assertTrue(validate_result("media_library", {"operation": "list", "ok": True, "assets": [broken]}))

    def test_library_stats_kind_values_are_typed(self) -> None:
        stats = {"kinds": {"audio": {"count": 1, "bytes": 2}}, "total": {"count": 1, "bytes": 2}, "database": "x"}
        self.assertEqual(validate_result("media_library", {"operation": "stats", "ok": True, "stats": stats}), [])
        self.assertTrue(validate_result("media_library", {
            "operation": "stats", "ok": True,
            "stats": {"kinds": {"audio": {"count": 1}}, "total": {"count": 1, "bytes": 2}, "database": "x"},
        }))

    def test_list_models_item_is_typed(self) -> None:
        item = {
            "name": "m", "aliases": [], "modelType": "mdx", "architecture": "mdx",
            "supported": True, "unsupportedReason": "", "category": "vocals",
            "categoryCn": "人声", "primaryCategory": "vocals", "primaryCategoryCn": "人声",
            "secondaryCategory": "", "secondaryCategoryCn": "", "targetStem": "vocals",
            "configInstruments": "", "configTargetInstrument": "", "classificationConfidence": "",
            "classificationBasis": "", "sizeBytes": 1, "sha256": "", "downloaded": True,
            "missingPaths": [], "modelPath": "m.ckpt", "configPath": None, "auxiliaryPaths": [],
        }
        self.assertEqual(validate_result("list_models", {"models": [item], "count": 1}), [])
        broken = dict(item); broken.pop("name")
        self.assertTrue(validate_result("list_models", {"models": [broken], "count": 1}))

    def test_runtime_result_maps_are_typed(self) -> None:
        self.assertEqual(validate_result("runtime_core_versions", {
            "channel": "stable",
            "packages": {"pymss": {"latestVersion": "1", "currentVersion": None, "updateAvailable": False}},
        }), [])
        self.assertTrue(validate_result("runtime_core_versions", {
            "channel": "stable", "packages": {"pymss": "bad"},
        }))
        self.assertEqual(validate_result("runtime_env_sizes", {
            "sizes": {"cuda": 1}, "totalBytes": 1, "incompleteBackends": [],
        }), [])
        self.assertTrue(validate_result("runtime_env_sizes", {
            "sizes": {"cuda": "bad"}, "totalBytes": 1, "incompleteBackends": [],
        }))

    def test_audio_tools_common_result_fields(self) -> None:
        self.assertEqual(validate_result("audio_tools", {
            "operation": "convert", "outputPaths": ["a.wav"], "succeeded": 1,
        }), [])
        self.assertTrue(validate_result("audio_tools", {
            "operation": "convert", "outputPaths": "a.wav",
        }))

    def test_result_validator_rejects_a_wrong_shape(self) -> None:
        self.assertEqual(validate_result("health", {"ok": True, "workerVersion": "0.1.0"}), [])
        with self.assertRaises(ResultContractError):
            with bind_event_validator(result_event_validator("health", "health")):
                emit("health", {"ok": "yes"})

    def test_result_validator_ignores_other_events(self) -> None:
        with bind_event_validator(result_event_validator("health", "health")):
            emit("task.progress", {"fraction": 0.5})

    def test_health_rejects_unknown_payload_fields(self) -> None:
        self.assertEqual(validate_payload("health", {}), [])
        problems = validate_payload("health", {"unknown": 1})
        self.assertTrue(any("unknown" in item for item in problems), problems)

    def test_test_connection_checks_types(self) -> None:
        self.assertEqual(validate_payload("test_connection", {}), [])
        self.assertEqual(validate_payload("test_connection", {"timeout": 5}), [])
        self.assertTrue(validate_payload("test_connection", {"timeout": "five"}))

    def test_worker_emits_payload_invalid_before_the_handler(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-contract-") as raw:
            payload = Path(raw) / "payload.json"
            payload.write_text('{"unknown": 1}', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "python" / "worker.py"), "health", "--payload", str(payload)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.assertTrue(events, result.stdout + result.stderr)
        first = events[0]
        self.assertEqual(first.get("type"), "error")
        self.assertEqual(first["payload"]["code"], "PAYLOAD_INVALID")

    def test_generated_frontend_contracts_are_current(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sync_worker_contracts.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
