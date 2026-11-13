"""Every public worker entry point rejects an old interpreter before dispatch."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
import worker


class WorkerPythonContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mf-worker-python312-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "runtime-manifest.json"
        self.manifest.write_text(json.dumps({"python":"3.12"}), encoding="utf-8")
        self.enterContext(mock.patch.object(worker, "__file__", str(self.root / "worker.py")))
        self.errors = self.enterContext(mock.patch.object(worker, "emit_error", return_value=1))
        self.dispatch = self.enterContext(mock.patch.object(worker, "dispatch_run", return_value=0))

    def test_python312_dispatches_without_rewriting_the_payload(self):
        payload = {"input":"fixture"}
        with mock.patch.object(worker.sys, "version_info", (3,12,13)):
            self.assertEqual(worker.run_command("health", payload), 0)
        self.dispatch.assert_called_once_with("health", payload)
        self.assertEqual(payload, {"input":"fixture"})
        self.errors.assert_not_called()

    def test_old_python_is_rejected_for_embedded_and_mcp_worker_calls(self):
        with mock.patch.object(worker.sys, "version_info", (3,11,9)):
            self.assertEqual(worker.run_command("infer", {"input":"not opened"}), 1)
        self.dispatch.assert_not_called()
        self.assertEqual(self.errors.call_args.args[0], "RUNTIME_PYTHON_MISMATCH")

    def test_cli_old_python_never_loads_payload_or_opens_persistent_logs(self):
        with mock.patch.object(worker.sys, "version_info", (3,11,9)), \
             mock.patch.object(worker, "load_payload") as payload, mock.patch.object(worker, "debug_log") as log, \
             mock.patch.object(worker, "protocol_logging") as logging:
            self.assertEqual(worker.main(["worker.py","health"]), 1)
        payload.assert_not_called()
        log.assert_not_called()
        logging.assert_not_called()
        self.dispatch.assert_not_called()

    def test_missing_old_or_malformed_target_contract_is_rejected(self):
        for content in ('{}', '{"python":"3.11"}', 'invalid json'):
            self.manifest.write_text(content, encoding="utf-8")
            with mock.patch.object(worker.sys, "version_info", (3,12,13)):
                self.assertEqual(worker.run_command("health", {}), 1)
            self.assertEqual(self.errors.call_args.args[0], "RUNTIME_PYTHON_CONTRACT_INVALID")
        self.manifest.unlink()
        self.assertFalse(worker._check_python_runtime())
        self.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
