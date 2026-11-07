"""Worker command for MediaForge image restoration."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import os
import traceback
from typing import Any

from shared.worker_protocol import emit, emit_error

from shared import worker_paths
from . import image_ops
from . import capabilities
from . import evaluation
from . import failures
from . import task_params


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

        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            worker_paths.export_model_root(model_dir)

        steps = task_params.normalize_steps(payload, modality="image")
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
        scale = float(task_params.value(params, "scale", 2.0))
        target = image_ops.resolve_target(
            str(task_params.value(params, "target", "none")),
            int(task_params.value(params, "width", 0)),
            int(task_params.value(params, "height", 0)),
        )
        fit = str(task_params.value(params, "fit", "contain"))
        conf = float(task_params.value(params, "conf", 0.25))
        expand = int(task_params.value(params, "expand", 6))
        if step["capability"] == "upscale":
            params["target"] = target

        if mode == "detect":
            model_path = capabilities.model_path(bindings["detector"])
            outputs = []
            failed = []
            for source in inputs:
                try:
                    boxes = image_ops.detect_objects(model_path, image_ops.read_image(source), conf=conf)
                    sidecar = Path(output_dir) / f"{Path(source).stem}_detect.json"
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    sidecar.write_text(json.dumps({"source": str(source), "boxes": boxes}, ensure_ascii=False, indent=2), encoding="utf-8")
                    outputs.append({"path": str(sidecar), "size": sidecar.stat().st_size, "boxes": boxes})
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode == "depth":
            model_dir = capabilities.model_path(bindings["depth_estimator"])
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
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode == "matting":
            model_dir = capabilities.model_path(bindings["background_remover"])
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
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode in {"face", "face_restoration"}:
            model_path = capabilities.model_path(bindings["face_restorer"])
            outputs = []
            failed = []
            for source in inputs:
                try:
                    restored = image_ops.restore_faces(model_path, image_ops.read_image(source))
                    target_path = Path(output_dir) / f"{Path(source).stem}_face.png"
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    image_ops.write_image(target_path, restored)
                    outputs.append({"path": str(target_path), "size": target_path.stat().st_size})
                except Exception as exc:  # noqa: BLE001
                    failed.append(failures.describe(source, exc))
            emit(
                "task.result",
                {"operation": operation, "outputs": outputs, "failed": failed, "skipped": [],
                 "total": len(inputs), "completed": len(outputs), "warnings": []},
                request_id=request_id,
            )
            return 0

        if mode == "interpolate":
            if len(inputs) < 2:
                return emit_error("MEDIA_IMAGE_INVALID", "插帧需要两张图作为前后帧", request_id=request_id)
            model_id = bindings["interpolator"]
            model_dir = capabilities.model_path(model_id)
            source_a, source_b = inputs[0], inputs[1]
            middle = image_ops.interpolate_frames(
                model_dir, image_ops.read_image(source_a), image_ops.read_image(source_b),
                float(task_params.value(params, "timestep", 0.5)),
            )
            target_path = Path(output_dir) / f"{Path(source_a).stem}_{Path(source_b).stem}_interp.png"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            image_ops.write_image(target_path, middle)
            emit(
                "task.result",
                {"operation": operation, "outputs": [{"path": str(target_path), "size": target_path.stat().st_size}],
                 "failed": [], "skipped": [], "total": 1, "completed": 1, "warnings": []},
                request_id=request_id,
            )
            return 0

        restorer = image_ops.ImageRestorer(device=str(payload.get("device") or "") or None, tile=int(task_params.value(params, "tile", 0)) or 512)
        outputs: list[dict] = []
        failed: list[dict] = []
        total = len(inputs)
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
            "MEDIA_IMAGE_FAILED",
            str(exc),
            traceback.format_exc(),
            request_id=request_id,
            extra={"operation": operation},
        )
