"""One zero-content lease for app-owned asset references and file deletion."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
import stat
import time


def _lock_name() -> str:
    path = Path(__file__).resolve().parents[2] / "manifest/library-schema.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    name = value.get("lockFile") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1
            or not isinstance(name, str) or not name or name in {".", ".."}
            or any(character in name for character in ("/", "\\", "\0", ":"))):
        raise ValueError("Invalid asset reference lease contract")
    return name


LOCK_NAME = _lock_name()


class AssetReferencesBusy(RuntimeError):
    pass


def ensure_plain_path(path: Path) -> None:
    """Reject links and Windows reparse points without resolving through them."""
    path = path.absolute()
    for entry in (path, *path.parents):
        try:
            metadata = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Asset reference path cannot be a link or reparse point: {entry}")


@contextmanager
def asset_reference_lock(data_root: Path, *, timeout: float = 5.0):
    """Never write or unlink this file: all processes must keep locking one inode."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Invalid asset reference lease timeout")
    root = Path(data_root).absolute()
    path = root / LOCK_NAME
    ensure_plain_path(path)
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout

    def wait_or_raise():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetReferencesBusy("Asset references are busy; no asset files were changed")
        time.sleep(min(.05, remaining))

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        invalid = ctypes.c_void_p(-1).value
        while True:
            # GENERIC_READ|WRITE, no sharing, OPEN_ALWAYS, OPEN_REPARSE_POINT.
            handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x00200080, None)
            if handle != invalid:
                break
            error = ctypes.get_last_error()
            if error not in {32, 33}:
                raise ctypes.WinError(error)
            wait_or_raise()
        try:
            ensure_plain_path(path)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("Asset reference lease must be a regular file")
            yield
        finally:
            kernel.CloseHandle(handle)
    else:
        import fcntl
        handle = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            ensure_plain_path(path)
            if not stat.S_ISREG(os.fstat(handle).st_mode):
                raise ValueError("Asset reference lease must be a regular file")
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    wait_or_raise()
            yield
        finally:
            os.close(handle)
