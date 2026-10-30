"""媒体能力的一键检查：模型能不能跑 + 依赖是否声明。

跑法：
    python scripts/check-media.py            # 依赖检查 + 冒烟（需要权重在盘上）
    python scripts/check-media.py --deps     # 只查依赖声明
    python scripts/check-media.py --smoke    # 只跑模型冒烟
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(script: str, label: str) -> bool:
    print(f"\n=== {label} ===")
    completed = subprocess.run([sys.executable, str(SCRIPTS / script)], cwd=str(ROOT))
    return completed.returncode == 0


def ensure_runtime() -> None:
    """用系统 python 跑时，自动切到应用运行时——否则依赖检查看不到 torch。"""
    import importlib.util
    import os

    if importlib.util.find_spec("torch") is not None or os.environ.get("MEDIAFORGE_SKIP_REEXEC"):
        return
    candidate = Path(os.environ["USERPROFILE"]) / ".mediaforge" / "runtime-envs" / "cuda" / "Scripts" / "python.exe"
    if candidate.is_file() and Path(sys.executable).resolve() != candidate.resolve():
        os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])


def main(argv: list[str]) -> int:
    only_deps = "--deps" in argv
    only_smoke = "--smoke" in argv
    checks = []
    if not only_smoke:
        checks.append(("依赖声明检查", "check-media-deps.py"))
    if not only_deps:
        checks.append(("模型冒烟测试", "smoke-media-models.py"))

    results = [(label, run(script, label)) for label, script in checks]
    print("\n=== 汇总 ===")
    for label, ok in results:
        print(f"  {label}: {'通过' if ok else '失败'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    ensure_runtime()
    raise SystemExit(main(sys.argv[1:]))