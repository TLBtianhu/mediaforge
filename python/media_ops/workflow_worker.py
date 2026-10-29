"""Multi-step image/video workflow engine.

The Operation Kernel owns the job lifecycle. This worker owns only the
step-to-step orchestration: each step is one capability plus one primary model,
and the output of a step becomes the input of the next step.

Image steps run in memory through ImageRestorer.process_steps. Video steps run
sequentially through video_ops.process_video and hand intermediate files to the
next step. The final artifact is copied into outputDir with a stable name.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from worker_protocol import emit, emit_error

from . import capabilities, image_ops, video_ops


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def _suffix(steps: list[dict[str, Any]]) -> str:
    names = []
    for step in steps:
        capability = str(step.get("capability") or "")
        if capability == "watermark_removal":
            names.append("clean")
        elif capability == "upscale":
            names.append("sr")
        else:
            names.append(capability or "step")
    return "_".join(names) or "workflow"


def _progress(request_id: str | None, fraction: float, current: str, completed: int, total: int) -> None:
    emit(
        "audio_tool_progress",
        {
            "operation": "media_workflow",
            "phase": "processing",
            "fraction": round(max(0.0, min(1.0, float(fraction))), 4),
            "completed": int(completed),
            "total": int(total),
            "current": current,
        },
        request_id=request_id,
    )


def _validate_models(steps: list[dict[str, Any]]) -> None:
    missing: list[str] = []
    for step in steps:
        for model_id in step["bindings"].values():
            path = capabilities.model_path(str(model_id))
            if not path.is_file():
                missing.append(f"{model_id} ({path})")
    if missing:
        raise FileNotFoundError("missing models: " + ", ".join(missing))


def _run_image(
    source: Path,
    output_dir: Path,
    steps: list[dict[str, Any]],
    request_id: str | None,
    index: int,
    total: int,
) -> dict[str, Any]:
    restorer = image_ops.ImageRestorer()
    name = source.name

    def report(fraction: float, message: str) -> None:
        _progress(
            request_id,
            (index + max(0.0, min(1.0, fraction))) / max(total, 1),
            f"{message} {name}",
            index,
            total,
        )

    result = restorer.process_file(
        source,
        output_dir,
        mode="workflow",
        steps=steps,
        progress=report,
    )
    result["workflow"] = {"steps": [step["capability"] for step in steps]}
    return result


def _run_video(
    source: Path,
    output_dir: Path,
    steps: list[dict[str, Any]],
    payload: dict[str, Any],
    request_id: str | None,
    index: int,
    total: int,
) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="mfworkflow_"))
    device_name = "cuda"
    try:
        current = source
        step_infos: list[dict[str, Any]] = []
        for step_index, step in enumerate(steps):
            capability = str(step.get("capability") or "")
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            bindings = step.get("bindings") if isinstance(step.get("bindings"), dict) else {}

            def report(fraction: float, message: str, _step=step_index) -> None:
                # Keep room for the remaining steps and inputs.
                base = (index + (_step + max(0.0, min(1.0, fraction))) / max(len(steps), 1)) / max(total, 1)
                _progress(request_id, base, f"{message} {source.name}", index, total)

            step_output = work / f"step{step_index + 1}"
            if capability == "watermark_removal":
                mode = "watermark"
                detect_mode = str(params.get("detectMode") or payload.get("detectMode") or "fixed")
                conf = float(params.get("conf") or payload.get("conf") or 0.25)
                expand = int(params.get("expand") or payload.get("expand") or 8)
                scale = 1.0
                preferred_scale = None
            elif capability == "upscale":
                mode = "upscale"
                detect_mode = "fixed"
                conf = 0.25
                expand = 8
                scale = float(params.get("scale") or payload.get("scale") or 2.0)
                upscaler = str(bindings.get("upscaler") or "")
                preferred_scale = 4 if "x4" in upscaler else 2
            else:
                raise ValueError(f"unsupported video workflow capability: {capability}")

            target = image_ops.resolve_target(
                str(params.get("target") or "none"),
                int(params.get("width") or payload.get("width") or 0),
                int(params.get("height") or payload.get("height") or 0),
            )
            model_paths: dict[str, str] = {}
            if capability == "watermark_removal":
                model_paths["detector"] = str(capabilities.model_path(str(bindings["detector"])))
                model_paths["inpainter"] = str(capabilities.model_path(str(bindings["inpainter"])))
            elif capability == "upscale":
                upscaler = str(bindings["upscaler"])
                key = "upscaler_x4" if preferred_scale == 4 else "upscaler_x2"
                model_paths[key] = str(capabilities.model_path(upscaler))
            restorer = image_ops.ImageRestorer(model_paths=model_paths)
            device_name = restorer.device
            result = video_ops.process_video(
                current,
                step_output,
                mode=mode,
                scale=scale,
                target=target,
                fit=str(params.get("fit") or payload.get("fit") or "contain"),
                conf=conf,
                expand=expand,
                detect_mode=detect_mode,
                preferred_scale=preferred_scale,
                preview_seconds=float(payload.get("previewSeconds") or 0),
                crf=int(payload.get("crf") or 18),
                preset=str(payload.get("preset") or "fast"),
                restorer=restorer,
                progress=report,
            )
            current = Path(result["path"])
            step_infos.append({"capability": capability, "path": str(current), "info": result.get("info")})

        final = output_dir / f"{source.stem}_workflow_{_suffix(steps)}.mp4"
        if final.resolve() != current.resolve():
            shutil.copy2(current, final)
        return {
            "path": str(final),
            "source": str(source),
            "mode": "workflow",
            "steps": [step["capability"] for step in steps],
            "info": {
                "device": device_name,
                "workflow_steps": step_infos,
                "elapsed": round(sum(float((item.get("info") or {}).get("elapsed") or 0) for item in step_infos), 2),
                "output_path": str(final),
            },
            "size": final.stat().st_size,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cmd_media_workflow(payload: dict[str, Any]) -> int:
    request_id = _request_id(payload)
    try:
        inputs = [Path(str(item)) for item in (payload.get("inputs") or []) if str(item).strip()]
        if not inputs:
            return emit_error("MEDIA_WORKFLOW_INVALID", "No input files", request_id=request_id)
        output_dir = Path(str(payload.get("outputDir") or "").strip())
        if not str(output_dir):
            return emit_error("MEDIA_WORKFLOW_INVALID", "No output directory", request_id=request_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        modality = str(payload.get("modality") or "").strip().lower()
        if modality not in {"image", "video"}:
            return emit_error("MEDIA_WORKFLOW_INVALID", "modality must be image or video", request_id=request_id)
        raw_steps = payload.get("steps")
        steps = capabilities.normalize_steps(raw_steps)
        if not steps:
            return emit_error("MEDIA_WORKFLOW_INVALID", "Workflow requires at least one step", request_id=request_id)
        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            os.environ["MEDIAFORGE_MODEL_DIR"] = model_dir
        _validate_models(steps)

        started = time.perf_counter()
        total = len(inputs)
        outputs: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for index, source in enumerate(inputs):
            try:
                if not source.is_file():
                    raise FileNotFoundError(f"input not found: {source}")
                if modality == "image":
                    outputs.append(_run_image(source, output_dir, steps, request_id, index, total))
                else:
                    outputs.append(_run_video(source, output_dir, steps, payload, request_id, index, total))
            except Exception as exc:  # noqa: BLE001
                failed.append({"path": str(source), "error": str(exc)})

        emit(
            "audio_tool_result",
            {
                "operation": "media_workflow",
                "modality": modality,
                "outputs": outputs,
                "failed": failed,
                "skipped": [],
                "total": total,
                "completed": len(outputs),
                "steps": [step["capability"] for step in steps],
                "elapsed": round(time.perf_counter() - started, 2),
                "warnings": [],
            },
            request_id=request_id,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error(
            "MEDIA_WORKFLOW_FAILED",
            str(exc),
            request_id=request_id,
            extra={"operation": "media_workflow"},
        )
