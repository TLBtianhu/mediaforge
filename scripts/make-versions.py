"""生成并签名 versions.json（应用/运行时两条版本轴的检测入口）。

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
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_runtime() -> dict:
    manifest = json.loads((ROOT / "manifest" / "runtime.json").read_text(encoding="utf-8"))
    expected_python = json.loads((ROOT / "python/runtime-manifest.json").read_text(encoding="utf-8"))["python"]
    if manifest.get("schemaVersion") != 2:
        raise ValueError("Runtime manifest must use the current schemaVersion 2")
    fallback = manifest.get("fallbackAssetId")
    for item in manifest["runtimes"]:
        if item["assetId"] == fallback:
            if item.get("python") != expected_python:
                raise ValueError("Runtime asset Python differs from the application runtime manifest")
            return {
                "assetId": item["assetId"],
                "backend": item["backend"],
                "python": item["python"],
                "sha256": item["sha256"],
                "size": item["size"],
                "urls": item["urls"],
            }
    raise SystemExit(f"manifest/runtime.json 里找不到 fallbackAssetId={fallback}")


def build(version: str | None = None) -> dict:
    app_version = version or json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?", app_version):
        raise ValueError("Invalid application version")
    return {
        "schemaVersion": 2,
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


def verify_signature(document: bytes, signature: str, public_key: str | None = None) -> None:
    """Match the app's minisign-verify contract, including the trusted comment."""
    from nacl.signing import VerifyKey

    if public_key is None:
        source = (ROOT / "src-tauri/src/version_check.rs").read_text(encoding="utf-8")
        match = re.search(r'const VERSIONS_PUBKEY: &str = "([^"\n]+)";', source)
        if not match:
            raise ValueError("Cannot resolve the application's trusted versions public key")
        public_key = match.group(1)
    key_text = base64.b64decode(public_key, validate=True).decode("utf-8")
    key_lines = key_text.splitlines()
    lines = signature.splitlines()
    if len(key_lines) != 2 or len(lines) != 4 or not lines[0].startswith("untrusted comment: ") or not lines[2].startswith("trusted comment: "):
        raise ValueError("Invalid minisign document")
    key = base64.b64decode(key_lines[1], validate=True)
    signed = base64.b64decode(lines[1], validate=True)
    global_signature = base64.b64decode(lines[3], validate=True)
    if len(key) != 42 or key[:2] != b"Ed" or len(signed) != 74 or signed[:2] != b"ED" or len(global_signature) != 64 or key[2:10] != signed[2:10]:
        raise ValueError("Signature does not match the trusted versions key or algorithm")
    verifier = VerifyKey(key[10:])
    verifier.verify(hashlib.blake2b(document, digest_size=64).digest(), signed[10:])
    verifier.verify(signed[10:] + lines[2].removeprefix("trusted comment: ").encode("utf-8"), global_signature)


def verify_previous(previous: Path, version: str) -> None:
    document = previous / "versions.json"
    signature = previous / "versions.json.sig"
    if not document.exists() and not signature.exists():
        return
    content = document.read_bytes()
    verify_signature(content, signature.read_text(encoding="utf-8"))
    previous_version = json.loads(content)["app"]["version"]
    def stable_order(value: str) -> tuple[int, int, int]:
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?", value):
            raise ValueError("Stable version notifications require stable semantic versions")
        return tuple(int(part) for part in value.split("+", 1)[0].split("."))
    if stable_order(previous_version) > stable_order(version):
        raise ValueError("Refusing to replace a newer stable version notification")


def sign(path: Path, cli: Path | None = None) -> Path:
    signer = cli or ROOT / "node_modules/@tauri-apps/cli/tauri.js"
    node = shutil.which("node")
    if not signer.is_file() or not node:
        raise SystemExit("需要 Node.js 与本地 Tauri CLI（可用 --cli 指定 tauri.js）")
    # This channel has its own trust root. Never silently reuse an updater key.
    key = os.environ.get("VERSIONS_SIGNING_PRIVATE_KEY", "").strip()
    password = os.environ.get("VERSIONS_SIGNING_PRIVATE_KEY_PASSWORD", "")
    if not key:
        raise SystemExit("需要专用 VERSIONS_SIGNING_PRIVATE_KEY（私钥内容）")
    env = {name: value for name, value in os.environ.items() if not re.search(r"KEY|PASSWORD|TOKEN|SECRET", name, re.IGNORECASE)}
    env.update(TAURI_SIGNING_PRIVATE_KEY=key, TAURI_SIGNING_PRIVATE_KEY_PASSWORD=password)
    command = [node, str(signer), "signer", "sign", str(path)]
    signature = path.with_name(path.name + ".sig")
    signature.unlink(missing_ok=True)
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as error:
        signature.unlink(missing_ok=True)
        raise SystemExit("版本清单签名进程未成功完成") from error
    if result.returncode != 0:
        # CLI diagnostics can contain its signing arguments/environment. Keep
        # credentials out of CI logs even on malformed keys or decryption errors.
        signature.unlink(missing_ok=True)
        raise SystemExit(f"签名失败（tauri signer 退出码 {result.returncode}）")
    if not signature.is_file():
        raise SystemExit(f"签名已执行但找不到产物：{signature}")
    try:
        decoded = decode_tauri_signature(signature.read_text(encoding="utf-8"))
        verify_signature(path.read_bytes(), decoded)
    except BaseException:
        signature.unlink(missing_ok=True)
        raise
    signature.write_text(decoded, encoding="utf-8", newline="\n")
    return signature


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="release")
    parser.add_argument("--version", help="Published app version; defaults to package.json")
    parser.add_argument("--cli", type=Path, help="Path to Tauri's tauri.js")
    parser.add_argument("--previous-dir", type=Path, help="Verify existing signed stable metadata and reject downgrades")
    args = parser.parse_args(argv[1:])
    document = build(args.version)
    if args.previous_dir:
        verify_previous(args.previous_dir, document["app"]["version"])
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    target = out / "versions.json"
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    signature = sign(target, args.cli)
    print(f"  versions.json      {target.stat().st_size} 字节")
    print(f"  versions.json.sig  {signature.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
