"""Media model catalog, dependency status, and downloads."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import time
import urllib.request
from pathlib import Path

from . import image_ops

CATALOG_PATH = Path(__file__).with_name("media_catalog.json")


def load_catalog() -> list[dict]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return list(data.get("models") or [])


def model_dir(override: str | None = None) -> Path:
    value = str(override or "").strip()
    root = Path(value).expanduser() if value else image_ops.model_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def dependency_status() -> dict:
    packages = {
        "torch": "torch",
        "spandrel": "spandrel",
        "ultralytics": "ultralytics",
        "opencv": "cv2",
        "pillow": "PIL",
    }
    result = {
        key: bool(importlib.util.find_spec(module))
        for key, module in packages.items()
    }
    result["ffmpeg"] = bool(shutil.which("ffmpeg") or shutil.which("ffmpeg.exe"))
    return result


def status(override: str | None = None) -> dict:
    root = model_dir(override)
    models: list[dict] = []
    for item in load_catalog():
        path = root / item["name"]
        actual = path.stat().st_size if path.is_file() else 0
        expected = int(item.get("size") or 0)
        models.append(
            {
                **item,
                "path": str(path),
                "actualSize": actual,
                "downloaded": actual > 0 and (expected == 0 or actual == expected),
                "partial": actual > 0 and expected > 0 and actual != expected,
            }
        )
    return {
        "modelDir": str(root),
        "models": models,
        "environment": dependency_status(),
        "ready": all(item["downloaded"] for item in models),
    }


def _download(item: dict, root: Path, progress=None) -> dict:
    target = root / item["name"]
    expected = int(item.get("size") or 0)
    if target.is_file() and (expected == 0 or target.stat().st_size == expected):
        return {"id": item["id"], "path": str(target), "skipped": True, "size": target.stat().st_size}
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "MediaForge/0.1"})
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
            if progress and (now - last > 0.12 or downloaded >= total):
                last = now
                fraction = (downloaded / total) if total else 0.0
                progress(fraction, f"下载 {item['label']} · {downloaded / 1024 / 1024:.0f} MB")
    actual = part.stat().st_size
    if expected and actual != expected:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{item['name']} size mismatch: {actual} != {expected}")
    part.replace(target)
    return {"id": item["id"], "path": str(target), "skipped": False, "size": actual}


def download(model_id: str, override: str | None = None, progress=None) -> dict:
    item = next((entry for entry in load_catalog() if entry["id"] == model_id), None)
    if item is None:
        raise ValueError(f"unknown model: {model_id}")
    root = model_dir(override)
    result = _download(item, root, progress=progress)
    return {"model": item, "modelDir": str(root), **result}
