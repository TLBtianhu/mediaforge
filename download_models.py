#!/usr/bin/env python3
"""Download the image models used by MediaForge.

Weights are not committed: they are large and carry their own licenses.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = ROOT / "models"

MODELS = [
    {
        "name": "watermark_yolo11x.pt",
        "url": "https://huggingface.co/corzent/yolo11x_watermark_detection/resolve/main/best.pt",
        "size": 114_434_898,
        "label": "YOLO11x watermark detector",
    },
    {
        "name": "big-lama.pt",
        "url": "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt",
        "size": 205_669_692,
        "label": "LaMa inpainting",
    },
    {
        "name": "RealESRGAN_x2plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "size": 67_061_725,
        "label": "Real-ESRGAN 2x",
    },
    {
        "name": "RealESRGAN_x4plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "size": 67_040_989,
        "label": "Real-ESRGAN 4x",
    },
]


def _mb(value: float) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


def _progress(downloaded: int, total: int, name: str) -> None:
    if total <= 0:
        print(f"\r  {name}  {_mb(downloaded)}", end="", flush=True)
        return
    percent = min(100.0, downloaded * 100.0 / total)
    filled = int(percent / 2)
    bar = "#" * filled + "-" * (50 - filled)
    print(f"\r  {name} [{bar}] {percent:5.1f}%  {_mb(downloaded)} / {_mb(total)}", end="", flush=True)


def download(item: dict, target_dir: Path, force: bool = False) -> bool:
    expected = int(item["size"])
    destination = target_dir / item["name"]
    if destination.exists() and not force:
        actual = destination.stat().st_size
        if actual == expected:
            print(f"[skip] {destination.name} already present ({_mb(actual)})")
            return True
        print(f"[warn] {destination.name} size mismatch, re-downloading")
    target_dir.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "MediaForge/0.1"})
    print(f"[get ] {item['label']} -> {destination.name}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as handle:
            total = int(response.headers.get("Content-Length") or expected)
            downloaded = 0
            last = 0.0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last > 0.15:
                    _progress(downloaded, total, destination.name)
                    last = now
            _progress(downloaded, total, destination.name)
            print()
        actual = part.stat().st_size
        if actual != expected:
            part.unlink(missing_ok=True)
            print(f"[fail] {destination.name} size check failed: {_mb(actual)} != {_mb(expected)}")
            return False
        part.replace(destination)
        print(f"[ok  ] {destination.name}  {_mb(actual)}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"\n[fail] {destination.name}: {exc}")
        part.unlink(missing_ok=True)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download MediaForge image models")
    parser.add_argument("--dir", default=str(DEFAULT_MODEL_DIR), help="target model directory")
    parser.add_argument("--proxy", help="proxy URL, for example http://127.0.0.1:7897")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--only", choices=[item["name"] for item in MODELS], help="download one model only")
    args = parser.parse_args()

    if args.proxy:
        os.environ["HTTP_PROXY"] = args.proxy
        os.environ["HTTPS_PROXY"] = args.proxy
        print(f"Using proxy: {args.proxy}")

    target_dir = Path(args.dir).expanduser().resolve()
    todo = [item for item in MODELS if not args.only or item["name"] == args.only]
    ok = 0
    for item in todo:
        if download(item, target_dir, force=args.force):
            ok += 1
    print(f"\nDone {ok}/{len(todo)}; model directory: {target_dir}")
    if ok != len(todo):
        print("Some downloads failed. Retry with --proxy if the network is slow.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
