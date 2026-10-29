"""Worker commands for MediaForge model catalog and downloads."""
from __future__ import annotations

import traceback
from typing import Any

from worker_protocol import emit, emit_error

from . import media_models


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def cmd_media_models(payload: dict[str, Any]) -> int:
    operation = str(payload.get("operation") or "status").strip().lower()
    try:
        if operation in {"status", "list"}:
            result = media_models.status(payload.get("modelDir"))
        else:
            return emit_error("MEDIA_MODELS_UNKNOWN", f"Unknown model operation: {operation}")
        emit("media_models_result", {"operation": operation, "ok": True, **result})
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("MEDIA_MODELS_FAILED", str(exc), traceback.format_exc())


def cmd_media_model_download(payload: dict[str, Any]) -> int:
    request_id = _request_id(payload)
    model_id = str(payload.get("modelId") or "").strip()
    if not model_id:
        return emit_error("MEDIA_MODEL_INVALID", "Missing modelId", request_id=request_id)
    try:
        def report(fraction: float, message: str) -> None:
            emit(
                "audio_tool_progress",
                {
                    "operation": "media_model_download",
                    "phase": "downloading",
                    "fraction": round(max(0.0, min(1.0, fraction)), 4),
                    "completed": 0,
                    "total": 1,
                    "current": message,
                },
                request_id=request_id,
            )

        result = media_models.download(model_id, payload.get("modelDir"), progress=report)
        emit(
            "audio_tool_result",
            {"operation": "media_model_download", "outputs": [result], "failed": [], "skipped": [], "warnings": []},
            request_id=request_id,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("MEDIA_MODEL_DOWNLOAD_FAILED", str(exc), traceback.format_exc(), request_id=request_id)
