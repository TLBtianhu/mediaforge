"""Cross-process compute admission; job files remain the only durable queue."""
from __future__ import annotations

import errno
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import stat
import threading
from typing import Any

from core.job_store import JobStoreError
from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "manifest" / "execution-quota.json"


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise JobStoreError(f"Duplicate quota JSON key: {key}")
        value[key] = item
    return value


def _parse(text: str) -> Any:
    def invalid_number(value):
        raise JobStoreError(f"Invalid quota JSON number: {value}")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            invalid_number(value)
        return number
    return json.loads(text, object_pairs_hook=_object, parse_constant=invalid_number, parse_float=finite_float)


def _load_contract() -> dict[str, Any]:
    try:
        value = _parse(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise JobStoreError(f"Cannot read execution quota contract: {error}") from error
    fields = {"schemaVersion", "defaultLimit", "maxSlots", "settingKey", "commands"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["maxSlots"]) is not int or value["maxSlots"] < 1
            or type(value["defaultLimit"]) is not int or not 1 <= value["defaultLimit"] <= value["maxSlots"]
            or not isinstance(value["settingKey"], str) or not value["settingKey"]
            or not isinstance(value["commands"], list)
            or not all(isinstance(command, str) and command for command in value["commands"])
            or len(value["commands"]) != len(set(value["commands"]))):
        raise JobStoreError("Invalid execution quota contract")
    return value


_CONTRACT = _load_contract()
MAX_COMPUTE_WORKERS = _CONTRACT["maxSlots"]


def applies(command: str) -> bool:
    return command in _CONTRACT["commands"]


def read_limit(data_root: Path) -> int:
    path = data_root / "settings" / "app.json"
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise JobStoreError("Quota settings cannot be a symlink or reparse point")
        settings = _parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _CONTRACT["defaultLimit"]
    except (OSError, ValueError) as error:
        raise JobStoreError(f"Invalid quota settings {path}: {error}") from error
    if not isinstance(settings, dict):
        raise JobStoreError(f"Invalid quota settings {path}: expected an object")
    limit = settings.get(_CONTRACT["settingKey"], _CONTRACT["defaultLimit"])
    if type(limit) is not int or not 1 <= limit <= MAX_COMPUTE_WORKERS:
        raise JobStoreError(f"Invalid {_CONTRACT['settingKey']}: expected an integer in 1..{MAX_COMPUTE_WORKERS}")
    return limit


@lru_cache(maxsize=1)
def _windows_file_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    return ctypes, create, close


def _probe_windows_open_conflict(path: str) -> bool:
    """Recover the Win32 cause erased by the CRT's plain EACCES error."""
    ctypes, create, close = _windows_file_api()
    # OPEN_EXISTING cannot create a second lock inode. The broad sharing flags
    # impose no new exclusion while checking the same read/write access as a+b.
    handle = create(path, 0x80000000 | 0x40000000, 1 | 2 | 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in {32, 33}:
            return True
        raise ctypes.WinError(code)  # ACL/missing/other IO failures are not busy.
    if not close(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    # The exclusive handle may have closed between CRT failure and this probe;
    # retry admission instead of treating that ordinary race as a failed job.
    return True


def is_busy(error: BaseException) -> bool:
    if isinstance(error, RuntimeLockBusy):
        return True
    if not isinstance(error, OSError):
        return False
    code = getattr(error, "winerror", None)
    if code in {32, 33}:
        return True
    if code is None and os.name == "nt" and error.errno == errno.EACCES and error.filename:
        return _probe_windows_open_conflict(os.fsdecode(error.filename))
    return False


class QuotaLease:
    def __init__(self, index: int, guard, fence_path: Path):
        self.index = index
        self.fence_path = fence_path
        self._guard = guard
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._guard is not None:
                guard, self._guard = self._guard, None
                guard.__exit__(None, None, None)


def try_acquire(jobs_root: Path, limit: int) -> QuotaLease | None:
    """Caller holds catalog; probe every fixed slot, including slots above limit."""
    if type(limit) is not int or not 1 <= limit <= MAX_COMPUTE_WORKERS:
        raise JobStoreError("Invalid compute quota limit")
    free = []
    busy = 0
    quota_root = jobs_root / ".quota"
    try:
        for index in range(MAX_COMPUTE_WORKERS):
            guard = runtime_lock(quota_root / f"global-{index}.lock", timeout=0)
            try:
                guard.__enter__()
            except (OSError, RuntimeLockBusy) as error:
                if not is_busy(error):
                    raise
                busy += 1
                continue
            # The owner may have exited while its supervisor is still reaping.
            # Check the second lease while holding the primary so a live pair
            # occupies one slot, including after the owner's handle closes.
            fence_path = quota_root / "reaping" / f"global-{index}.lock"
            try:
                with runtime_lock(fence_path, timeout=0):
                    pass
            except (OSError, RuntimeLockBusy) as error:
                guard.__exit__(None, None, None)
                if not is_busy(error):
                    raise
                busy += 1
            except BaseException:
                guard.__exit__(None, None, None)
                raise
            else:
                free.append((index, guard, fence_path))
        if busy >= limit or not free:
            return None
        index, guard, fence_path = free.pop(0)
        return QuotaLease(index, guard, fence_path)
    finally:
        for _, guard, _ in reversed(free):
            guard.__exit__(None, None, None)
