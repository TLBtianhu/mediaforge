"""Generate the Tauri command -> worker command map from the Rust sources.

The Rust `#[tauri::command]` functions are the runtime truth for command names;
this manifest makes the mapping reviewable and lets CI fail when a command is
added, renamed or rerouted without updating the map.

Usage:
    python scripts/sync-tauri-commands.py          # rewrite manifest/tauri-commands.json
    python scripts/sync-tauri-commands.py --check  # fail if the manifest is stale
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUST = ROOT / "src-tauri" / "src"
PROTOCOL = ROOT / "manifest" / "protocol.json"
OUTPUT = ROOT / "manifest" / "tauri-commands.json"

COMMAND = re.compile(r"#\[tauri::command\]\s*(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z0-9_]+)")
WORKER_CALL = re.compile(
    r'(?:run_worker_with_payload|run_worker_once|spawn_worker_background|run_worker_command)'
    r'\s*\([^;]{0,500}?"([a-z0-9_.]+)"',
    re.S,
)


def protocol_commands() -> dict[str, dict]:
    data = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    commands: dict[str, dict] = {}
    for domain in (data.get("domains") or {}).values():
        for name, entry in (domain.get("commands") or {}).items():
            command = entry.get("command") or name
            if isinstance(command, str):
                commands[command] = entry
    return commands


def function_body(text: str, signature_end: int) -> str:
    """Return the function body, ignoring comments and string literals."""
    open_at = text.find("{", signature_end)
    if open_at < 0:
        return ""
    depth = 0
    index = open_at
    while index < len(text):
        char = text[index]
        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if char == '"':
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:index]
        index += 1
    return text[open_at + 1:]


def rust_commands() -> list[dict]:
    commands: list[dict] = []
    for path in sorted(RUST.rglob("*.rs")):
        text = path.read_text(encoding="utf-8")
        matches = list(COMMAND.finditer(text))
        for index, match in enumerate(matches):
            body = function_body(text, match.end())
            strings = sorted(set(WORKER_CALL.findall(body)) & set(protocol_commands()))
            commands.append({
                "tauri": match.group(1),
                "source": path.relative_to(ROOT).as_posix(),
                "workers": strings,
                "starts": "spawn_worker_background" in body or "thread::spawn" in body,
            })
    return commands


def render() -> dict:
    protocol = protocol_commands()
    commands = rust_commands()
    ambiguous = [item for item in commands if len(item["workers"]) > 1]
    if ambiguous:
        raise ValueError("ambiguous worker mappings: " + ", ".join(
            f"{item['tauri']}={item['workers']}" for item in ambiguous
        ))
    rows = []
    for item in sorted(commands, key=lambda value: value["tauri"]):
        worker = item["workers"][0] if item["workers"] else None
        entry = protocol.get(worker) if worker else None
        rows.append({
            "tauri": item["tauri"],
            "worker": worker,
            "workerKind": entry.get("kind") if entry else None,
            "dispatch": "native" if worker is None else ("start" if item["starts"] else "sync"),
            "source": item["source"],
        })
    return {"schemaVersion": 1, "commands": rows}


def main() -> int:
    rendered = json.dumps(render(), ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != rendered:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date; run scripts/sync-tauri-commands.py")
            return 1
        print(f"{len(json.loads(rendered)['commands'])} Tauri commands")
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(json.loads(rendered)['commands'])} commands)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
