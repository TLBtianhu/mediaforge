"""Inline MCP library calls use each bridge's explicit profile, never global env."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from agent_bridge import Bridge


class LibraryBridgeScopeTests(unittest.TestCase):
    def test_two_inline_bridges_use_independent_roots_without_changing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ambient = root / "ambient"
            ambient.mkdir()
            original = ambient / "library.db"
            original.write_bytes(b"not the requested profile; must remain untouched")
            bridges = []
            for name in ("first", "second"):
                bridge = Bridge.__new__(Bridge)
                bridge.data_root = root / name
                bridges.append(bridge)
            env = {"MEDIAFORGE_DATA_ROOT": str(ambient), "MEDIAFORGE_DATA_DIR": str(ambient)}
            with mock.patch.dict(os.environ, env):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(lambda bridge: bridge._dispatch_contract("media.library", {}), bridges))
                self.assertEqual({key: os.environ[key] for key in env}, env)
            self.assertTrue(all(outcome["ok"] and outcome["assets"] == [] for outcome in outcomes), outcomes)
            self.assertEqual(original.read_bytes(), b"not the requested profile; must remain untouched")
            for bridge in bridges:
                database = bridge.data_root / "library.db"
                self.assertTrue(database.is_file())
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                finally:
                    connection.close()

    def test_caller_cannot_redirect_the_library_using_a_data_root_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = Bridge.__new__(Bridge)
            bridge.data_root = root / "bridge"
            outcome = bridge._dispatch_contract("media.library", {"dataRoot": str(root / "injected")})
            self.assertFalse(outcome["ok"])
            self.assertFalse((root / "injected").exists())


if __name__ == "__main__":
    unittest.main()
