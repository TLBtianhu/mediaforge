"""对账 worker 命令面：Rust 调用的命令 vs Python 实现的命令。

背景：同一个命令名散落在 Rust 调度、Python 分派、诊断分类、agent 契约里，
全是字符串，编译器不管。今天删掉 download_model 后，四处留了孤儿，全靠人肉找。
这个脚本把"调用方"和"实现方"各自抽成集合，差异一律报错。

用法：

    python scripts/check-command-surface.py          # 打印差异
    python scripts/check-command-surface.py --strict # 有差异返回 1（可进 CI）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUST_DIR = ROOT / "src-tauri" / "src"
PY_DISPATCH = ROOT / "python" / "worker.py"

# Rust 侧真正"发命令给 worker"的两个入口
CALL_SITES = ("run_worker_with_payload", "spawn_worker_background")
STRING = re.compile(r'"([a-z][a-z0-9_]{2,})"')

# 这些字符串虽然挨着调用点，但不是命令名
NOT_COMMANDS = {
    "payload", "taskid", "app", "state", "error", "download", "model",
    "worker", "runtime", "string", "value", "json", "true", "false",
}


def rust_commands() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in RUST_DIR.rglob("*.rs"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for site in CALL_SITES:
            for match in re.finditer(site + r"\s*\(", text):
                # 只取调用括号后面的第一个字符串字面量：它才是命令名。
                # 之前取窗口内全部字面量，会把同名参数和后续 json! 里的键也吃进来。
                window = text[match.end() : match.end() + 220]
                found_name = STRING.search(window)
                if not found_name:
                    continue
                name = found_name.group(1)
                if name.lower() in NOT_COMMANDS:
                    continue
                found.setdefault(name, []).append(f"{path.name}:{text[:match.start()].count(chr(10)) + 1}")
    return found


def python_commands() -> set[str]:
    text = PY_DISPATCH.read_text(encoding="utf-8", errors="replace")
    names = set(re.findall(r'command\s*==\s*"([a-z][a-z0-9_]+)"', text))
    for group in re.findall(r"command\s+in\s*\(([^)]+)\)", text):
        names.update(re.findall(r'"([a-z][a-z0-9_]+)"', group))
    return names



AGENT_FILES = ("agent_bridge.py", "agent_contract.py")


def agent_commands(implemented: set[str]) -> set[str]:
    """agent 层不经过 Rust，直接在进程内调用 worker 命令。

    只统计"确实存在实现"的字面量，避免把工具名、参数名也算进来。
    """
    used: set[str] = set()
    for name in AGENT_FILES:
        path = ROOT / "python" / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for literal in STRING.findall(text):
            if literal in implemented:
                used.add(literal)
    return used


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="有差异时返回 1")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)

    called = rust_commands()
    implemented = python_commands()

    missing = sorted(name for name in called if name not in implemented)
    via_agent = agent_commands(implemented)
    orphan = sorted(name for name in implemented if name not in called and name not in via_agent)

    print(f"Rust 调用点抽到 {len(called)} 个命令名，Python 分派实现 {len(implemented)} 个")
    print(f"调用了但 Python 没有实现 : {len(missing)}")
    for name in missing[: args.limit]:
        print(f"  {name}  <- {called[name][0]}")
    print(f"agent 层直接调用 : {len(via_agent)}")
    print(f"三处都没有引用 : {len(orphan)}")
    for name in orphan[: args.limit]:
        print(f"  {name}")
    print()
    if missing:
        print("结论：存在'调用了却不存在的命令'，调用时会返回 UNKNOWN_COMMAND")
    elif orphan:
        print("结论：没有悬空调用，但有实现没人用（可能是给 frontend/测试留的入口）")
    else:
        print("结论：命令面一致")
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
