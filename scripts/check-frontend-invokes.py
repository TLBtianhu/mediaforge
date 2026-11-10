"""Fail when a view/store bypasses the typed API for a worker-contract command.

The typed layer lives in `src/api/`. A direct `invoke('list_models', ...)` outside
it is how payload/result shapes silently drift again, so this check only allows
the two documented exceptions below.

Run locally:
    python scripts/check-frontend-invokes.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
API = SRC / "api"
PROTOCOL = ROOT / "manifest" / "protocol.json"

# Direct invokes that are intentionally not in src/api. Keep the reason short and
# delete an entry as soon as the exception disappears; a stale entry fails too.
ALLOWED = {
    ("media_precheck", "src/utils/mediaPrecheck.ts"): "Node test loader cannot resolve internal aliases",
    ("manage_plugin", "src/features/plugin-marketplace/composable.ts"): "Rust start command returns taskId; result arrives via event",
}


def worker_commands() -> set[str]:
    data = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    commands: set[str] = set()
    for domain in (data.get("domains") or {}).values():
        for name, entry in (domain.get("commands") or {}).items():
            command = entry.get("command") or name
            if isinstance(command, str):
                commands.add(command)
    return commands


def main() -> int:
    commands = worker_commands()
    pattern = re.compile(r"invoke(?:<[^>]+>)?\(\s*['\"]([a-z0-9_.]+)['\"]")
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted([*SRC.rglob("*.ts"), *SRC.rglob("*.vue")]):
        if API in path.parents:
            continue
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            command = match.group(1)
            if command not in commands:
                continue
            seen.add((command, relative))
            if (command, relative) in ALLOWED:
                continue
            line = text[: match.start()].count("\n") + 1
            problems.append(
                f"{relative}:{line} direct invoke('{command}') bypasses src/api"
            )

    for command, relative in sorted(ALLOWED):
        if (command, relative) not in seen:
            problems.append(
                f"stale allowlist entry {relative}: invoke('{command}') no longer exists; remove it"
            )

    if problems:
        print(f"frontend invoke contract: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"frontend invoke contract: ok ({len(seen)} direct calls, {len(ALLOWED)} allowed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
