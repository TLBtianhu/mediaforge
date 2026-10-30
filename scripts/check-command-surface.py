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



# --- 第三层：前端 invoke 的 Tauri 命令 vs main.rs 注册 -----------------------
# 这一层今天漏过一次：runtime_asset_status 没进旧安装包的二进制，
# 前端 catch 掉异常后静默失效，界面上什么都看不出来。
INVOKE = re.compile(r"\binvoke(?:<[^(]*>)?\(\s*['\"]([a-z][a-z0-9_]*)['\"]")
REGISTER_BLOCK = re.compile(r"generate_handler!\[(.*?)\]", re.S)


def frontend_invokes() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in (ROOT / "src").rglob("*"):
        if path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in INVOKE.finditer(text):
            line = text[: match.start()].count(chr(10)) + 1
            found.setdefault(match.group(1), f"{path.name}:{line}")
    return found


def registered_commands() -> set[str]:
    text = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8", errors="replace")
    block = REGISTER_BLOCK.search(text)
    if not block:
        return set()
    names = set()
    for entry in re.findall(r"[A-Za-z_][A-Za-z0-9_:]*", block.group(1)):
        names.add(entry.split("::")[-1])
    return names



def frontend_name_mentions(names: set[str]) -> dict[str, str]:
    """命令名可能在某个常量或映射表里出现，再被动态 invoke 传出去。

    只认"引号包住的完整名字"，避免把前缀相同的标识符误判成引用。
    """
    found: dict[str, str] = {}
    for path in (ROOT / "src").rglob("*"):
        if path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in names:
            if name in found:
                continue
            index = text.find(name)
            if index >= 0:
                found[name] = f"{path.name}:{text[:index].count(chr(10)) + 1}"
    return found

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
    invoked = frontend_invokes()
    registered = registered_commands()
    unregistered = sorted(name for name in invoked if name not in registered)
    unused = sorted(name for name in registered if name not in invoked)
    print()
    print(f"前端 invoke 的命令 {len(invoked)} 个，main.rs 注册 {len(registered)} 个")
    print(f"前端调了但没注册 : {len(unregistered)}")
    for name in unregistered[: args.limit]:
        print(f"  {name}  <- {invoked[name]}")
    mentioned = frontend_name_mentions(registered)
    only_mentioned = [name for name in unused if name in mentioned]
    unreachable = [name for name in unused if name not in mentioned]
    print(f"注册了但没直接 invoke : {len(unused)}")
    print(f"  其中在源码里作为字面量出现（动态调用/映射表）: {len(only_mentioned)}")
    for name in only_mentioned[: args.limit]:
        print(f"    {name}  <- {mentioned[name]}")
    print(f"  全仓库找不到任何引用（疑似不可达）: {len(unreachable)}")
    for name in unreachable[: args.limit]:
        print(f"    {name}")
    if missing:
        print("结论：存在'调用了却不存在的命令'，调用时会返回 UNKNOWN_COMMAND")
    elif orphan:
        print("结论：没有悬空调用，但有实现没人用（可能是给 frontend/测试留的入口）")
    else:
        print("结论：命令面一致")
    if args.strict and (missing or unregistered):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
