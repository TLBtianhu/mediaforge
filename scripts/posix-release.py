"""Stage and inspect POSIX release resources without running native applications."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
CACHE_NAMES = {"__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def check_runtime_version(root: Path) -> None:
    expected = json.loads((root / "python/runtime-manifest.json").read_text(encoding="utf-8"))["python"]
    if expected != "3.12":
        raise ValueError("The release runtime target must be Python 3.12")
    candidates = (root / "python-runtime/bin/python3", root / "python-runtime/python.exe")
    python = next((path for path in candidates if path.is_file()), None)
    if python is None:
        raise ValueError("The release bootstrap Python is missing")
    result = subprocess.run([str(python), "-I", "-B", "-c",
                             "import json,sys; print(json.dumps({'python':'.'.join(map(str,sys.version_info[:2])),'selfContained':sys.prefix==sys.base_prefix}))"],
                            capture_output=True, text=True, timeout=20, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        actual = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("Release Python version probe failed") from None
    if result.returncode or actual.get("python") != expected or actual.get("selfContained") is not True:
        raise ValueError("Release bootstrap must be self-contained Python 3.12")


def verify_resources(resources: Path) -> None:
    for name in ("python/worker.py", "python/core/__init__.py", "python/shared/protocol_contract.py",
                 "python/surfaces/dispatch.py", "python/runtime-manifest.json", "manifest/runtime.json"):
        if not (resources / name).is_file():
            raise ValueError(f"Incomplete release resources: {name}")
    protocol = json.loads((resources / "manifest/protocol.json").read_text(encoding="utf-8"))
    for domain in protocol["domains"].values():
        for entry in domain.get("commands", {}).values():
            module = entry["handler"].split(":", 1)[0].replace(".", "/")
            if not any((resources / "python" / name).is_file() for name in (module + ".py", module + "/__init__.py")):
                raise ValueError(f"Release is missing protocol handler module: {module}")


def stage_resources(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    for name in ("python", "manifest"):
        origin, target = source / name, destination / name
        if target == origin or target.is_relative_to(origin) or origin.is_relative_to(target):
            raise ValueError("Release staging must not overwrite its source")
    check_runtime_version(source)
    for name in ("python", "manifest"):
        origin, target = source / name, destination / name
        if target == origin or target.is_relative_to(origin) or origin.is_relative_to(target):
            raise ValueError("Release staging must not overwrite its source")
        if target.is_symlink():
            raise ValueError(f"Release staging refuses linked destination: {target}")

        def ignored(directory, names):
            excluded = {item for item in names if item in CACHE_NAMES or item.endswith((".pyc", ".pyo", ".log", ".jsonl"))}
            for item in set(names) - excluded:
                if (Path(directory) / item).is_symlink():
                    raise ValueError(f"Release source contains a link: {Path(directory) / item}")
            return excluded

        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(origin, target, ignore=ignored)
    verify_resources(destination)


def bundle_config(resources: Path, platform: str) -> dict:
    resources = resources.resolve()
    verify_resources(resources)
    check_runtime_version(resources)
    bundle = {"resources": {str(resources / name): name for name in ("python", "manifest", "python-runtime", "bin")}}
    bundle["resources"][str(resources / "plugin-sdk")] = "resources/plugin-sdk"
    if platform == "linux":
        bundle["linux"] = {"appimage": {"bundleMediaFramework": True}}
    return {"bundle": bundle}


def mac_app(directory: Path, config: dict) -> Path:
    # Exact selection prevents publishing a stale app or a similarly named test copy.
    app = directory / (config["productName"] + ".app")
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != config["identifier"]:
        raise ValueError("The macOS bundle identifier does not match the release")
    name = info.get("CFBundleExecutable")
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Invalid macOS bundle executable")
    executable = app / "Contents/MacOS" / name
    if not executable.is_file() or not executable.resolve().is_relative_to(app.resolve()):
        raise ValueError("The declared macOS executable is missing or escapes its bundle")
    verify_resources(app / "Contents/Resources")
    check_runtime_version(app / "Contents/Resources")
    return app.resolve()


def mac_update(app: Path, output: Path, version: str, config: dict) -> None:
    if mac_app(app.parent, config) != app.resolve():
        raise ValueError("The update must use the exact release app")
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleShortVersionString") != version:
        raise ValueError("The app version does not match the release")
    resources = app / "Contents/Resources"
    manifest = json.loads((resources / "python/runtime-manifest.json").read_text(encoding="utf-8"))
    active = json.loads((resources / "python-runtime/runtime-envs/active-runtime.json").read_text(encoding="utf-8"))
    if active.get("backend") != "mlx" or active.get("source") != "bundled" or active.get("pythonPath") != "../bin/python3":
        raise ValueError("The macOS update is missing its bundled MLX runtime contract")
    if not (resources / "python-runtime/bin/python3").is_file():
        raise ValueError("The macOS update is missing its bundled Python executable")
    if output.resolve().is_relative_to(app.resolve()):
        raise ValueError("The update archive must not modify the signed app")
    for item in app.rglob("*"):
        if item.is_symlink() and not item.resolve().is_relative_to(app.resolve()):
            raise ValueError(f"The macOS bundle contains an external link: {item}")
    size = sum(item.stat().st_size for item in app.rglob("*") if item.is_file() and not item.is_symlink())
    if not 0 < size <= 16 * 1024**3:
        raise ValueError("The macOS update unpacked size exceeds its release limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        archive.add(app, arcname=app.name)
    sidecar = {"version": version, "pymss_unpacked_size": size, "pymss_runtime_contract": {
        "baseGeneration": manifest["baseGeneration"], "manifestVersion": manifest["manifestVersion"],
        "python": manifest["python"], "backend": active["backend"], "source": active["source"],
        "packageVersions": active.get("packageVersions", {}),
    }}
    Path(str(output) + ".json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("destination", type=Path)
    stage.add_argument("--source", type=Path, default=ROOT)
    config = commands.add_parser("config")
    config.add_argument("resources", type=Path)
    config.add_argument("--platform", choices=("linux", "macos"), required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("resources", type=Path)
    app = commands.add_parser("mac-app")
    app.add_argument("directory", type=Path)
    update = commands.add_parser("mac-update")
    update.add_argument("app", type=Path)
    update.add_argument("output", type=Path)
    update.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.command == "stage":
        stage_resources(args.source, args.destination)
    elif args.command == "config":
        print(json.dumps(bundle_config(args.resources, args.platform)))
    elif args.command == "verify":
        verify_resources(args.resources)
    else:
        tauri = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        if args.command == "mac-app":
            print(mac_app(args.directory, tauri))
        else:
            mac_update(args.app, args.output, args.version, tauri)


if __name__ == "__main__":
    main()
