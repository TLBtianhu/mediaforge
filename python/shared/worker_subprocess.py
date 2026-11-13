"""Options for noninteractive child processes launched by the worker."""
from __future__ import annotations

import os
import subprocess
import venv


def hidden_subprocess_kwargs(*, inherit_console: bool = False) -> dict[str, object]:
    """Hide a Windows child, optionally giving its descendants a hidden console too."""
    if os.name != "nt":
        return {}
    if not inherit_console:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startupinfo}


class HiddenVenvBuilder(venv.EnvBuilder):
    """Keep ensurepip and its own child Python inside a hidden Windows console."""

    def _call_new_python(self, context, *py_args, **kwargs):
        kwargs.update(hidden_subprocess_kwargs(inherit_console=True))
        return super()._call_new_python(context, *py_args, **kwargs)
