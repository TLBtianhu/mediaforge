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

from media_ops.slots import ARCH_SLOT_OVERRIDES, FORBIDDEN_INTERFACE_KEYS, SLOT_DEFAULTS, VR_ONLY_SLOTS

from . import registry

#: 每个槽位的默认绑定与范围。值只是"起点"，架构模板可以覆盖。
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


def vocabulary() -> dict[str, dict[str, Any]]:
    """槽位词汇表（对外入口）：数据只有一份，在 media_ops.slots；这里只做转发。"""
    from media_ops.slots import vocabulary as _vocabulary

    return _vocabulary()
