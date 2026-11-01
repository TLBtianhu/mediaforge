"""生成并签名 versions.json（三条版本轴的检测入口）。

用法：
    python scripts/make-versions.py --out release

产物：
    release/versions.json
    release/versions.json.sig     ← minisign 原文（tauri signer 的 base64 产物已解码）

设计约束：
  * 版本信息必须签名，未签名的版本信息一律不采信（Rust 侧 versions_check 会校验）；
  * 签名私钥只从环境变量取，绝不写进仓库；
  * 运行时描述符直接取自 manifest/runtime.json，避免"版本清单"和"基线清单"两份真源。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_runtime() -> dict:
    manifest = json.loads((ROOT / "manifest" / "runtime.json").read_text(encoding="utf-8"))
    fallback = manifest.get("fallbackAssetId")
    for item in manifest["runtimes"]:
        if item["assetId"] == fallback:
            return {
                "assetId": item["assetId"],
                "backend": item["backend"],
                "sha256": item["sha256"],
                "size": item["size"],
                "urls": item["urls"],
            }
    raise SystemExit(f"manifest/runtime.json 里找不到 fallbackAssetId={fallback}")


def build() -> dict:
    app_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    return {
        "schemaVersion": 1,
        "app": {"version": app_version},
        "runtime": load_runtime(),
    }


def decode_tauri_signature(envelope: str) -> str:
    """tauri signer 输出整个 minisign 文件的 base64；versions_check 读取其原文。"""
    try:
        decoded = base64.b64decode(envelope.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise SystemExit(f"tauri signer 的签名产物不是合法 base64/UTF-8：{error}") from error
    lines = decoded.splitlines()
    if (
        len(lines) != 4
        or not lines[0].startswith("untrusted comment: ")
        or not lines[2].startswith("trusted comment: ")
    ):
        raise SystemExit("tauri signer 的签名产物不是完整的 minisign 签名")
    return decoded


def sign(path: Path) -> Path:
    signer = ROOT / "node_modules" / ".bin" / "tauri.cmd"
    if not signer.is_file():
        raise SystemExit(f"找不到 tauri CLI：{signer}")
    key = os.environ.get("TAURI_SIGNING_PRIVATE_KEY", "").strip()
    password = os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")
    if not key:
        raise SystemExit("需要 TAURI_SIGNING_PRIVATE_KEY（私钥路径或内容）")
    command = [str(signer), "signer", "sign"]
    # tauri CLI 把环境变量也算作"已经给了 key"，与显式 -f/-p 同时存在会直接报冲突。
    env = dict(os.environ)
    env.pop("TAURI_SIGNING_PRIVATE_KEY", None)
    env.pop("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", None)
    if Path(key).is_file():
        command += ["-f", key]
    else:
        command += ["-k", key]
    if password:
        command += ["-p", password]
    command.append(str(path))
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(f"签名失败（tauri signer 退出码 {result.returncode}）")
    signature = path.with_name(path.name + ".sig")
    if not signature.is_file():
        raise SystemExit(f"签名已执行但找不到产物：{signature}")
    signature.write_text(
        decode_tauri_signature(signature.read_text(encoding="utf-8")),
        encoding="utf-8",
        newline="\n",
    )
    return signature


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="release")
    args = parser.parse_args(argv[1:])
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    target = out / "versions.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    signature = sign(target)
    print(f"  versions.json      {target.stat().st_size} 字节")
    print(f"  versions.json.sig  {signature.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
