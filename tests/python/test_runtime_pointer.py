"""The pointer reader shared by bootstrap, MCP and tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from shared import runtime_pointer  # noqa: E402


class RuntimePointerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))
        self.pointer = self.root / "active-runtime.json"

    def test_missing_pointer_is_not_an_error(self) -> None:
        self.assertEqual(runtime_pointer.load_active_runtime(self.pointer), (None, None))

    def test_corrupt_json_names_the_file(self) -> None:
        self.pointer.write_text("{not json", encoding="utf-8")
        state, error = runtime_pointer.load_active_runtime(self.pointer)
        self.assertIsNone(state)
        self.assertIn("active-runtime.json", error)
        self.assertIn("not valid JSON", error)

    def test_relative_interpreter_resolves_against_the_pointer_directory(self) -> None:
        python = self.root / "cpu" / "Scripts" / ("python.exe" if __import__("os").name == "nt" else "python")
        python.parent.mkdir(parents=True)
        python.write_text("stub", encoding="utf-8")
        relative = python.relative_to(self.root).as_posix()
        self.pointer.write_text(json.dumps({"pythonPath": relative, "backend": "cpu"}), encoding="utf-8")
        state, error = runtime_pointer.load_active_runtime(self.pointer)
        self.assertIsNone(error)
        self.assertEqual(Path(state["pythonPath"]), python.parent.resolve() / python.name)

    def test_missing_interpreter_is_an_error(self) -> None:
        self.pointer.write_text(json.dumps({"pythonPath": "cpu/Scripts/gone.exe"}), encoding="utf-8")
        state, error = runtime_pointer.load_active_runtime(self.pointer)
        self.assertIsNone(state)
        self.assertIn("missing interpreter", error)

    def test_non_object_pointer_is_an_error(self) -> None:
        self.pointer.write_text("[]", encoding="utf-8")
        state, error = runtime_pointer.load_active_runtime(self.pointer)
        self.assertIsNone(state)
        self.assertIn("not a JSON object", error)


if __name__ == "__main__":
    unittest.main()
