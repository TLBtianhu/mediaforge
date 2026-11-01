"""把 modelscope file index 里的 sha256 写回 audio_catalog.json。

为什么需要它：音频 catalog 只有 size_bytes，没有哈希，所以 infer 的自动补下载
过去只能靠 pymss 联网 file index 做完整性校验。把哈希直接写进 catalog 后，
清单/文件/校验/路径就住在同一份声明里，离线下载与离线校验都成立——
这是"统一模型元数据"的第一步（后续 infer 改走统一下载器）。

用法：
    python scripts/sync-audio-catalog-hashes.py            # 抓取并写回
    python scripts/sync-audio-catalog-hashes.py --check    # 只报告，不写文件

schema：每条模型新增一个 `sha256` 映射（relpath → 哈希），覆盖主权重、config、
auxiliary 全部文件；不改变既有字段。同时校验 size_bytes 与 index 是否一致。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "python" / "audio_catalog.json"
INDEX_API = "https://www.modelscope.cn/api/v1/models/baicai1145/pymss/repo/files?Revision=master&Recursive=true"


def fetch_index() -> dict[str, dict]:
    request = urllib.request.Request(INDEX_API, headers={"User-Agent": "MediaForge/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    files = data.get("Data", {}).get("Files", [])
    return {item["Path"]: item for item in files if item.get("Type") == "blob"}


def model_relpaths(model: dict) -> list[str]:
    paths = []
    for key in ("relpath", "config_relpath"):
        if model.get(key):
            paths.append(model[key])
    for auxiliary in model.get("auxiliary_relpaths") or []:
        if auxiliary:
            paths.append(auxiliary)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只报告，不写回")
    args = parser.parse_args()

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    index = fetch_index()
    print(f"index files: {len(index)}")

    filled = 0
    incomplete: list[str] = []
    size_mismatch: list[str] = []
    for model in catalog["models"]:
        name = model.get("name") or "?"
        hashes: dict[str, str] = {}
        missing: list[str] = []
        for relpath in model_relpaths(model):
            item = index.get(relpath)
            sha = str((item or {}).get("Sha256") or "")
            if sha:
                hashes[relpath] = sha
            else:
                missing.append(relpath)
        if hashes:
            model["sha256"] = hashes
            filled += 1
        if missing:
            incomplete.append(f"{name}: {len(missing)} 个文件无哈希")
        # size_bytes 是主权重大小，应与 index 的 Size 一致；不一致说明 catalog 过期。
        primary = model.get("relpath")
        expected = model.get("size_bytes")
        actual = (index.get(primary) or {}).get("Size") if primary else None
        if primary and expected and actual and int(expected) != int(actual):
            size_mismatch.append(f"{name}: catalog={expected} index={actual}")

    print(f"补全哈希的模型: {filled}/{len(catalog['models'])}")
    if incomplete:
        print(f"有文件缺哈希的模型: {len(incomplete)}")
        for line in incomplete[:10]:
            print(f"  {line}")
    if size_mismatch:
        print(f"size_bytes 与 index 不一致: {len(size_mismatch)}")
        for line in size_mismatch[:10]:
            print(f"  {line}")

    if args.check:
        return 0
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写回 {CATALOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
