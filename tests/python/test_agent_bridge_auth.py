from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent_bridge import Handler, write_pairing_file  # noqa: E402


def handler_with(token: str | None, supplied: str | None) -> Handler:
    handler = object.__new__(Handler)
    handler.headers = {} if supplied is None else {"Authorization": supplied}
    handler.server = SimpleNamespace(token=token)
    return handler


class BridgeAuthorizationTests(unittest.TestCase):
    def test_matching_bearer_token_is_accepted(self) -> None:
        handler = handler_with("secret-token", "Bearer secret-token")
        with mock.patch("agent_bridge.secrets.compare_digest", wraps=secrets.compare_digest) as compare:
            self.assertTrue(handler._authorized())
        compare.assert_called_once()

    def test_wrong_or_missing_token_is_rejected(self) -> None:
        self.assertFalse(handler_with("secret-token", "Bearer other")._authorized())
        self.assertFalse(handler_with("secret-token", "secret-token")._authorized())
        self.assertFalse(handler_with("secret-token", None)._authorized())

    def test_no_configured_token_disables_auth(self) -> None:
        self.assertTrue(handler_with(None, None)._authorized())
        self.assertTrue(handler_with("", None)._authorized())


class PairingFileTests(unittest.TestCase):
    def test_pairing_file_is_written_with_the_token(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "nested" / "mcp-bridge.json"
            write_pairing_file(path, {"url": "http://127.0.0.1:1421/mcp", "token": "t"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["token"], "t")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
