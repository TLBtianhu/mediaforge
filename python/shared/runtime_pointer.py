"""Single reader for ``active-runtime.json``.

Rust (``python::worker``), the bootstrap worker and the MCP bridge all resolve
the same pointer. They must agree on one rule: a missing file is a legitimate
"nothing activated", while an unreadable or unresolvable file is an error that
callers report instead of silently falling back to another interpreter.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_active_runtime(file: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(state, error)``; exactly one of them is ``None``."""
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as error:
        return None, f"runtime pointer {file} cannot be read: {error}"
    try:
        state = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return None, f"runtime pointer {file} is not valid JSON: {error}"
    if not isinstance(state, dict):
        return None, f"runtime pointer {file} is not a JSON object"
    python_path = state.get("pythonPath")
    if not python_path:
        return None, f"runtime pointer {file} does not name a pythonPath"
    if not isinstance(python_path, str):
        return None, f"runtime pointer {file} has a non-string pythonPath"
    candidate = Path(python_path)
    if not candidate.is_absolute():
        resolved = file.parent / candidate
        if not resolved.is_file():
            return None, f"runtime pointer {file} points to a missing interpreter: {resolved}"
        # Keep the executable link: POSIX venvs locate pyvenv.cfg through argv[0].
        state["pythonPath"] = str(resolved.parent.resolve() / resolved.name)
    elif not candidate.is_file():
        return None, f"runtime pointer {file} points to a missing interpreter: {candidate}"
    return state, None
