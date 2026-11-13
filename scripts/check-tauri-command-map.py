"""Validate the Tauri command map against Rust registration and the frontend API.

`manifest/tauri-commands.json` is generated from the Rust `#[tauri::command]`
functions by `sync-tauri-commands.py`. This check proves that the generated map
also covers the actual `generate_handler!` registration and every Tauri command
name used by `src/api`.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "manifest" / "tauri-commands.json"
MAIN = ROOT / "src-tauri" / "src" / "main.rs"
API = ROOT / "src" / "api"
PROTOCOL = ROOT / "manifest" / "protocol.json"

HANDLER = re.compile(r"generate_handler!\s*\[(.*?)\]", re.S)

API_INVOKE = re.compile(r"invoke(?:<[^>]+>)?\(\s*['\"]([a-z0-9_.]+)['\"]")


def main() -> int:
    data = json.loads(MAP.read_text(encoding="utf-8"))
    rows = data.get("commands") or []
    problems: list[str] = []

    names = [row.get("tauri") for row in rows]
    if len(names) != len(set(names)):
        problems.append("duplicate tauri command names in the map")

    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    worker_commands = {
        entry.get("command") or name
        for domain in (protocol.get("domains") or {}).values()
        for name, entry in (domain.get("commands") or {}).items()
    }
    for row in rows:
        name = row.get("tauri")
        worker = row.get("worker")
        if worker is not None and worker not in worker_commands:
            problems.append(f"{name}: worker {worker!r} is not in protocol.json")

    handler_block = HANDLER.search(MAIN.read_text(encoding="utf-8")).group(1)
    registered = {
        part.strip().rsplit("::", 1)[-1]
        for part in handler_block.split(",")
        if "::" in part.strip()
    }
    mapped = set(names)
    for name in sorted(mapped - registered):
        problems.append(f"{name}: in tauri-commands.json but not generate_handler!")
    for name in sorted(registered - mapped):
        problems.append(f"{name}: registered in generate_handler! but missing from tauri-commands.json")

    for path in sorted(API.glob("*.ts")):
        for match in API_INVOKE.finditer(path.read_text(encoding="utf-8")):
            command = match.group(1)
            if command not in mapped:
                problems.append(f"{path.relative_to(ROOT)}: invoke('{command}') is not in tauri-commands.json")

    if problems:
        print(f"tauri command map: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"tauri command map: ok ({len(rows)} commands, {len(worker_commands)} worker commands)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
