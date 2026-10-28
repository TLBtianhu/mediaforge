"""FFmpeg-backed media inspection and conversion for MediaForge.

The module is intentionally independent from pymss audio internals: it works
with image, audio and video files and only shells out to ffmpeg/ffprobe.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

VIDEO_FORMATS = {"mp4", "mkv", "mov", "webm", "avi"}
AUDIO_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg"}
IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "gif"}
SUPPORTED_FORMATS = VIDEO_FORMATS | AUDIO_FORMATS | IMAGE_FORMATS

ProgressCallback = Callable[[float, str], None]


def _find_tool(name: str) -> str:
    env_name = "MEDIAFORGE_FFMPEG" if name == "ffmpeg" else "MEDIAFORGE_FFPROBE"
    explicit = os.environ.get(env_name)
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which(name)
    if found:
        return found
    # PyMSS portable builds keep helper binaries under <root>/tools/.
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "tools" / "ffmpeg" / "bin" / f"{name}.exe",
        root / "tools" / f"{name}.exe",
        root / "tools" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        f"{name} not found. Install FFmpeg or set {env_name} to its executable."
    )


def _run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-1200:] or f"ffprobe failed ({proc.returncode})")
    return json.loads(proc.stdout or "{}")


def probe(path: str | Path) -> dict:
    """Return normalized media metadata for one file."""
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"media file not found: {src}")
    data = _run_json([
        _find_tool("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(src),
    ])
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or (video or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "path": str(src),
        "name": src.name,
        "size": int(fmt.get("size") or src.stat().st_size),
        "duration": duration,
        "format": str(fmt.get("format_name") or ""),
        "video": {
            "codec": video.get("codec_name"),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        } if video else None,
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
        } if audio else None,
    }


def _fps(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        num, den = value.split("/", 1)
        return round(float(num) / float(den), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _kind(path: str | Path) -> str:
    meta = probe(path)
    return "image" if _is_image(meta) else ("video" if meta["video"] else "audio")


def _is_image(meta: dict) -> bool:
    fmt = str(meta.get("format") or "").lower()
    return any(token in fmt for token in ("image2", "jpeg_pipe", "png_pipe", "webp_pipe", "gif"))


def _output_path(src: Path, output_dir: Path, fmt: str) -> Path:
    dst = output_dir / f"{src.stem}.{fmt}"
    if dst.resolve() == src.resolve() or dst.exists():
        dst = output_dir / f"{src.stem}_converted.{fmt}"
    return dst


def _video_args(fmt: str, crf: int, preset: str) -> list[str]:
    if fmt == "webm":
        return ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"]
    if fmt == "mkv":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-c:a", "aac", "-b:a", "192k"]
    return [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
    ]


def _audio_args(fmt: str) -> list[str]:
    if fmt == "mp3":
        return ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
    if fmt == "flac":
        return ["-vn", "-c:a", "flac"]
    if fmt == "wav":
        return ["-vn", "-c:a", "pcm_s16le"]
    if fmt == "m4a":
        return ["-vn", "-c:a", "aac", "-b:a", "192k"]
    if fmt == "aac":
        return ["-vn", "-c:a", "aac", "-b:a", "192k"]
    if fmt == "ogg":
        return ["-vn", "-c:a", "libvorbis", "-q:a", "6"]
    return ["-vn"]


def _image_args(fmt: str) -> list[str]:
    if fmt in {"jpg", "jpeg"}:
        return ["-frames:v", "1", "-q:v", "2"]
    return ["-frames:v", "1"]


def _build_command(src: Path, dst: Path, src_kind: str, fmt: str, crf: int, preset: str) -> list[str]:
    cmd = [_find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if src_kind == "image" and fmt in VIDEO_FORMATS:
        cmd += ["-loop", "1", "-t", "3"]
    cmd += ["-i", str(src)]
    if fmt == "gif":
        cmd += ["-vf", "fps=12", "-loop", "0"]
    elif fmt in VIDEO_FORMATS:
        # libx264 requires even dimensions; pad odd media by one pixel.
        cmd += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        cmd += _video_args(fmt, crf, preset)
    elif fmt in AUDIO_FORMATS:
        cmd += _audio_args(fmt)
    else:
        cmd += _image_args(fmt)
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def _progress_seconds(line: str) -> float | None:
    match = re.match(r"out_time_(?:ms|us)=(\d+)", line.strip())
    if not match:
        return None
    # ffmpeg's out_time_ms/out_time_us are both microseconds.
    return int(match.group(1)) / 1_000_000.0


def convert_one(
    src: str | Path,
    output_dir: str | Path,
    fmt: str,
    *,
    crf: int = 20,
    preset: str = "medium",
    progress: ProgressCallback | None = None,
) -> dict:
    source = Path(src)
    target_format = fmt.lower().lstrip(".")
    if target_format == "jpeg":
        target_format = "jpg"
    if target_format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported output format: {fmt}")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_meta = probe(source)
    src_kind = "image" if _is_image(src_meta) else ("video" if src_meta["video"] else "audio")
    dst = _output_path(source, out_dir, target_format)
    cmd = _build_command(source, dst, src_kind, target_format, int(crf), str(preset))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    duration = float(src_meta.get("duration") or 0.0)
    last_fraction = 0.0
    assert proc.stdout is not None
    for line in proc.stdout:
        seconds = _progress_seconds(line)
        if seconds is not None and duration > 0:
            last_fraction = max(last_fraction, min(0.99, seconds / duration))
            if progress:
                progress(last_fraction, f"正在转换 {source.name}")
    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed for {source.name}: {stderr.strip()[-1200:]}")
    if progress:
        progress(1.0, f"完成 {source.name}")
    return {
        "path": str(dst),
        "source": str(source),
        "kind": "image" if target_format in IMAGE_FORMATS else ("video" if target_format in VIDEO_FORMATS else "audio"),
        "format": target_format,
        "size": dst.stat().st_size if dst.exists() else 0,
    }


def convert_many(
    inputs: Iterable[str | Path],
    output_dir: str | Path,
    fmt: str,
    *,
    crf: int = 20,
    preset: str = "medium",
    progress: ProgressCallback | None = None,
) -> dict:
    files = [Path(item) for item in inputs if str(item).strip()]
    total = len(files)
    outputs: list[dict] = []
    failed: list[dict] = []
    for index, source in enumerate(files):
        def report(fraction: float, message: str, _index=index) -> None:
            if progress:
                progress((_index + max(0.0, min(1.0, fraction))) / max(total, 1), message)
        try:
            outputs.append(convert_one(source, output_dir, fmt, crf=crf, preset=preset, progress=report))
        except Exception as exc:  # noqa: BLE001
            failed.append({"path": str(source), "error": str(exc)})
    return {
        "outputs": outputs,
        "failed": failed,
        "skipped": [],
        "total": total,
        "completed": len(outputs),
    }
