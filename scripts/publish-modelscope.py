"""把发布包上传到魔搭数据集，作为国内镜像。

    python scripts/publish-modelscope.py --check
    python scripts/publish-modelscope.py release/MediaForge-*.exe release/MediaForge-*.zip

令牌来自 `scripts/dev_env.py`（环境变量 → `.dev-data/dev.env` → 历史文件）。
上传后会用**匿名**请求回读文件大小，确认别人真的能下——只看"上传成功"不够，
私有数据集也会返回成功，但下载全 404（这个坑已经踩过一次）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 本项目的运行时里没有装 modelscope SDK：发布时用的是隔离安装
# （.dev-data/tools/modelscope-sdk 或 MODELSCOPE_SDK_PATH 指向的目录），
# 否则会 ModuleNotFoundError。
for candidate in (
    Path(os.environ.get("MODELSCOPE_SDK_PATH", "")).expanduser() if os.environ.get("MODELSCOPE_SDK_PATH") else None,
    REPO_ROOT / ".dev-data" / "tools" / "modelscope-sdk",
):
    if candidate and candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from dev_env import MissingToken, modelscope_token  # noqa: E402

DATASET = "TLBtianhu/MediaForge-Dist"
REPO_TYPE = "dataset"
REMOTE_PREFIX = "release/"
PUBLIC_BASE = f"https://www.modelscope.cn/datasets/{DATASET}/resolve/master/"


def _api():
    from modelscope.hub.api import HubApi

    return HubApi


def head_matches(remote: str, expected: int, *, attempts: int = 4, delay: float = 15.0) -> bool:
    """匿名 HEAD 校验，带重试。

    魔搭的 CDN 有传播延迟：刚 commit 完立刻查会 404，过十几秒才 200。
    不重试的话每次发布都会误报"数据集还是私有"，让人误以为上传失败。
    """
    url = PUBLIC_BASE + remote
    last: str = ""
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MediaForge/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                length = int(response.headers.get("Content-Length") or 0)
            if length == expected:
                print(f"  anonymous HEAD {response.status} {length} bytes -> OK" + ("" if attempt == 1 else f"（第 {attempt} 次）"))
                return True
            print(f"  anonymous HEAD {response.status} {length} != {expected} -> SIZE MISMATCH")
            return False
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code == 403:
                print(f"  anonymous HEAD failed: {last}（数据集是私有的，需要设为公开）")
                return False
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
        if attempt < attempts:
            print(f"  HEAD {last}，{delay:.0f} 秒后重试（{attempt}/{attempts - 1}）")
            time.sleep(delay)
    print(f"  anonymous HEAD failed after {attempts} attempts: {last}"
          "（可能是传播延迟，也可能是文件确实不存在）")
    return False


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

        if not head_matches(remote, path.stat().st_size):
            failures += 1

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="只检查令牌与网络，不传")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要上传什么")
    parser.add_argument("--verify-only", action="store_true", help="只做匿名回读，不重新上传")
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
    if args.verify_only:
        failures = sum(0 if head_matches(REMOTE_PREFIX + p.name, p.stat().st_size) else 1 for p in args.files)
        return 1 if failures else 0
    return 1 if upload(args.files, dry_run=args.dry_run) else 0


if __name__ == "__main__":
    raise SystemExit(main())
