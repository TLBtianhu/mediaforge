"""模型接口：从登记表 + 架构模板生成"这个模型支持哪些槽位"。

接口只回答三件事：

    slot   → 映射到模型的原生键（`bind`）
    范围   → min / max / step / default（可被架构模板覆盖）
    约束   → 派生约束名（如 chunk_size 必须满足 (chunk - offset) % step == 0）

**它不回答"放在哪里"。** 布局在 `src/litegraph/layouts.ts` 的布局档里，
由前端 `buildRenderPlan()` 把"接口 ∩ 布局"合成界面。于是：

* 换模型不改 UI（接口只声明槽位）
* 调布局不改模型（布局档独立）
* 模型多出来的槽位不会消失（前端按词汇表兜底进自动分组）

槽位词汇表（kind / unit / help / diagram）归前端所有：`src/litegraph/slots.ts`。
两边的一致性由 `tests/python/test_model_interface.py` 双向检查。
"""
from __future__ import annotations

from typing import Any

from . import registry

#: 每个槽位的默认绑定与范围。值只是"起点"，架构模板可以覆盖。
SLOT_DEFAULTS: dict[str, dict[str, Any]] = {
    # 音频
    "chunk_size": {"bind": "chunk_size", "min": 64, "max": 8192, "step": 64, "default": 1024,
                   "derive": "chunk_size_constraint"},
    "overlap": {"bind": "overlap", "min": 0, "max": 8, "step": 1, "default": 2},
    "stems": {"bind": "stems", "default": "vocals"},
    # 图像 / 视频
    "scale": {"bind": "scale", "min": 1, "max": 4, "step": 1, "default": 2},
    "tile": {"bind": "tile", "min": 0, "max": 1024, "step": 32, "default": 0},
    "denoise_strength": {"bind": "denoise_strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "strength": {"bind": "strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "fidelity": {"bind": "fidelity", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.7},
    "expand": {"bind": "expand", "min": 0, "max": 64, "step": 1, "default": 10},
    "steps": {"bind": "steps", "min": 1, "max": 100, "step": 1, "default": 20},
    "upscale": {"bind": "upscale", "min": 1, "max": 4, "step": 1, "default": 2},
    "threshold": {"bind": "threshold", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5},
    "conf": {"bind": "conf", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.25},
    "iou": {"bind": "iou", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.45},
    "multiplier": {"bind": "multiplier", "min": 2, "max": 8, "step": 1, "default": 2},
    "scene_cut": {"bind": "scene_cut", "default": True},
    "neighbor_frames": {"bind": "neighbor_frames", "min": 2, "max": 20, "step": 1, "default": 10},
    "fp16": {"bind": "fp16", "default": True},
}

#: 按架构覆盖默认值。单位是"架构"，不是模型——326 个音频模型只有 12 种架构。
ARCH_SLOT_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "mel_band_roformer": {
        # 分块必须与 STFT 步长对齐，所以步长比通用值大
        "chunk_size": {"step": 128, "default": 512},
    },
    "bs_roformer": {
        "chunk_size": {"step": 256, "default": 1024},
    },
    "vr": {
        "chunk_size": {"min": 512, "max": 16384, "step": 512, "default": 4096},
    },
    "demucs": {
        # demucs 不走分块推理，没有 chunk_size
        "chunk_size": None,
    },
    "htdemucs": {"chunk_size": None},
    "mdx23c": {
        "chunk_size": {"step": 128, "default": 1024},
    },
}

FORBIDDEN_INTERFACE_KEYS = {"order", "group", "section", "sections", "width", "row", "column", "collapsed", "index"}


def _task_slots(task: str) -> list[str]:
    spec = registry.tasks().get(task) or {}
    return list(spec.get("slots") or [])


def interface_for(model_id: str) -> dict[str, Any] | None:
    """为一个模型生成接口。模型不存在时返回 None（而不是抛异常）。"""
    model = registry.find(model_id)
    if model is None:
        return None
    task = model["task"]
    slots: dict[str, Any] = {}
    for slot in _task_slots(task):
        default = SLOT_DEFAULTS.get(slot)
        if default is None:
            continue
        slots[slot] = dict(default)
    overrides = ARCH_SLOT_OVERRIDES.get(str(model.get("architecture") or ""), {})
    for slot, patch in overrides.items():
        if slot not in slots:
            continue
        if patch is None:
            slots.pop(slot, None)
        else:
            slots[slot].update(patch)
    return {"modelId": model_id, "task": task, "slots": slots}


def validate_interface(interface: dict[str, Any]) -> list[str]:
    """结构守卫：接口里出现布局字段就判违规（这条边界靠测试守住）。"""
    problems: list[str] = []
    model_id = interface.get("modelId")
    if not model_id:
        problems.append("interface without modelId")
    for slot, binding in (interface.get("slots") or {}).items():
        if slot not in SLOT_DEFAULTS:
            problems.append(f"{model_id}: unknown slot {slot}")
        if not isinstance(binding, dict):
            problems.append(f"{model_id}: slot {slot} must be an object")
            continue
        if not binding.get("bind"):
            problems.append(f"{model_id}: slot {slot} needs a bind target")
        for key in binding:
            if key in FORBIDDEN_INTERFACE_KEYS:
                problems.append(f"{model_id}: slot {slot} carries layout key {key}")
            elif key not in {"bind", "min", "max", "step", "default", "derive", "choices", "kind"}:
                problems.append(f"{model_id}: slot {slot} has unknown key {key}")
    return problems
