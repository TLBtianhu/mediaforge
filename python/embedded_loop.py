"""内嵌 agent 的最小闭环：计划 → 审批 → 执行 → 自评价 → 收敛判断。

它**不调用大模型**。模型负责"想做什么"，这里负责"把想做的做完并确认做对了"，
所以它是确定性的、可测试的。外部 agent 走 MCP 时用的是同一批执行器
（`agent_contract.dispatch`），区别只是没有进程边界。

四道闸门：

1. **计划**：从目标推出工具与步骤（能力 + 模型 + 参数），不做开放推理
2. **审批**：契约里的 `approval` 决定能不能直接跑；`explicit` 必须由调用方显式放行
3. **执行**：短暂任务 `dispatch`（自带失败纠正）；长任务拒绝并交回作业台账
4. **收敛**：产物做客观评价，给出 `ok` 与"为什么不行"，不猜也不假装成功
"""
from __future__ import annotations

from typing import Any, Callable

import agent_contract
import media_eval

MAX_STEPS = 6

#: 目标 kind → 媒体能力
CAPABILITY_FOR_KIND = {
    "upscale": "upscale",
    "watermark_removal": "watermark_removal",
    "face_restoration": "face_restoration",
}


def plan_for(goal: dict[str, Any]) -> dict[str, Any]:
    """把目标翻成一次工具调用。"""
    if "tool" in goal:
        return {"tool": str(goal["tool"]), "arguments": dict(goal.get("arguments") or {})}

    kind = str(goal.get("kind") or "").strip()
    capability = CAPABILITY_FOR_KIND.get(kind)
    if capability is None:
        raise ValueError(f"unsupported goal kind: {kind or '(missing)'}")
    modality = str(goal.get("modality") or "image").strip().lower()
    if modality not in {"image", "video"}:
        raise ValueError(f"unsupported modality: {modality}")
    inputs = [str(value) for value in goal.get("inputs") or [] if str(value).strip()]
    if not inputs:
        raise ValueError("goal needs at least one input")
    output_dir = str(goal.get("outputDir") or "").strip()
    if not output_dir:
        raise ValueError("goal needs an outputDir")

    bindings = dict(goal.get("bindings") or {})
    if not bindings:
        bindings = agent_contract_bindings(capability)
    step: dict[str, Any] = {"capability": capability, "bindings": bindings}
    if goal.get("params"):
        step["params"] = dict(goal["params"])
    arguments: dict[str, Any] = {
        "modality": modality,
        "inputs": inputs,
        "outputDir": output_dir,
        "steps": [step],
    }
    if goal.get("modelDir"):
        arguments["modelDir"] = str(goal["modelDir"])
    return {"tool": f"media.{modality}.{capability}", "arguments": arguments}


def agent_contract_bindings(capability: str) -> dict[str, str]:
    """能力的默认绑定（唯一真源在 capabilities.json）。"""
    from media_ops import capabilities

    return capabilities.default_bindings(capability)


def approval_for(tool: str) -> str:
    return str(agent_contract.find_tool(tool).get("approval") or "confirm")


def _media_paths(result: dict[str, Any]) -> list[dict[str, Any]]:
    payload = (result.get("result") or {}).get("payload") or {}
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), list) else []
    return [item for item in outputs if isinstance(item, dict) and item.get("path")]


def _verdict(goal_inputs: list[str], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """收敛判断：产物在不在、有没有真的变化、有没有变糊。"""
    if not outputs:
        return {"ok": False, "reasons": ["没有产物"], "retryable": False}
    reference = goal_inputs[0] if goal_inputs else ""
    evaluations: list[dict[str, Any]] = []
    reasons: list[str] = []
    ok = True
    for item in outputs:
        path = str(item.get("path") or "")
        produced = media_eval.evaluate_file(path)
        entry = {"path": path, "kind": produced.get("kind"), "metrics": produced.get("metrics")}
        if produced.get("error"):
            ok = False
            reasons.append(f"{path}: {produced['error']}")
            entry["notes"] = []
            evaluations.append(entry)
            continue
        notes = media_eval.compare(produced, media_eval.evaluate_file(reference)) if reference else []
        entry["notes"] = notes
        evaluations.append(entry)
        if any("字节完全相同" in note for note in notes):
            ok = False
            reasons.append("产物与输入完全相同，可能没有真正处理")
        if any("变糊" in note for note in notes):
            ok = False
            reasons.append("产物比输入更模糊")
    return {"ok": ok, "reasons": reasons, "retryable": not ok, "evaluations": evaluations}


def run(
    goal: dict[str, Any],
    *,
    allow_explicit: bool = False,
    dispatch: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行一个目标并判断是否做成。返回结构化结果，不抛业务异常。"""
    dispatch = dispatch or agent_contract.dispatch
    plan = plan_for(goal)
    tool = plan["tool"]
    arguments = plan["arguments"]

    approval = approval_for(tool)
    if approval == "explicit" and not allow_explicit:
        return {
            "ok": False,
            "tool": tool,
            "blocked": "approval_required",
            "approval": approval,
            "summary": f"{tool} 需要显式同意（{approval}）才能执行",
            "steps": [],
        }
    tool_info = agent_contract.find_tool(tool)
    if tool_info.get("longRunning"):
        return {
            "ok": False,
            "tool": tool,
            "blocked": "long_running",
            "summary": f"{tool} 是长任务，请走作业台账（pipeline_start / job_retry）",
            "steps": [],
        }

    try:
        outcome = dispatch(tool, arguments)
    except Exception as exc:  # noqa: BLE001 - 执行器异常也要变成可判断的结果
        return {"ok": False, "tool": tool, "blocked": "exception", "summary": str(exc), "steps": []}

    step = {
        "tool": tool,
        "ok": bool(outcome.get("ok")),
        "attempts": outcome.get("recoveryAttempts") or [],
        "retryBlocked": outcome.get("retryBlocked"),
    }
    if not step["ok"]:
        payload = (outcome.get("result") or {}).get("payload") or {}
        return {
            "ok": False,
            "tool": tool,
            "blocked": "execution_failed",
            "summary": str(payload.get("message") or "执行失败"),
            "recovery": payload.get("recovery"),
            "steps": [step],
        }

    outputs = _media_paths(outcome)
    verdict = _verdict([str(value) for value in arguments.get("inputs") or []], outputs)
    return {
        "ok": verdict["ok"],
        "tool": tool,
        "steps": [step],
        "outputs": [item.get("path") for item in outputs],
        "evaluations": verdict.get("evaluations") or [],
        "reasons": verdict.get("reasons") or [],
        "summary": "完成" if verdict["ok"] else "; ".join(verdict.get("reasons") or ["未通过自评价"]),
    }


def _main() -> int:
    import argparse
    import json
    from pathlib import Path as _Path

    parser = argparse.ArgumentParser(description="Run one goal through the embedded loop.")
    parser.add_argument("--payload", required=True, help="Path to a JSON goal file")
    parser.add_argument("--allow-explicit", action="store_true", help="Permit tools that need explicit approval")
    args = parser.parse_args()
    goal = json.loads(_Path(args.payload).read_text(encoding="utf-8-sig"))
    result = run(goal, allow_explicit=args.allow_explicit)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
