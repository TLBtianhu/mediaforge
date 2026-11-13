"""Worker command for MediaForge video restoration."""
from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any

from shared.worker_protocol import emit, emit_error

from shared import worker_paths
from . import image_ops, video_ops
from . import capabilities
from . import evaluation
from . import failures
from . import task_params


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
        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            worker_paths.export_model_root(model_dir)
        steps = task_params.normalize_steps(payload, modality="video")
        watermark_step = next((step for step in steps if step["capability"] == "watermark_removal"), None)
        upscale_step = next((step for step in steps if step["capability"] == "upscale"), None)
        if len(steps) != 1:
            return emit_error(
                "MEDIA_ONE_MODEL_PER_TASK",
                "One task may run exactly one capability/model. Use a workflow for multiple steps.",
                request_id=request_id,
            )
        step = steps[0]
        params = step["params"]
        bindings = step["bindings"]
        mode = capabilities.mode_for_steps(steps)
        detect_mode = str(task_params.value(params, "detectMode", "fixed"))
        preferred_scale = None
        model_paths: dict[str, str] = {}
        if watermark_step:
            model_paths["detector"] = str(capabilities.model_path(watermark_step["bindings"]["detector"]))
            model_paths["inpainter"] = str(capabilities.model_path(watermark_step["bindings"]["inpainter"]))
        if upscale_step:
            upscaler_id = str(upscale_step["bindings"]["upscaler"])
            preferred_scale = capabilities.model_scale(upscaler_id)
            model_paths["upscaler_x2" if preferred_scale == 2 else "upscaler_x4"] = str(capabilities.model_path(upscaler_id))
        if mode == "interpolate":
            model_dir = capabilities.model_path(bindings["interpolator"])
            outputs = []
            failed = []
            for source in inputs:
                try:
                    outputs.append(
                        video_ops.interpolate_video(
                            model_dir, source, output_dir,
                            factor=int(params["multiplier"]),
                            crf=int(task_params.value(payload, "crf", 18)),
                            preset=str(task_params.value(payload, "preset", "fast")),
                            progress=lambda f, m: emit("task.progress", {"fraction": f, "message": m}, request_id=request_id),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode in {"video_inpaint", "video_inpainting"}:
            model_dir = capabilities.model_path(bindings["primary"])
            subvideo_length = int(params["subvideoLength"])
            mask_path = str(payload.get("maskPath") or "").strip() or None
            detector_path = None
            if mask_path is None:
                detector_path = capabilities.model_path(capabilities.default_bindings("watermark_removal")["detector"])
            outputs = []
            failed = []
            for source in inputs:
                try:
                    outputs.append(
                        video_ops.inpaint_video(
                            model_dir, source, output_dir,
                            mask_path=mask_path,
                            detector_path=detector_path,
                            conf=float(task_params.value(params, "conf", 0.25)),
                            expand=int(task_params.value(params, "expand", 8)),
                            detect_mode=detect_mode,
                            subvideo_length=subvideo_length,
                            preview_seconds=float(payload.get("previewSeconds") or 0),
                            crf=int(task_params.value(payload, "crf", 18)),
                            preset=str(payload.get("preset") or "fast"),
                            progress=lambda f, m: emit("task.progress", {"fraction": f, "message": m}, request_id=request_id),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode in {"video_sr", "video_super_resolution"}:
            model_path = capabilities.model_path(bindings["upscaler"])
            outputs = []
            failed = []
            for source in inputs:
                try:
                    outputs.append(
                        video_ops.upscale_video(
                            model_path, source, output_dir,
                            preview_seconds=float(payload.get("previewSeconds") or 0),
                            crf=int(task_params.value(payload, "crf", 18)),
                            preset=str(payload.get("preset") or "fast"),
                            progress=lambda f, m: emit("task.progress", {"fraction": f, "message": m}, request_id=request_id),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        restorer = image_ops.ImageRestorer(device=str(payload.get("device") or "") or None, model_paths=model_paths, tile=int(task_params.value(params, "tile", 0)) or 512)
        target = image_ops.resolve_target(
            str(task_params.value(params, "target", "none")),
            int(task_params.value(params, "width", 0)),
            int(task_params.value(params, "height", 0)),
        )
        total = len(inputs)
        outputs: list[dict] = []
        failed: list[dict] = []
        for index, source in enumerate(inputs):
            def report(fraction: float, message: str, _index=index) -> None:
                emit(
                    "task.progress",
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
                        scale=float(task_params.value(params, "scale", 1.0)),
                        target=target,
                        fit=str(task_params.value(params, "fit", "contain")),
                        conf=float(task_params.value(params, "conf", 0.25)),
                        expand=int(task_params.value(params, "expand", 8)),
                        detect_mode=detect_mode,
                        preferred_scale=preferred_scale,
                        preview_seconds=float(payload.get("previewSeconds") or 0),
                        crf=int(task_params.value(payload, "crf", 18)),
                        preset=str(payload.get("preset") or "fast"),
                        restorer=restorer,
                        progress=report,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed.append(failures.describe(source, exc))

        evaluation.attach(outputs)
        emit(
            "task.result",
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
