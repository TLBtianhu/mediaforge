"""Video restoration: decode once -> run every step per frame -> encode once.

2026-10-29 两次改造：

1. **帧不再落盘**。原来是"抽出全部帧成 PNG → 逐帧写回另一套 PNG → 再编码"，
   1080p 十分钟 ≈ 18000 帧 × 1.5–3MB × 2 ≈ 50–100GB，全在 `%TEMP%`。
   现在两条管道对接：ffmpeg 解 `rawvideo rgb24` 走 stdout，逐帧在内存处理，
   直接写进编码器的 stdin。磁盘占用从 O(帧数 × 帧大小) 降到 O(1)。

2. **多步工作流只编解码一次**。原来工作流逐步产出中间文件：N 步 = N 次解码 + N 次编码
   （H.264 代际损失 × N、时间 × N、每步还重建一次 ImageRestorer 重新加载模型）。
   现在步骤被抽象成 `FrameStep`，一帧跑完所有步骤再进编码器，**一次解码、一次编码**。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _read_exact(stream, size: int) -> bytes | None:
    """从一个管道里读满 `size` 字节；对端关闭则返回 None。

    管道会短读——`read(n)` 拿到多少算多少，帧边界靠我们自己拼。
    """
    if size <= 0:
        return b""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return chunks[0] if len(chunks) == 1 else b"".join(chunks)


class _StreamProcess:
    """跑一条 ffmpeg 管道，并用后台线程排空 stderr。

    必须排空：编码器可能跑几分钟，stderr 管道（约 64KB）一满，ffmpeg 就会阻塞在写日志上，
    而我们在等它的输入——典型的管道死锁。顺手保留最后几十行，失败时能给出真实原因。
    """

    def __init__(
        self,
        command: list[str],
        label: str,
        *,
        want_stdin: bool = False,
        want_stdout: bool = False,
    ) -> None:
        self.label = label
        self._tail: list[str] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if want_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if want_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._drain = threading.Thread(target=self._drain_stderr, name=f"mf-{label}", daemon=True)
        self._drain.start()

    def _drain_stderr(self) -> None:
        stream = self.proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                with self._lock:
                    self._tail.append(line)
                    del self._tail[:-40]
        except (OSError, ValueError):
            return

    def tail(self) -> str:
        with self._lock:
            return "\n".join(self._tail[-12:])

    def error(self) -> str:
        return f"{self.label} failed:\n{self.tail() or '(no ffmpeg output)'}"

    def kill(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass


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


@dataclass(frozen=True)
class FrameStep:
    """逐帧管线里的一个步骤。`mode` 只认 "watermark" / "upscale"。"""

    mode: str
    scale: float = 1.0
    target: tuple[int, int] | None = None
    fit: str = "contain"
    conf: float = 0.25
    expand: int = 8
    detect_mode: str = "fixed"
    preferred_scale: int | None = None


@dataclass
class _StepState:
    fixed_mask: np.ndarray | None = None
    last_mask: np.ndarray | None = None
    applied_scale: float = 1.0

    def summary(self, step: FrameStep) -> dict[str, Any]:
        return {
            "mode": step.mode,
            "scale": round(float(step.scale), 3),
            "applied_scale": round(float(self.applied_scale), 3),
            "target": list(step.target) if step.target else None,
            "fit": step.fit if step.target else None,
            "detect_mode": step.detect_mode if step.mode == "watermark" else None,
            "conf": step.conf if step.mode == "watermark" else None,
        }


_MODE_SUFFIX = {"upscale": "sr", "watermark": "clean", "watermark_upscale": "clean_sr"}


def steps_from_mode(
    mode: str,
    *,
    scale: float = 1.0,
    target: tuple[int, int] | None = None,
    fit: str = "contain",
    conf: float = 0.25,
    expand: int = 8,
    detect_mode: str = "fixed",
    preferred_scale: int | None = None,
) -> list[FrameStep]:
    """把旧的 `mode` 三选一翻译成步骤列表（顺序与 `capabilities.steps_from_mode` 一致）。"""
    steps: list[FrameStep] = []
    if mode in {"watermark", "watermark_upscale"}:
        steps.append(FrameStep(mode="watermark", conf=conf, expand=expand, detect_mode=detect_mode))
    if mode in {"upscale", "watermark_upscale"}:
        steps.append(
            FrameStep(mode="upscale", scale=scale, target=target, fit=fit, preferred_scale=preferred_scale)
        )
    if not steps:
        raise ValueError(f"unsupported video mode: {mode}")
    return steps


def _stream(
    input_path: str | Path,
    output_dir: str | Path,
    steps: list[FrameStep],
    *,
    suffix: str,
    layout: str = "video",
    preview_seconds: float = 0.0,
    crf: int = 18,
    preset: str = "fast",
    restorer: ImageRestorer | None = None,
    progress=None,
) -> dict:
    if not steps:
        raise ValueError("video pipeline requires at least one step")
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"video not found: {source}")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="mfvideo_"))
    started = time.perf_counter()
    try:
        ascii_source = work / f"input{source.suffix.lower()}"
        shutil.copy2(source, ascii_source)
        meta = ffmpeg.probe(ascii_source)
        video = meta.get("video") or {}
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("cannot determine the input frame size")
        fps = float(video.get("fps") or 25.0)
        if fps <= 1:
            fps = 25.0
        duration = float(meta.get("duration") or 0.0)
        if preview_seconds and preview_seconds > 0:
            duration = min(duration, float(preview_seconds)) if duration else float(preview_seconds)
        frame_bytes = width * height * 3
        estimated_total = max(1, int(round(duration * fps))) if duration else 0

        audio_path = work / "audio.m4a"
        audio_ok = bool(meta.get("audio")) and _extract_audio(
            ascii_source, audio_path, float(preview_seconds or 0)
        )

        restorer = restorer or ImageRestorer()
        states = [_StepState() for _ in steps]
        first_boxes: np.ndarray = np.zeros((0, 4))
        first_scores: np.ndarray = np.zeros((0,))
        actual_scale = 1.0

        decode_command = [
            ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", str(ascii_source),
        ]
        if preview_seconds and preview_seconds > 0:
            decode_command += ["-t", f"{float(preview_seconds):.3f}"]
        decode_command += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-vsync", "0", "-"]

        if progress:
            progress(0.02, "解码首帧…")
        decoder = _StreamProcess(decode_command, "frame decoding", want_stdout=True)
        encoder: _StreamProcess | None = None

        def run_steps(rgb: np.ndarray, first: bool) -> np.ndarray:
            nonlocal first_boxes, first_scores, actual_scale
            frame = rgb
            for step, state in zip(steps, states):
                if step.mode == "watermark":
                    if step.detect_mode == "per_frame":
                        boxes, scores, detected = restorer.detector.detect(
                            frame, conf=step.conf, expand=step.expand
                        )
                        if first:
                            if int(detected.max()) == 0:
                                raise ValueError("没有在首帧检测到水印；请降低阈值，或改用逐帧检测")
                            if not len(first_boxes):
                                first_boxes, first_scores = boxes, scores
                        if int(detected.max()) > 0:
                            state.last_mask = detected
                        mask = state.last_mask
                    else:
                        if first:
                            boxes, scores, detected = restorer.detector.detect(
                                frame, conf=step.conf, expand=step.expand
                            )
                            if int(detected.max()) == 0:
                                raise ValueError("没有在首帧检测到水印；请降低阈值，或改用逐帧检测")
                            if not len(first_boxes):
                                first_boxes, first_scores = boxes, scores
                            state.fixed_mask = detected
                            state.last_mask = detected
                        mask = state.fixed_mask
                    if mask is not None and int(mask.max()) > 0:
                        frame = restorer.inpainter.inpaint(frame, mask, crop_margin=160)
                elif step.mode == "upscale":
                    if step.target is not None or float(step.scale) > 1.01:
                        frame, applied = restorer.upscaler.upscale(
                            frame,
                            scale=float(step.scale),
                            target=step.target,
                            fit=step.fit,
                            preferred_scale=step.preferred_scale,
                        )
                        state.applied_scale = float(applied)
                        actual_scale = float(applied)
                else:
                    raise ValueError(f"unsupported frame step: {step.mode}")
            return frame

        try:
            stream = decoder.proc.stdout
            if stream is None:
                raise RuntimeError("frame decoder has no stdout pipe")
            first_raw = _read_exact(stream, frame_bytes)
            if first_raw is None:
                raise RuntimeError("no video frames were decoded")

            first_rgb = np.frombuffer(first_raw, dtype=np.uint8).reshape(height, width, 3).copy()
            first_out = run_steps(first_rgb, True)
            out_height, out_width = int(first_out.shape[0]), int(first_out.shape[1])

            encode_command = [
                ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_width}x{out_height}",
                "-framerate", f"{fps:.6f}", "-i", "-",
            ]
            if audio_ok:
                encode_command += ["-i", str(audio_path)]
            encode_command += [
                "-c:v", "libx264", "-preset", str(preset), "-crf", str(int(crf)),
                "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            ]
            if audio_ok:
                encode_command += [
                    "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-map", "0:v:0", "-map", "1:a:0",
                ]
            encoded = work / "output.mp4"
            encode_command += ["-movflags", "+faststart", str(encoded)]

            encoder = _StreamProcess(encode_command, "video encode", want_stdin=True)
            sink = encoder.proc.stdin
            if sink is None:
                raise RuntimeError("encoder has no stdin pipe")

            last_size = [width, height]

            def emit(frame: np.ndarray) -> None:
                nonlocal last_size
                array = np.ascontiguousarray(frame)
                if array.dtype != np.uint8:
                    array = array.astype(np.uint8)
                if array.ndim != 3 or array.shape[2] != 3:
                    raise RuntimeError(f"unexpected frame shape: {array.shape}")
                if array.shape[0] != out_height or array.shape[1] != out_width:
                    raise RuntimeError(
                        "输出尺寸在逐帧处理中发生了变化（首帧 %dx%d，本帧 %dx%d）；"
                        "编码器无法中途改变尺寸，请把缩放目标固定为同一尺寸。"
                        % (out_width, out_height, array.shape[1], array.shape[0])
                    )
                sink.write(array.tobytes())
                last_size = [int(array.shape[1]), int(array.shape[0])]

            processed_at = time.perf_counter()
            emit(first_out)
            frames = 1
            if progress:
                total_text = f"/约 {estimated_total}" if estimated_total else ""
                progress(0.06, f"逐帧处理 1{total_text}")

            while True:
                raw = _read_exact(stream, frame_bytes)
                if raw is None:
                    break
                rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
                emit(run_steps(rgb, False))
                frames += 1
                if progress:
                    elapsed = time.perf_counter() - processed_at
                    eta = (
                        elapsed / frames * (estimated_total - frames)
                        if estimated_total and estimated_total > frames
                        else 0.0
                    )
                    detail = f"逐帧处理 {frames}"
                    if estimated_total:
                        detail += f"/约 {estimated_total}"
                    if eta > 0:
                        eta_text = f"{eta / 60:.1f} min" if eta >= 60 else f"{eta:.0f}s"
                        detail += f" · 剩余约 {eta_text}"
                    ratio = (frames / estimated_total) if estimated_total else 0.0
                    progress(0.06 + 0.9 * min(1.0, ratio), detail)

            if progress:
                progress(0.97, "封装视频（含音频）…")
            sink.close()
            encoder.proc.wait()
            if encoder.proc.returncode != 0:
                raise RuntimeError(encoder.error())
            decoder.proc.wait()
            if decoder.proc.returncode not in (0, None):
                raise RuntimeError(decoder.error())
        except BrokenPipeError as error:
            raise RuntimeError(encoder.error() if encoder is not None else str(error)) from error
        finally:
            if encoder is not None:
                encoder.kill()
            decoder.kill()

        # 命名保持既有约定：单模态 `<stem>_video_<suffix>.mp4`，工作流 `<stem>_workflow_<suffix>.mp4`
        destination = output_root / f"{source.stem}_{layout}_{suffix}.mp4"
        shutil.copy2(work / "output.mp4", destination)
        step_infos = [state.summary(step) for step, state in zip(steps, states)]
        info: dict[str, Any] = {
            "fps": round(fps, 3),
            "frames": frames,
            "duration": round(duration or frames / fps, 3),
            "input_size": [width, height],
            "output_size": last_size,
            "audio": audio_ok,
            "elapsed": round(time.perf_counter() - started, 2),
            "output_path": str(destination),
            "steps": step_infos,
            # 单步时保留旧字段，避免调用方/前端按旧结构读不到信息
            "mode": steps[0].mode if len(steps) == 1 else "workflow",
            "scale": round(float(actual_scale), 3),
            "target_size": list(steps[-1].target) if steps[-1].target else None,
            "fit": steps[-1].fit if steps[-1].target else None,
            "detect_mode": steps[0].detect_mode if (len(steps) == 1 and steps[0].mode == "watermark") else None,
            "boxes": int(len(first_boxes)),
            "scores": [round(float(value), 3) for value in first_scores[:10]],
        }
        if progress:
            progress(1.0, "完成")
        return {
            "path": str(destination),
            "source": str(source),
            "mode": info["mode"],
            "info": info,
            "size": destination.stat().st_size,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
    """单模态入口（旧签名，保持兼容）：把 mode 翻译成步骤后走同一条流式管线。"""
    if detect_mode not in {"fixed", "per_frame"}:
        raise ValueError("detect_mode must be fixed or per_frame")
    steps = steps_from_mode(
        mode,
        scale=scale,
        target=target,
        fit=fit,
        conf=conf,
        expand=expand,
        detect_mode=detect_mode,
        preferred_scale=preferred_scale,
    )
    return _stream(
        input_path,
        output_dir,
        steps,
        suffix=_MODE_SUFFIX.get(mode, "out"),
        preview_seconds=preview_seconds,
        crf=crf,
        preset=preset,
        restorer=restorer,
        progress=progress,
    )


def process_video_steps(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    steps: list[FrameStep],
    suffix: str = "workflow",
    preview_seconds: float = 0.0,
    crf: int = 18,
    preset: str = "fast",
    restorer: ImageRestorer | None = None,
    progress=None,
) -> dict:
    """多步入口：**一次解码、逐帧跑完所有步骤、一次编码**。

    工作流原来是"每步产出中间文件"，N 步 = N 次 H.264 编解码；现在只编一次，
    模型也只需要一个 `ImageRestorer`（调用方合并各步的 model_paths 后传进来）。
    """
    return _stream(
        input_path,
        output_dir,
        list(steps),
        suffix=suffix,
        layout="workflow",
        preview_seconds=preview_seconds,
        crf=crf,
        preset=preset,
        restorer=restorer,
        progress=progress,
    )
