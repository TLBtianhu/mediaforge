"""Shared cross-process lease for Python workers.

Rust owns the write side (runtime install/activate/delete). This module lets a
Python worker that was **not** started by the Studio (MCP bridge, CLI) participate
in the same `.runtime-envs/.runtime-lease.lock` gate:

* worker process holds a shared handle for its whole lifetime;
* the Rust installer's exclusive handle fails while a worker is alive.

Windows opens the lock file with `FILE_SHARE_READ`; readers therefore coexist and
a writer that requests write access cannot open it. Unix uses `flock(LOCK_SH)`.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import protocol_contract, worker_paths

LEASE_FILE = ".runtime-lease.lock"
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = -1


class RuntimeLeaseError(RuntimeError):
    code = "RUNTIME_BUSY"

    def __init__(self, message: str):
        super().__init__(message)


def _runtime_bound_commands() -> frozenset[str]:
    try:
        protocol = protocol_contract.load_protocol()
    except Exception:  # noqa: BLE001 - a broken manifest must not block bootstrap
        return frozenset()
    jobs = (protocol.get("execution") or {}).get("resultJobs") or {}
    return frozenset(jobs.get("commands") or [])


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "a", encoding="utf-8"):
            pass


def _is_plain(path: Path) -> bool:
    metadata = os.lstat(path)
    if os.path.islink(path):
        return False
    if os.name == "nt":
        return not bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    return True


def _open_shared(path: Path) -> object:
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
        invalid = ctypes.c_void_p(-1).value
        handle = create_file(
            str(path), _GENERIC_READ, _FILE_SHARE_READ, None,
            _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None,
        )
        if not handle or handle == invalid:
            raise RuntimeLeaseError(
                "RUNTIME_BUSY: 运行时正在被另一进程使用，请等待当前操作完成后重试"
            )
        return handle
    import fcntl

    descriptor = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(descriptor)
        raise RuntimeLeaseError(
            "RUNTIME_BUSY: 运行时正在被另一进程使用，请等待当前操作完成后重试"
        ) from exc
    return descriptor


def _close_shared(handle: object) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        close_handle(handle)
        return
    import fcntl

    try:
        fcntl.flock(int(handle), fcntl.LOCK_UN)
    finally:
        os.close(int(handle))


@contextmanager
def runtime_lease(command: str, envs: Path | None = None) -> Iterator[None]:
    """Hold a shared lease for runtime-bound commands; no-op otherwise."""
    if command not in _runtime_bound_commands():
        yield
        return
    root = Path(envs) if envs is not None else worker_paths.runtime_envs_root()
    path = root / LEASE_FILE
    _ensure_file(path)
    if not _is_plain(path):
        raise RuntimeLeaseError("运行时租约文件不能是链接")
    handle = _open_shared(path)
    try:
        yield
    finally:
        _close_shared(handle)
