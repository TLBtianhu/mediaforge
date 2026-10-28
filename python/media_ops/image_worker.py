"""Worker command for MediaForge image restoration."""
from __future__ import annotations

import os
import traceback
from typing import Any

from worker_protocol import emit, emit_error

from . import image_ops


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def cmd_media_image(payload: dict[str, Any]) -> int:
    operation = "media_image"
    request_id = _request_id(payload)
    try:
        inputs = [str(item) for item in (payload.get("inputs") or []) if str(item).strip()]
        if not inputs:
            return emit_error("MEDIA_IMAGE_INVALID", "No input images", request_id=request_id)
        output_dir = str(payload.get("outputDir") or "").strip()
        if not output_dir:
            return emit_error("MEDIA_IMAGE_INVALID", "No output directory", request_id=request_id)

        mode = str(payload.get("mode") or "watermark_upscale").strip().lower()
        if mode not in {"upscale", "watermark", "watermark_upscale"}:
            return emit_error("MEDIA_IMAGE_INVALID", f"Unknown image mode: {mode}", request_id=request_id)
        scale = float(payload.get("scale") or 2.0)
        target = image_ops.resolve_target(
            str(payload.get("target") or "none"),
            int(payload.get("width") or 0),
            int(payload.get("height") or 0),
        )
        fit = str(payload.get("fit") or "contain")
        conf = float(payload.get("conf") or 0.25)
        expand = int(payload.get("expand") or 6)
        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            os.environ["MEDIAFORGE_MODEL_DIR"] = model_dir

        restorer = image_ops.ImageRestorer()
        outputs: list[dict] = []
        failed: list[dict] = []
        total = len(inputs)
        for index, source in enumerate(inputs):
            def report(fraction: float, message: str, _index=index) -> None:
                emit(
                    "audio_tool_progress",
                    {
                        "operation": operation,
                        "phase": "processing",
                        "fraction": round((_index + max(0.0, min(1.0, fraction))) / max(total, 1), 4),
                        "completed": index,
                        "total": total,
                        "current": f"{message} {source.split(chr(92))[-1].split('/')[-1]}",
                    },
                    request_id=request_id,
                )

            try:
                outputs.append(
                    restorer.process_file(
                        source, output_dir, mode=mode, scale=scale, target=target,
                        fit=fit, conf=conf, expand=expand, progress=report,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed.append({"path": str(source), "error": str(exc)})

        emit(
            "audio_tool_result",
            {
                "operation": operation,
                "outputs": outputs,
                "failed": failed,
                "skipped": [],
                "total": total,
                "completed": len(outputs),
                "warnings": [],
            },
            request_id=request_id,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error(
            "MEDIA_IMAGE_FAILED",
            str(exc),
            traceback.format_exc(),
            request_id=request_id,
            extra={"operation": operation},
        )
