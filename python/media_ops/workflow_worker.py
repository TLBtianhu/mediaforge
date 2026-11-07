"""Multi-step image/video workflow engine.

The Operation Kernel owns the job lifecycle. This worker owns only the
step-to-step orchestration: each step is one capability plus one primary model,
and the output of a step becomes the input of the next step.

Image steps run in memory through ImageRestorer.process_steps. Video frame steps
share one decoder/encoder; whole-sequence adapters currently run alone.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from shared.worker_protocol import emit, emit_error

from shared import worker_paths
from . import capabilities, evaluation, failures, image_ops, task_params, video_ops, video_step
from .workflow_contract import workflow_support


def _shared_tile(steps: list[dict[str, Any]], payload: dict[str, Any]) -> int:
    tiles = {int(step["params"].get("tile") or 0) for step in steps if step["capability"] == "upscale"}
    tiles.discard(0)
    if len(tiles) > 1:
        raise ValueError("workflow steps share one restorer; conflicting tile values are not supported")
    tile = next(iter(tiles), int(task_params.value(payload, "tile", 0))) or 512
    if tile < 64:
        raise ValueError("workflow tile must be 0 (automatic) or at least 64")
    return tile


def validate_workflow(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate every static step constraint before model loading or output creation."""
    modality = str(payload.get("modality") or "").strip().lower()
    if modality not in {"image", "video"}:
        raise ValueError("modality must be image or video")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Workflow requires at least one step")
    catalog = capabilities._catalog()
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ValueError("workflow step must be an object")
        capability_id = str(raw.get("capability") or raw.get("type") or "")
        declaration = capabilities.capability(capability_id)
        support = workflow_support(capability_id)[modality]
        if not support["supported"]:
            raise ValueError(f"{capability_id}: {support['reason']}")
        if support.get("maxSteps") == 1 and len(raw_steps) != 1:
            raise ValueError(f"{capability_id}: sequence video capabilities currently require a single-step workflow")
        bindings = raw.get("bindings") or {}
        if not isinstance(bindings, dict):
            raise ValueError(f"{capability_id}: bindings must be an object")
        unknown_roles = set(bindings) - set(declaration["roles"])
        if unknown_roles:
            raise ValueError(f"{capability_id}: unsupported bindings: {', '.join(sorted(unknown_roles))}")
        for role, model_id in bindings.items():
            record = catalog.get(str(model_id)) or {}
            if record.get("task") != declaration["roles"][role].get("task"):
                raise ValueError(f"{capability_id}: model {model_id} cannot fulfill role {role}")
        if raw.get("params") is not None and not isinstance(raw["params"], dict):
            raise ValueError(f"{capability_id}: params must be an object")
    steps = task_params.normalize_steps(payload, modality=modality)
    if any(workflow_support(step["capability"])[modality]["execution"] == "video-sequence" for step in steps):
        if payload.get("tile") not in (None, 0):
            raise ValueError("sequence workflow adapters do not support a tile override")
        if str(payload.get("device") or "").strip():
            raise ValueError("sequence workflow adapters select their own device; a device override is not supported")
    slots: dict[str, str] = {}
    for step in steps:
        capability_id = step["capability"]
        support = workflow_support(capability_id)[modality]
        specs = capabilities.parameter_specs(capability_id, step["bindings"])
        for name, value in step["params"].items():
            if name not in support["supportedParams"] and value != specs[name].get("default"):
                raise ValueError(f"{capability_id}: parameter {name} is not supported by the {modality} workflow adapter")
        if capability_id == "upscale":
            params = step["params"]
            if params["fit"] not in {"contain", "cover", "pad", "stretch"}:
                raise ValueError(f"unsupported workflow fit: {params['fit']}")
            image_ops.resolve_target(str(params["target"]), int(params["width"]), int(params["height"]))
        if modality == "video" and support["execution"] == "video-frame":
            for role, model_id in step["bindings"].items():
                slot = role
                if role == "upscaler":
                    scale = capabilities.model_scale(model_id)
                    if scale not in {2, 4}:
                        raise ValueError(f"workflow upscaler model scale is not supported: {scale}")
                    slot = f"upscaler_x{scale}"
                if slot in slots and slots[slot] != model_id:
                    raise ValueError(f"video workflow model slot {slot} has conflicting bindings: {slots[slot]} and {model_id}")
                slots[slot] = model_id
    _shared_tile(steps, payload)
    return steps


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def _suffix(steps: list[dict[str, Any]]) -> str:
    # 后缀归注册表管（clean / sr），不再在这里长第二个 if/elif
    return video_step.suffix_for_steps(str(step.get("capability") or "") for step in steps)


