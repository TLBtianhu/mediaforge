"""把发布包上传到魔搭数据集，作为国内镜像。

    python scripts/publish-modelscope.py --check
    python scripts/publish-modelscope.py release/MediaForge-*.exe release/MediaForge-*.zip

令牌来自 `scripts/dev_env.py`（环境变量 → `.dev-data/dev.env` → 历史文件）。
上传后会用**匿名**请求回读文件大小，确认别人真的能下——只看"上传成功"不够，
私有数据集也会返回成功，但下载全 404（这个坑已经踩过一次）。
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dev_env import MissingToken, modelscope_token  # noqa: E402

DATASET = "TLBtianhu/MediaForge-Dist"
REPO_TYPE = "dataset"
REMOTE_PREFIX = "release/"
PUBLIC_BASE = f"https://www.modelscope.cn/datasets/{DATASET}/resolve/master/"


def _api():
    from modelscope.hub.api import HubApi

    return HubApi


def upload(paths: list[Path], *, dry_run: bool = False) -> int:
    token = modelscope_token()
    api_cls = _api()
    api = api_cls()
    api.login(token)

    failures = 0
    for path in paths:
        if not path.is_file():
            print(f"[skip] {path} 不存在")
            failures += 1
            continue
        remote = REMOTE_PREFIX + path.name
        size_mb = round(path.stat().st_size / 1024 / 1024, 1)
        print(f"[upload] {path.name} ({size_mb} MB) -> {DATASET}/{remote}")
        if dry_run:
            continue
        try:
            result = api.upload_file(
                path_or_fileobj=str(path.resolve()),
                path_in_repo=remote,
                repo_id=DATASET,
                token=token,
                repo_type=REPO_TYPE,
            )
            print(f"  committed: {str(result)[:80]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            failures += 1
            continue

        # 匿名回读：这是"别人能不能下"的唯一可信证据
        url = PUBLIC_BASE + remote
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MediaForge/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                length = int(response.headers.get("Content-Length") or 0)
            ok = length == path.stat().st_size
            print(f"  anonymous HEAD {response.status} {length} bytes -> {'OK' if ok else 'SIZE MISMATCH'}")
            if not ok:
                failures += 1
        except urllib.error.HTTPError as exc:
            print(f"  anonymous HEAD failed: HTTP {exc.code}（数据集很可能还是私有）")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  anonymous HEAD failed: {type(exc).__name__}")
            failures += 1

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="只检查令牌与网络，不传")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要上传什么")
    args = parser.parse_args(argv)

    if args.check and not args.files:
        try:
            token = modelscope_token()
        except MissingToken as exc:
            print(f"未就绪: {exc}")
            return 1
        print(f"令牌可用（长度 {len(token)}）")
        return 0

    if not args.files:
        parser.error("没有指定要上传的文件")
    return 1 if upload(args.files, dry_run=args.dry_run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
