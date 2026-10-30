"""Worker command for MediaForge image restoration."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import os
import traceback
from typing import Any

from worker_protocol import emit, emit_error

import worker_paths
from . import image_ops
from . import capabilities
from . import evaluation
from . import failures


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
        if mode not in {"upscale", "watermark", "watermark_upscale", "denoise", "detect", "depth", "matting", "custom"}:
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
            worker_paths.export_model_root(model_dir)

        raw_steps = payload.get("steps")
        if raw_steps:
            steps = capabilities.normalize_steps(raw_steps)
            for step in steps:
                if step["capability"] == "upscale":
                    target_value = step["params"].get("target")
                    if not isinstance(target_value, tuple):
                        step["params"]["target"] = image_ops.resolve_target(
                            str(target_value or "none"),
                            int(payload.get("width") or 0),
                            int(payload.get("height") or 0),
                        )
            mode = capabilities.mode_for_steps(steps)
        else:
            steps = capabilities.normalize_steps(capabilities.steps_from_mode(mode))
            for step in steps:
                if step["capability"] == "watermark_removal":
                    step["params"].update({"conf": conf, "expand": expand})
                elif step["capability"] == "upscale":
                    step["params"].update({"scale": scale, "target": target, "fit": fit})
                elif step["capability"] == "denoise":
                    # 允许指定替代去噪模型；不指定就用能力的默认绑定。
                    # 图像独有能力（去噪/深度/抠图/检测）走不了 video_step 的 steps 路由，
                    # 所以模型选择必须在这里透传。
                    override = str(payload.get("denoiserModelId") or "").strip()
                    step["params"].update({})
                    if override:
                        step["bindings"] = {"denoiser": override}
        if len(steps) != 1:
            return emit_error(
                "MEDIA_ONE_MODEL_PER_TASK",
                "One task may run exactly one capability/model. Use a workflow for multiple steps.",
                request_id=request_id,
            )

        if mode == "detect":
            binding = capabilities.default_bindings("object_detection")["detector"]
            model_path = capabilities.model_path(str(binding))
            outputs = []
            failed = []
            for source in inputs:
                try:
                    boxes = image_ops.detect_objects(model_path, image_ops.read_image(source), conf=float(payload.get("conf") or 0.25))
                    sidecar = Path(output_dir) / f"{Path(source).stem}_detect.json"
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    sidecar.write_text(json.dumps({"source": str(source), "boxes": boxes}, ensure_ascii=False, indent=2), encoding="utf-8")
                    outputs.append({"path": str(sidecar), "size": sidecar.stat().st_size, "boxes": boxes})
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "audio_tool_result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode == "depth":
            binding = capabilities.default_bindings("depth")["depth_estimator"]
            model_dir = capabilities.model_path(str(binding))
            outputs = []
            failed = []
            for source in inputs:
                try:
                    depth = image_ops.estimate_depth(model_dir, image_ops.read_image(source))
                    target_path = Path(output_dir) / f"{Path(source).stem}_depth.png"
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    image_ops.write_image(target_path, np.stack([depth] * 3, axis=-1))
                    outputs.append({"path": str(target_path), "size": target_path.stat().st_size})
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "audio_tool_result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode == "matting":
            binding = capabilities.default_bindings("matting")["background_remover"]
            model_dir = capabilities.model_path(str(binding))
            outputs = []
            failed = []
            for source in inputs:
                try:
                    rgba = image_ops.remove_background(model_dir, image_ops.read_image(source))
                    target_path = Path(output_dir) / f"{Path(source).stem}_matte.png"
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    image_ops.write_image(target_path, rgba)
                    outputs.append({"path": str(target_path), "size": target_path.stat().st_size})
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "audio_tool_result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        restorer = image_ops.ImageRestorer(device=str(payload.get("device") or "") or None, tile=int(payload.get("tile") or 0) or 512)
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
                        fit=fit, conf=conf, expand=expand, steps=steps, progress=report,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed.append(failures.describe(source, exc))

        evaluation.attach(outputs)
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
