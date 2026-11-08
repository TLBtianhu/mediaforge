"""Synchronous worker command for the MediaForge asset library."""
from __future__ import annotations

import traceback
from typing import Any

from shared.worker_protocol import emit, emit_error

from . import library


def cmd_media_library(payload: dict[str, Any]) -> int:
    operation = str(payload.get("operation") or "list").strip().lower()
    root = payload.get("dataRoot")
    try:
        if operation == "import":
            result = library.import_paths(payload.get("paths") or [], data_root=root)
        elif operation == "list":
            result = library.list_assets(
                str(payload.get("query") or ""),
                str(payload.get("kind") or "all"),
                int(payload.get("limit") or 500),
                data_root=root,
            )
        elif operation == "remove":
            result = library.remove_assets(payload.get("ids") or [], data_root=root)
        elif operation == "tags":
            result = library.update_tags(int(payload.get("id") or 0), str(payload.get("tags") or ""), data_root=root)
        elif operation == "stats":
            result = library.stats(data_root=root)
        else:
            return emit_error("MEDIA_LIBRARY_UNKNOWN", f"Unknown library operation: {operation}")
        emit("media_library_result", {"operation": operation, "ok": True, **result})
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error(
            "MEDIA_LIBRARY_FAILED",
            str(exc),
            traceback.format_exc(),
            extra={"operation": operation},
        )
