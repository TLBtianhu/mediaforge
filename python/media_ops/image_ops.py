"""Image restoration for MediaForge.

Three modes:
- upscale: Real-ESRGAN 2x/4x or a target resolution
- watermark: YOLO watermark detection + LaMa inpainting
- watermark_upscale: both, in that order

The implementation is adapted from the local WatermarkWipe core so the two
projects can share the same model weights and behavior.
"""
from __future__ import annotations

import gc
import time
from pathlib import Path

import numpy as np

from . import capabilities

MAX_OUTPUT_SIDE = 8192
PRESETS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
}


def pick_device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def resolve_target(choice: str | None, width: int = 0, height: int = 0) -> tuple[int, int] | None:
    value = str(choice or "none").strip().lower()
    if value in {"", "none", "off", "original"}:
        return None
    if value in PRESETS:
        return PRESETS[value]
    if value == "custom":
        tw, th = max(64, int(width or 0)), max(64, int(height or 0))
        if tw <= 0 or th <= 0:
            raise ValueError("自定义分辨率需要同时提供宽度和高度")
        longest = max(tw, th)
        if longest > MAX_OUTPUT_SIDE:
            factor = MAX_OUTPUT_SIDE / float(longest)
            tw, th = max(64, int(round(tw * factor))), max(64, int(round(th * factor)))
        return tw, th
    raise ValueError(f"unsupported target preset: {choice}")


def read_image(path: str | Path) -> np.ndarray:
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def write_image(path: str | Path, image: np.ndarray) -> None:
    from PIL import Image

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(image)).save(target, quality=95)


def ensure_rgb_uint8(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    if array.ndim == 2:
        array = np.stack([array, array, array], axis=-1)
    if array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"unsupported image shape: {array.shape}")
    return np.ascontiguousarray(array)


def mask_area_ratio(mask: np.ndarray) -> float:
    if mask is None or mask.size == 0:
        return 0.0
    return float(np.count_nonzero(mask > 127)) / float(mask.size)


def boxes_to_mask(shape: tuple[int, int], boxes: np.ndarray, expand: int = 6) -> np.ndarray:
    import cv2

    h, w = int(shape[0]), int(shape[1])
    mask = np.zeros((h, w), dtype=np.uint8)
    for box in np.asarray(boxes, dtype=np.float32).reshape(-1, 4):
        x1, y1, x2, y2 = box
        x1, x2 = max(0, int(x1) - int(expand)), min(w, int(x2) + int(expand))
        y1, y2 = max(0, int(y1) - int(expand)), min(h, int(y2) + int(expand))
        if x2 > x1 and y2 > y1:
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def feather_composite(original: np.ndarray, repaired: np.ndarray, mask: np.ndarray, feather: int = 2) -> np.ndarray:
    import cv2

    alpha = (mask.astype(np.float32) / 255.0)
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), float(feather))
    alpha = alpha[:, :, None]
    blended = repaired.astype(np.float32) * alpha + original.astype(np.float32) * (1.0 - alpha)
    return np.clip(blended, 0, 255).round().astype(np.uint8)


# 只在图像路径上实现的 capability（视频逐帧管线没有对应 planner）。
IMAGE_CAPABILITIES: tuple[str, ...] = ("denoise", "object_detection", "depth", "matting")


