"""Normalize single-task parameters at the worker boundary.

Only explicit steps define capability bindings and parameters. Consumers read
their validated values and supported defaults, including valid zero values.
"""
from __future__ import annotations

from typing import Any

from . import capabilities


def value(params: dict[str, Any], key: str, default: Any) -> Any:
    result = params.get(key)
    return default if result is None else result


def normalize_steps(payload: dict[str, Any], *, modality: str) -> list[dict[str, Any]]:
    raw_steps = payload.get("steps")
    obsolete = sorted(set(payload) & {"mode", "modelId", "denoiserModelId", "factor"})
    if obsolete:
        raise ValueError(f"Use explicit steps/bindings/params instead of: {', '.join(obsolete)}")
    steps = capabilities.normalize_steps(raw_steps)
    for raw, step in zip(raw_steps, steps):
        explicit = raw.get("params", {})
        if not isinstance(explicit, dict):
            raise ValueError("step params must be an object")
        if modality not in capabilities.capability(step["capability"])["modalities"]:
            raise ValueError(f"{step['capability']} is not available for {modality} processing")
        specs = capabilities.parameter_specs(step["capability"], step["bindings"])
        for key in list(step["params"]):
            spec = specs[key]
            supported = spec.get("supported", True) and modality in spec.get("modalities", [modality])
            if not supported:
                if key in explicit:
                    raise ValueError(f"{step['capability']}: parameter {key} is not available for {modality} processing")
                # The modality-agnostic normalizer supplies every schema default.
                # Omitted parameters are not requests to use unsupported features.
                del step["params"][key]
        params = step["params"]
        if step["capability"] == "upscale" and str(params.get("target") or "none").strip().lower() == "custom":
            if int(value(params, "width", 0)) <= 0 or int(value(params, "height", 0)) <= 0:
                raise ValueError("自定义分辨率需要同时提供宽度和高度")
    return steps
