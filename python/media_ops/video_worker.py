"""Worker command for MediaForge video restoration."""
from __future__ import annotations

import os
import traceback
from typing import Any

from worker_protocol import emit, emit_error

import worker_paths
from . import image_ops, video_ops
from . import capabilities


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def cmd_media_video(payload: dict[str, Any]) -> int:
    operation = "media_video"
    request_id = _request_id(payload)
    try:
        inputs = [str(item) for item in (payload.get("inputs") or []) if str(item).strip()]
        if not inputs:
            return emit_error("MEDIA_VIDEO_INVALID", "No input videos", request_id=request_id)
        output_dir = str(payload.get("outputDir") or "").strip()
        if not output_dir:
            return emit_error("MEDIA_VIDEO_INVALID", "No output directory", request_id=request_id)
        mode = str(payload.get("mode") or "watermark_upscale").strip().lower()
        if mode not in {"upscale", "watermark", "watermark_upscale", "custom"}:
            return emit_error("MEDIA_VIDEO_INVALID", f"Unknown video mode: {mode}", request_id=request_id)
        detect_mode = str(payload.get("detectMode") or "fixed").strip().lower()
        if detect_mode not in {"fixed", "per_frame"}:
            return emit_error("MEDIA_VIDEO_INVALID", f"Unknown detection mode: {detect_mode}", request_id=request_id)
        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            worker_paths.export_model_root(model_dir)
        raw_steps = payload.get("steps")
        if raw_steps:
            steps = capabilities.normalize_steps(raw_steps)
            mode = capabilities.mode_for_steps(steps)
        else:
            steps = capabilities.normalize_steps(capabilities.steps_from_mode(mode))
        watermark_step = next((step for step in steps if step["capability"] == "watermark_removal"), None)
        upscale_step = next((step for step in steps if step["capability"] == "upscale"), None)
        if len(steps) != 1:
            return emit_error(
                "MEDIA_ONE_MODEL_PER_TASK",
                "One task may run exactly one capability/model. Use a workflow for multiple steps.",
                request_id=request_id,
            )
        if watermark_step:
            detect_mode = str(watermark_step["params"].get("detectMode") or detect_mode)
        preferred_scale = None
        model_paths: dict[str, str] = {}
        if watermark_step:
            model_paths["detector"] = str(capabilities.model_path(watermark_step["bindings"]["detector"]))
            model_paths["inpainter"] = str(capabilities.model_path(watermark_step["bindings"]["inpainter"]))
        if upscale_step:
            upscaler_id = str(upscale_step["bindings"]["upscaler"])
            preferred_scale = capabilities.model_scale(upscaler_id)
            model_paths["upscaler_x2" if preferred_scale == 2 else "upscaler_x4"] = str(capabilities.model_path(upscaler_id))
        restorer = image_ops.ImageRestorer(model_paths=model_paths)
        target = image_ops.resolve_target(
            str(payload.get("target") or "none"),
            int(payload.get("width") or 0),
            int(payload.get("height") or 0),
        )
        total = len(inputs)
        outputs: list[dict] = []
        failed: list[dict] = []
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
                        "current": message,
                    },
                    request_id=request_id,
                )

            try:
                outputs.append(
                    video_ops.process_video(
                        source,
                        output_dir,
                        mode=mode,
                        scale=float(payload.get("scale") or 1.0),
                        target=target,
                        fit=str(payload.get("fit") or "contain"),
                        conf=float(payload.get("conf") or 0.25),
                        expand=int(payload.get("expand") or 8),
                        detect_mode=detect_mode,
                        preferred_scale=preferred_scale,
                        preview_seconds=float(payload.get("previewSeconds") or 0),
                        crf=int(payload.get("crf") or 18),
                        preset=str(payload.get("preset") or "fast"),
                        restorer=restorer,
                        progress=report,
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
            "MEDIA_VIDEO_FAILED",
            str(exc),
            traceback.format_exc(),
            request_id=request_id,
            extra={"operation": operation},
        )
