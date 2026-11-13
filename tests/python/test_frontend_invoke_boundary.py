from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_frontend_invokes", ROOT / "scripts" / "check-frontend-invokes.py"
)
assert spec.loader is not None
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)

NATIVE_FILE = """
export function revealPath(path: string) {
  return invoke('reveal_path', { path })
}
"""
PROTOCOL = {
    "domains": {"system": {"commands": {"health": {"command": "health"}}}},
}


def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="frontend-invokes-"))
    base = {"manifest/protocol.json": json.dumps(PROTOCOL)}
    for name, content in {**base, **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class NativeBoundaryTests(unittest.TestCase):
    def test_native_ipc_keeps_id_based_removal_and_excludes_raw_path_recycling(self) -> None:
        main = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertNotRegex(main, r"\bmove_paths_to_trash\b")
        for command in ("remove_audio_jobs", "remove_result_jobs", "delete_editor_project"):
            with self.subTest(command=command):
                self.assertRegex(main, rf"\b{command}\b")

    def test_wrapped_commands_are_derived_from_native_api(self) -> None:
        root = make_tree({"src/api/native.ts": NATIVE_FILE})
        self.assertEqual(boundary.wrapped_native_commands(root), {"reveal_path"})

    def test_direct_invoke_of_a_wrapped_command_fails(self) -> None:
        root = make_tree({
            "src/api/native.ts": NATIVE_FILE,
            "src/views/Foo.vue": "<script setup lang=\"ts\">\nasync function open() {\n  await invoke('reveal_path', { path: '/x' })\n}\n</script>\n",
        })
        with mock.patch.dict(boundary.ALLOWED, {}, clear=True):
            problems, _worker, native_commands, bypasses = boundary.check(root)
        self.assertEqual(native_commands, 1)
        self.assertEqual(bypasses, 1)
        self.assertTrue(any("src/api/native.ts wrapper" in problem for problem in problems))

    def test_wrapper_itself_and_other_api_files_may_invoke(self) -> None:
        root = make_tree({
            "src/api/native.ts": NATIVE_FILE,
            "src/api/extra.ts": "invoke('reveal_path', { path: '/x' })\n",
        })
        with mock.patch.dict(boundary.ALLOWED, {}, clear=True):
            problems, _worker, _native, bypasses = boundary.check(root)
        self.assertEqual(problems, [])
        self.assertEqual(bypasses, 0)

    def test_worker_command_outside_api_still_fails(self) -> None:
        root = make_tree({"src/views/Foo.vue": "<script>\ninvoke('health', { payload: {} })\n</script>\n"})
        with mock.patch.dict(boundary.ALLOWED, {}, clear=True):
            problems, _worker, _native, _bypasses = boundary.check(root)
        self.assertTrue(any("direct invoke('health') bypasses src/api" in problem for problem in problems))

    def test_documented_exception_still_matches_its_call_site(self) -> None:
        root = make_tree({"src/views/Foo.vue": "<script>\ninvoke('health', { payload: {} })\n</script>\n"})
        allowed = {("health", "src/views/Foo.vue"): "intentional fixture"}
        with mock.patch.dict(boundary.ALLOWED, allowed, clear=True):
            problems, _worker, _native, _bypasses = boundary.check(root)
        self.assertEqual(problems, [])

    def test_stale_allowlist_entry_fails(self) -> None:
        root = make_tree({"src/views/Foo.vue": "<script>\nexport const x = 1\n</script>\n"})
        allowed = {("health", "src/views/Foo.vue"): "no longer exists"}
        with mock.patch.dict(boundary.ALLOWED, allowed, clear=True):
            problems, _worker, _native, _bypasses = boundary.check(root)
        self.assertTrue(any("stale allowlist entry" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
