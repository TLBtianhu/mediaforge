"""运行时能力声明：这套解释器能做什么。

与 `manifest/protocol.json` 的分工：

    protocol.json   命令与事件（程序之间怎么说话）
    runtime.json    能力清单（这套运行时能承担哪些工作）

一个能力"声明支持"但实际依赖缺失，仍然会在执行时报错；这里防的是另一种更糟的
情况——能力根本不在这套运行时里，却让用户点下按钮等十几秒才看到"没有 cv2"。

判定顺序（越具体的越优先）：

    1. 环境变量 MEDIAFORGE_RUNTIME_ASSET（Rust 解析指针后传入）
    2. 当前解释器目录里的 .asset-id 标记（直接跑 worker / 开发态）
    3. 清单里的 fallbackAssetId（两者都没有时）

没有指针时返回 fallback，并在 describe() 里标 source=fallback，便于排查。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


class RuntimeManifestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def manifest_path() -> Path:
    here = Path(__file__).resolve()
    for base in (here.parents[2], here.parents[1]):
        candidate = base / "manifest" / "runtime.json"
        if candidate.is_file():
            return candidate
    raise RuntimeManifestError(
        "RUNTIME_MANIFEST_MISSING", "找不到 manifest/runtime.json（安装包与开发树都应带有）"
    )


def load_manifest() -> dict[str, Any]:
    path = manifest_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", f"{path} 不是合法 JSON：{exc}") from exc
    if data.get("schemaVersion") != SCHEMA_VERSION:
        raise RuntimeManifestError(
            "RUNTIME_SCHEMA_MISMATCH",
            f"runtime.json schemaVersion={data.get('schemaVersion')!r}，本程序支持 {SCHEMA_VERSION}",
        )
    if not data.get("runtimes"):
        raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "runtime.json 没有任何 runtimes")
    return data


def data_root() -> Path:
    override = os.environ.get("MEDIAFORGE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mediaforge"


def active_asset_id() -> str | None:
    override = os.environ.get("MEDIAFORGE_RUNTIME_ASSET", "").strip()
    if override:
        return override

    # pointer 的解析只发生在 Rust 侧，并通过 MEDIAFORGE_RUNTIME_ASSET 传下来。
    # 这里不再读 active-runtime.json，避免第二套推导规则。
    marker = Path(sys.prefix) / ".asset-id"
    if marker.is_file():
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
        asset = str(record.get("assetId") or "").strip()
        if asset:
            return asset
    return None


def describe() -> dict[str, Any]:
    data = load_manifest()
    by_id = {str(item.get("assetId")): item for item in data["runtimes"]}
    asset = active_asset_id()
    if asset and asset in by_id:
        entry, source = by_id[asset], "pointer"
    else:
        fallback = str(data.get("fallbackAssetId") or "")
        entry = by_id.get(fallback) or data["runtimes"][0]
        source = "fallback" if fallback == entry.get("assetId") else "first"
        asset = str(entry.get("assetId"))
    return {
        "assetId": asset,
        "backend": entry.get("backend"),
        "python": entry.get("python"),
        "torch": entry.get("torch"),
        "capabilities": list(entry.get("capabilities") or []),
        "source": source,
    }


def supported_capabilities() -> set[str]:
    return set(describe()["capabilities"])


def missing_capabilities(requested: Iterable[str]) -> list[str]:
    supported = supported_capabilities()
    return sorted({str(item) for item in requested if str(item) and str(item) not in supported})
