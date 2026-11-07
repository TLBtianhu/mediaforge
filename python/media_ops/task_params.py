"""Normalize single-task parameters at the worker boundary.

Explicit steps are authoritative. Legacy mode/top-level fields are translated
once before schema validation; consumers read only the resulting bindings and
params, including valid zero values.
"""
from __future__ import annotations

from typing import Any

from . import capabilities


def value(params: dict[str, Any], key: str, default: Any) -> Any:
    result = params.get(key)
    return default if result is None else result


def normalize_steps(payload: dict[str, Any], *, modality: str) -> list[dict[str, Any]]:
    raw_steps = payload.get("steps")
    if raw_steps is None:
        raw_steps = capabilities.steps_from_mode(str(payload.get("mode") or "watermark_upscale").strip().lower())
        for step in raw_steps:
            bindings = step["bindings"]
            override = str(payload.get("modelId") or "").strip()
            if step["capability"] == "denoise" and not override:
                override = str(payload.get("denoiserModelId") or "").strip()
            if override and len(bindings) == 1:
                bindings[next(iter(bindings))] = override
            specs = capabilities.parameter_specs(step["capability"], bindings)
            step["params"] = {key: payload[key] for key in specs if key in payload and payload[key] is not None}
            # The legacy video API called the interpolation multiplier 'factor'.
            if step["capability"] == "frame_interpolation" and "multiplier" not in step["params"] and payload.get("factor") is not None:
                step["params"]["multiplier"] = payload["factor"]
    steps = capabilities.normalize_steps(raw_steps)
    for step in steps:
        if modality not in capabilities.capability(step["capability"])["modalities"]:
            raise ValueError(f"{step['capability']} is not available for {modality} processing")
        specs = capabilities.parameter_specs(step["capability"], step["bindings"])
        for key, setting in step["params"].items():
            spec = specs[key]
            supported = spec.get("supported", True) and modality in spec.get("modalities", [modality])
            # Old clients may include a dormant default in their saved schema.
            # Non-default values must never be silently ignored.
            if not supported and setting != spec.get("default"):
                raise ValueError(f"{step['capability']}: parameter {key} is not available for {modality} processing")
        params = step["params"]
        if step["capability"] == "upscale" and str(params.get("target") or "none").strip().lower() == "custom":
            if int(value(params, "width", 0)) <= 0 or int(value(params, "height", 0)) <= 0:
                raise ValueError("自定义分辨率需要同时提供宽度和高度")
    return steps
