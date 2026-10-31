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

from surfaces import agent_contract
from core import media_eval

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

    plan_steps = goal.get("steps")
    kind = str(goal.get("kind") or "").strip()
    capability = CAPABILITY_FOR_KIND.get(kind) if kind else None
    if capability is None and not (isinstance(plan_steps, list) and plan_steps):
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

    # 单能力与多能力的差别只在 steps 长度：worker 侧单步走 media_image/media_video，
    # 多步必须走 media_workflow（image_worker/video_worker 会拒绝多步）。
    plan_steps = goal.get("steps")
    if isinstance(plan_steps, list) and plan_steps:
        steps = [dict(step) for step in plan_steps if isinstance(step, dict)]
        for step in steps:
            if not step.get("capability"):
                raise ValueError("each step needs a capability")
            if not step.get("bindings"):
                step["bindings"] = agent_contract_bindings(str(step["capability"]))
        if not steps:
            raise ValueError("goal steps are empty")
    else:
        bindings = dict(goal.get("bindings") or {})
        if not bindings:
            bindings = agent_contract_bindings(capability)
        step = {"capability": capability, "bindings": bindings}
        if goal.get("params"):
            step["params"] = dict(goal["params"])
        steps = [step]

    arguments: dict[str, Any] = {
        "modality": modality,
        "inputs": inputs,
        "outputDir": output_dir,
        "steps": steps,
    }
    if goal.get("modelDir"):
        arguments["modelDir"] = str(goal["modelDir"])
    if len(steps) > 1:
        return {"tool": f"media.{modality}.workflow", "arguments": arguments}
    return {"tool": f"media.{modality}.{steps[0]['capability']}", "arguments": arguments}


def agent_contract_bindings(capability: str) -> dict[str, str]:
    """能力的默认绑定（唯一真源在 capabilities.json）。"""
    from media_ops import capabilities

    return capabilities.default_bindings(capability)


from media_ops.precheck import precheck  # noqa: F401  真实实现在 media_ops：worker 与 loop 共用同一份预检


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

    # 跑之前先看有没有"必然失败"的步骤（目前是干净图上的去水印）
    skipped: list[dict[str, Any]] = []
    check = precheck(list(arguments.get("steps") or []), [str(value) for value in arguments.get("inputs") or []])
    if check and not check.get("ok"):
        findings = check.get("findings") or []
        if check.get("blocking"):
            return {
                "ok": False,
                "tool": tool,
                "blocked": "precheck_failed",
                "summary": "; ".join(str(item.get("message")) for item in findings) or "预检未通过",
                "precheck": check,
                "steps": [],
            }
        droppable = {str(item.get("capability")) for item in findings if item.get("severity") == "skip"}
        remaining = [step for step in arguments["steps"] if str(step.get("capability")) not in droppable]
        if not remaining:
            return {
                "ok": False,
                "tool": tool,
                "blocked": "precheck_failed",
                "summary": "; ".join(str(item.get("message")) for item in findings) or "预检未通过",
                "precheck": check,
                "steps": [],
            }
        skipped.extend(findings)
        arguments = {**arguments, "steps": remaining}
        if len(remaining) == 1:
            tool = f"media.{arguments['modality']}.{remaining[0]['capability']}"
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
    terminal = outcome.get("result") or {}
    payload = terminal.get("payload") if isinstance(terminal.get("payload"), dict) else {}
    failed = payload.get("failed") if isinstance(payload.get("failed"), list) else []
    if not step["ok"]:
        first = failed[0] if failed and isinstance(failed[0], dict) else {}
        return {
            "ok": False,
            "tool": tool,
            "blocked": "execution_failed",
            "summary": str(first.get("error") or payload.get("message") or "执行失败"),
            "recovery": first.get("recovery") or payload.get("recovery"),
            "steps": [step],
        }

    outputs = _media_paths(outcome)
    verdict = _verdict([str(value) for value in arguments.get("inputs") or []], outputs)
    return {
        "ok": verdict["ok"],
        "tool": tool,
        "steps": [step],
        "skipped": skipped,
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
