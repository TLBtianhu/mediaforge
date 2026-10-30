"""Unified model hub for audio and image/video models.

Both catalogs are normalized to the same record shape. Providers only supply
metadata and file descriptors; browsing/status/downloads do not require an
inference runtime.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import time

from download_watchdog import ProgressWatchdog, stall_seconds
import urllib.parse
import urllib.request

import worker_paths
from pathlib import Path
from typing import Any, Callable

PYMSS_REPO = "baicai1145/pymss"
AUDIO_CATALOG = Path(__file__).with_name("audio_catalog.json")
MEDIA_CATALOG = Path(__file__).with_name("media_ops") / "media_catalog.json"

AUDIO_SOURCES = {
    "modelscope": f"https://www.modelscope.cn/models/{PYMSS_REPO}/resolve/master",
    "huggingface": f"https://huggingface.co/{PYMSS_REPO}/resolve/main",
    "hf-mirror": f"https://hf-mirror.com/{PYMSS_REPO}/resolve/main",
}

Progress = Callable[[float, str], None]


def _load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("models") or [])


def model_dir(override: str | None = None) -> Path:
    """模型根目录。解析规则只有一份，在 `worker_paths.model_root()`。"""
    root = worker_paths.model_root(override)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _audio_files(item: dict, source: str) -> list[dict]:
    base = AUDIO_SOURCES.get(source, AUDIO_SOURCES["modelscope"])
    paths: list[tuple[str, int]] = []
    primary = str(item.get("relpath") or "")
    if primary:
        paths.append((primary, int(item.get("size_bytes") or 0)))
    config = str(item.get("config_relpath") or "")
    if config:
        paths.append((config, 0))
    for auxiliary in item.get("auxiliary_relpaths") or []:
        if auxiliary:
            paths.append((str(auxiliary), 0))
    return [
        {
            "path": relpath,
            "size": size,
            "url": f"{base}/{urllib.parse.quote(relpath, safe='/')}",
        }
        for relpath, size in paths
    ]


def _media_files(item: dict) -> list[dict]:
    return [{
        "path": worker_paths.media_model_relpath(item.get("name") or ""),
        "size": int(item.get("size") or 0),
        "url": str(item.get("url") or ""),
        # catalog 里给了 sha256 就校验（下坏了/被替换都能发现）；没给就只比大小
        "sha256": str(item.get("sha256") or ""),
    }]


def _status(files: list[dict], root: Path) -> tuple[bool, bool, list[str]]:
    missing = [str(root / file["path"]) for file in files if not (root / file["path"]).is_file()]
    partial = any(
        (root / file["path"]).is_file()
        and int(file.get("size") or 0) > 0
        and (root / file["path"]).stat().st_size != int(file["size"])
        for file in files
    )
    return not missing, partial, missing


def _audio_record(item: dict, root: Path, source: str) -> dict:
    files = _audio_files(item, source)
    downloaded, partial, missing = _status(files, root)
    name = str(item.get("name") or "")
    category = str(item.get("primary_category") or "")
    category_cn = str(item.get("primary_category_cn") or category)
    return {
        "id": f"audio:{name}",
        "provider": "pymss-audio",
        "modality": "audio",
        "modalities": ["audio"],
        "name": name,
        "label": Path(name).stem,
        "task": str(item.get("model_type") or item.get("architecture") or "separation"),
        "capabilities": [],
        "roles": [],
        "category": category,
        "categoryCn": category_cn,
        "architecture": str(item.get("architecture") or ""),
        "size": int(item.get("size_bytes") or 0),
        "license": "pymss model license",
        "description": f"{category_cn} · {item.get('architecture') or item.get('model_type') or ''}".strip(" ·"),
        "files": files,
        "downloaded": downloaded,
        "partial": partial,
        "missingPaths": missing,
        "runtime": "pymss",
        "implemented": True,
        "downloadable": True,
        # --- 与 list_models（B 套）对齐的字段 ---------------------------------
        # 选择器和推理读这些值：少了它们，音频页会把模型判成"不可用"。
        # 值全部来自 catalog 与已解析的文件路径，不引入新的判定逻辑。
        "supported": bool(item.get("supported", True)),
        "sizeBytes": int(item.get("size_bytes") or 0),
        "modelPath": str(root / files[0]["path"]) if files else "",
        "configPath": str(root / files[1]["path"]) if len(files) > 1 else "",
        "source": "catalog",
        "modelType": str(item.get("model_type") or item.get("architecture") or ""),
        "targetStem": str(item.get("target_stem") or ""),
    }


def _media_record(item: dict, root: Path) -> dict:
    files = _media_files(item)
    downloaded, partial, missing = _status(files, root)
    return {
        "id": f"media:{item.get('id') or item.get('name')}",
        "provider": "media-image",
        "modality": str(item.get("modality") or "image"),
        "modalities": list(item.get("modalities") or [item.get("modality") or "image"]),
        "implemented": bool(item.get("implemented", True)),
        "name": str(item.get("name") or ""),
        "label": str(item.get("label") or item.get("name") or ""),
        "task": str(item.get("task") or ""),
        "capabilities": list(item.get("capabilities") or []),
        "roles": list(item.get("roles") or []),
        "category": str(item.get("task") or ""),
        "categoryCn": str(item.get("task") or ""),
        "architecture": str(item.get("architecture") or ""),
        "size": int(item.get("size") or 0),
        "license": str(item.get("license") or ""),
        "description": str(item.get("description") or ""),
        "files": files,
        "downloaded": downloaded,
        "partial": partial,
        "missingPaths": missing,
        "runtime": "torch",
        "downloadable": True,
    }


def list_models(
    query: str = "",
    modality: str = "all",
    *,
    source: str = "modelscope",
    root: str | None = None,
) -> dict:
    model_root = model_dir(root)
    media_items = _load(MEDIA_CATALOG)
    # 老布局（权重在根下）先搬到 media/，否则老用户会看到"模型未下载"。
    worker_paths.migrate_media_models(model_root, [item.get("name") for item in media_items])
    records = [
        _audio_record(item, model_root, source)
        for item in _load(AUDIO_CATALOG)
        if item.get("supported", True)
    ]
    records.extend(_media_record(item, model_root) for item in media_items)
    text = str(query or "").strip().lower()
    if text:
        records = [
            record for record in records
            if text in record["name"].lower()
            or text in record["label"].lower()
            or text in record["categoryCn"].lower()
            or text in record["architecture"].lower()
        ]
    if modality in {"audio", "image", "video"}:
        records = [record for record in records if modality in record.get("modalities", [record["modality"]])]
    records.sort(key=lambda record: (record["modality"] != "audio", record["categoryCn"], record["label"].lower()))
    environment = {
        "pymss": importlib.util.find_spec("pymss") is not None,
        "torch": importlib.util.find_spec("torch") is not None,
        "spandrel": importlib.util.find_spec("spandrel") is not None,
        "ultralytics": importlib.util.find_spec("ultralytics") is not None,
        "opencv": importlib.util.find_spec("cv2") is not None,
        "ffmpeg": bool(shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")),
    }
    return {
        "modelDir": str(model_root),
        "models": records,
        "environment": environment,
        "count": len(records),
        "downloadedCount": sum(1 for record in records if record["downloaded"]),
    }


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(
    url: str,
    target: Path,
    expected: int,
    progress: Progress | None,
    label: str,
    expected_sha256: str = "",
) -> dict:
    """下载一个文件：`.part` 落盘 → 大小校验 → 内容校验 → 原子改名。

    内容校验（2026-10-29 补）：只比大小挡不住"被代理改写""服务端回了错误页但长度凑巧对上"
    这类损坏——坏文件会安静地留在 models/ 里，等到 `torch.load` 才炸，报错和"下载"毫无关系。
    catalog 里给了 `sha256` 才校验；没给就仍然只比大小（向后兼容，也不会误杀）。
    """
    if target.is_file():
        size_ok = not expected or target.stat().st_size == expected
        hash_ok = True
        if size_ok and expected_sha256:
            hash_ok = _sha256_of(target).lower() == expected_sha256.lower()
        if size_ok and hash_ok:
            return {"path": str(target), "skipped": True, "size": target.stat().st_size}
        # 大小对但内容不对：这是"看起来下载完了"的坏文件，必须重下。
        target.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "MediaForge/0.1"})
    # 看门狗：连接建起来但一直不吐数据（或吐一半停住）时要报错，不能永远挂着。
    # urlopen 的 timeout 只覆盖"连不上/等不到响应"，管不了"响应来了但不继续"。
    watchdog = ProgressWatchdog(label, timeout=stall_seconds())
    with watchdog, urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as handle:
        total = int(response.headers.get("Content-Length") or expected or 0)
        done = 0
        last = 0.0
        while True:
            # read 也可能永久阻塞：用较短超时的 socket 读不到就交给看门狗
            try:
                chunk = response.read(1024 * 1024)
            except TimeoutError:
                watchdog.beat(0)
                continue
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            watchdog.beat(len(chunk))
            if progress and (time.time() - last > 0.12 or (total and done >= total)):
                last = time.time()
                progress(min(0.99, done / total) if total else 0.5, f"下载 {label} · {done / 1024 / 1024:.0f} MB")
    actual = part.stat().st_size
    if expected and actual != expected:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"{target.name} size mismatch: {actual} != {expected}")
    if expected_sha256:
        actual_hash = _sha256_of(part)
        if actual_hash.lower() != expected_sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"{target.name} checksum mismatch: got {actual_hash[:16]}... "
                f"expected {expected_sha256[:16]}... (已删除半成品，重新下载即可)"
            )
    part.replace(target)
    return {"path": str(target), "skipped": False, "size": actual}


def download_model(
    model_id: str,
    root: str | None = None,
    *,
    source: str = "modelscope",
    progress: Progress | None = None,
) -> dict:
    data = list_models(source=source, root=root)
    record = next((item for item in data["models"] if item["id"] == model_id), None)
    if record is None:
        raise ValueError(f"unknown model: {model_id}")
    model_root = Path(data["modelDir"])
    files = [file for file in record["files"] if file.get("path") and file.get("url")]
    if not files:
        raise RuntimeError(f"model has no downloadable files: {model_id}")
    total = max(1, len(files))
    results = []
    for index, file in enumerate(files):
        def report(fraction: float, message: str, _index=index) -> None:
            if progress:
                progress((_index + max(0.0, min(1.0, fraction))) / total, message)
        results.append(
            _download_file(
                file["url"],
                model_root / file["path"],
                int(file.get("size") or 0),
                report,
                record["label"],
                str(file.get("sha256") or ""),
            )
        )
    return {"model": record, "files": results, "modelDir": str(model_root)}
