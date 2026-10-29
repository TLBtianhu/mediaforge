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
import time
from pathlib import Path
from typing import Any

from worker_protocol import emit, emit_error

import worker_paths
from . import capabilities, evaluation, failures, image_ops, video_ops, video_step


def _request_id(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("requestId") or "").strip()
    return value or None


def _suffix(steps: list[dict[str, Any]]) -> str:
    # 后缀归注册表管（clean / sr），不再在这里长第二个 if/elif
    return video_step.suffix_for_steps(str(step.get("capability") or "") for step in steps)


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
    *,
    device: str | None = None,
    tile: int = 512,
) -> dict[str, Any]:
    # 设备与分块由调用方传入：`_run_image` 没有 payload 作用域（这个坑真实调用才暴露）
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
            int(params.get("width") or payload.get("width") or 0),
            int(params.get("height") or payload.get("height") or 0),
        )
        frame_steps.append(
            video_ops.FrameStep(
                mode=step_plan.mode,
                scale=step_plan.scale,
                target=target,
                fit=str(params.get("fit") or payload.get("fit") or "contain"),
                conf=step_plan.conf,
                expand=step_plan.expand,
                detect_mode=step_plan.detect_mode,
                preferred_scale=step_plan.preferred_scale,
            )
        )
        step_meta.append({"capability": capability, "model": dict(bindings)})

    # 模型只加载一次：把各步需要的权重合并到一个 ImageRestorer
    restorer = image_ops.ImageRestorer(device=str(payload.get("device") or "") or None, model_paths=merged_paths)

    def report(fraction: float, message: str) -> None:
        base = (index + max(0.0, min(1.0, fraction))) / max(total, 1)
        _progress(request_id, base, f"{message} {source.name}", index, total)

    result = video_ops.process_video_steps(
        source,
        output_dir,
        steps=frame_steps,
        suffix=_suffix(steps),
        preview_seconds=float(payload.get("previewSeconds") or 0),
        crf=int(payload.get("crf") or 18),
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
            worker_paths.export_model_root(model_dir)
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
                    outputs.append(
                        _run_image(
                            source,
                            output_dir,
                            steps,
                            request_id,
                            index,
                            total,
                            device=str(payload.get("device") or "") or None,
                            tile=int(payload.get("tile") or 0) or 512,
                        )
                    )
                else:
                    outputs.append(_run_video(source, output_dir, steps, payload, request_id, index, total))
            except Exception as exc:  # noqa: BLE001
                failed.append(failures.describe(source, exc))

        evaluation.attach(outputs)
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
