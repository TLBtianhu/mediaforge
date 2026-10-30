"""把客观评价附到媒体产物上，省掉"跑完再问一次"的往返。

agent 的自评价有两种用法：

* **拉**：自己调 `media.evaluate`（适合想自定义指标/对比的场景）
* **推**：产物里直接带一份 `eval`（默认行为，loop 不用再发一次请求）

这里实现"推"。原则：**只做加法**——评价失败绝不影响主结果；
`MEDIAFORGE_AUTO_EVALUATE=0` 可以整个关掉（比如批量处理时想省 IO）。
"""
from __future__ import annotations

import os
from typing import Any

_DISABLED_VALUES = {"0", "false", "no", "off"}


def enabled() -> bool:
    return os.environ.get("MEDIAFORGE_AUTO_EVALUATE", "").strip().lower() not in _DISABLED_VALUES


def attach(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给每个产物加一个 `eval` 字段（指标 + 相对输入的判断）。原地修改并返回。"""
    if not enabled() or not outputs:
        return outputs
    try:
        from media_eval import compare, evaluate_file
    except Exception:  # 评价是增值能力，缺依赖时静默跳过
        return outputs
    for item in outputs:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        try:
            produced = evaluate_file(path)
            entry: dict[str, Any] = {
                "kind": produced.get("kind"),
                "metrics": produced.get("metrics"),
                "notes": [],
            }
            reference = str(item.get("source") or "").strip()
            if reference:
                entry["notes"] = compare(produced, evaluate_file(reference))
            if produced.get("error"):
                entry["error"] = produced["error"]
            item["eval"] = entry
        except Exception:
            continue
    return outputs
