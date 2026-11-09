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

from shared.command_schemas import validate_payload  # noqa: E402


class CommandContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads((ROOT / "manifest" / "protocol.json").read_text(encoding="utf-8"))
        cls.system = cls.protocol["domains"]["system"]["commands"]
        cls.graph = cls.protocol["domains"]["graph"]["commands"]
        cls.audio = cls.protocol["domains"]["audio"]["commands"]
        cls.media = cls.protocol["domains"]["media"]["commands"]

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
