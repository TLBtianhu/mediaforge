"""槽位词汇表（后端视角）：kind / 范围 / 默认 / 派生约束。

这是引擎层的声明数据：capabilities 只依赖它，不依赖 app_core/interface。
前端 `src/litegraph/slots.ts` 是同一份词汇表的 UI 视角，两边 id 与 kind 由测试强制一致。
"""
from __future__ import annotations

from typing import Any


SLOT_DEFAULTS: dict[str, dict[str, Any]] = {
    # 音频
    "chunk_size": {"kind": "int", "bind": "chunk_size", "min": 64, "max": 8192, "step": 64, "default": 1024,
                   "derive": "chunk_size_constraint"},
    "overlap": {"kind": "int", "bind": "overlap", "min": 0, "max": 8, "step": 1, "default": 2},
    "stems": {"kind": "multi", "bind": "stems", "default": "vocals"},
    "batch_size": {"kind": "int", "bind": "batch_size", "min": 1, "max": 32, "step": 1, "default": 1},
    "overlap_count": {"kind": "int", "bind": "num_overlap", "min": 0, "max": 128, "step": 1, "default": 4},
    "window_size": {"kind": "int", "bind": "window_size", "min": 0, "max": 4096, "step": 64, "default": 512},
    "aggression": {"kind": "int", "bind": "aggression", "min": 0, "max": 100, "step": 1, "default": 5},
    "post_process": {"kind": "bool", "bind": "enable_post_process", "default": True},
    "high_end_process": {"kind": "bool", "bind": "high_end_process", "default": False},
    "post_process_threshold": {"kind": "float", "bind": "post_process_threshold", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.3},
    "standardize": {"kind": "bool", "bind": "standardize", "default": False},
    # 图像 / 视频
    "scale": {"kind": "int", "bind": "scale", "min": 1, "max": 4, "step": 1, "default": 2},
    "tile": {"kind": "int", "bind": "tile", "min": 0, "max": 1024, "step": 32, "default": 0},
    "denoise_strength": {"kind": "float", "bind": "denoise_strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "strength": {"kind": "float", "bind": "strength", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.5},
    "fidelity": {"kind": "float", "bind": "fidelity", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.7},
    "expand": {"kind": "int", "bind": "expand", "min": 0, "max": 64, "step": 1, "default": 10},
    "steps": {"kind": "int", "bind": "steps", "min": 1, "max": 100, "step": 1, "default": 20},
    "upscale": {"kind": "int", "bind": "upscale", "min": 1, "max": 4, "step": 1, "default": 2},
    "threshold": {"kind": "float", "bind": "threshold", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.5},
    "conf": {"kind": "float", "bind": "conf", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.25},
    "iou": {"kind": "float", "bind": "iou", "min": 0.0, "max": 1.0, "step": 0.01, "default": 0.45},
    "multiplier": {"kind": "enum", "bind": "multiplier", "min": 2, "max": 8, "step": 1, "default": 2},
    "scene_cut": {"kind": "bool", "bind": "scene_cut", "default": True},
    "neighbor_frames": {"kind": "int", "bind": "neighbor_frames", "min": 2, "max": 20, "step": 1, "default": 10},
    "fp16": {"kind": "bool", "bind": "fp16", "default": True},
}

#: 按架构覆盖默认值。单位是"架构"，不是模型——326 个音频模型只有 12 种架构。
ARCH_SLOT_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "mel_band_roformer": {
        # 分块必须与 STFT 步长对齐，所以步长比通用值大
        "chunk_size": {"step": 128, "default": 512},
    },
    "bs_roformer": {
        "chunk_size": {"step": 256, "default": 1024},
    },
    "vr": {
        "chunk_size": {"min": 512, "max": 16384, "step": 512, "default": 4096},
    },
    "demucs": {
        # demucs 不走分块推理，既没有 chunk_size 也没有 overlap 概念
        "chunk_size": None,
        "overlap": None,
        "overlap_count": None,
    },
    "htdemucs": {"chunk_size": None, "overlap": None, "overlap_count": None},
    "vr": {
        # VR 系用窗口 + 强度，而不是分块 + 重叠样本数
        "overlap": None,
        "standardize": None,
    },
    "apollo": {
        # Apollo 用重叠次数而不是重叠样本数
        "overlap": None,
    },
    "mdx23c": {
        "chunk_size": {"step": 128, "default": 1024},
    },
}

#: 只属于 VR / Apollo 的槽位：其它分离架构不该看到它们
VR_ONLY_SLOTS = ["window_size", "aggression", "post_process", "high_end_process", "post_process_threshold", "overlap_count"]

FORBIDDEN_INTERFACE_KEYS = {"order", "group", "section", "sections", "width", "row", "column", "collapsed", "index"}


def vocabulary() -> dict[str, dict[str, Any]]:
    """槽位词汇表（后端视角）：kind / 范围 / 默认 / 派生约束。

    前端 `src/litegraph/slots.ts` 是同一份词汇表的 UI 视角（unit/help/diagram），
    两边的 id 与 kind 由测试强制一致。
    """
    return {slot: dict(spec) for slot, spec in SLOT_DEFAULTS.items()}
