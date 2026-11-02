#!/usr/bin/env python3
"""Download the original image-model set through MediaForge's shared catalog.

Weights are not committed: they are large and carry their own licenses.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "python"))
from core import model_hub  # noqa: E402

DEFAULT_MODEL_DIR = ROOT / "models"
# 仅定义这个历史脚本的默认选择；资产元数据与校验信息只读 catalog。
DEFAULT_MODEL_IDS = (
    "media:watermark-yolo11x",
    "media:lama",
    "media:real-esrgan-x2",
    "media:real-esrgan-x4",
)


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


def default_records(target_dir: Path) -> list[dict]:
    data = model_hub.list_models(modality="image", root=str(target_dir))
    by_id = {item["id"]: item for item in data["models"]}
    missing = [model_id for model_id in DEFAULT_MODEL_IDS if model_id not in by_id]
    if missing:
        raise RuntimeError(f"media catalog is missing: {', '.join(missing)}")
    return [by_id[model_id] for model_id in DEFAULT_MODEL_IDS]


def download(item: dict, target_dir: Path, force: bool = False) -> bool:
    """复用统一下载器；有 sha256 的条目会同时验证大小与内容。"""
    try:
        if force:
            for file in item["files"]:
                (target_dir / file["path"]).unlink(missing_ok=True)
        print(f"[get ] {item['label']} -> {item['name']}")

        def report(downloaded: int, total: int, _index: int, _count: int, _message: str) -> None:
            _progress(downloaded, total, item["name"])

        result = model_hub.download_model(
            item["id"], root=str(target_dir), detailed_progress=report,
        )
        print()
        for file in result["files"]:
            state = "skip" if file.get("skipped") else "ok  "
            print(f"[{state}] {Path(file['path']).name}  {_mb(int(file['size']))}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"\n[fail] {item['name']}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download MediaForge image models")
    parser.add_argument("--dir", default=str(DEFAULT_MODEL_DIR), help="target model directory")
    parser.add_argument("--proxy", help="proxy URL, for example http://127.0.0.1:7897")
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--only", help="download one model only, by catalog filename")
    args = parser.parse_args()

    if args.proxy:
        os.environ["HTTP_PROXY"] = args.proxy
        os.environ["HTTPS_PROXY"] = args.proxy
        print(f"Using proxy: {args.proxy}")

    target_dir = Path(args.dir).expanduser().resolve()
    records = default_records(target_dir)
    names = [item["name"] for item in records]
    if args.only and args.only not in names:
        parser.error("--only must be one of: " + ", ".join(names))
    todo = [item for item in records if not args.only or item["name"] == args.only]
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
