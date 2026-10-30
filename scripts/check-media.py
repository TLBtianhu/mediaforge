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
    raise SystemExit(main(sys.argv[1:]))