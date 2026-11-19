"""Platform details of the Python interpreter that runs the worker."""
from __future__ import annotations

import platform
import sys
import sysconfig


def interpreter_architecture() -> str:
    # Windows sysconfig describes this binary, including under CPU emulation.
    if sys.platform == "win32":
        machine = sysconfig.get_platform().removeprefix("win-")
    else:
        machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