def run_scale1_model(model_path: str | Path, image: np.ndarray, device: str | None = None, tile: int = 512, overlap: int = 32) -> np.ndarray:
    """跑 scale=1 的模型（去噪/修复这类输出尺寸不变的网络）。

    与超分不同，这里不放大也不重采样，只把同一尺寸的结果拼回去；
    分块是为了让 4K 图不会一次吃满显存，块间留 overlap 以避免接缝。
    """
    import torch
    from spandrel import ModelLoader

    dev = device or pick_device()
    descriptor = ModelLoader().load_from_file(str(model_path))
    model = getattr(descriptor, "model", descriptor)
    model.eval().to(dev)

    source = ensure_rgb_uint8(image)
    height, width = source.shape[:2]
    step = max(64, int(tile))
    pad = max(0, int(overlap))
    output = np.zeros_like(source)

    for top in range(0, height, step):
        for left in range(0, width, step):
            bottom = min(top + step, height)
            right = min(left + step, width)
            y0 = max(0, top - pad)
            x0 = max(0, left - pad)
            y1 = min(height, bottom + pad)
            x1 = min(width, right + pad)
            patch = source[y0:y1, x0:x1]
            tensor = torch.from_numpy(np.array(patch, copy=True)).permute(2, 0, 1).float().div(255.0).unsqueeze(0).to(dev)
            with torch.inference_mode():
                restored = model(tensor)
            array = restored.clamp(0.0, 1.0)[0].permute(1, 2, 0).cpu().numpy()
            array = (array * 255.0).round().astype(np.uint8)
            # 只取中心区域，边缘交给相邻块的重叠部分
            output[top:bottom, left:right] = array[top - y0 : top - y0 + (bottom - top), left - x0 : left - x0 + (right - left)]
    return output


