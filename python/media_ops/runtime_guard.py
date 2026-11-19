"""运行时能力声明：这套解释器能做什么。

与 `manifest/protocol.json` 的分工：

    protocol.json   命令与事件（程序之间怎么说话）
    runtime.json    能力清单（这套运行时能承担哪些工作）

一个能力"声明支持"但实际依赖缺失，仍然会在执行时报错；这里防的是另一种更糟的
情况——能力根本不在这套运行时里，却让用户点下按钮等十几秒才看到"没有 cv2"。

判定顺序（越具体的越优先）：

    1. 环境变量 MEDIAFORGE_RUNTIME_ASSET（Rust 解析指针后传入）
    2. 当前解释器目录里的 .asset-id 标记（直接跑 worker / 开发态）
    3. 清单里与当前解释器平台及架构匹配的 fallbackAssetId（没有明确选择时）

独立解释器使用本地适配器，不借用其它平台或其它运行时的声明。
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Iterable

from shared import worker_paths
from shared.runtime_platform import interpreter_architecture

SCHEMA_VERSION = 3
PLATFORMS = ("windows", "linux", "macos")
ARCHITECTURES = ("x86_64", "aarch64")
BACKENDS = ("cpu", "cuda", "rocm", "mlx")


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
    if not isinstance(data, dict):
        raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "runtime.json 必须是 JSON 对象")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != SCHEMA_VERSION:
        raise RuntimeManifestError(
            "RUNTIME_SCHEMA_MISMATCH",
            f"runtime.json schemaVersion={data.get('schemaVersion')!r}，本程序支持 {SCHEMA_VERSION}",
        )
    runtimes = data.get("runtimes")
    if not isinstance(runtimes, list) or not runtimes:
        raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "runtime.json 没有任何 runtimes")
    ids = set()
    targets = set()
    for entry in runtimes:
        if (not isinstance(entry, dict) or not isinstance(entry.get("assetId"), str)
                or not entry["assetId"] or entry["assetId"].lower() in ids
                or entry.get("platform") not in PLATFORMS
                or entry.get("arch") not in ARCHITECTURES
                or entry.get("backend") not in BACKENDS):
            raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "运行时需要唯一资产 ID 及明确平台、架构和后端")
        target = (entry["platform"], entry["arch"], entry["backend"])
        if target in targets:
            raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "运行时清单中的资产目标必须唯一")
        targets.add(target)
        ids.add(entry["assetId"].lower())
    fallback = data.get("fallbackAssetId")
    if not any(entry["assetId"] == fallback for entry in runtimes):
        raise RuntimeManifestError("RUNTIME_MANIFEST_INVALID", "fallbackAssetId 必须指向清单中的资产")
    target_abi = json.loads(Path(__file__).resolve().parents[1].joinpath("runtime-manifest.json").read_text(encoding="utf-8")).get("python")
    if target_abi != "3.12" or any(item.get("python") != target_abi for item in runtimes):
        raise RuntimeManifestError("RUNTIME_PYTHON_MISMATCH", "运行时必须声明目标 Python 3.12，不能沿用旧 ABI")
    return data


def data_root() -> Path:
    return worker_paths.data_root()


def active_asset_id() -> str | None:
    override = os.environ.get("MEDIAFORGE_RUNTIME_ASSET")
    if override is not None:
        return override.strip() or None

    # pointer 的解析只发生在 Rust 侧，并通过 MEDIAFORGE_RUNTIME_ASSET 传下来。
    # 这里不再读 active-runtime.json，避免第二套推导规则。
    marker = Path(sys.prefix) / ".asset-id"
    if marker.is_file():
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}
        asset = str(record.get("assetId") or "").strip() if isinstance(record, dict) else ""
        if asset:
            return asset
    return None


def host_target() -> tuple[str | None, str | None]:
    target_platform = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform)
    arch = interpreter_architecture()
    return target_platform, arch if arch in ARCHITECTURES else None


def describe() -> dict[str, Any]:
    data = load_manifest()
    target_platform, target_arch = host_target()
    by_id = {item["assetId"]: item for item in data["runtimes"]
             if (item["platform"], item["arch"]) == (target_platform, target_arch)}
    asset = active_asset_id()
    entry, source = by_id.get(asset), "pointer"
    if asset is None and "MEDIAFORGE_RUNTIME_ASSET" not in os.environ:
        entry, source = by_id.get(data["fallbackAssetId"]), "fallback"
    if entry is None:
        from .capabilities import adapter_capability_ids

        # Dynamic and bundled interpreters are not described by this asset
        # manifest. Their media adapters still run their normal dependency checks.
        try:
            capabilities = sorted(adapter_capability_ids())
        except ImportError:
            # The minimal interpreter still serves media probe and conversion
            # even when it has no optional inference dependencies.
            capabilities = []
        return {
            "assetId": asset,
            "backend": None,
            "python": platform.python_version(),
            "torch": None,
            "platform": target_platform,
            "arch": target_arch,
            "capabilities": capabilities,
            "source": "local",
        }
    return {
        "assetId": entry["assetId"],
        "backend": entry.get("backend"),
        "python": entry.get("python"),
        "torch": entry.get("torch"),
        "platform": target_platform,
        "arch": target_arch,
        "capabilities": list(entry.get("capabilities") or []),
        "source": source,
    }


def supported_capabilities() -> set[str]:
    return set(describe()["capabilities"])


def missing_capabilities(requested: Iterable[str]) -> list[str]:
    supported = supported_capabilities()
    return sorted({str(item) for item in requested if str(item) and str(item) not in supported})
