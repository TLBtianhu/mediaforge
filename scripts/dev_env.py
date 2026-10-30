"""本机开发用的小工具：读 `.dev-data/dev.env`，拿魔搭令牌。

为什么需要它：令牌不能进仓库、不能进聊天记录，也不该每次手写进脚本。
开发目录里的 `dev.env` 是唯一放它的地方（`.dev-data` 已在 .gitignore）。

查找顺序（先到先得）：
  1. 环境变量 `MODELSCOPE_API_TOKEN`
  2. `.dev-data/dev.env` 里的 `MODELSCOPE_API_TOKEN=...`
  3. `.dev-data/ms-token.txt`（历史格式：文件里只有令牌）
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_DATA = REPO_ROOT / ".dev-data"
DEV_ENV = DEV_DATA / "dev.env"
LEGACY_TOKEN_FILE = DEV_DATA / "ms-token.txt"

PLACEHOLDER = "PASTE_TOKEN_HERE"


class MissingToken(RuntimeError):
    """没找到可用的令牌——错误信息要直接告诉用户往哪儿贴。"""


def _registry_env(name: str) -> str:
    """读用户级环境变量（HKCU\Environment）。

    为什么：`setx NAME value` 写的是注册表，已经在跑的 DSH 进程不会继承新值，
    所以"我在终端里 setx 了"这种最常见操作对我们不可见。直接读注册表就绕开重启。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg  # type: ignore
    except Exception:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except OSError:
        return ""


def read_dev_env(path: Path | None = None) -> dict[str, str]:
    """解析 `KEY=VALUE` 形式的开发环境文件；忽略空行与 `#` 注释。"""
    target = path or DEV_ENV
    values: dict[str, str] = {}
    if not target.is_file():
        return values
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        # 先脱引号再去空白：`MODELSCOPE_API_TOKEN="  spaced  "` 要变成 `spaced`
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        values[key.strip()] = value
    return values


def modelscope_token(environ: dict[str, str] | None = None) -> str:
    """返回可用的令牌，找不到或还是占位符时抛 `MissingToken`。"""
    env = os.environ if environ is None else environ
    candidates: list[tuple[str, str]] = []
    if env.get("MODELSCOPE_API_TOKEN"):
        candidates.append(("环境变量 MODELSCOPE_API_TOKEN", env["MODELSCOPE_API_TOKEN"].strip()))
    registry_value = _registry_env("MODELSCOPE_API_TOKEN")
    if registry_value:
        candidates.append(("HKCU\\Environment\\MODELSCOPE_API_TOKEN", registry_value))
    dev = read_dev_env()
    if dev.get("MODELSCOPE_API_TOKEN"):
        candidates.append((str(DEV_ENV), dev["MODELSCOPE_API_TOKEN"].strip()))
    if LEGACY_TOKEN_FILE.is_file():
        legacy = LEGACY_TOKEN_FILE.read_text(encoding="utf-8-sig")
        lines = [l.strip() for l in legacy.splitlines() if l.strip() and not l.strip().startswith("#")]
        if lines:
            candidates.append((str(LEGACY_TOKEN_FILE), lines[-1]))

    for source, token in candidates:
        if token and token != PLACEHOLDER:
            return token
    tried = ", ".join(source for source, _ in candidates) or "（三处都没有值）"
    raise MissingToken(
        "没有找到魔搭令牌。请把令牌写进 " + str(DEV_ENV) + " 的 MODELSCOPE_API_TOKEN=... 一行，"
        f"或设置环境变量 MODELSCOPE_API_TOKEN。已尝试：{tried}"
    )


def main() -> int:
    import sys

    if "--check" in sys.argv[1:]:
        try:
            token = modelscope_token()
        except MissingToken as exc:
            print(f"未就绪: {exc}")
            return 1
        print(f"令牌可用（长度 {len(token)}，来源已确认）")
        return 0
    print(str(DEV_ENV))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