def remove_background(model_dir: str | Path, image: np.ndarray) -> np.ndarray:
    """RMBG-1.4 抠图，返回 RGBA。

    注意两点，都是踩过的：
    * 输入按官方做法归一化 (x - 0.5)，不是 ImageNet mean/std；
    * 模型返回 (side_outputs, ...) 的 tuple，`out[0][-1]` 已经是 0-1 的概率，
      再套一次 sigmoid 会把前景压扁到 0.5 附近。
    """
    import torch
    from PIL import Image as PILImage
    from transformers import AutoModelForImageSegmentation

    device = pick_device()
    model = AutoModelForImageSegmentation.from_pretrained(str(model_dir), trust_remote_code=True)
    model.eval().to(device)
    rgb = ensure_rgb_uint8(image)
    pil = PILImage.fromarray(rgb)
    tensor = torch.from_numpy(rgb.astype("float32") / 255.0).permute(2, 0, 1).unsqueeze(0)
    tensor = (tensor - 0.5)
    tensor = torch.nn.functional.interpolate(tensor, size=(1024, 1024), mode="bilinear", align_corners=False).to(device)
    with torch.inference_mode():
        outputs = model(tensor)
    mask = outputs[0][-1]
    mask = torch.nn.functional.interpolate(mask, size=pil.size[::-1], mode="bilinear", align_corners=False)[0, 0]
    alpha = (mask.detach().cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    return np.dstack([rgb, alpha])


def estimate_depth(model_dir: str | Path, image: np.ndarray) -> np.ndarray:
    """Depth-Anything-V2 深度估计，输出 0-255 的灰度图（近白远黑）。

    模型目录里有 config.json + model.safetensors；原始预测是视差，数值范围不稳定，
    所以按当前图的最小/最大值归一化后再存图——同一条命令前后两次结果可比。
    """
    import torch
    from PIL import Image as PILImage
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    device = pick_device()
    processor = AutoImageProcessor.from_pretrained(str(model_dir))
    model = AutoModelForDepthEstimation.from_pretrained(str(model_dir))
    model.eval().to(device)

    pil = PILImage.fromarray(ensure_rgb_uint8(image))
    inputs = processor(images=pil, return_tensors="pt").to(device)
    with torch.inference_mode():
        predicted = model(**inputs).predicted_depth
    predicted = torch.nn.functional.interpolate(
        predicted.unsqueeze(1), size=pil.size[::-1], mode="bicubic", align_corners=False
    )[0, 0]
    depth = predicted.detach().cpu().numpy().astype("float32")
    low, high = float(depth.min()), float(depth.max())
    span = high - low
    normalized = (depth - low) / span if span > 1e-6 else np.zeros_like(depth)
    return (normalized * 255.0).clip(0, 255).astype("uint8")


def detect_objects(model_path: str | Path, image: np.ndarray, conf: float = 0.25) -> list[dict]:
    """YOLO 系列检测：返回 label / confidence / box，不产生图像文件。

    ultralytics 的 predict 需要路径或 ndarray；这里传 ndarray（BGR→RGB 由它自己处理）。
    """
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    result = model.predict(ensure_rgb_uint8(image), conf=float(conf), verbose=False)[0]
    names = getattr(model, "names", {})
    boxes = []
    for box in result.boxes:
        cls = int(box.cls[0])
        boxes.append({
            "label": names.get(cls, str(cls)),
            "confidence": round(float(box.conf[0]), 4),
            "box": [round(float(v), 2) for v in box.xyxy[0].tolist()],
        })
    return boxes


class RealESRGANUpscaler:
    def __init__(self, paths: dict[int, str], device: str = "cuda", tile: int = 512, context: int = 16):
        self.paths = {int(key): str(value) for key, value in paths.items()}
        self.device = device
        # tile=0 会让分块循环步长为 0（死循环），这里兜底到安全值
        self.tile = max(64, int(tile or 512))
        self.context = int(context)
        self._cache: dict[int, tuple[object, int]] = {}

    def _load(self, scale: int):
        import torch
        from spandrel import ModelLoader

        if scale in self._cache:
            return self._cache[scale]
        path = self.paths.get(scale)
        if not path or not Path(path).is_file():
            raise FileNotFoundError(f"Real-ESRGAN x{scale} model not found: {path}")
        try:
            descriptor = ModelLoader().load_from_file(path)
        except TypeError:
            descriptor = ModelLoader(device=torch.device(self.device)).load_from_file(path)
        model = getattr(descriptor, "model", descriptor)
        model_scale = int(getattr(descriptor, "scale", scale) or scale)
        model.eval().to(self.device)
        if self.device == "cuda":
            model.half()
        self._cache[scale] = (model, model_scale)
        return model, model_scale

    def _run_tile(self, model, tile_rgb: np.ndarray) -> np.ndarray:
        import torch

        tensor = torch.from_numpy(np.array(tile_rgb, copy=True)).permute(2, 0, 1).float().div(255.0).unsqueeze(0).to(self.device)
        # 统一走兜底路径：cuda 上先试 fp16，出现 NaN 或类型错误就降级 fp32。
        output = self._ensure_finite(model, tensor)
        output = torch.clamp(output.float(), 0, 1).detach()[0].permute(1, 2, 0).cpu().numpy()
        return (output * 255.0).round().astype(np.uint8)

    def _pad(self, image: np.ndarray, multiple: int = 4) -> tuple[np.ndarray, int, int]:
        import cv2

        h, w = image.shape[:2]
        ph, pw = (multiple - h % multiple) % multiple, (multiple - w % multiple) % multiple
        if not ph and not pw:
            return image, h, w
        border = cv2.BORDER_REFLECT_101 if min(h, w) > 4 else cv2.BORDER_REPLICATE
        return cv2.copyMakeBorder(image, 0, ph, 0, pw, border), h, w

    def _ensure_finite(self, model, tensor):
        """跑一次并保证输出有限；不行就用 fp32 重跑。

        SwinIR 这类网络内部有 fp32 常量，fp16 下会产出 NaN 或直接报
        "expected scalar type Half but found Float"。与其为每个模型维护一个精度字段，
        不如在出错时降级一次——结果正确优先于速度。
        """
        import torch

        def run(device_type, dtype):
            local = tensor
            if device_type == "cuda":
                local = tensor.half()
                with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=dtype):
                    return model(local)
            with torch.inference_mode():
                return model(local)

        try:
            output = run(self.device, torch.float16) if self.device == "cuda" else run("cpu", torch.float32)
        except RuntimeError:
            output = None
        if output is not None and torch.isfinite(output).all():
            return output
        model.float()
        with torch.inference_mode():
            return model(tensor.float())

    def _upscale_with_model(self, image: np.ndarray, scale: int) -> np.ndarray:
        model, real_scale = self._load(scale)
        h, w = image.shape[:2]
        padded, orig_h, orig_w = self._pad(image, 4)
        ph, pw = padded.shape[:2]
        output = np.zeros((ph * real_scale, pw * real_scale, 3), dtype=np.uint8)
        for y in range(0, ph, self.tile):
            y2 = min(ph, y + self.tile)
            for x in range(0, pw, self.tile):
                x2 = min(pw, x + self.tile)
                cy0, cy1 = max(0, y - self.context), min(ph, y2 + self.context)
                cx0, cx1 = max(0, x - self.context), min(pw, x2 + self.context)
                tile, _, _ = self._pad(padded[cy0:cy1, cx0:cx1], 4)
                tile_out = self._run_tile(model, tile)
                oy0, ox0 = (y - cy0) * real_scale, (x - cx0) * real_scale
                oy1 = oy0 + (y2 - y) * real_scale
                ox1 = ox0 + (x2 - x) * real_scale
                output[y * real_scale:y2 * real_scale, x * real_scale:x2 * real_scale] = tile_out[oy0:oy1, ox0:ox1]
        return output[: orig_h * real_scale, : orig_w * real_scale]

    def _apply_target(self, image: np.ndarray, target: tuple[int, int], fit: str) -> np.ndarray:
        import cv2

        tw, th = target
        h, w = image.shape[:2]
        if fit == "stretch":
            return cv2.resize(image, (tw, th), interpolation=cv2.INTER_LANCZOS4)
        if fit == "cover":
            factor = max(tw / float(w), th / float(h))
            rw, rh = max(tw, int(round(w * factor))), max(th, int(round(h * factor)))
            resized = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_LANCZOS4)
            x, y = (rw - tw) // 2, (rh - th) // 2
            return resized[y:y + th, x:x + tw]
        factor = min(tw / float(w), th / float(h))
        rw, rh = max(1, int(round(w * factor))), max(1, int(round(h * factor)))
        resized = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_LANCZOS4)
        if fit != "pad":
            return resized
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        x, y = (tw - rw) // 2, (th - rh) // 2
        canvas[y:y + rh, x:x + rw] = resized
        return canvas

    def upscale(self, image: np.ndarray, scale: float = 2.0, target: tuple[int, int] | None = None, fit: str = "contain", preferred_scale: int | None = None) -> tuple[np.ndarray, float]:
        import cv2

        h, w = image.shape[:2]
        if target is not None:
            tw, th = target
            if fit in {"contain", "pad"}:
                needed = min(tw / float(w), th / float(h))
            else:
                needed = max(tw / float(w), th / float(h))
            if needed <= 1.01:
                output = image.copy()
            else:
                output = self._upscale_with_model(image, preferred_scale or (2 if needed <= 2.0 else 4))
            output = self._apply_target(output, (tw, th), fit)
            actual = float(output.shape[1]) / float(w)
        else:
            factor = min(float(scale), MAX_OUTPUT_SIDE / float(max(h, w)))
            if factor <= 1.01:
                return image.copy(), 1.0
            output = self._upscale_with_model(image, preferred_scale or (2 if factor <= 2.0 else 4))
            actual = float(output.shape[1]) / float(w)
            if abs(actual - factor) > 0.01:
                output = cv2.resize(output, (max(1, int(round(w * factor))), max(1, int(round(h * factor)))), interpolation=cv2.INTER_LANCZOS4)
                actual = float(output.shape[1]) / float(w)
        if self.device == "cuda":
            import torch

            torch.cuda.empty_cache()
        else:
            gc.collect()
        return output, actual


