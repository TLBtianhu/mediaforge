"""失败模式统计：把作业台账里的失败按"能不能救"归类。

为什么要它：我们知道有哪些恢复动作，但不知道该先修哪个。
这份统计回答三个问题：

* 失败里有多少是**可恢复**的（有 recovery 建议）
* 哪一类最常见（`out_of_memory` 还是 `missing_weights`）
* 哪条命令最容易失败（分发/媒体/音频）

数据来源就是作业台账本身：`<data_root>/agent-jobs/*.json`，
每个文件带 `command / status / error / result`，不额外埋点。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shared.recovery import classify

_ABSOLUTE_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"]+|/(?:[\w.\-]+/)+[\w.\-]+")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
_WHITESPACE = re.compile(r"\s+")


def default_root() -> Path:
    import os

    explicit = os.environ.get("MEDIAFORGE_DATA_DIR") or os.environ.get("MEDIAFORGE_DATA_ROOT")
    root = Path(explicit).expanduser() if explicit else Path.home() / ".mediaforge"
    return root


def normalize_message(message: str, limit: int = 120) -> str:
    """把路径与数字抹掉，让同类失败聚到一起。"""
    text = _ABSOLUTE_PATH.sub("<path>", str(message or ""))
    text = _NUMBER.sub("#", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:limit]


def _job_files(jobs_root: Path, limit: int) -> list[Path]:
    if not jobs_root.is_dir():
        return []
    files = [path for path in jobs_root.glob("*.json") if path.name != "index.json"]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit] if limit > 0 else files


def collect(root: Path | None = None, *, limit: int = 500) -> dict[str, Any]:
    jobs_root = Path(root or default_root()) / "agent-jobs"
    files = _job_files(jobs_root, limit)
    scanned = 0
    failed = 0
    recoverable = 0
    by_code: dict[str, int] = {}
    by_command: dict[str, int] = {}
    issues: dict[tuple[str, str], dict[str, Any]] = {}

    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        scanned += 1
        if str(record.get("status") or "") != "failed":
            continue
        failed += 1
        command = str(record.get("command") or "?")
        by_command[command] = by_command.get(command, 0) + 1
        message = str(record.get("error") or (record.get("result") or {}).get("message") or "unknown")
        hint = classify(message)
        code = str((hint or {}).get("code") or "unclassified")
        if hint and hint.get("retryable"):
            recoverable += 1
        by_code[code] = by_code.get(code, 0) + 1
        key = (code, normalize_message(message))
        entry = issues.setdefault(
            key,
            {
                "code": code,
                "message": key[1],
                "count": 0,
                "retryable": bool(hint and hint.get("retryable")),
                "actions": [action.get("action") for action in (hint or {}).get("actions") or []],
            },
        )
        entry["count"] += 1

    top = sorted(issues.values(), key=lambda item: item["count"], reverse=True)[:5]
    return {
        "scanned": scanned,
        "failed": failed,
        "recoverable": recoverable,
        "recoverableShare": round(recoverable / failed, 3) if failed else 0.0,
        "byCode": dict(sorted(by_code.items(), key=lambda item: item[1], reverse=True)),
        "byCommand": dict(sorted(by_command.items(), key=lambda item: item[1], reverse=True)),
        "topIssues": top,
        "recommendations": recommendations(by_code, failed, top),
    }


def recommendations(by_code: dict[str, int], failed: int, top: list[dict[str, Any]]) -> list[str]:
    """把统计翻译成一句可执行的结论——只给得出来的建议，不硬凑。"""
    notes: list[str] = []
    if failed == 0:
        return notes
    for code, count in by_code.items():
        share = count / failed
        if share < 0.3:
            continue
        if code == "out_of_memory":
            notes.append("显存类失败占多数：优先降低默认分块/倍率，或默认提供 CPU 回退")
        elif code == "missing_weights":
            notes.append("权重缺失占多数：检查默认模型的下载预置，或在计划阶段就提示缺失")
        elif code == "input_missing":
            notes.append("输入路径错误占多数：调用方需要先解析/校验素材路径")
        elif code == "bad_input":
            notes.append("输入无法解码占多数：考虑在入口统一转码")
        elif code == "unclassified":
            notes.append("多数失败无法归类：值得为最高频的那条补一条恢复规则")
    if not notes and top:
        notes.append("失败分散：先看 topIssues 里最多的一条")
    return notes
