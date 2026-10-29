"""Model-driven capability registry for media processing.

A capability describes *what* to do; a model describes *which weights* to use.
Pipelines are ordered capability steps with explicit model bindings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("media_catalog.json")

CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "upscale",
        "label": "超分",
        "labelEn": "Upscale",
        "modalities": ["image", "video"],
        "roles": {
            "upscaler": {
                "label": "超分模型",
                "task": "super_resolution",
                "required": True,
            }
        },
        "modelIds": ["media:real-esrgan-x2", "media:real-esrgan-x4"],
        "params": {
            "target": {"type": "string", "default": "none"},
            "width": {"type": "integer", "default": 0},
            "height": {"type": "integer", "default": 0},
            "fit": {"type": "string", "default": "contain"},
            "scale": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
        },
        "implemented": True,
    },
    {
        "id": "watermark_removal",
        "label": "去水印 / 物体移除",
        "labelEn": "Watermark / object removal",
        "modalities": ["image", "video"],
        "roles": {
            "detector": {
                "label": "检测模型",
                "task": "watermark_detection",
                "required": True,
            },
            "inpainter": {
                "label": "修复模型",
                "task": "inpainting",
                "required": True,
            },
        },
        "modelIds": ["media:watermark-yolo11x", "media:lama"],
        "params": {
            "conf": {"type": "number", "default": 0.25},
            "expand": {"type": "integer", "default": 6},
            "detectMode": {"type": "string", "enum": ["fixed", "per_frame"], "default": "fixed"},
        },
        "implemented": True,
    },
    {
        "id": "face_restoration",
        "label": "人脸修复",
        "labelEn": "Face restoration",
        "modalities": ["image"],
        "roles": {"face_restorer": {"label": "人脸模型", "task": "face_restoration", "required": True}},
        "modelIds": ["media:gfpgan-v14"],
        "params": {},
        "implemented": False,
    },
    {
        "id": "frame_interpolation",
        "label": "视频插帧",
        "labelEn": "Frame interpolation",
        "modalities": ["video"],
        "roles": {"interpolator": {"label": "插帧模型", "task": "frame_interpolation", "required": True}},
        "modelIds": ["media:rife-v46"],
        "params": {"multiplier": {"type": "integer", "default": 2}},
        "implemented": False,
    },
    {
        "id": "video_inpainting",
        "label": "视频去水印 / 修复",
        "labelEn": "Video inpainting",
        "modalities": ["video"],
        "roles": {"primary": {"label": "视频修复模型", "task": "video_inpainting", "required": True}},
        "modelIds": ["media:propainter"],
        "params": {},
        "implemented": False,
    },
    {
        "id": "video_super_resolution",
        "label": "视频超分",
        "labelEn": "Video super-resolution",
        "modalities": ["video"],
        "roles": {"upscaler": {"label": "视频超分模型", "task": "video_super_resolution", "required": True}},
        "modelIds": ["media:basicvsrpp"],
        "params": {},
        "implemented": False,
    },
]


def _catalog() -> dict[str, dict[str, Any]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {f"media:{item['id']}": {**item, "id": f"media:{item['id']}"} for item in data.get("models", [])}


def model_dir() -> Path:
    override = os.environ.get("MEDIAFORGE_MODEL_DIR") or os.environ.get("PYMSS_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "models"


def model_path(model_id: str) -> Path:
    record = _catalog().get(model_id)
    if record is None:
        raise KeyError(f"unknown model: {model_id}")
    return model_dir() / str(record.get("name") or "")


def capability(capability_id: str) -> dict[str, Any]:
    for item in CAPABILITIES:
        if item["id"] == capability_id:
            return item
    raise KeyError(f"unknown capability: {capability_id}")


def list_capabilities() -> dict[str, Any]:
    catalog = _catalog()
    result = []
    for item in CAPABILITIES:
        models = []
        for model_id in item["modelIds"]:
            record = catalog.get(model_id)
            if record is None:
                continue
            path = model_dir() / str(record.get("name") or "")
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
                "implemented": item["implemented"],
                "models": models,
            }
        )
    return {"capabilities": result, "modelDir": str(model_dir())}


def normalize_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("pipeline steps must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    catalog = _catalog()
    for raw in steps:
        if not isinstance(raw, dict):
            raise ValueError("pipeline step must be an object")
        cap = capability(str(raw.get("capability") or raw.get("type") or ""))
        if not cap["implemented"]:
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
        defaults = {key: spec.get("default") for key, spec in cap["params"].items()}
        for model_id in resolved.values():
            record = catalog.get(model_id) or {}
            for key, spec in (record.get("params") or {}).items():
                if isinstance(spec, dict):
                    defaults[key] = spec.get("default")
        normalized.append(
            {
                "capability": cap["id"],
                "bindings": resolved,
                "params": {**defaults, **params},
            }
        )
    return normalized


def steps_from_mode(mode: str) -> list[dict[str, Any]]:
    """Compatibility bridge for existing UI modes."""
    steps: list[dict[str, Any]] = []
    if mode in {"watermark", "watermark_upscale"}:
        steps.append(
            {
                "capability": "watermark_removal",
                "bindings": {"detector": "media:watermark-yolo11x", "inpainter": "media:lama"},
                "params": {},
            }
        )
    if mode in {"upscale", "watermark_upscale"}:
        steps.append(
            {
                "capability": "upscale",
                "bindings": {"upscaler": "media:real-esrgan-x2"},
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
