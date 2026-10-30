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
    "chunk_size": {"kind": "int", "bind": "chunk_size", "min": 64, "max": 8192, "step": 64, "default": 1024,
                   "derive": "chunk_size_constraint"},
    "overlap": {"kind": "int", "bind": "overlap", "min": 0, "max": 8, "step": 1, "default": 2},
    "stems": {"kind": "multi", "bind": "stems", "default": "vocals"},
    "batch_size": {"kind": "int", "bind": "batch_size", "min": 1, "max": 32, "step": 1, "default": 1},
    "overlap_count": {"kind": "int", "bind": "num_overlap", "min": 0, "max": 128, "step": 1, "default": 4},
    "window_size": {"kind": "int", "bind": "window_size", "min": 0, "max": 4096, "step": 64, "default": 512},
    "aggression": {"kind": "int", "bind": "aggression", "min": 0, "max": 100, "step": 1, "default": 5},
    "post_process": {"kind": "bool", "bind": "enable_post_process", "default": True},
    "high_end_process": {"kind": "bool", "bind": "high_end_process", "default": False},
    "post_process_threshold": {"kind": "float", "bind": "post_process_threshold", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.3},
    "standardize": {"kind": "bool", "bind": "standardize", "default": False},
    # 图像 / 视频
    "scale": {"kind": "int", "bind": "scale", "min": 1, "max": 4, "step": 1, "default": 2},
    "tile": {"kind": "int", "bind": "tile", "min": 0, "max": 1024, "step": 32, "default": 0},
    "denoise_strength": {"kind": "float", "bind": "denoise_strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "strength": {"kind": "float", "bind": "strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "fidelity": {"kind": "float", "bind": "fidelity", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.7},
    "expand": {"kind": "int", "bind": "expand", "min": 0, "max": 64, "step": 1, "default": 10},
    "steps": {"kind": "int", "bind": "steps", "min": 1, "max": 100, "step": 1, "default": 20},
    "upscale": {"kind": "int", "bind": "upscale", "min": 1, "max": 4, "step": 1, "default": 2},
    "threshold": {"kind": "float", "bind": "threshold", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5},
    "conf": {"kind": "float", "bind": "conf", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.25},
    "iou": {"kind": "float", "bind": "iou", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.45},
    "multiplier": {"kind": "enum", "bind": "multiplier", "min": 2, "max": 8, "step": 1, "default": 2},
    "scene_cut": {"kind": "bool", "bind": "scene_cut", "default": True},
    "neighbor_frames": {"kind": "int", "bind": "neighbor_frames", "min": 2, "max": 20, "step": 1, "default": 10},
    "fp16": {"kind": "bool", "bind": "fp16", "default": True},
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
        # demucs 不走分块推理，既没有 chunk_size 也没有 overlap 概念
        "chunk_size": None,
        "overlap": None,
        "overlap_count": None,
    },
    "htdemucs": {"chunk_size": None, "overlap": None, "overlap_count": None},
    "vr": {
        # VR 系用窗口 + 强度，而不是分块 + 重叠样本数
        "overlap": None,
        "standardize": None,
    },
    "apollo": {
        # Apollo 用重叠次数而不是重叠样本数
        "overlap": None,
    },
    "mdx23c": {
        "chunk_size": {"step": 128, "default": 1024},
    },
}

#: 只属于 VR / Apollo 的槽位：其它分离架构不该看到它们
VR_ONLY_SLOTS = ["window_size", "aggression", "post_process", "high_end_process", "post_process_threshold", "overlap_count"]

FORBIDDEN_INTERFACE_KEYS = {"order", "group", "section", "sections", "width", "row", "column", "collapsed", "index"}


def vocabulary() -> dict[str, dict[str, Any]]:
    """槽位词汇表（后端视角）：kind / 范围 / 默认 / 派生约束。

    前端 `src/litegraph/slots.ts` 是同一份词汇表的 UI 视角（unit/help/diagram），
    两边的 id 与 kind 由测试强制一致。
    """
    return {slot: dict(spec) for slot, spec in SLOT_DEFAULTS.items()}


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
    architecture = str(model.get("architecture") or "")
    if task == "separation":
        # 按架构裁剪：VR 有全套；Apollo 只借用后处理与重叠次数；其余没有这些概念
        if architecture == "vr":
            drop: list[str] = []
        elif architecture == "apollo":
            drop = ["window_size", "aggression"]
        else:
            drop = VR_ONLY_SLOTS
        for slot in drop:
            slots.pop(slot, None)
    overrides = ARCH_SLOT_OVERRIDES.get(architecture, {})
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
