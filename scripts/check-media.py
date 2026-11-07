"""媒体能力的一键检查：模型能不能跑 + 依赖是否声明。

跑法：
    python scripts/check-media.py --python <test-python> --model-dir <test-models> --data-root <isolated-data>
    python scripts/check-media.py --deps     # 只查依赖声明
    python scripts/check-media.py --smoke    # 只跑模型冒烟
"""
from __future__ import annotations

import subprocess
import sys
import argparse
import tempfile
from pathlib import Path

from media_smoke_env import add_arguments, interpreter, isolated_environment, model_paths, test_path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(script: str, label: str, python: Path, env: dict[str, str], arguments: list[str]) -> bool:
    print(f"\n=== {label} ===")
    completed = subprocess.run([str(python), "-B", str(SCRIPTS / script), *arguments], cwd=str(ROOT), env=env)
    return completed.returncode == 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--deps", action="store_true", help="Static dependency declaration check only; no models")
    mode.add_argument("--smoke", action="store_true", help="Model smoke tests only; explicit test paths required")
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        paths = model_paths(args) if not args.deps else None
        python = paths[0] if paths else interpreter(args.python or sys.executable)
        deps_data = test_path(args.data_root, "--data-root") if args.data_root else None
        if args.output_dir:
            args.output_dir = test_path(args.output_dir, "--output-dir")
        if args.input:
            args.input = test_path(args.input, "--input")
            if not args.input.is_file():
                raise ValueError(f"sample image does not exist: {args.input}")
    except ValueError as error:
        parser.error(str(error))
    checks = []
    if not args.smoke:
        checks.append(("依赖声明检查", "check-media-deps.py"))
    if not args.deps:
        checks.append(("模型冒烟测试", "smoke-media-models.py"))
    with tempfile.TemporaryDirectory(prefix="mediaforge-check-media-") as temporary:
        data = paths[2] if paths else deps_data or Path(temporary)
        env = isolated_environment(data, python, paths[1] if paths else None, args.output_dir)
        forwarded = ["--python", str(python), "--model-dir", str(paths[1]), "--data-root", str(data)] if paths else []
        for flag, value in (("--output-dir", args.output_dir), ("--input", args.input)):
            if value is not None:
                forwarded.extend([flag, str(value)])
        results = [(label, run(script, label, python, env, forwarded if script == "smoke-media-models.py" else [])) for label, script in checks]
    print("\n=== 汇总 ===")
    for label, ok in results:
        print(f"  {label}: {'通过' if ok else '失败'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
