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
import os
import time
from pathlib import Path

import numpy as np

MAX_OUTPUT_SIDE = 8192
PRESETS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
}


def model_dir() -> Path:
    override = os.environ.get("MEDIAFORGE_MODEL_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "models"


def model_path(name: str) -> Path:
    return model_dir() / name


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


class RealESRGANUpscaler:
    def __init__(self, paths: dict[int, str], device: str = "cuda", tile: int = 512, context: int = 16):
        self.paths = {int(key): str(value) for key, value in paths.items()}
        self.device = device
        self.tile = int(tile)
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

        tensor = torch.from_numpy(tile_rgb).permute(2, 0, 1).float().div_(255.0).unsqueeze(0).to(self.device)
        if self.device == "cuda":
            tensor = tensor.half()
            with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.float16):
                output = model(tensor)
        else:
            with torch.inference_mode():
                output = model(tensor)
        output = output.float().clamp_(0, 1).detach()[0].permute(1, 2, 0).cpu().numpy()
        return (output * 255.0).round().astype(np.uint8)

    def _pad(self, image: np.ndarray, multiple: int = 4) -> tuple[np.ndarray, int, int]:
        import cv2

        h, w = image.shape[:2]
        ph, pw = (multiple - h % multiple) % multiple, (multiple - w % multiple) % multiple
        if not ph and not pw:
            return image, h, w
        border = cv2.BORDER_REFLECT_101 if min(h, w) > 4 else cv2.BORDER_REPLICATE
        return cv2.copyMakeBorder(image, 0, ph, 0, pw, border), h, w

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

    def upscale(self, image: np.ndarray, scale: float = 2.0, target: tuple[int, int] | None = None, fit: str = "contain") -> tuple[np.ndarray, float]:
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
                output = self._upscale_with_model(image, 2 if needed <= 2.0 else 4)
            output = self._apply_target(output, (tw, th), fit)
            actual = float(output.shape[1]) / float(w)
        else:
            factor = min(float(scale), MAX_OUTPUT_SIDE / float(max(h, w)))
            if factor <= 1.01:
                return image.copy(), 1.0
            output = self._upscale_with_model(image, 2 if factor <= 2.0 else 4)
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
    def __init__(self, device: str | None = None, max_side: int = 2048):
        self.device = device or pick_device()
        self.max_side = int(max_side)
        self._detector: WatermarkDetector | None = None
        self._inpainter: LamaInpainter | None = None
        self._upscaler: RealESRGANUpscaler | None = None

    @property
    def detector(self) -> WatermarkDetector:
        if self._detector is None:
            self._detector = WatermarkDetector(str(model_path("watermark_yolo11x.pt")), device=self.device)
        return self._detector

    @property
    def inpainter(self) -> LamaInpainter:
        if self._inpainter is None:
            self._inpainter = LamaInpainter(str(model_path("big-lama.pt")), device=self.device, max_side=self.max_side)
        return self._inpainter

    @property
    def upscaler(self) -> RealESRGANUpscaler:
        if self._upscaler is None:
            self._upscaler = RealESRGANUpscaler(
                {2: str(model_path("RealESRGAN_x2plus.pth")), 4: str(model_path("RealESRGAN_x4plus.pth"))},
                device=self.device,
            )
        return self._upscaler

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
        image = ensure_rgb_uint8(image)
        start = time.perf_counter()
        info: dict = {"device": self.device, "input_size": [int(image.shape[1]), int(image.shape[0])]}
        if mode in {"watermark", "watermark_upscale"}:
            if progress:
                progress(0.05, "检测水印…")
            boxes, scores, mask = self.detector.detect(image, conf=conf, expand=expand)
            info.update({"boxes": int(len(boxes)), "scores": [round(float(v), 3) for v in scores[:20]], "mask_ratio": round(mask_area_ratio(mask), 4)})
            if int(mask.max()) == 0:
                raise ValueError("没有检测到水印；请降低检测阈值，或改用超分模式")
            if mask_area_ratio(mask) > 0.75:
                raise ValueError("检测到的遮罩覆盖了超过 75% 的画面，可能是误检；请调高检测阈值")
            if progress:
                progress(0.2, "LaMa 修复中…")
            image = self.inpainter.inpaint(image, mask)
        if mode in {"upscale", "watermark_upscale"}:
            if progress:
                progress(0.55, "Real-ESRGAN 超分中…")
            image, actual = self.upscaler.upscale(image, scale=scale, target=target, fit=fit)
            info["scale"] = round(float(actual), 3)
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
        progress=None,
    ) -> dict:
        source = Path(input_path)
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        suffix = {"upscale": "sr", "watermark": "clean", "watermark_upscale": "clean_sr"}.get(mode, "out")
        target_path = output_root / f"{source.stem}_{suffix}.png"
        if target_path.resolve() == source.resolve():
            target_path = output_root / f"{source.stem}_{suffix}_out.png"
        image, info = self.process(read_image(source), mode=mode, scale=scale, target=target, fit=fit, conf=conf, expand=expand, progress=progress)
        write_image(target_path, image)
        return {"path": str(target_path), "source": str(source), "mode": mode, "info": info, "size": target_path.stat().st_size}
