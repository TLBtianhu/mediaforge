"""检查 media_ops 用到的第三方包是否都写进了 requirements。

动机：packaged 运行时缺依赖只会在用户机器上炸。这个脚本用静态 import 分析
反查"代码里 import 了、但 requirements 没声明"的包，比装一遍新环境快得多。
"""
from __future__ import annotations

import ast
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DIR = ROOT / "python"

REQ_FILES = [PY_DIR / "requirements.txt", PY_DIR / "requirements-media.txt"]

STDLIB = set(sys.stdlib_module_names)
# 仓库里 vendor 进来的第三方代码（通过 sys.path 注入），不算 pip 依赖
VENDORED = {"RIFE_HDv3", "train_log"}
# worker_optional_packages 里用 "import funasr, torchaudio" 这样的探针字符串
# 检查可选包是否装好 —— 它们本来就允许缺席，不算必需依赖。
ALWAYS_OPTIONAL = {"funasr", "torchaudio", "pynini", "onnxruntime"}
# 这些不是 pip 包：本地模块、运行时内置、以及 torch 家族的自带命名空间
# 仓库内自己的模块：python/ 下的 <name>.py 或 <name>/ 都算本地，不需要声明
LOCAL = {"mediaforge"} | {
    entry.stem if entry.is_file() else entry.name
    for entry in PY_DIR.iterdir()
    if entry.name != "__pycache__" and (entry.suffix == ".py" or entry.is_dir())
}
SPECIAL = {"torch", "torchvision", "torchaudio"}


def declared() -> dict[str, str]:
    names: dict[str, str] = {}
    for path in REQ_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name = line.split("==", 1)[0].strip().lower()
            names[name] = path.name
    return names


def imported_modules() -> dict[str, set[str]]:
    module_to_files: dict[str, set[str]] = {}
    for path in list(PY_DIR.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # try/except ImportError 里的导入是可选的（例如 funasr 只在装了才有 ASR）
        optional: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                guarded = any(
                    isinstance(handler.type, ast.Name) and handler.type.id == "ImportError"
                    or isinstance(handler.type, ast.Tuple)
                    and any(isinstance(elt, ast.Name) and elt.id in {"ImportError", "ModuleNotFoundError"} for elt in handler.type.elts)
                    for handler in node.handlers if handler.type is not None
                )
                if guarded:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Import):
                            for alias in child.names:
                                optional.add(alias.name.split(".")[0])
                        elif isinstance(child, ast.ImportFrom) and child.module and child.level == 0:
                            optional.add(child.module.split(".")[0])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_to_files.setdefault(alias.name.split(".")[0], set()).add(path.name)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                module_to_files.setdefault(node.module.split(".")[0], set()).add(path.name)
        for name in optional:
            module_to_files.setdefault(f"{name}#optional", set()).add(path.name)
    return module_to_files


def main() -> int:
    known = declared()
    mapping = packages_distributions()
    rows = []
    for raw in sorted(imported_modules()):
        optional_only = raw.endswith("#optional")
        module = raw.removesuffix("#optional")
        if module in STDLIB or module in LOCAL or module in VENDORED:
            continue
        if module in ALWAYS_OPTIONAL:
            rows.append((module, "?", "可选能力（允许缺席）", True))
            continue
        if optional_only:
            rows.append((module, "?", "可选依赖（try/except ImportError）", True))
            continue
        dists = [d.lower() for d in (mapping.get(module) or [])]
        if module in SPECIAL:
            rows.append((module, "torch", "内置声明", True))
            continue
        if not dists:
            rows.append((module, "?", "未声明（找不到对应发行包）", False))
            continue
        hit = next((d for d in dists if d in known), None)
        if hit is not None or dists[0] in SPECIAL:
            rows.append((module, dists[0], "已声明", True))
            continue
        # 没显式声明，但当前环境能 import —— 说明它来自某个已声明包的传递依赖
        # （例如 pymss 会带 numpy/requests/soundfile/yaml）。这类记为 warn，
        # 只有"既没声明、又装不出来"才算失败。
        import importlib.util

        available = importlib.util.find_spec(module) is not None
        rows.append((module, dists[0], "传递依赖" if available else "未声明且不可用", False if not available else True))

    width = max(len(r[0]) for r in rows) if rows else 10
    bad = 0
    transp = 0
    for module, dist, where, ok in rows:
        if not ok:
            bad += 1
        elif where == "传递依赖":
            transp += 1
        mark = "OK" if ok and where != "传递依赖" else ("WARN" if ok else "MISSING")
        print(f"  {module.ljust(width)}  {dist:28}  {mark:7}  {where}")
    print(f"\n  第三方模块 {len(rows)} 个：已声明 {len(rows) - transp - bad} / 传递依赖 {transp} / 缺失 {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())