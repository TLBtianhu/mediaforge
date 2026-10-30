"""计划预检：在花掉几十秒加载模型之前，先判哪些步骤注定做不成。

三件事，按代价从低到高：

1. **输入**（纯 stat）：不存在、不是文件、0 字节 → `block`
2. **目标尺寸**（读一次图片头）：目标不比原图大 → `skip` 那一步
3. **水印检测**（要加载 YOLO）：没检测到水印、遮罩过大 → `skip` 那一步

severity 的含义：

* `block` —— 整条计划不该跑
* `skip`  —— 只摘掉对应能力的那一步，其余继续

预检**永不拦路**：模型没下载、torch 缺失、检测器抛错都返回"没预检"，
把判断交给执行层。它是优化，不是闸门。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

NONE_TARGETS = {"", "none", "off", "original"}


def _check_inputs(inputs: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for value in inputs:
        path = Path(value)
        if not path.exists():
            findings.append({"code": "input_missing", "severity": "block", "message": f"输入不存在：{value}"})
        elif not path.is_file():
            findings.append({"code": "input_not_file", "severity": "block", "message": f"输入不是文件：{value}"})
        elif path.stat().st_size == 0:
            findings.append({"code": "input_empty", "severity": "block", "message": f"输入是空文件：{value}"})
    return findings


def _check_upscale_target(steps: list[dict[str, Any]], inputs: list[str]) -> list[dict[str, Any]]:
    """目标尺寸比原图还小 → 这一步是缩图，不是放大。"""
    findings: list[dict[str, Any]] = []
    upscale = next((step for step in steps if str(step.get("capability")) == "upscale"), None)
    if upscale is None or not inputs:
        return findings
    params = upscale.get("params") or {}
    target = str(params.get("target") or "none")
    if target.strip().lower() in NONE_TARGETS:
        return findings
    try:
        from PIL import Image

        from . import image_ops

        resolved = image_ops.resolve_target(target, int(params.get("width") or 0), int(params.get("height") or 0))
        if not resolved:
            return findings
        with Image.open(inputs[0]) as image:
            width, height = image.size
        if resolved[0] <= width and resolved[1] <= height:
            findings.append(
                {
                    "code": "target_not_larger",
                    "severity": "skip",
                    "capability": "upscale",
                    "message": f"目标 {resolved[0]}x{resolved[1]} 不大于原图 {width}x{height}；跳过放大",
                }
            )
    except Exception:  # noqa: BLE001 - 解析不了就不判，交给执行层报错
        return findings
    return findings


def _load_detector(detector_path: str, device: str | None):
    from . import image_ops

    return image_ops.WatermarkDetector(detector_path, device=device or "cuda")


def _check_watermark(
    steps: list[dict[str, Any]],
    inputs: list[str],
    detector_factory: Callable[..., Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    step = next((item for item in steps if str(item.get("capability")) == "watermark_removal"), None)
    if step is None or not inputs:
        return findings
    try:
        import numpy as np
        from PIL import Image

        from . import capabilities

        bindings = step.get("bindings") or {}
        params = step.get("params") or {}
        defaults = capabilities.default_bindings("watermark_removal")
        detector_path = str(capabilities.model_path(str(bindings.get("detector") or defaults["detector"])))
        factory = detector_factory or _load_detector
        detector = factory(detector_path, None)
    except Exception:  # noqa: BLE001 - 预检不可用就不拦
        return findings
    for value in inputs:
        try:
            image = np.asarray(Image.open(value).convert("RGB"))
            _boxes, _scores, mask = detector.detect(
                image,
                conf=float(params.get("conf") or 0.25),
                expand=int(params.get("expand") or 6),
            )
        except Exception:  # noqa: BLE001
            return findings
        if int(mask.max()) == 0:
            findings.append(
                {
                    "code": "no_watermark",
                    "severity": "skip",
                    "capability": "watermark_removal",
                    "path": value,
                    "message": "没有检测到水印；跳过这一步，或降低检测阈值",
                }
            )
            break
        covered = float((mask > 0).mean())
        if covered > 0.75:
            findings.append(
                {
                    "code": "mask_too_large",
                    "severity": "skip",
                    "capability": "watermark_removal",
                    "path": value,
                    "message": f"检测到的遮罩覆盖了 {covered:.0%} 的画面，可能是误检；跳过这一步",
                }
            )
            break
    return findings


def precheck(
    steps: list[dict[str, Any]],
    inputs: list[str],
    *,
    detector_factory: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """返回 `{"ok", "blocking", "findings"}`；没有输入可查时返回 None（= 没预检）。"""
    if not inputs:
        return None
    findings = _check_inputs(inputs)
    findings.extend(_check_upscale_target(steps, inputs))
    # 输入层面已经挡住就不必再加载检测器
    if not any(item["severity"] == "block" for item in findings):
        findings.extend(_check_watermark(steps, inputs, detector_factory))
    return {
        "ok": not findings,
        "blocking": any(item["severity"] == "block" for item in findings),
        "findings": findings,
    }
