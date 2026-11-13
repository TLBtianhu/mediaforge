"""Fail when a view/store bypasses the typed API surface in `src/api/`.

Two invariants:

1. Worker-contract commands are called through `src/api/*` (or one of the two
   documented exceptions below); a direct `invoke('list_models', ...)` elsewhere is
   how payload/result shapes silently drift again.
2. A native command that already has a wrapper in `src/api/native.ts` must be called
   through that wrapper. The list is derived from `native.ts` itself, so adding a
   wrapper immediately bans direct invokes of the same command everywhere else.

Run locally:
    python scripts/check-frontend-invokes.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Direct worker invokes that are intentionally not in src/api. Keep the reason short
# and delete an entry as soon as the exception disappears; a stale entry fails too.
ALLOWED = {
    ("media_precheck", "src/utils/mediaPrecheck.ts"): "Node test loader cannot resolve internal aliases",
    ("manage_plugin", "src/features/plugin-marketplace/composable.ts"): "Rust start command returns taskId; result arrives via event",
}

INVOKE = re.compile(r"invoke(?:<[^>]+>)?\(\s*['\"]([a-z0-9_.]+)['\"]")


def worker_commands(root: Path = ROOT) -> set[str]:
    data = json.loads((root / "manifest" / "protocol.json").read_text(encoding="utf-8"))
    commands: set[str] = set()
    for domain in (data.get("domains") or {}).values():
        for name, entry in (domain.get("commands") or {}).items():
            command = entry.get("command") or name
            if isinstance(command, str):
                commands.add(command)
    return commands


def wrapped_native_commands(root: Path = ROOT) -> set[str]:
    """Native commands owned by `src/api/native.ts`; every invoke there counts."""
    path = root / "src" / "api" / "native.ts"
    if not path.exists():
        return set()
    return set(INVOKE.findall(path.read_text(encoding="utf-8")))


def source_files(root: Path) -> list[Path]:
    return sorted([*(root / "src").rglob("*.ts"), *(root / "src").rglob("*.vue")])


def is_api_file(relative: Path) -> bool:
    return relative.parts[:2] == ("src", "api")


def check(root: Path = ROOT) -> tuple[list[str], int, int, int]:
    """Return (problems, allowed worker calls, wrapped native commands, native bypasses)."""
    commands = worker_commands(root)
    native = wrapped_native_commands(root)
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    bypasses = 0
    for path in source_files(root):
        relative_path = path.relative_to(root)
        if is_api_file(relative_path):
            continue
        relative = relative_path.as_posix()
        text = path.read_text(encoding="utf-8")
        for match in INVOKE.finditer(text):
            command = match.group(1)
            line = text[: match.start()].count("\n") + 1
            if command in commands:
                seen.add((command, relative))
                if (command, relative) in ALLOWED:
                    continue
                problems.append(
                    f"{relative}:{line} direct invoke('{command}') bypasses src/api"
                )
            elif command in native:
                bypasses += 1
                problems.append(
                    f"{relative}:{line} direct invoke('{command}') bypasses its src/api/native.ts wrapper"
                )

    for command, relative in sorted(ALLOWED):
        if (command, relative) not in seen:
            problems.append(
                f"stale allowlist entry {relative}: invoke('{command}') no longer exists; remove it"
            )
    return problems, len(seen), len(native), bypasses


def main() -> int:
    problems, worker_calls, native_commands, bypasses = check()
    if problems:
        print(f"frontend invoke boundary: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "frontend invoke boundary: ok "
        f"(worker: {worker_calls} direct calls / {len(ALLOWED)} allowed; "
        f"native: {native_commands} wrapped commands, {bypasses} bypasses)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
