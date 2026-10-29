"""视频工作流每一步的"计划"：capability -> process_video 需要的参数。

为什么单独一层（2026-10-29）：
`workflow_worker._run_video` 原来用 `if capability == ...` 的分派，加一个能力要动
`workflow_worker` + `image_ops` + `capabilities` + 前端 + MCP schema。这一步先把**最容易
腐烂的那一处**（按名字分派）收成注册表：新能力 = 注册一个 planner。能力表本身搬进 JSON
是另一件事（见 memory.md §15.5），这里不越界。

planner 是纯计算：读 bindings/params/payload，查 catalog 拿倍率和模型路径，不碰模型、
不碰文件、不调 torch —— 所以可以在任何解释器里被测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import capabilities


@dataclass(frozen=True)
class StepInputs:
    """一步的全部输入：这一步做什么（capability）、怎么配（params）、用哪套权重（bindings）。"""

    capability: str
    params: dict[str, Any]
    bindings: dict[str, Any]
    payload: dict[str, Any]


@dataclass(frozen=True)
class StepPlan:
    """交给 `video_ops.process_video` 的那几个参数。

    注意 `scale` 与 `preferred_scale` 是两件事：
    - `scale` 是**用户要的**倍率（可能被 target 分辨率覆盖）；
    - `preferred_scale` 是**加载哪个权重文件**（2× 还是 4×），由 catalog 声明决定。
    """

    mode: str
    scale: float
    detect_mode: str
    conf: float
    expand: int
    preferred_scale: int | None
    model_paths: dict[str, str]
    suffix: str


_PLANNERS: dict[str, Callable[[StepInputs], StepPlan]] = {}
_SUFFIXES: dict[str, str] = {}


def register(capability_id: str, suffix: str) -> Callable[[Callable[[StepInputs], StepPlan]], Callable[[StepInputs], StepPlan]]:
    """注册一个能力在视频工作流里的计划器。suffix 用于输出文件名（clean / sr …）。"""

    def decorate(function: Callable[[StepInputs], StepPlan]) -> Callable[[StepInputs], StepPlan]:
        _PLANNERS[capability_id] = function
        _SUFFIXES[capability_id] = suffix
        return function

    return decorate


def registered() -> tuple[str, ...]:
    return tuple(sorted(_PLANNERS))


def plan(inputs: StepInputs) -> StepPlan:
    planner = _PLANNERS.get(inputs.capability)
    if planner is None:
        raise ValueError(
            f"unsupported video workflow capability: {inputs.capability}"
            f"（已注册：{', '.join(registered()) or '无'}）"
        )
    return planner(inputs)


def suffix_for(capability_id: str) -> str:
    return _SUFFIXES.get(capability_id, capability_id or "step")


def suffix_for_steps(capability_ids: Iterable[str]) -> str:
    return "_".join(suffix_for(str(item)) for item in capability_ids) or "workflow"


@register("watermark_removal", "clean")
def _plan_watermark_removal(inputs: StepInputs) -> StepPlan:
    params = inputs.params
    payload = inputs.payload
    return StepPlan(
        mode="watermark",
        # 去水印本身不改分辨率；要放大是后面另一步的事
        scale=1.0,
        detect_mode=str(params.get("detectMode") or payload.get("detectMode") or "fixed"),
        conf=float(params.get("conf") or payload.get("conf") or 0.25),
        expand=int(params.get("expand") or payload.get("expand") or 8),
        preferred_scale=None,
        model_paths={
            "detector": str(capabilities.model_path(str(inputs.bindings["detector"]))),
            "inpainter": str(capabilities.model_path(str(inputs.bindings["inpainter"]))),
        },
        suffix="clean",
    )


@register("upscale", "sr")
def _plan_upscale(inputs: StepInputs) -> StepPlan:
    upscaler = str(inputs.bindings["upscaler"])
    model_scale = capabilities.model_scale(upscaler)
    return StepPlan(
        mode="upscale",
        scale=float(inputs.params.get("scale") or inputs.payload.get("scale") or 2.0),
        detect_mode="fixed",
        conf=0.25,
        expand=8,
        preferred_scale=model_scale,
        model_paths={("upscaler_x4" if model_scale == 4 else "upscaler_x2"): str(capabilities.model_path(upscaler))},
        suffix="sr",
    )
