from __future__ import annotations

import os
import sys
from pathlib import Path


def rename_exclusive(temporary: Path, path: Path) -> None:
    if os.name == 'nt':
        os.rename(temporary, path)
        return
    import ctypes
    import errno

    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = getattr(library, 'renamex_np', None)
        arguments = (os.fsencode(temporary), os.fsencode(path), 0x4)  # RENAME_EXCL
        signature = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    elif sys.platform.startswith('linux'):
        rename = getattr(library, 'renameat2', None)
        arguments = (-100, os.fsencode(temporary), -100, os.fsencode(path), 1)  # AT_FDCWD, RENAME_NOREPLACE
        signature = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    else:
        rename = None
    if rename is None:
        raise OSError(errno.ENOTSUP, 'Atomic output publishing is unavailable', str(path))
    rename.argtypes = signature
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        code = ctypes.get_errno() or errno.EIO
        raise OSError(code, os.strerror(code), str(path))


def publish_output_path(temporary: Path, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        candidate = path if index == 1 else path.with_name(f"{path.stem}_{index}{path.suffix}")
        try:
            os.link(temporary, candidate)
        except FileExistsError:
            continue
        except OSError:
            # Filesystems without hard links use the platform's exclusive
            # rename operation; never expose an empty final placeholder.
            try:
                rename_exclusive(temporary, candidate)
            except FileExistsError:
                continue
            return candidate
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # The final hard link is already complete. Worker/Rust cleanup can
            # remove a transiently locked task temp after the process exits.
            pass
        return candidate
    raise FileExistsError(f"Failed to publish a unique export filename: {path}")
