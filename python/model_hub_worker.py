"""Worker commands for the unified model hub."""
from __future__ import annotations

import traceback
from typing import Any

from worker_protocol import emit, emit_error

import model_hub


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def cmd_model_hub(payload: dict[str, Any]) -> int:
    try:
        result = model_hub.list_models(
            str(payload.get("query") or ""),
            str(payload.get("modality") or "all"),
            source=str(payload.get("source") or "modelscope"),
            root=payload.get("modelDir"),
        )
        emit("model_hub_result", {"ok": True, **result})
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("MODEL_HUB_FAILED", str(exc), traceback.format_exc())


def cmd_model_download(payload: dict[str, Any]) -> int:
    request_id = _request_id(payload)
    model_id = str(payload.get("modelId") or "").strip()
    if not model_id:
        return emit_error("MODEL_DOWNLOAD_INVALID", "Missing modelId", request_id=request_id)
    try:
        def report(fraction: float, message: str) -> None:
            emit(
                "audio_tool_progress",
                {
                    "operation": "model_download",
                    "phase": "downloading",
                    "fraction": round(max(0.0, min(1.0, fraction)), 4),
                    "completed": 0,
                    "total": 1,
                    "current": message,
                },
                request_id=request_id,
            )

        result = model_hub.download_model(
            model_id,
            payload.get("modelDir"),
            source=str(payload.get("source") or "modelscope"),
            progress=report,
        )
        emit(
            "audio_tool_result",
            {
                "operation": "model_download",
                "outputs": [{"path": file["path"], "size": file["size"], "skipped": file["skipped"]} for file in result["files"]],
                "failed": [],
                "skipped": [],
                "warnings": [],
            },
            request_id=request_id,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("MODEL_DOWNLOAD_FAILED", str(exc), traceback.format_exc(), request_id=request_id)
