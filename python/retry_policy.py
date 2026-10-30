"""自动纠正：把恢复建议变成"改参数再试一次"，但有预算。

为什么需要预算：一个 agent 拿到"显存不足 → 降 tile"如果无限重试，会卡在同一个
错误上烧时间；而完全不重试又浪费了明明可救的任务。所以：

* **只自动做确定安全的动作**：`reduce_param`（改步骤参数）、`switch_device`（换设备）
* **需要用户同意的动作不自动做**：`download_model`、`install_tool`、`free_space`…
  遇到就停下，把建议原样交回去
* **预算有限**：默认最多 1 次自动纠正，可配置；用尽后返回最后一次结果并说明原因
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any

#: 自动执行的动作（其余动作只建议，不动手）
AUTO_APPLICABLE = {"reduce_param", "switch_device"}

DEFAULT_BUDGET = 1


def budget_from_env(default: int = DEFAULT_BUDGET) -> int:
    raw = os.environ.get("MEDIAFORGE_RETRY_BUDGET", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, min(value, 5))


@dataclass
class RetryBudget:
    remaining: int = DEFAULT_BUDGET
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def spend(self, hint: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        self.remaining -= 1
        self.attempts.append(
            {
                "code": hint.get("code"),
                "applied": [action.get("action") for action in actions],
            }
        )


def applicable_actions(hint: dict[str, Any] | None) -> list[dict[str, Any]]:
    """只挑出可以自动执行、且知道怎么执行的动作。"""
    actions: list[dict[str, Any]] = []
    for action in (hint or {}).get("actions") or []:
        if action.get("action") not in AUTO_APPLICABLE:
            continue
        if action.get("action") == "reduce_param" and not action.get("slot"):
            continue
        actions.append(dict(action))
    return actions


def adjust_arguments(arguments: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按动作调整调用参数；调整不出实际变化时返回 None（别做无意义的重试）。"""
    # 深拷贝：job_retry 传入的是台账里存的载荷，改它就是改历史
    adjusted = copy.deepcopy(arguments or {})
    changed = False
    steps = adjusted.get("steps")
    for action in actions:
        if action.get("action") == "reduce_param":
            slot = str(action.get("slot") or "")
            value = action.get("to")
            if not slot or value is None:
                continue
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    params = step.get("params")
                    if not isinstance(params, dict):
                        continue
                    if slot in params or slot in _declared_params(step):
                        if params.get(slot) != value:
                            params[slot] = value
                            changed = True
            if slot in adjusted and adjusted[slot] != value:
                adjusted[slot] = value
                changed = True
        elif action.get("action") == "switch_device":
            device = str(action.get("to") or "")
            if device and adjusted.get("device") != device:
                adjusted["device"] = device
                changed = True
    return adjusted if changed else None


def _declared_params(step: dict[str, Any]) -> set[str]:
    try:
        from media_ops import capabilities

        capability = capabilities.capability(str(step.get("capability") or step.get("type") or ""))
        return set((capability.get("params") or {}).keys())
    except Exception:  # noqa: BLE001
        return set()


def recovery_from_result(result: Any) -> dict[str, Any] | None:
    """从终止载荷里找出可用的恢复建议（错误事件或 failed[] 条目）。"""
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("recovery"), dict):
        return result["recovery"]
    failed = result.get("failed")
    if isinstance(failed, list):
        for entry in failed:
            if isinstance(entry, dict) and isinstance(entry.get("recovery"), dict):
                return entry["recovery"]
    return None


def retry_blocked_reason(remaining: int, completed: int, hint: dict[str, Any] | None) -> str | None:
    """不能自动重试时说明原因——别让调用方以为系统漏了。"""
    if not hint:
        return "no recovery hint"
    if not hint.get("retryable"):
        return "failure is not retryable"
    if completed > 0:
        return "partial success; not retrying the whole batch"
    if remaining <= 0:
        return "retry budget exhausted"
    if not applicable_actions(hint):
        return "no automatically applicable action"
    return None


def plan_retry(
    payload: dict[str, Any] | None,
    result: Any,
    *,
    attempts_used: int = 0,
    budget: int | None = None,
) -> dict[str, Any]:
    """长任务（作业台账）的重试计划。

    异步任务不能在提交时重试——所以要能**事后**判断：这次失败能不能再试、
    该改成什么参数。返回 `{"ok": True, "payload": {...}, "actions": [...]}`，
    或者 `{"ok": False, "error": 原因, "hint": ...}`。

    这里只做计划，不动手：真正提交由调用方（bridge.job_retry / 内嵌 loop）决定。
    """
    hint = recovery_from_result(result)
    if not hint:
        return {"ok": False, "error": "no recovery hint"}
    if not hint.get("retryable"):
        return {"ok": False, "error": "failure is not retryable", "hint": hint}
    remaining = (budget_from_env() if budget is None else budget) - attempts_used
    if remaining <= 0:
        return {"ok": False, "error": "retry budget exhausted", "hint": hint}
    actions = applicable_actions(hint)
    if not actions:
        return {"ok": False, "error": "no automatically applicable action", "hint": hint}
    adjusted = adjust_arguments(payload or {}, actions)
    if adjusted is None:
        return {"ok": False, "error": "adjustment produced no change", "hint": hint}
    return {
        "ok": True,
        "payload": adjusted,
        "actions": [action.get("action") for action in actions],
        "hint": hint,
    }
