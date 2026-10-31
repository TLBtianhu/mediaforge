"""媒体失败条目的统一形状：`{path, error, recovery}`。

三个 worker（image / video / workflow）都以 `failed.append({...})` 收尾；
把形状收在这里，免得三处各写一份、字段慢慢漂移。
"""
from __future__ import annotations

from typing import Any

from shared.recovery import classify


def describe(path: Any, error: BaseException | str) -> dict[str, Any]:
    message = str(error)
    entry: dict[str, Any] = {"path": str(path), "error": message}
    hint = classify(message)
    if hint:
        entry["recovery"] = hint
    return entry
