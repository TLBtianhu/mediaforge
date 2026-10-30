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
        # 纯度量就在本模块里（P1 从 app 层搬下来，引擎不再反向依赖）
        compare, evaluate_file = globals()["compare"], globals()["evaluate_file"]
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


# ---- 产物客观度量（原 python/media_eval.py，P1 搬入引擎层）----

from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}

#：锐度比低于这个值才提示"可能变糊"，避免把正常差异当问题
SOFT_RATIO = 0.92


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return "unknown"


def _image_metrics(path: Path) -> dict[str, Any]:
    from PIL import Image

    import numpy as np

    with Image.open(path) as handle:
        image = handle.convert("RGB")
        width, height = image.size
        array = np.asarray(image, dtype=np.uint8)

    gray = (array[..., 0] * 0.299 + array[..., 1] * 0.587 + array[..., 2] * 0.114).astype(np.float32)
    inner = gray[1:-1, 1:-1]
    laplacian = (
        -4.0 * inner
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    sharpness = float(laplacian.var())
    mean = float(gray.mean()) or 1.0
    return {
        "width": width,
        "height": height,
        "pixels": width * height,
        "sharpness": round(sharpness, 3),
        # 亮度归一化后的锐度，跨图比较更公平
        "sharpnessNorm": round(sharpness / (mean * mean), 6),
        "meanLuma": round(mean, 2),
        "darkClipped": round(float((gray < 2).mean()), 6),
        "brightClipped": round(float((gray > 253).mean()), 6),
    }


def _probe_metrics(path: Path) -> dict[str, Any]:
    from . import ffmpeg

    meta = ffmpeg.probe(path)
    video = meta.get("video") or {}
    audio = meta.get("audio") or {}
    metrics: dict[str, Any] = {"duration": round(float(meta.get("duration") or 0.0), 3)}
    if video:
        metrics.update(
            {
                "width": int(video.get("width") or 0),
                "height": int(video.get("height") or 0),
                "fps": round(float(video.get("fps") or 0.0), 3),
            }
        )
        metrics["pixels"] = metrics["width"] * metrics["height"]
        if metrics["duration"] and metrics["fps"]:
            metrics["frames"] = int(round(metrics["duration"] * metrics["fps"]))
    if audio:
        metrics["channels"] = int(audio.get("channels") or 0)
        metrics["sampleRate"] = int(audio.get("sample_rate") or 0)
    return metrics


def evaluate_file(path: str | Path) -> dict[str, Any]:
    """一个文件的客观指标。失败不抛：把原因放进 `error`，让调用方自己决定。"""
    target = Path(path)
    result: dict[str, Any] = {"path": str(target), "kind": _kind(target), "exists": target.is_file()}
    if not target.is_file():
        result["error"] = "file not found"
        return result
    result["bytes"] = target.stat().st_size
    try:
        if result["kind"] == "image":
            result["metrics"] = _image_metrics(target)
        elif result["kind"] in {"video", "audio"}:
            result["metrics"] = _probe_metrics(target)
        else:
            result["error"] = "unsupported media type"
    except Exception as exc:  # 自评价失败不应该让调用方崩掉
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _compare(produced: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if produced.get("bytes") == reference.get("bytes"):
        notes.append("与参考文件字节完全相同：可能没有真正处理")
    pm = produced.get("metrics") or {}
    rm = reference.get("metrics") or {}
    if pm.get("pixels") and rm.get("pixels"):
        if pm["pixels"] > rm["pixels"]:
            notes.append(f"尺寸放大：{rm.get('width')}x{rm.get('height')} -> {pm.get('width')}x{pm.get('height')}")
        elif pm["pixels"] == rm["pixels"]:
            notes.append("尺寸未变化")
        else:
            notes.append(f"尺寸缩小：{rm.get('width')}x{rm.get('height')} -> {pm.get('width')}x{pm.get('height')}")
    # 锐度只在两张图之间才有意义：视频/音频没有这个指标，
    # 不然会输出一句"参考图过于平坦"，把"没这项指标"误导成"参考图有问题"。
    if produced.get("kind") == "image" and reference.get("kind") == "image":
        ps, rs = pm.get("sharpnessNorm"), rm.get("sharpnessNorm")
        if ps and rs:
            ratio = ps / rs
            notes.append(f"锐度比 {ratio:.2f}")
            if ratio < SOFT_RATIO:
                notes.append("锐度下降：放大后变糊，可能模型或参数不合适")
        elif not rs:
            # 参考图本身没有高频细节（纯色/极平滑），比值没有意义——说清楚，别让人以为漏了。
            notes.append("参考图过于平坦，锐度对比已跳过")
    for key, label in (("darkClipped", "暗部"), ("brightClipped", "高光")):
        value = pm.get(key)
        if isinstance(value, (int, float)) and value > 0.25:
            notes.append(f"{label}裁切过多（{value:.0%}），检查是否过曝/欠曝")
    return notes


def compare(produced: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    """公开的比较入口（`evaluation.attach` 用它把判断附到产物上）。"""
    return _compare(produced, reference)