class LamaInpainter:
    def __init__(self, path: str, device: str = "cuda", max_side: int = 2048):
        import torch

        if not Path(path).is_file():
            raise FileNotFoundError(f"LaMa model not found: {path}")
        self.device = device
        self.max_side = int(max_side)
        self.model = torch.jit.load(path, map_location="cpu")
        self.model.eval().to(torch.device(device))

    def _forward(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        import cv2
        import torch

        h, w = image.shape[:2]
        pad_h, pad_w = (8 - h % 8) % 8, (8 - w % 8) % 8
        border = cv2.BORDER_REFLECT_101 if min(h, w) > 8 else cv2.BORDER_REPLICATE
        image_p = cv2.copyMakeBorder(image, 0, pad_h, 0, pad_w, border)
        mask_p = cv2.copyMakeBorder(mask, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=0)
        image_t = torch.from_numpy(image_p).permute(2, 0, 1).float().div_(255.0).unsqueeze(0).to(self.device)
        mask_t = torch.from_numpy((mask_p > 127).astype(np.float32)).unsqueeze(0).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            output = self.model(image_t, mask_t)
        if isinstance(output, (tuple, list)):
            output = output[0]
        output = output[0].permute(1, 2, 0).float().cpu().numpy()
        if float(output.min()) < -0.05:
            output = (output + 1.0) * 0.5
        output = np.clip(output, 0.0, 1.0)
        return (output * 255.0).round().astype(np.uint8)[:h, :w]

    def inpaint(self, image: np.ndarray, mask: np.ndarray, feather: int = 2, crop_margin: int | None = None) -> np.ndarray:
        import cv2

        if image is None or mask is None or int(mask.max()) == 0:
            return image.copy()
        h, w = image.shape[:2]
        if crop_margin and crop_margin > 0:
            ys, xs = np.where(mask > 127)
            if len(xs):
                x0, x1 = int(xs.min()), int(xs.max()) + 1
                y0, y1 = int(ys.min()), int(ys.max()) + 1
                if (x1 - x0) * (y1 - y0) < 0.45 * h * w:
                    margin = int(crop_margin)
                    cx0, cy0 = max(0, x0 - margin), max(0, y0 - margin)
                    cx1, cy1 = min(w, x1 + margin), min(h, y1 + margin)
                    min_side = 256
                    if cx1 - cx0 < min_side:
                        grow = (min_side - (cx1 - cx0)) // 2 + 1
                        cx0, cx1 = max(0, cx0 - grow), min(w, cx1 + grow)
                    if cy1 - cy0 < min_side:
                        grow = (min_side - (cy1 - cy0)) // 2 + 1
                        cy0, cy1 = max(0, cy0 - grow), min(h, cy1 + grow)
                    repaired = image.copy()
                    repaired[cy0:cy1, cx0:cx1] = self._forward(image[cy0:cy1, cx0:cx1], mask[cy0:cy1, cx0:cx1])
                    return feather_composite(image, repaired, mask, feather=feather)
        longest = max(h, w)
        factor = min(1.0, float(self.max_side) / float(longest)) if longest else 1.0
        if factor < 1.0:
            small = cv2.resize(image, (max(8, int(round(w * factor))), max(8, int(round(h * factor)))), interpolation=cv2.INTER_AREA)
            small_mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            small, small_mask = image, mask
        repaired_small = self._forward(small, small_mask)
        repaired = cv2.resize(repaired_small, (w, h), interpolation=cv2.INTER_LANCZOS4)
        return feather_composite(image, repaired, mask, feather=feather)


class WatermarkDetector:
    def __init__(self, path: str, device: str = "cuda", imgsz: int = 1280):
        from ultralytics import YOLO

        if not Path(path).is_file():
            raise FileNotFoundError(f"watermark detector not found: {path}")
        self.device = 0 if str(device).startswith("cuda") else "cpu"
        self.imgsz = int(imgsz)
        self.model = YOLO(path)

    def detect(self, image: np.ndarray, conf: float = 0.25, expand: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        from PIL import Image

        h, w = image.shape[:2]
        results = self.model.predict(
            source=Image.fromarray(image), imgsz=self.imgsz, conf=float(conf),
            iou=0.5, verbose=False, device=self.device,
        )
        if not results or getattr(results[0], "boxes", None) is None or len(results[0].boxes) == 0:
            return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32), np.zeros((h, w), dtype=np.uint8)
        boxes_t = results[0].boxes
        boxes = boxes_t.xyxy.detach().float().cpu().numpy()
        scores = boxes_t.conf.detach().float().cpu().numpy()
        keep = (boxes[:, 2] > boxes[:, 0] + 2) & (boxes[:, 3] > boxes[:, 1] + 2)
        boxes, scores = boxes[keep], scores[keep]
        return boxes.astype(np.float32), scores.astype(np.float32), boxes_to_mask((h, w), boxes, expand=expand)


class ImageRestorer:
    def __init__(
        self,
        device: str | None = None,
        max_side: int = 2048,
        model_paths: dict[str, str] | None = None,
        tile: int = 512,
        context: int = 16,
    ):
        self.device = device or pick_device()
        self.max_side = int(max_side)
        # 分块大小决定显存峰值：OOM 的自动纠正就是把它调小。
        # 真正使用它的是 RealESRGANUpscaler，这里只负责把值传下去。
        self.tile = max(64, int(tile or 512))
        self.context = max(0, int(context))
        # 默认值也走 catalog + 唯一路径解析（`capabilities.model_path`），
        # 不再硬编码文件名：名字是投影，路径规则只有一份。
        watermark = capabilities.default_bindings("watermark_removal")
        self.model_paths = {
            "detector": str(capabilities.model_path(watermark["detector"])),
            "inpainter": str(capabilities.model_path(watermark["inpainter"])),
            "upscaler_x2": str(capabilities.model_path(capabilities.default_model_by_scale("upscale", 2))),
            "upscaler_x4": str(capabilities.model_path(capabilities.default_model_by_scale("upscale", 4))),
            **(model_paths or {}),
        }
        self._detector: WatermarkDetector | None = None
        self._inpainter: LamaInpainter | None = None
        self._upscaler: RealESRGANUpscaler | None = None
        self._detectors: dict[str, WatermarkDetector] = {}
        self._inpainters: dict[str, LamaInpainter] = {}
        self._upscalers: dict[tuple[str, str], RealESRGANUpscaler] = {}

    @property
    def detector(self) -> WatermarkDetector:
        return self.detector_for(self.model_paths["detector"])

    def detector_for(self, path: str) -> WatermarkDetector:
        if path not in self._detectors:
            self._detectors[path] = WatermarkDetector(path, device=self.device)
        return self._detectors[path]

    @property
    def inpainter(self) -> LamaInpainter:
        return self.inpainter_for(self.model_paths["inpainter"])

    def inpainter_for(self, path: str) -> LamaInpainter:
        if path not in self._inpainters:
            self._inpainters[path] = LamaInpainter(path, device=self.device, max_side=self.max_side)
        return self._inpainters[path]

    @property
    def upscaler(self) -> RealESRGANUpscaler:
        key = (self.model_paths["upscaler_x2"], self.model_paths["upscaler_x4"])
        if key not in self._upscalers:
            self._upscalers[key] = RealESRGANUpscaler(
                {2: self.model_paths["upscaler_x2"], 4: self.model_paths["upscaler_x4"]},
                device=self.device,
                tile=self.tile,
                context=self.context,
            )
        return self._upscalers[key]

    def upscaler_for(self, x2_path: str, x4_path: str) -> RealESRGANUpscaler:
        key = (x2_path, x4_path)
        if key not in self._upscalers:
            self._upscalers[key] = RealESRGANUpscaler(
                {2: x2_path, 4: x4_path}, device=self.device, tile=self.tile, context=self.context
            )
        return self._upscalers[key]

    def process(
        self,
        image: np.ndarray,
        mode: str = "watermark_upscale",
        scale: float = 2.0,
        target: tuple[int, int] | None = None,
        fit: str = "contain",
        conf: float = 0.25,
        expand: int = 6,
        progress=None,
    ) -> tuple[np.ndarray, dict]:
        steps = capabilities.steps_from_mode(mode)
        for step in steps:
            if step["capability"] == "watermark_removal":
                step["params"].update({"conf": conf, "expand": expand})
            elif step["capability"] == "upscale":
                step["params"].update({"scale": scale, "target": target, "fit": fit})
        return self.process_steps(image, steps, progress=progress)

    def process_steps(
        self,
        image: np.ndarray,
        steps: list[dict],
        progress=None,
    ) -> tuple[np.ndarray, dict]:
        image = ensure_rgb_uint8(image)
        start = time.perf_counter()
        info: dict = {"device": self.device, "input_size": [int(image.shape[1]), int(image.shape[0])], "steps": []}
        for index, step in enumerate(steps):
            capability = str(step.get("capability") or "")
            bindings = step.get("bindings") if isinstance(step.get("bindings"), dict) else {}
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            fraction = (index + 0.5) / max(len(steps), 1)
            if capability == "watermark_removal":
                if progress:
                    progress(fraction * 0.35, "检测水印…")
                watermark_defaults = capabilities.default_bindings("watermark_removal")
                detector_path = str(capabilities.model_path(str(bindings.get("detector") or watermark_defaults["detector"])))
                inpainter_path = str(capabilities.model_path(str(bindings.get("inpainter") or watermark_defaults["inpainter"])))
                boxes, scores, mask = self.detector_for(detector_path).detect(
                    image,
                    conf=float(params.get("conf") or 0.25),
                    expand=int(params.get("expand") or 6),
                )
                info["steps"].append({"capability": capability, "boxes": int(len(boxes)), "scores": [round(float(v), 3) for v in scores[:20]], "mask_ratio": round(mask_area_ratio(mask), 4)})
                if int(mask.max()) == 0:
                    raise ValueError("没有检测到水印；请降低检测阈值，或改用超分模式")
                if mask_area_ratio(mask) > 0.75:
                    raise ValueError("检测到的遮罩覆盖了超过 75% 的画面，可能是误检；请调高检测阈值")
                if progress:
                    progress(fraction, "LaMa 修复中…")
                image = self.inpainter_for(inpainter_path).inpaint(image, mask)
            elif capability == "denoise":
                if progress:
                    progress(fraction, "去噪中…")
                model_id = str(bindings.get("denoiser") or capabilities.default_bindings("denoise")["denoiser"])
                image = run_scale1_model(
                    capabilities.model_path(model_id),
                    image,
                    device=self.device,
                    tile=self.tile,
                )
                info["steps"].append({"capability": capability, "model": model_id})
            elif capability == "upscale":
                if progress:
                    progress(fraction, "Real-ESRGAN 超分中…")
                model_id = str(bindings.get("upscaler") or capabilities.default_bindings("upscale")["upscaler"])
                model_scale = capabilities.model_scale(model_id)
                upscaler = self.upscaler_for(self.model_paths["upscaler_x2"], self.model_paths["upscaler_x4"])
                image, actual = upscaler.upscale(
                    image,
                    scale=float(params.get("scale") or model_scale),
                    target=params.get("target") if isinstance(params.get("target"), tuple) else None,
                    fit=str(params.get("fit") or "contain"),
                    preferred_scale=model_scale,
                )
                info["steps"].append({"capability": capability, "model": model_id, "scale": round(float(actual), 3)})
                info["scale"] = round(float(actual), 3)
            else:
                raise ValueError(f"unsupported image capability: {capability}")
        info["output_size"] = [int(image.shape[1]), int(image.shape[0])]
        info["elapsed"] = round(time.perf_counter() - start, 2)
        if progress:
            progress(1.0, "完成")
        return image, info

    def process_file(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        mode: str = "watermark_upscale",
        scale: float = 2.0,
        target: tuple[int, int] | None = None,
        fit: str = "contain",
        conf: float = 0.25,
        expand: int = 6,
        steps: list[dict] | None = None,
        progress=None,
    ) -> dict:
        source = Path(input_path)
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        steps = steps or capabilities.steps_from_mode(mode)
        _SUFFIX = {"watermark_removal": "clean", "denoise": "denoise", "upscale": "sr"}
        suffix = "_".join(_SUFFIX.get(str(step.get("capability")), "out") for step in steps)
        target_path = output_root / f"{source.stem}_{suffix}.png"
        if target_path.resolve() == source.resolve():
            target_path = output_root / f"{source.stem}_{suffix}_out.png"
        image, info = self.process_steps(read_image(source), steps=steps, progress=progress)
        write_image(target_path, image)
        return {"path": str(target_path), "source": str(source), "mode": mode, "steps": [step["capability"] for step in steps], "info": info, "size": target_path.stat().st_size}