def _progress(request_id: str | None, fraction: float, current: str, completed: int, total: int) -> None:
    emit(
        "task.progress",
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


def _validate_models(steps: list[dict[str, Any]], payload: dict[str, Any] | None = None) -> None:
    missing: list[str] = []
    catalog = capabilities._catalog()
    required = {str(model_id) for step in steps for model_id in step["bindings"].values()}
    if any(step.get("capability") == "video_inpainting" for step in steps):
        mask = str((payload or {}).get("maskPath") or "").strip()
        if mask:
            path = Path(mask)
            if not path.exists():
                raise FileNotFoundError(f"mask not found: {path}")
            if path.is_dir() and not any(item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"} for item in path.iterdir()):
                raise ValueError("mask directory contains no image masks")
        else:
            required.add(capabilities.default_bindings("watermark_removal")["detector"])
    for model_id in sorted(required):
        path = capabilities.model_path(model_id)
        files = (catalog.get(model_id) or {}).get("files") or []
        expected = [path / item["path"] for item in files] if files else [path]
        for file in expected:
            if not file.is_file():
                missing.append(f"{model_id} ({file})")
    if missing:
        raise FileNotFoundError("missing models: " + ", ".join(missing))


def _run_image(
    source: Path,
    output_dir: Path,
    steps: list[dict[str, Any]],
    request_id: str | None,
    index: int,
    total: int,
    *,
    device: str | None = None,
    tile: int = 512,
) -> dict[str, Any]:
    # 设备与分块由调用方传入：`_run_image` 没有 payload 作用域（这个坑真实调用才暴露）
    # ImageRestorer accepts resolved tuple targets, while the public schema uses
    # names/custom dimensions. Do not mutate the original serialized request.
    steps = [{**step, "params": dict(step["params"])} for step in steps]
    for step in steps:
        if step["capability"] == "upscale":
            params = step["params"]
            params["target"] = image_ops.resolve_target(str(params.get("target") or "none"), int(params.get("width") or 0), int(params.get("height") or 0))
    restorer = image_ops.ImageRestorer(device=device, tile=tile)
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


def _run_video_sequence(
    source: Path,
    output_dir: Path,
    steps: list[dict[str, Any]],
    payload: dict[str, Any],
    request_id: str | None,
    index: int,
    total: int,
) -> dict[str, Any]:
    """整段序列能力的单步工作流入口，与逐帧 FrameStep 管线分开。"""
    capability = str(steps[0].get("capability") or "")
    params = steps[0].get("params") if isinstance(steps[0].get("params"), dict) else {}
    bindings = steps[0].get("bindings") if isinstance(steps[0].get("bindings"), dict) else {}

    def report(fraction: float, message: str) -> None:
        base = (index + max(0.0, min(1.0, fraction))) / max(total, 1)
        _progress(request_id, base, f"{message} {source.name}", index, total)

    if capability == "video_super_resolution":
        result = video_ops.upscale_video(
            capabilities.model_path(str(bindings["upscaler"])),
            source,
            output_dir,
            preview_seconds=float(payload.get("previewSeconds") or 0),
            crf=int(task_params.value(payload, "crf", 18)),
            preset=str(payload.get("preset") or "fast"),
            progress=report,
        )
    elif capability == "video_inpainting":
        mask_path = str(payload.get("maskPath") or "").strip() or None
        detector_path = None
        if mask_path is None:
            detector_path = capabilities.model_path(capabilities.default_bindings("watermark_removal")["detector"])
        result = video_ops.inpaint_video(
            capabilities.model_path(str(bindings["primary"])),
            source,
            output_dir,
            mask_path=mask_path,
            detector_path=detector_path,
            conf=float(task_params.value(params, "conf", task_params.value(payload, "conf", 0.25))),
            expand=int(task_params.value(params, "expand", task_params.value(payload, "expand", 8))),
            detect_mode=str(task_params.value(params, "detectMode", task_params.value(payload, "detectMode", "fixed"))),
            subvideo_length=int(task_params.value(params, "subvideoLength", task_params.value(payload, "subvideoLength", 80))),
            preview_seconds=float(payload.get("previewSeconds") or 0),
            crf=int(task_params.value(payload, "crf", 18)),
            preset=str(payload.get("preset") or "fast"),
            progress=report,
        )
    else:
        raise ValueError(f"unsupported sequence workflow capability: {capability}")
    return {
        "path": result["path"],
        "source": str(source),
        "mode": "workflow",
        "steps": [capability],
        "info": result,
        "size": result["size"],
    }


def _run_video(
    source: Path,
    output_dir: Path,
    steps: list[dict[str, Any]],
    payload: dict[str, Any],
    request_id: str | None,
    index: int,
    total: int,
) -> dict[str, Any]:
    """把工作流所有步骤压进**一次**解码 + **一次**编码。

    2026-10-29 改：原来每步调一次 `process_video`，N 步 = N 次 H.264 编解码
    （代际损失 × N）、每步还新建一个 `ImageRestorer` 把模型重新加载一遍。
    现在步骤被翻译成 `FrameStep`，一帧跑完所有步骤再进编码器。
    """
    sequence = [str(step.get("capability") or "") for step in steps if str(step.get("capability") or "") in video_step.SEQUENCE_CAPABILITIES]
    if sequence:
        if len(steps) != 1:
            raise ValueError("sequence video capabilities currently require a single-step workflow")
        return _run_video_sequence(source, output_dir, steps, payload, request_id, index, total)

    merged_paths: dict[str, str] = {}
    frame_steps: list[video_ops.FrameStep] = []
    step_meta: list[dict[str, Any]] = []
    for step in steps:
        capability = str(step.get("capability") or "")
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        bindings = step.get("bindings") if isinstance(step.get("bindings"), dict) else {}
        # 能力分派归注册表（video_step），这里只负责把每一步翻成逐帧操作
        step_plan = video_step.plan(
            video_step.StepInputs(capability=capability, params=params, bindings=bindings, payload=payload)
        )
        merged_paths.update(step_plan.model_paths)
        target = image_ops.resolve_target(
            str(params.get("target") or "none"),
            int(task_params.value(params, "width", task_params.value(payload, "width", 0))),
            int(task_params.value(params, "height", task_params.value(payload, "height", 0))),
        )
        frame_steps.append(
            video_ops.FrameStep(
                mode=step_plan.mode,
                scale=step_plan.scale,
                target=target,
                fit=str(task_params.value(params, "fit", task_params.value(payload, "fit", "contain"))),
                conf=step_plan.conf,
                expand=step_plan.expand,
                detect_mode=step_plan.detect_mode,
                preferred_scale=step_plan.preferred_scale,
            )
        )
        step_meta.append({"capability": capability, "model": dict(bindings)})

    # 模型只加载一次：把各步需要的权重合并到一个 ImageRestorer
    restorer = image_ops.ImageRestorer(device=str(payload.get("device") or "") or None, model_paths=merged_paths, tile=_shared_tile(steps, payload))

    def report(fraction: float, message: str) -> None:
        base = (index + max(0.0, min(1.0, fraction))) / max(total, 1)
        _progress(request_id, base, f"{message} {source.name}", index, total)

    result = video_ops.process_video_steps(
        source,
        output_dir,
        steps=frame_steps,
        suffix=_suffix(steps),
        preview_seconds=float(payload.get("previewSeconds") or 0),
        crf=int(task_params.value(payload, "crf", 18)),
        preset=str(payload.get("preset") or "fast"),
        restorer=restorer,
        progress=report,
    )
    info = result.get("info") or {}
    step_infos = [
        {**meta, "info": detail}
        for meta, detail in zip(step_meta, info.get("steps") or [])
    ]
    return {
        "path": result["path"],
        "source": str(source),
        "mode": "workflow",
        "steps": [step["capability"] for step in steps],
        "info": {
            "device": restorer.device,
            "workflow_steps": step_infos,
            # 单次编解码：耗时/帧数/尺寸都是整条工作流的，不再按步累加
            "elapsed": info.get("elapsed"),
            "frames": info.get("frames"),
            "input_size": info.get("input_size"),
            "output_size": info.get("output_size"),
            "output_path": result["path"],
        },
        "size": result["size"],
    }


def cmd_media_workflow(payload: dict[str, Any]) -> int:
    request_id = _request_id(payload)
    try:
        raw_inputs = payload.get("inputs")
        if not isinstance(raw_inputs, list) or not raw_inputs or any(not isinstance(item, str) or not item.strip() for item in raw_inputs):
            return emit_error("MEDIA_WORKFLOW_INVALID", "No input files", request_id=request_id)
        inputs = [Path(item) for item in raw_inputs]
        output_text = str(payload.get("outputDir") or "").strip()
        if not output_text:
            return emit_error("MEDIA_WORKFLOW_INVALID", "No output directory", request_id=request_id)
        output_dir = Path(output_text)
        modality = str(payload.get("modality") or "").strip().lower()
        if modality not in {"image", "video"}:
            return emit_error("MEDIA_WORKFLOW_INVALID", "modality must be image or video", request_id=request_id)
        steps = validate_workflow(payload)
        for source in inputs:
            if not source.is_file():
                raise FileNotFoundError(f"input not found: {source}")
            if source.stat().st_size == 0:
                raise ValueError(f"input is empty: {source}")
        model_dir = str(payload.get("modelDir") or "").strip()
        if model_dir:
            worker_paths.export_model_root(model_dir)
        _validate_models(steps, payload)
        output_dir.mkdir(parents=True, exist_ok=True)

        started = time.perf_counter()
        total = len(inputs)
        outputs: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for index, source in enumerate(inputs):
            try:
                if not source.is_file():
                    raise FileNotFoundError(f"input not found: {source}")
                if modality == "image":
                    outputs.append(
                        _run_image(
                            source,
                            output_dir,
                            steps,
                            request_id,
                            index,
                            total,
                            device=str(payload.get("device") or "") or None,
                            tile=_shared_tile(steps, payload),
                        )
                    )
                else:
                    outputs.append(_run_video(source, output_dir, steps, payload, request_id, index, total))
            except Exception as exc:  # noqa: BLE001
                failed.append(failures.describe(source, exc))

        evaluation.attach(outputs)
        emit(
            "task.result",
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
