"""Model-driven capability registry for media processing.

A capability describes *what* to do; a model describes *which weights* to use.
Pipelines are ordered capability steps with explicit model bindings.

2026-10-29 结构调整：**能力声明从代码搬成了数据**（`capabilities.json`），
并且删掉了手写的 `implemented` 布尔。

为什么：`implemented` 原本是人手维护的，而真正的适配器在 `video_step.register()` /
`image_ops` 里注册。两个地方各说各话，谁也不会因为对方变了而报错——等哪天有人删了一个
适配器却忘了改布尔，UI 会继续显示"可用"，用户点下去才在运行时报错。

现在的规矩（对应"声明是数据、实现是代码、加载时对账"）：

* `capabilities.json` 只说"有什么能力、要哪些角色、参数长什么样"；
* `implemented` 由 `adapter_capability_ids()` **算出来**，写在任何地方都会被忽略；
* `audit_capabilities()` 一次性列出四类漂移，测试要求它们为空（或恰好等于"计划中"的那几条）。

将来插件要贡献能力时，就是"往 JSON 里加一条 + 注册一个适配器"，对账逻辑原样适用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import worker_paths

CATALOG_PATH = Path(__file__).with_name("media_catalog.json")
CAPABILITIES_PATH = Path(__file__).with_name("capabilities.json")


def _load_declarations() -> tuple[list[dict[str, Any]], list[str]]:
    """读能力声明，返回 (声明列表, 写了 implemented 字段被忽略的 id 列表)。"""
    data = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    items = data.get("capabilities")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{CAPABILITIES_PATH.name} must declare a non-empty capabilities array")

    declarations: list[dict[str, Any]] = []
    hand_written_flag: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each capability declaration must be an object")
        capability_id = str(item.get("id") or "").strip()
        if not capability_id:
            raise ValueError("capability declaration is missing an id")
        if capability_id in seen:
            raise ValueError(f"duplicate capability id: {capability_id}")
        seen.add(capability_id)
        if "implemented" in item:
            # 保留旧写法兼容，但绝不采信：实现与否只认注册表。
            hand_written_flag.append(capability_id)
            item = {key: value for key, value in item.items() if key != "implemented"}
        for key in ("label", "modalities", "roles", "modelIds", "params"):
            if key not in item:
                raise ValueError(f"capability {capability_id} is missing '{key}'")
        declarations.append(item)
    return declarations, hand_written_flag


CAPABILITIES, _HAND_WRITTEN_IMPLEMENTED = _load_declarations()


def _catalog() -> dict[str, dict[str, Any]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {f"media:{item['id']}": {**item, "id": f"media:{item['id']}"} for item in data.get("models", [])}


def _ensure_layout() -> Path:
    """确保媒体权重都在 `<模型根>/media/` 下，并返回模型根。

    老布局（权重直接躺在根目录）会在这里被搬一次；不搬的话，升级后的用户会看到
    "模型未下载"，重下代价是几百 MB。迁移是幂等的，且只动 catalog 点名的文件。
    """
    root = worker_paths.model_root()
    worker_paths.migrate_media_models(root, [str(record.get("name") or "") for record in _catalog().values()])
    return root


def model_dir() -> Path:
    """媒体权重的根目录。解析规则只有一份，在 `worker_paths.model_root()`。

    2026-10-29 之前这里兜底到 `Path(__file__).parents[2] / "models"`（仓库根），
    打包后等于安装目录（Program Files、只读、且不含 models/）。
    平时不发作，因为主流程都从 payload 传 modelDir——**但只要某条路径忘了传，
    就是发布后才炸**。
    """
    return worker_paths.model_root()


def model_path(model_id: str) -> Path:
    record = _catalog().get(model_id)
    if record is None:
        raise KeyError(f"unknown model: {model_id}")
    name = str(record.get("name") or "")
    if not name:
        raise KeyError(f"model has no file name: {model_id}")
    return _ensure_layout() / worker_paths.media_model_relpath(name)


def model_scale(model_id: str, fallback: int = 2) -> int:
    """模型自报的输出倍率（catalog 里 params.scale.default）。

    不要再靠模型 id 里有没有 x4 字样来猜倍率。名字是投影，倍率是属性：
    - 改名（加前后缀）、加变体、换命名风格，嗅探都会**静默**失效；
    - 失效的表现不是报错，而是"选了 4× 却按 2× 加载"，画质对不上却查不出原因。

    catalog 没有声明时退化为 fallback（默认 2），保持与旧行为一致。
    """
    record = _catalog().get(model_id) or {}
    spec = (record.get("params") or {}).get("scale") or {}
    try:
        value = int(spec.get("default") or 0)
    except (TypeError, ValueError):
        return fallback
    return value if value > 1 else fallback


def adapter_capability_ids() -> set[str]:
    """真的能被执行的 capability id —— 唯一真源。

    目前 `upscale` / `watermark_removal` 在 `video_step` 里注册（图像和视频共用同一条
    逐帧管线，所以同一个 planner 同时服务两种模态）。将来出现**只在图像上**的适配器时，
    让它在这里追加，别在别处另起一套判断。
    """
    from . import image_ops, video_step

    ids = set(video_step.registered())
    return ids | set(getattr(image_ops, "IMAGE_CAPABILITIES", ()))


def audit_capabilities() -> dict[str, list[str]]:
    """四类漂移，全空才算自洽。

    - `declared_without_adapter`：声明了但没有任何适配器 → 用户看得到、点不动；
    - `adapter_without_declaration`：注册了但没声明 → 能跑但 UI/MCP 里查不到；
    - `declared_model_missing`：capability 引用了 catalog 里不存在的模型；
    - `hand_written_implemented`：还在手写 implemented（会被忽略，但应当清掉）。
    """
    declared = {item["id"]: item for item in CAPABILITIES}
    adapters = adapter_capability_ids()
    catalog = _catalog()
    referenced = {model_id for item in CAPABILITIES for model_id in item.get("modelIds") or []}
    return {
        "declared_without_adapter": sorted(set(declared) - adapters),
        "adapter_without_declaration": sorted(adapters - set(declared)),
        "declared_model_missing": sorted(model_id for model_id in referenced if model_id not in catalog),
        "hand_written_implemented": sorted(_HAND_WRITTEN_IMPLEMENTED),
    }


def capability(capability_id: str) -> dict[str, Any]:
    for item in CAPABILITIES:
        if item["id"] == capability_id:
            return item
    raise KeyError(f"unknown capability: {capability_id}")


def _layout_root(override_dir: Any = None) -> Path:
    """可用性检查也要认"用户选的模型目录"，否则界面上永远是"没有可用模型"。"""
    if override_dir:
        root = worker_paths.model_root(override_dir)
        worker_paths.migrate_media_models(
            root, [str(record.get("name") or "") for record in _catalog().values()]
        )
        return root
    return _ensure_layout()


def list_capabilities(override_dir: Any = None) -> dict[str, Any]:
    """`override_dir` 是用户选的模型目录；不传就走全局默认根。"""
    catalog = _catalog()
    root = _layout_root(override_dir)
    implemented = adapter_capability_ids()
    result = []
    for item in CAPABILITIES:
        models = []
        for model_id in item["modelIds"]:
            record = catalog.get(model_id)
            if record is None:
                continue
            name = str(record.get("name") or "")
            path = root / worker_paths.media_model_relpath(name) if name else root
            models.append(
                {
                    "id": model_id,
                    "label": record.get("label") or record.get("name"),
                    "roles": [role for role, spec in item["roles"].items() if spec.get("task") == record.get("task")],
                    "downloaded": path.is_file(),
                    "implemented": bool(record.get("implemented", True)),
                    "params": record.get("params") or {},
                }
            )
        result.append(
            {
                "id": item["id"],
                "label": item["label"],
                "labelEn": item["labelEn"],
                "modalities": item["modalities"],
                "roles": item["roles"],
                "params": item["params"],
                # 算出来的，不是声明里抄的
                "implemented": item["id"] in implemented,
                "models": models,
            }
        )
    # audit 一起带出去：让 MCP / UI / 日志都能看见"声明 vs 实现"的漂移，
    # 而不是只有测试知道。多一个字段是加法的，老消费者忽略即可。
    return {"capabilities": result, "modelDir": str(model_dir()), "audit": audit_capabilities()}


_PARAM_TYPES: dict[str, Any] = {
    "integer": int,
    "number": (int, float),
    "string": str,
    "boolean": bool,
}


def _slot_ranges() -> dict[str, dict[str, Any]]:
    try:
        from . import interface as model_interface

        return model_interface.vocabulary()
    except Exception:  # noqa: BLE001
        return {}


def _validate_step_params(capability_id: str, params: dict[str, Any], declared: dict[str, Any]) -> None:
    """步骤参数必须是这个能力（或它绑定的模型）声明过的，且类型/范围正确。

    以前未知参数会静默透传给 worker —— 调用方以为自己调了个旋钮，其实什么都没发生。
    """
    allowed = set(declared)
    unknown = sorted(key for key in params if key not in allowed)
    if unknown:
        raise ValueError(
            f"capability {capability_id} does not accept parameters: {', '.join(unknown)}"
            f" (allowed: {', '.join(sorted(allowed)) or 'none'})"
        )
    ranges = _slot_ranges()
    for key, value in params.items():
        spec = declared.get(key) or {}
        expected = str(spec.get("type") or "")
        python_type = _PARAM_TYPES.get(expected)
        if python_type is not None:
            if expected in {"integer", "number"} and isinstance(value, bool):
                raise ValueError(f"capability {capability_id}: parameter {key} should be {expected}")
            if not isinstance(value, python_type):
                raise ValueError(f"capability {capability_id}: parameter {key} should be {expected}")
        slot = ranges.get(key)
        if slot and isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = slot.get("min")
            maximum = slot.get("max")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise ValueError(
                    f"capability {capability_id}: parameter {key} must be >= {minimum} (got {value})"
                )
            if isinstance(maximum, (int, float)) and value > maximum:
                raise ValueError(
                    f"capability {capability_id}: parameter {key} must be <= {maximum} (got {value})"
                )


def normalize_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("pipeline steps must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    catalog = _catalog()
    implemented = adapter_capability_ids()
    for raw in steps:
        if not isinstance(raw, dict):
            raise ValueError("pipeline step must be an object")
        cap = capability(str(raw.get("capability") or raw.get("type") or ""))
        if cap["id"] not in implemented:
            raise ValueError(f"capability adapter is not implemented: {cap['id']}")
        bindings = raw.get("bindings") if isinstance(raw.get("bindings"), dict) else {}
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        resolved: dict[str, str] = {}
        for role, spec in cap["roles"].items():
            model_id = str(bindings.get(role) or "").strip()
            if not model_id:
                if spec.get("required", True):
                    raise ValueError(f"capability {cap['id']} requires binding: {role}")
                continue
            if model_id not in cap["modelIds"]:
                raise ValueError(f"model {model_id} is not allowed for capability {cap['id']}")
            resolved[role] = model_id
        declared = dict(cap["params"])
        defaults = {key: spec.get("default") for key, spec in cap["params"].items()}
        for model_id in resolved.values():
            record = catalog.get(model_id) or {}
            for key, spec in (record.get("params") or {}).items():
                if isinstance(spec, dict):
                    declared[key] = spec
                    defaults[key] = spec.get("default")
        _validate_step_params(cap["id"], params, declared)
        normalized.append(
            {
                "capability": cap["id"],
                "bindings": resolved,
                "params": {**defaults, **params},
            }
        )
    return normalized


def default_bindings(capability_id: str) -> dict[str, str]:
    """每个角色的默认模型：取该能力声明的 `modelIds` 里第一个 task 匹配的。

    唯一真源是 `capabilities.json`——**不要在别处再硬编码 model id**。
    2026-10-29 之前 `steps_from_mode()` 和 `agent_bridge.pipeline_plan()` 各写了一份，
    改一个 id 要记得改三处，漏一处就是"计划里说有、实际找不到"。
    """
    item = capability(capability_id)
    catalog = _catalog()
    bindings: dict[str, str] = {}
    for role, spec in item["roles"].items():
        task = str(spec.get("task") or "")
        for model_id in item.get("modelIds") or []:
            record = catalog.get(model_id) or {}
            if str(record.get("task") or "") == task:
                bindings[role] = model_id
                break
    return bindings


def default_model_by_scale(capability_id: str, scale: int) -> str:
    """同一能力里按输出倍率挑一个（x2 / x4 这种变体）。

    没有匹配就抛 KeyError：宁可启动即报错，也不要拿一个写死的 id 兜底——
    兜底的后果是"catalog 改了名、程序还在用一个不存在的 id，直到运行时才炸"。
    """
    item = capability(capability_id)
    for model_id in item.get("modelIds") or []:
        if model_scale(model_id) == int(scale):
            return model_id
    raise KeyError(f"capability {capability_id} declares no model with scale {scale}")


def steps_from_mode(mode: str) -> list[dict[str, Any]]:
    """Compatibility bridge for existing UI modes."""
    steps: list[dict[str, Any]] = []
    if mode in {"detect"}:
        steps.append(
            {
                "capability": "object_detection",
                "bindings": default_bindings("object_detection"),
                "params": {},
            }
        )
    if mode in {"denoise"}:
        steps.append(
            {
                "capability": "denoise",
                "bindings": default_bindings("denoise"),
                "params": {},
            }
        )
    if mode in {"watermark", "watermark_upscale"}:
        steps.append(
            {
                "capability": "watermark_removal",
                "bindings": default_bindings("watermark_removal"),
                "params": {},
            }
        )
    if mode in {"upscale", "watermark_upscale"}:
        steps.append(
            {
                "capability": "upscale",
                "bindings": default_bindings("upscale"),
                "params": {},
            }
        )
    if not steps:
        raise ValueError(f"unsupported media mode: {mode}")
    return steps


def mode_for_steps(steps: list[dict[str, Any]]) -> str:
    ids = {step["capability"] for step in steps}
    if ids == {"watermark_removal"}:
        return "watermark"
    if ids == {"upscale"}:
        return "upscale"
    if ids == {"watermark_removal", "upscale"}:
        return "watermark_upscale"
    raise ValueError("mixed pipeline steps are not supported by the current frame adapter")
