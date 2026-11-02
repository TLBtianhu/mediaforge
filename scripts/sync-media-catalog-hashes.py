#!/usr/bin/env python3
"""为媒体 catalog 补真实 SHA-256；--check 只做离线覆盖检查。

下载仅在显式运行时发生。每个文件计算完哈希即删除，报告留在独立工作目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "python" / "media_ops" / "media_catalog.json"
sys.path.insert(0, str(ROOT / "python"))
from core import model_hub  # noqa: E402

SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def downloadable(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in catalog["models"] if item.get("implemented") and item.get("url")]


def hash_problems(catalog: dict[str, Any]) -> list[str]:
    problems = []
    for item in downloadable(catalog):
        declared = item.get("files")
        if declared:
            hashes = item.get("file_hashes") or {}
            for file in declared:
                relative = str(file.get("path") or "")
                value = str(file.get("sha256") or hashes.get(relative) or "")
                if not SHA256.fullmatch(value):
                    problems.append(f"{item.get('id')}:{relative}: missing or invalid sha256")
            continue
        value = str(item.get("sha256") or "")
        if not SHA256.fullmatch(value):
            problems.append(f"{item.get('id') or item.get('name')}: missing or invalid sha256")
    return problems


def apply_hashes(catalog: dict[str, Any], hashes: dict[str, Any]) -> int:
    updated = 0
    for item in downloadable(catalog):
        declared = item.get("files")
        if declared:
            reported = hashes.get(str(item.get("id")))
            if not isinstance(reported, dict):
                continue
            target = item.setdefault("file_hashes", {})
            changed = False
            for file in declared:
                relative = str(file.get("path") or "")
                existing = str(file.get("sha256") or target.get(relative) or "")
                if SHA256.fullmatch(existing):
                    continue
                digest = str(reported.get(relative) or "")
                if SHA256.fullmatch(digest):
                    target[relative] = digest.lower()
                    changed = True
            if changed:
                item["size"] = sum(int(file.get("size") or 0) for file in declared)
                updated += 1
            continue
        if SHA256.fullmatch(str(item.get("sha256") or "")):
            continue
        digest = str(hashes.get(str(item.get("id"))) or "")
        if SHA256.fullmatch(digest):
            item["sha256"] = digest.lower()
            updated += 1
    return updated


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_files(work: Path, result: dict[str, Any]) -> None:
    root = work.resolve()
    for file in result.get("files") or []:
        target = Path(file["path"])
        resolved = target.resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError(f"refusing to clean file outside the temporary work root: {resolved}")
        target.unlink(missing_ok=True)
        target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
def hashes_complete(item: dict[str, Any], report: dict[str, Any]) -> bool:
    declared = item.get("files")
    if declared:
        target = item.get("file_hashes") or {}
        reported = report.get(str(item.get("id")))
        for file in declared:
            relative = str(file.get("path") or "")
            existing = str(file.get("sha256") or target.get(relative) or "")
            pending = str(reported.get(relative) or "") if isinstance(reported, dict) else ""
            if not SHA256.fullmatch(existing) and not SHA256.fullmatch(pending):
                return False
        return True
    return bool(
        SHA256.fullmatch(str(item.get("sha256") or ""))
        or SHA256.fullmatch(str(report.get(str(item.get("id"))) or ""))
    )
def coverage(catalog: dict[str, Any]) -> tuple[int, int]:
    items = downloadable(catalog)
    return sum(1 for item in items if hashes_complete(item, {})), len(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查可下载条目是否都有有效 SHA-256")
    parser.add_argument("--work-dir", help="独立临时目录；逐个删除权重，只保留 hashes.json 报告")
    args = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if args.check:
        problems = hash_problems(catalog)
        for problem in problems:
            print(problem)
        complete, total = coverage(catalog)
        print(f"media sha256 coverage: {complete}/{total}")
        return 1 if problems else 0

    work = Path(args.work_dir).expanduser().resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="mediaforge-media-hashes-"))
    work.mkdir(parents=True, exist_ok=True)
    report_path = work / "hashes.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    model_root = work / "models"
    print(f"work directory: {work}", flush=True)
    failures: list[str] = []
    for item in downloadable(catalog):
        model_id = f"media:{item['id']}"
        if hashes_complete(item, report):
            continue
        print(f"download {model_id} ({int(item.get('size') or 0) / 1024 / 1024:.1f} MiB)", flush=True)
        result = None
        try:
            result = model_hub.download_model(model_id, root=str(model_root))
            files = result["files"]
            declared = item.get("files")
            if not files or (declared and len(files) != len(declared)):
                raise RuntimeError(f"expected {len(declared or [None])} downloadable files, got {len(files)}")
            if declared:
                base = model_root / model_hub.worker_paths.media_model_relpath(item["name"])
                digests = {}
                for file in files:
                    relative = Path(file["path"]).relative_to(base).as_posix()
                    digests[relative] = sha256_of(Path(file["path"]))
                    print(f"  sha256 {relative} {digests[relative]}", flush=True)
                report[item["id"]] = digests
            else:
                if len(files) != 1:
                    raise RuntimeError(f"expected one downloadable file, got {len(files)}")
                digest = sha256_of(Path(files[0]["path"]))
                report[item["id"]] = digest
                print(f"  sha256 {digest}", flush=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            cleanup_files(work, result)
        except Exception as error:  # noqa: BLE001
            failures.append(f"{model_id}: {error}")
            print(f"  FAILED {error}", flush=True)
            cleanup_files(work, result or {"files": []})
            target = model_root / model_hub._media_files(item)[0]["path"]
            cleanup_files(work, {"files": [{"path": str(target)}]})

    updated = apply_hashes(catalog, report)
    if updated:
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"updated {CATALOG}: {updated} hashes", flush=True)
    problems = hash_problems(catalog)
    for failure in failures:
        print(failure, file=sys.stderr)
    complete, total = coverage(catalog)
    print(f"media sha256 coverage: {complete}/{total}", flush=True)
    print(f"report: {report_path}", flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
