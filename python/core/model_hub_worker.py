"""Worker commands for the unified model hub."""
from __future__ import annotations

import time
import traceback
from typing import Any

from shared.worker_protocol import emit, emit_error

from core import model_hub


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def cmd_model_hub(payload: dict[str, Any]) -> int:
    operation = str(payload.get("operation") or "list").strip().lower()
    try:
        if operation == "verify":
            model_id = str(payload.get("modelId") or "").strip()
            if not model_id:
                return emit_error("MODEL_HUB_INVALID", "Missing modelId", request_id=_request_id(payload))
            result = model_hub.verify_model(model_id, root=payload.get("modelDir"))
        elif operation == "list":
            result = model_hub.list_models(
                str(payload.get("query") or ""),
                str(payload.get("modality") or "all"),
                source=str(payload.get("source") or "modelscope"),
                root=payload.get("modelDir"),
            )
        else:
            raise ValueError(f"Unknown model hub operation: {operation}")
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
        emit("asset.started", {"modelId": model_id, "model": model_id}, request_id=request_id)
        last = [time.monotonic(), 0]  # (时刻, 已下字节) —— 速度滑动窗口

        def report(done_bytes: int, total_bytes: int, completed_files: int, total_files: int, message: str) -> None:
            # 富进度：真实字节/速度/逐文件，与前端下载卡片的字段对齐。
            # 统一事件通道：与 worker_download 保持同一套 asset.* 语义。
            now = time.monotonic()
            prev_t, prev_b = last
            speed = int((done_bytes - prev_b) / (now - prev_t)) if now > prev_t else 0
            last[:] = [now, done_bytes]
            emit(
                "asset.progress",
                {
                    "model": model_id,
                    "modelId": model_id,
                    "progress": round(done_bytes / total_bytes * 100, 2) if total_bytes else 0,
                    "completedFiles": completed_files,
                    "totalFiles": total_files,
                    "aggregateDownloadedBytes": done_bytes,
                    "aggregateTotalBytes": total_bytes,
                    "speedBytesPerSecond": max(0, speed),
                    "message": message,
                },
                request_id=request_id,
            )

        result = model_hub.download_model(
            model_id,
            payload.get("modelDir"),
            source=str(payload.get("source") or "modelscope"),
            detailed_progress=report,
        )
        emit(
            "asset.done",
            {
                "model": model_id,
                "modelId": model_id,
                "downloaded": [file["path"] for file in result["files"]],
                "skipped": [],
                "modelDir": result.get("modelDir"),
                "modelInfo": result.get("model"),
            },
            request_id=request_id,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("MODEL_DOWNLOAD_FAILED", str(exc), traceback.format_exc(), request_id=request_id)
