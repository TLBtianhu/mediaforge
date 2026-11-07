"""Dependency-light serial workflow support declarations.

Metadata queries must not import image/video execution engines. The worker uses
this same contract for static validation before loading models or writing files.
"""
from __future__ import annotations

from typing import Any

from . import capabilities, video_step


# These are executable pipeline adapters, not the broader single-task capability
# list. Bindings and parameter schemas still come from capabilities/catalog data.
_IMAGE_PIPELINE_PARAMS = {
    "upscale": {"target", "width", "height", "fit", "scale", "tile"},
    "watermark_removal": {"conf", "expand"},
    "denoise": set(),
}
_VIDEO_FRAME_PARAMS = {
    "upscale": {"target", "width", "height", "fit", "scale", "tile"},
    "watermark_removal": {"conf", "expand", "detectMode"},
}
_VIDEO_SEQUENCE_PARAMS = {
    "video_super_resolution": set(),
    "video_inpainting": {"conf", "expand", "detectMode", "subvideoLength"},
}


def workflow_support(capability_id: str) -> dict[str, Any]:
    """Per-step input arity; the command itself accepts a batch of independent inputs."""
    declaration = capabilities.capability(capability_id)
    result = {}
    for modality in ("image", "video"):
        execution = None
        supported_params: set[str] = set()
        max_steps = None
        reason = "This capability has no serial workflow adapter; use its single-task page."
        if modality not in declaration["modalities"]:
            reason = f"This capability does not accept {modality} inputs."
        elif modality == "image" and capability_id in _IMAGE_PIPELINE_PARAMS:
            execution, supported_params = "image-pipeline", _IMAGE_PIPELINE_PARAMS[capability_id]
        elif modality == "video" and capability_id in video_step.registered() and capability_id in _VIDEO_FRAME_PARAMS:
            execution, supported_params = "video-frame", _VIDEO_FRAME_PARAMS[capability_id]
        elif modality == "video" and capability_id in video_step.SEQUENCE_CAPABILITIES and capability_id in _VIDEO_SEQUENCE_PARAMS:
            execution, supported_params, max_steps = "video-sequence", _VIDEO_SEQUENCE_PARAMS[capability_id], 1
        if capability_id == "frame_interpolation":
            reason = ("Image RIFE consumes two frames and produces one image; this per-input workflow cannot compose that shape. Use the single-task interpolation page."
                      if modality == "image" else "Video interpolation has no serial workflow adapter; use the single-task interpolation page.")
        support = {
            "supported": execution is not None,
            "execution": execution,
            "inputArity": {"min": 2 if capability_id == "frame_interpolation" and modality == "image" else 1,
                           "max": 2 if capability_id == "frame_interpolation" and modality == "image" else 1},
            "batchInputs": True,
            "supportedParams": sorted(supported_params),
            "reason": "" if execution else reason,
        }
        if max_steps is not None:
            support["maxSteps"] = max_steps
        if execution in {"image-pipeline", "video-frame"} and "tile" in supported_params:
            support["sharedParams"] = ["tile"]
        if execution == "video-frame":
            support["sharedModelSlots"] = True
        if capability_id == "video_inpainting" and execution:
            support["mask"] = {"pathField": "maskPath", "alternativeCapability": "watermark_removal", "alternativeRole": "detector"}
        result[modality] = support
    return result


