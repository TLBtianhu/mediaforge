"""The peaks executor receives its bridge root as internal request scope."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from agent_bridge import Bridge
from shared import worker_protocol
from surfaces import agent_contract
import worker


class EditorPeaksBridgeScopeTests(unittest.TestCase):
    def test_parallel_bridges_inject_their_own_root_without_mutating_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridges = []
            for name in ("first", "second"):
                bridge = Bridge.__new__(Bridge)
                bridge.data_root = root / name
                bridges.append(bridge)
            barrier = threading.Barrier(2)
            def fake_command(command, payload):
                self.assertEqual(command, "waveform_peaks")
                barrier.wait(timeout=5)
                worker_protocol.emit("waveform_peaks", {"receivedRoot":payload["dataRoot"],"path":payload["path"]})
                return 0
            ambient = str(root / "ambient")
            with mock.patch.dict(os.environ,{"MEDIAFORGE_DATA_ROOT":ambient}), \
                    mock.patch.object(worker,"run_command",side_effect=fake_command):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda bridge:bridge.call("editor.peaks",{"path":"fixture.wav"}), bridges))
                self.assertEqual(os.environ["MEDIAFORGE_DATA_ROOT"],ambient)
            self.assertEqual([result["receivedRoot"] for result in results],[str(bridge.data_root) for bridge in bridges])
            self.assertTrue(all(result["ok"] for result in results))
            self.assertFalse(any(bridge.data_root.exists() for bridge in bridges))

    def test_caller_cannot_supply_or_replace_the_internal_root(self):
        bridge = Bridge.__new__(Bridge)
        bridge.data_root = Path("unused-fixture")
        with mock.patch.object(worker,"run_command") as command:
            result = bridge.call("editor.peaks",{"path":"fixture.wav","dataRoot":"untrusted"})
        self.assertFalse(result["ok"])
        command.assert_not_called()

    def test_dispatch_scope_injection_does_not_mutate_the_callers_arguments(self):
        arguments = {"path":"fixture.wav"}
        def fake_command(command, payload):
            self.assertEqual(payload["dataRoot"],"explicit-fixture")
            worker_protocol.emit("waveform_peaks",{})
            return 0
        with mock.patch.object(worker,"run_command",side_effect=fake_command):
            self.assertTrue(agent_contract.dispatch("editor.peaks",arguments,data_root="explicit-fixture")["ok"])
        self.assertEqual(arguments,{"path":"fixture.wav"})

    def test_current_peaks_contract_uses_the_real_handler_fields_and_rejects_old_inputs(self):
        tool = agent_contract.find_tool("editor.peaks")
        self.assertEqual(tool["inputSchema"]["required"],["path"])
        self.assertEqual(set(tool["inputSchema"]["properties"]),{"path","resolution","cacheDir"})
        with mock.patch.object(worker,"run_command") as command:
            result = agent_contract.dispatch("editor.peaks",{"inputs":["old.wav"]},data_root="fixture")
        self.assertFalse(result["ok"])
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
