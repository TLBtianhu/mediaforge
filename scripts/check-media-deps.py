"""Statically match worker imports to declared dependencies, without importing them.

The installed packages of the interpreter running this check are not evidence
of a release dependency. Requirements, runtime manifests, optional components
and the documented pip bootstrap are the only declaration sources.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PY_DIR = ROOT / "python"
REQ_FILES = [PY_DIR / "requirements.txt", PY_DIR / "requirements-media.txt"]
RUNTIME_MANIFEST = PY_DIR / "runtime-manifest.json"
OPTIONAL_PACKAGES = PY_DIR / "core/worker_optional_packages.py"
STDLIB = set(sys.stdlib_module_names)
VENDORED = {"RIFE_HDv3", "train_log"}
MODULE_DISTRIBUTIONS = {
    "PIL": ("pillow",),
    "cv2": ("opencv-python-headless", "opencv-python", "opencv-contrib-python-headless", "opencv-contrib-python"),
    "yaml": ("pyyaml",),
    "pymss_core": ("pymss-core",),
    "socks": ("pysocks",),
}
# Both prepare scripts bootstrap pip/ensurepip; worker_bootstrap imports its
# vendored packaging parser. It is an interpreter prerequisite, not a model dep.
BOOTSTRAP = {"pip": "ensurepip (scripts/prepare-python-runtime.ps1/.sh)"}


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def requirement_name(value: str) -> str:
    text = value.strip()
    if "://" in text and re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        filename = unquote(urlsplit(text).path.rsplit("/", 1)[-1])
        if filename.endswith(".whl"):
            return canonical_name(filename.split("-", 1)[0])
        archive = re.fullmatch(r"(.+)-[0-9][^/]*\.(?:tar\.gz|zip)", filename)
        if archive:
            return canonical_name(archive.group(1))
        raise ValueError(f"Dependency URL has no static distribution name: {value}")
    match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\s*\[([A-Za-z0-9._,\s-]+)\])?\s*(.*)", text)
    if not match or (match.group(3) and not match.group(3).startswith(("<", ">", "=", "!", "~", "@", ";", "#", "("))):
        raise ValueError(f"Unrecognized requirement declaration: {value}")
    return canonical_name(match.group(1))


def declared() -> dict[str, str]:
    declarations: dict[str, str] = {}
    visited: set[Path] = set()

    def read_file(path: Path) -> None:
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ", "--requirement=")):
                included = re.split(r"[ =]", line, maxsplit=1)[1].strip().strip("\"'")
                read_file(path.parent / included)
            elif not line.startswith("-"):
                declarations[requirement_name(line)] = path.name
            # Index URLs, hashes and constraint files do not install a package.

    for path in REQ_FILES:
        read_file(path)
    if RUNTIME_MANIFEST.is_file():
        manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8-sig"))
        for group in ("bootstrap", "common"):
            for value in manifest.get(group, {}).values():
                declarations.setdefault(requirement_name(value), f"runtime-manifest.json:{group}")
        for backend, entry in manifest.get("backends", {}).items():
            torch = entry.get("torch", {})
            for value in [torch.get("requirement"), *torch.get("requirements", []), *entry.get("extras", [])]:
                if value:
                    declarations.setdefault(requirement_name(value), f"runtime-manifest.json:{backend}")
    return declarations


def optional_declarations() -> dict[str, str]:
    """Read the literal sidecar declarations without importing their manager."""
    if not OPTIONAL_PACKAGES.is_file():
        return {}
    tree = ast.parse(OPTIONAL_PACKAGES.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "OPTIONAL_PACKAGES":
            entries = ast.literal_eval(node.value)
            return {requirement_name(entry["requirement"]): "core/worker_optional_packages.py:OPTIONAL_PACKAGES" for entry in entries.values()}
    return {}


def imported_modules() -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    optional: dict[str, set[str]] = {}

    class Imports(ast.NodeVisitor):
        guarded = False

        def visit_Import(self, node):
            for alias in node.names:
                (optional if self.guarded else required).setdefault(alias.name.split(".")[0], set()).add(filename)

        def visit_ImportFrom(self, node):
            if node.module and node.level == 0:
                (optional if self.guarded else required).setdefault(node.module.split(".")[0], set()).add(filename)

        def visit_Try(self, node):
            def catches_missing(value):
                return isinstance(value, ast.Name) and value.id in {"ImportError", "ModuleNotFoundError"} or isinstance(value, ast.Tuple) and any(catches_missing(item) for item in value.elts)
            previous = self.guarded
            self.guarded = previous or any(catches_missing(handler.type) and not any(isinstance(child, ast.Raise) for child in ast.walk(handler)) for handler in node.handlers)
            for child in node.body:
                self.visit(child)
            self.guarded = previous
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                self.visit(child)

        def visit_FunctionDef(self, node):
            previous = self.guarded
            self.guarded = False  # Deferred function execution is outside an enclosing try.
            self.generic_visit(node)
            self.guarded = previous

        visit_AsyncFunctionDef = visit_FunctionDef

    for path in sorted(PY_DIR.rglob("*.py")):
        if "vendor" in path.relative_to(PY_DIR).parts:
            continue
        filename = str(path.relative_to(PY_DIR))
        Imports().visit(ast.parse(path.read_text(encoding="utf-8-sig"), filename=filename))
    return {**required, **{f"{name}#optional": files for name, files in optional.items() if name not in required}}


def check() -> list[tuple[str, str, str, bool]]:
    known, optional = declared(), optional_declarations()
    local = {"mediaforge"} | {entry.stem if entry.is_file() else entry.name for entry in PY_DIR.iterdir()
                             if entry.name != "__pycache__" and (entry.suffix == ".py" or entry.is_dir())}
    rows = []
    for raw in sorted(imported_modules()):
        module = raw.removesuffix("#optional")
        if module in STDLIB or module in local or module in VENDORED:
            continue
        candidates = MODULE_DISTRIBUTIONS.get(module, (canonical_name(module),))
        hit = next((candidate for candidate in candidates if candidate in known), None)
        sidecar = next((candidate for candidate in candidates if candidate in optional), None)
        if hit:
            rows.append((module, hit, known[hit], True))
        elif sidecar:
            rows.append((module, sidecar, f"optional: {optional[sidecar]}", True))
        elif module in BOOTSTRAP:
            rows.append((module, module, BOOTSTRAP[module], True))
        elif raw.endswith("#optional"):
            rows.append((module, candidates[0], "optional: guarded ImportError fallback", True))
        else:
            rows.append((module, candidates[0], "missing declaration", False))
    return rows


def main() -> int:
    try:
        rows = check()
    except (OSError, ValueError, SyntaxError) as error:
        print(f"Static dependency check failed: {error}", file=sys.stderr)
        return 1
    width = max((len(row[0]) for row in rows), default=10)
    for module, distribution, source, ok in rows:
        print(f"  {module.ljust(width)}  {distribution:28}  {'OK' if ok else 'MISSING':7}  {source}")
    bad = sum(not row[3] for row in rows)
    print(f"\nStatic dependency declarations: {len(rows)} modules, {len(rows) - bad} declared/optional/bootstrap, {bad} missing")
    return int(bad > 0)


if __name__ == "__main__":
    raise SystemExit(main())
