"""产物自评价的应用层入口：批量评估与 worker 命令。

纯度量（尺寸/锐度/裁切比例等）在 `media_ops.evaluation`；这里只负责把它们
组织成 agent 可消费的形状，并把结果通过 worker 协议发出去。
"""
from __future__ import annotations

from typing import Any

from media_ops.evaluation import _compare, compare, evaluate_file  # noqa: F401  兼容旧引用

def evaluate(inputs: list[str], reference: str | None = None) -> dict[str, Any]:
    """批量评估；给了 reference 就做对比。返回值可直接塞进 agent 的观察里。"""
    reference_result = evaluate_file(reference) if reference else None
    results = []
    for path in inputs:
        produced = evaluate_file(path)
        item = dict(produced)
        if reference_result:
            item["notes"] = _compare(produced, reference_result)
        results.append(item)
    payload: dict[str, Any] = {"evaluations": results}
    if reference_result:
        payload["reference"] = reference_result
    return payload


def cmd_evaluate_media(payload: dict[str, Any]) -> int:
    from worker_protocol import emit, emit_error

    inputs = [str(value) for value in payload.get("inputs") or [] if str(value).strip()]
    if not inputs:
        return emit_error("EVALUATE_INVALID", "inputs must contain at least one path")
    reference = str(payload.get("reference") or "").strip() or None
    try:
        emit("media_evaluation", evaluate(inputs, reference))
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("EVALUATE_FAILED", str(exc))
