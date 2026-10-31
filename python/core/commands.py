"""命令层的小包装：把原本写在 worker if 链里的逻辑落成可被协议调用的函数。

这些命令的共同点是"先算一个结果，再用 emit 发出去"，没有独立的业务模块可去。
放在 core 而不是 surfaces，是因为它们属于状态/数据查询，不是协议翻译。
"""
from __future__ import annotations

from typing import Any

from shared.worker_protocol import emit


def cmd_media_capabilities(payload: dict[str, Any]) -> int:
    from media_ops.capabilities import list_capabilities

    emit("media_capabilities_result", list_capabilities(payload.get("modelDir")))
    return 0


def cmd_media_precheck(payload: dict[str, Any]) -> int:
    from media_ops.precheck import precheck

    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    inputs = [str(value) for value in payload.get("inputs") or [] if str(value).strip()]
    check = precheck(steps, inputs)
    # 顶层 ok 表达"计划可行吗"，而不是"命令跑成功了吗"
    emit(
        "media_precheck_result",
        {"ok": (check or {}).get("ok", True), "steps": steps, "inputs": inputs, "precheck": check},
    )
    return 0


def cmd_failure_stats(payload: dict[str, Any]) -> int:
    from core.failure_stats import collect

    emit("failure_stats", collect())
    return 0


def cmd_audio_tools_entry(payload: dict[str, Any]) -> int:
    """ASR 走可选组件，先激活再进音频工具。"""
    if str(payload.get("operation") or "").strip().lower() == "asr":
        from core.worker_optional_packages import activate_optional_packages

        activate_optional_packages()
    from core.worker_tools import cmd_audio_tools

    return cmd_audio_tools(payload)
