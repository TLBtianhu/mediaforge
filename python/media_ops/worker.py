"""Worker entry points for MediaForge media tools.

These commands are intentionally routed through the existing ``audio_tools``
Tauri task bridge, so the desktop shell can launch them without a new Rust
command. Events use the same ``task.progress`` / ``task.result``
protocol as PyMSS audio tools.
"""
from __future__ import annotations

import traceback
from typing import Any

from shared.worker_protocol import emit, emit_error

from . import ffmpeg


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def _progress(request_id: str | None, operation: str, fraction: float, current: str, completed: int, total: int) -> None:
    emit(
        "task.progress",
        {
            "operation": operation,
            "phase": "converting",
            "fraction": round(max(0.0, min(1.0, float(fraction))), 4),
            "completed": int(completed),
            "total": int(total),
            "current": current,
        },
        request_id=request_id,
    )


# 模式 → 需要的能力。放在 worker 入口，是因为这里能同时看到 steps 与 mode 两种调用形态。
_MODE_CAPABILITIES = {
    "upscale": ["upscale"],
    "watermark": ["watermark_removal"],
    "watermark_upscale": ["upscale", "watermark_removal"],
    "denoise": ["denoise"],
    "detect": ["object_detection"],
    "depth": ["depth"],
    "matting": ["matting"],
    "interpolate": ["frame_interpolation"],
}


def _requested_capabilities(payload: dict[str, Any]) -> set[str]:
    """这次任务需要运行时具备哪些能力（steps 优先，其次 mode）。"""
    requested: set[str] = set()
    for step in payload.get("steps") or []:
        if isinstance(step, dict):
            capability = str(step.get("capability") or "").strip()
            if capability:
                requested.add(capability)
    mode = str(payload.get("mode") or "").strip().lower()
    requested.update(_MODE_CAPABILITIES.get(mode, []))
    return requested


def cmd_media_tool(payload: dict[str, Any]) -> int:
    operation = str(payload.get("operation") or "").strip().lower()
    request_id = _request_id(payload)

    from . import runtime_guard

    requested = _requested_capabilities(payload)
    missing = runtime_guard.missing_capabilities(requested)
    if missing:
        described = runtime_guard.describe()
        return emit_error(
            "RUNTIME_CAPABILITY_MISSING",
            f"当前运行时（{described['assetId']}，来源 {described['source']}）不支持：{', '.join(missing)}。"
            "请安装包含该能力的运行时，或换用已支持的能力。",
            request_id=request_id,
            extra={"runtime": described, "missing": missing, "requested": sorted(requested)},
        )
    try:
        if operation == "media_image":
            from .image_worker import cmd_media_image

            return cmd_media_image(payload)

        if operation == "media_video":
            from .video_worker import cmd_media_video

            return cmd_media_video(payload)

        if operation == "media_workflow":
            from .workflow_worker import cmd_media_workflow

            return cmd_media_workflow(payload)

        if operation == "media_probe":
            inputs = [str(item) for item in (payload.get("inputs") or []) if str(item).strip()]
            if not inputs:
                return emit_error("MEDIA_INVALID", "No input files", request_id=request_id)
            probes = [ffmpeg.probe(path) for path in inputs]
            emit(
                "task.result",
                {"operation": operation, "probes": probes, "probe": probes[0]},
                request_id=request_id,
            )
            return 0

        if operation == "media_convert":
            inputs = [str(item) for item in (payload.get("inputs") or []) if str(item).strip()]
            if not inputs:
                return emit_error("MEDIA_INVALID", "No input files", request_id=request_id)
            output_dir = str(payload.get("outputDir") or "").strip()
            if not output_dir:
                return emit_error("MEDIA_INVALID", "No output directory", request_id=request_id)
            fmt = str(payload.get("format") or "mp4").strip().lower()
            crf = int(payload.get("crf") or 20)
            preset = str(payload.get("preset") or "medium")

            def report(fraction: float, message: str) -> None:
                completed = min(len(inputs), int(fraction * len(inputs)))
                _progress(request_id, operation, fraction, message, completed, len(inputs))

            result = ffmpeg.convert_many(
                inputs, output_dir, fmt, crf=crf, preset=preset, progress=report
            )
            emit(
                "task.result",
                {"operation": operation, **result, "warnings": []},
                request_id=request_id,
            )
            return 0

        return emit_error("MEDIA_UNKNOWN", f"Unknown media operation: {operation}", request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        return emit_error(
            "MEDIA_TOOL_FAILED",
            str(exc),
            traceback.format_exc(),
            request_id=request_id,
            extra={"operation": operation},
        )
