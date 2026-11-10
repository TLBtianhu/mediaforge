"""Cross-process runtime lease, including the Python-side shared handle."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from shared.runtime_lease import RuntimeLeaseError, runtime_lease  # noqa: E402


class RuntimeLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))

    def test_runtime_command_creates_and_holds_the_lock(self) -> None:
        with runtime_lease("infer", envs=self.root):
            self.assertTrue((self.root / ".runtime-lease.lock").exists())

    def test_two_shared_leases_coexist(self) -> None:
        with runtime_lease("infer", envs=self.root):
            with runtime_lease("infer", envs=self.root):
                pass

    def test_non_runtime_command_does_not_create_a_lock(self) -> None:
        with runtime_lease("health", envs=self.root):
            self.assertFalse((self.root / ".runtime-lease.lock").exists())

    def test_writer_blocks_a_new_shared_lease(self) -> None:
        lock = self.root / ".runtime-lease.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        lock.touch()
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                str(lock), 0x80000000 | 0x40000000, 0, None,
                3, 0x80, None,
            )
            invalid = ctypes.c_void_p(-1).value
            self.assertTrue(handle and handle != invalid)
            try:
                with self.assertRaises(RuntimeLeaseError):
                    with runtime_lease("infer", envs=self.root):
                        pass
            finally:
                close = kernel32.CloseHandle
                close.argtypes = [wintypes.HANDLE]
                close.restype = wintypes.BOOL
                close(handle)
            return
        import fcntl

        descriptor = os.open(lock, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeLeaseError):
                with runtime_lease("infer", envs=self.root):
                    pass
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
