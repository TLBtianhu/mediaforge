"""Video restoration: extract frames -> image restoration -> encode + mux audio."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from . import ffmpeg
from .image_ops import ImageRestorer


def _run(command: list[str], label: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise RuntimeError(f"{label} failed:\n{tail}")
    return proc


def _extract_audio(source: Path, target: Path, seconds: float = 0.0) -> bool:
    command = [
        ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-vn", "-ac", "2", "-c:a", "aac", "-b:a", "192k",
    ]
    if seconds and seconds > 0:
        command += ["-t", f"{float(seconds):.3f}"]
    command.append(str(target))
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode == 0 and target.exists() and target.stat().st_size > 500


def process_video(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    mode: str = "watermark_upscale",
    scale: float = 1.0,
    target: tuple[int, int] | None = None,
    fit: str = "contain",
    conf: float = 0.25,
    expand: int = 8,
    detect_mode: str = "fixed",
    preferred_scale: int | None = None,
    preview_seconds: float = 0.0,
    crf: int = 18,
    preset: str = "fast",
    restorer: ImageRestorer | None = None,
    progress=None,
) -> dict:
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"video not found: {source}")
    if detect_mode not in {"fixed", "per_frame"}:
        raise ValueError("detect_mode must be fixed or per_frame")
    if mode not in {"upscale", "watermark", "watermark_upscale"}:
        raise ValueError(f"unsupported video mode: {mode}")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="mfvideo_"))
    started = time.perf_counter()
    try:
        ascii_source = work / f"input{source.suffix.lower()}"
        shutil.copy2(source, ascii_source)
        meta = ffmpeg.probe(ascii_source)
        video = meta.get("video") or {}
        fps = float(video.get("fps") or 25.0)
        if fps <= 1:
            fps = 25.0
        duration = float(meta.get("duration") or 0.0)
        if preview_seconds and preview_seconds > 0:
            duration = min(duration, float(preview_seconds)) if duration else float(preview_seconds)

        if progress:
            progress(0.02, "提取视频帧…")
        in_dir = work / "frames_in"
        out_dir = work / "frames_out"
        in_dir.mkdir()
        out_dir.mkdir()
        extract = [ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(ascii_source)]
        if preview_seconds and preview_seconds > 0:
            extract += ["-t", f"{float(preview_seconds):.3f}"]
        extract += ["-vsync", "0", "-compression_level", "3", str(in_dir / "%08d.png")]
        _run(extract, "frame extraction")

        frames = sorted(in_dir.glob("*.png"))
        if not frames:
            raise RuntimeError("no frames extracted")
        total = len(frames)

        audio_path = work / "audio.m4a"
        audio_ok = bool(meta.get("audio")) and _extract_audio(ascii_source, audio_path, float(preview_seconds or 0))

        import cv2

        first_bgr = cv2.imread(str(frames[0]))
        if first_bgr is None:
            raise RuntimeError("cannot read the first frame")
        first_rgb = cv2.cvtColor(first_bgr, cv2.COLOR_BGR2RGB)
        height, width = first_rgb.shape[:2]

        restorer = restorer or ImageRestorer()
        fixed_mask: np.ndarray | None = None
        last_mask: np.ndarray | None = None
        if mode in {"watermark", "watermark_upscale"}:
            first_boxes, first_scores, first_mask = restorer.detector.detect(first_rgb, conf=conf, expand=expand)
            if int(first_mask.max()) == 0:
                raise ValueError("没有在首帧检测到水印；请降低阈值，或改用逐帧检测")
            fixed_mask = first_mask if detect_mode == "fixed" else None
            last_mask = first_mask
        else:
            first_boxes, first_scores = np.zeros((0, 4)), np.zeros((0,))

        if progress:
            progress(0.06, f"共 {total} 帧，开始逐帧处理…")
        processed_at = time.perf_counter()
        actual_scale = 1.0
        last_size = [width, height]
        for index, frame_path in enumerate(frames):
            bgr = cv2.imread(str(frame_path))
            if bgr is None:
                raise RuntimeError(f"cannot read frame: {frame_path.name}")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

            frame_mask = fixed_mask
            if mode in {"watermark", "watermark_upscale"} and detect_mode == "per_frame":
                _boxes, _scores, detected = restorer.detector.detect(rgb, conf=conf, expand=expand)
                if int(detected.max()) > 0:
                    frame_mask = detected
                    last_mask = detected
                else:
                    frame_mask = last_mask
            if frame_mask is not None and int(frame_mask.max()) > 0:
                rgb = restorer.inpainter.inpaint(rgb, frame_mask, crop_margin=160)

            if mode in {"upscale", "watermark_upscale"}:
                if target is not None or float(scale) > 1.01:
                    rgb, actual_scale = restorer.upscaler.upscale(rgb, scale=float(scale), target=target, fit=fit, preferred_scale=preferred_scale)
            last_size = [int(rgb.shape[1]), int(rgb.shape[0])]
            cv2.imwrite(str(out_dir / f"{index + 1:08d}.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

            if progress:
                done = index + 1
                elapsed = time.perf_counter() - processed_at
                eta = elapsed / done * (total - done)
                eta_text = f"{eta / 60:.1f} min" if eta >= 60 else f"{eta:.0f}s"
                progress(0.06 + 0.9 * done / total, f"逐帧处理 {done}/{total} · 剩余约 {eta_text}")

        if progress:
            progress(0.97, "编码视频（含音频）…")
        encoded = work / "output.mp4"
        encode = [
            ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", f"{fps:.6f}", "-i", str(out_dir / "%08d.png"),
        ]
        if audio_ok:
            encode += ["-i", str(audio_path)]
        encode += [
            "-c:v", "libx264", "-preset", str(preset), "-crf", str(int(crf)),
            "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        ]
        if audio_ok:
            encode += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        encode += ["-movflags", "+faststart", str(encoded)]
        _run(encode, "video encode")

        suffix = {"upscale": "sr", "watermark": "clean", "watermark_upscale": "clean_sr"}.get(mode, "out")
        destination = output_root / f"{source.stem}_video_{suffix}.mp4"
        shutil.copy2(encoded, destination)
        info = {
            "fps": round(fps, 3),
            "frames": total,
            "duration": round(duration or total / fps, 3),
            "input_size": [width, height],
            "output_size": last_size,
            "scale": round(float(actual_scale), 3),
            "target_size": list(target) if target else None,
            "fit": fit if target else None,
            "audio": audio_ok,
            "mode": mode,
            "detect_mode": detect_mode if mode != "upscale" else None,
            "boxes": int(len(first_boxes)),
            "scores": [round(float(v), 3) for v in first_scores[:10]],
            "elapsed": round(time.perf_counter() - started, 2),
            "output_path": str(destination),
        }
        if progress:
            progress(1.0, "完成")
        return {"path": str(destination), "source": str(source), "mode": mode, "info": info, "size": destination.stat().st_size}
    finally:
        shutil.rmtree(work, ignore_errors=True)
