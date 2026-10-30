"""统一模型登记表的读取入口（音视频共用一份记录格式）。

`model_registry.json` 里的每条记录只回答四件事：

    io      类型签名（image->image / video->video / image->mask / image->depth / audio->audio* …）
    task    任务族（upscale / restoration / frame_interpolation / separation …）
    slots   可调槽位（不是每个模型自造参数，而是共用的槽位词汇表）
    source  来源与状态（verified / local / arch-only，见下）

`source.status` 是这份表诚实性的关键：

* `verified`  仓库与权重文件名都来自 HuggingFace API（可复现）
* `local`     已经在本应用的 media_catalog 里、权重可下载
* `arch-only` 只知道架构存在（来自 spandrel 的架构表），**具体 checkpoint 未绑定**

有了它，音频和视频就不再是两套表格：326 个音频模型与 77 个图像/视频模型
用的是同一个 schema，差别只在 `slots` 的具体名字。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("model_registry.json")

VALID_IO_TYPES = {"image", "video", "audio", "audio*", "mask", "depth", "midi", "text", "json"}
VALID_SOURCE_STATUS = {"verified", "local", "arch-only", "catalog"}


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def tasks() -> dict[str, Any]:
    return load_registry()["taxonomy"]["tasks"]


def models(task: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    items = list(load_registry()["models"])
    if task:
        items = [item for item in items if item["task"] == task]
    if status:
        items = [item for item in items if item["source"]["status"] == status]
    return items


def find(model_id: str) -> dict[str, Any] | None:
    return next((item for item in load_registry()["models"] if item["id"] == model_id), None)


def signature(model: dict[str, Any]) -> str:
    """类型签名，例如 `image->image`、`video->video`、`audio->audio*`。"""
    return f"{model['io']['in']}->{model['io']['out']}"


def validate(registry: dict[str, Any] | None = None) -> list[str]:
    """结构校验，返回问题列表（空 = 通过）。测试和生成脚本共用同一套规则。"""
    data = registry if registry is not None else load_registry()
    problems: list[str] = []
    known_tasks = data.get("taxonomy", {}).get("tasks", {})
    seen: set[str] = set()
    for item in data.get("models", []):
        model_id = item.get("id")
        if not model_id:
            problems.append("entry without id")
            continue
        if model_id in seen:
            problems.append(f"duplicate id: {model_id}")
        seen.add(model_id)
        task = item.get("task")
        if task not in known_tasks:
            problems.append(f"{model_id}: unknown task {task!r}")
        io = item.get("io") or {}
        for key in ("in", "out"):
            if io.get(key) not in VALID_IO_TYPES:
                problems.append(f"{model_id}: bad io.{key}={io.get(key)!r}")
        status = (item.get("source") or {}).get("status")
        if status not in VALID_SOURCE_STATUS:
            problems.append(f"{model_id}: bad source.status={status!r}")
        if status == "verified":
            source = item["source"]
            if not source.get("repo") or not source.get("file"):
                problems.append(f"{model_id}: verified entry needs repo and file")
        if status == "catalog" and not item["source"].get("file"):
            problems.append(f"{model_id}: catalog entry needs a relative file path")
        if status == "arch-only" and not item.get("architecture"):
            problems.append(f"{model_id}: arch-only entry needs architecture")
        for slot in item.get("slots") or []:
            if not isinstance(slot, str) or not slot:
                problems.append(f"{model_id}: bad slot {slot!r}")
    return problems
