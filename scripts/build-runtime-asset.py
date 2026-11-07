"""Build a new, self-contained Windows runtime asset from the declared Python.

This never updates an existing runtime or copies packages from another Python ABI.
The stage doubles as an isolated development runtime; only one archive is created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OWNER = ".runtime-build.json"


def linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def isolated_environment(stage: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("MEDIAFORGE_", "PYMSS_", "TAURI_", "VERSIONS_SIGNING_", "SIGNPATH_"))
           and not re.search(r"KEY|PASSWORD|TOKEN|SECRET", name, re.I)
           and name not in {"PYTHONHOME", "PYTHONPATH"}}
    profile = stage / ".build-data/profile"
    cache = stage / ".build-data/cache"
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PIP_NO_CACHE_DIR="1",
               HOME=str(profile), USERPROFILE=str(profile),
               APPDATA=str(profile / "AppData/Roaming"), LOCALAPPDATA=str(profile / "AppData/Local"),
               XDG_CONFIG_HOME=str(profile / ".config"), XDG_CACHE_HOME=str(cache),
               MPLCONFIGDIR=str(cache / "matplotlib"), YOLO_CONFIG_DIR=str(cache / "ultralytics"),
               NUMBA_CACHE_DIR=str(cache / "numba"),
               PIP_CONFIG_FILE=os.devnull, PIP_INDEX_URL="https://pypi.org/simple", PIP_EXTRA_INDEX_URL="",
               MEDIAFORGE_DATA_ROOT=str(stage / ".build-data"),
               MEDIAFORGE_MODEL_DIR=str(stage / ".build-data/models"),
               MEDIAFORGE_RUNTIME_ENVS_DIR=str(stage / ".build-data/runtime-envs"),
               HF_HOME=str(stage / ".build-data/cache/hf"),
               TORCH_HOME=str(stage / ".build-data/cache/torch"),
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    for key, relative in {
        "HF_HUB_CACHE": "cache/hf/hub", "HUGGINGFACE_HUB_CACHE": "cache/hf/hub",
        "HF_ASSETS_CACHE": "cache/hf/assets", "HF_XET_CACHE": "cache/hf/xet",
        "HF_MODULES_CACHE": "cache/hf/modules", "TRANSFORMERS_CACHE": "cache/transformers",
        "MODELSCOPE_CACHE": "cache/modelscope", "TORCH_EXTENSIONS_DIR": "cache/torch-extensions",
        "TRITON_CACHE_DIR": "cache/triton", "U2NET_HOME": "cache/u2net",
        "WEBVIEW2_USER_DATA_FOLDER": "webview", "XDG_DATA_HOME": "state",
        "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
    }.items():
        env[key] = str(stage / ".build-data" / relative)
    if stage.is_dir():
        (stage / ".build-data/tmp").mkdir(parents=True, exist_ok=True)
    env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(profile))
    env["MODELSCOPE_OFFLINE"] = "1"
    return env


def run(python: Path, args: list[str], stage: Path, *, capture: bool = False) -> str:
    result = subprocess.run([str(python), "-B", *args], env=isolated_environment(stage),
                            cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE if capture else None, check=True)
    return result.stdout or ""


def python_info(python: Path, stage: Path) -> dict:
    return json.loads(run(python, ["-I", "-c", "import json,sys; print(json.dumps({"
        "'version':'.'.join(map(str,sys.version_info[:2])), 'fullVersion':sys.version.split()[0],"
        "'prefix':sys.prefix, 'basePrefix':sys.base_prefix, 'path':sys.path}))"], stage, capture=True))


def checked_stage(asset_id: str, stage: Path) -> Path:
    if not re.fullmatch(r"runtime-(cuda|cpu)-py312-[0-9]+(?:\.[0-9]+)+", asset_id):
        raise ValueError("Asset ID must identify the backend, py312 and dated build")
    full = stage.absolute()
    allowed = ROOT / ".dev-data/runtime-envs"
    if full.parent != allowed or full.name != asset_id:
        raise ValueError("Stage must be the exact asset directory under .dev-data/runtime-envs")
    for parent in (full, *full.parents):
        if linked(parent):
            raise ValueError("Runtime build refuses linked stage paths")
    return full


def copy_base(python: Path, stage: Path, expected: str) -> None:
    info = python_info(python, stage)
    if info["version"] != expected or info["prefix"] != info["basePrefix"]:
        raise ValueError(f"A non-venv Python {expected} host is required")
    base = Path(info["basePrefix"])
    if stage.exists():
        raise FileExistsError("Refusing to overwrite an existing runtime stage")
    for required in (base / "python.exe", base / "python312.dll", base / "Lib/encodings"):
        if not required.exists():
            raise ValueError("Host Python lacks the declared interpreter/stdlib layout")
    stage.mkdir(parents=True)
    scripts = stage / "Scripts"
    scripts.mkdir()
    for file in base.iterdir():
        if file.is_file() and (file.name == "python.exe" or file.suffix.lower() == ".dll"):
            shutil.copy2(file, scripts / file.name)
    shutil.copy2(base / "LICENSE.txt", stage / "LICENSE.txt")
    shutil.copytree(base / "DLLs", stage / "DLLs", ignore=shutil.ignore_patterns("*_tkinter*"))
    shutil.copytree(base / "Lib", stage / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "EXTERNALLY-MANAGED", "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter"))
    (stage / "Lib/site-packages").mkdir()
    actual = python_info(scripts / "python.exe", stage)
    if actual["version"] != expected or Path(actual["prefix"]) != stage or actual["prefix"] != actual["basePrefix"]:
        raise ValueError("The staged interpreter did not discover its own standard library")
    (stage / OWNER).write_text(json.dumps({"schemaVersion": 1, "python": expected,
                                         "hostVersion": info["fullVersion"]}), encoding="utf-8")


def install(stage: Path, backend: str, manifest: dict) -> None:
    python = stage / "Scripts/python.exe"
    owned = json.loads((stage / OWNER).read_text(encoding="utf-8"))
    actual = python_info(python, stage)
    if (owned.get("schemaVersion") != 1 or owned.get("python") != manifest["python"]
            or actual["version"] != manifest["python"] or actual["prefix"] != actual["basePrefix"]
            or Path(actual["prefix"]) != stage):
        raise ValueError("Owned runtime stage no longer matches the declared interpreter")
    # This fresh application-owned copy is not the uv-managed source installation.
    (stage / "Lib/EXTERNALLY-MANAGED").unlink(missing_ok=True)
    run(python, ["-m", "ensurepip", "--default-pip"], stage)
    run(python, ["-m", "pip", "install", "--no-cache-dir", "pip", "setuptools", "wheel"], stage)
    torch = manifest["backends"][backend]["torch"]
    # Install the large backend wheel once. All remaining pins reuse that build.
    run(python, ["-m", "pip", "install", "--no-cache-dir", "--no-deps", "--index-url", torch["indexUrl"],
                 torch["requirement"], "torchvision==0.22.1"], stage)
    lock = ROOT / "python/runtime-lock-media.txt"
    requirements = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line.lower().startswith(("torch==", "torchvision==", "opencv-python-headless==")):
            continue
        requirements.append(line)
    # Ultralytics/GFPGAN require opencv-python; install one cv2 provider, not two.
    pinned = stage / ".build-requirements.txt"
    pinned.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    try:
        run(python, ["-m", "pip", "install", "--no-cache-dir", "-r", str(pinned)], stage)
    finally:
        pinned.unlink(missing_ok=True)


def verify(stage: Path, backend: str, expected: str) -> dict:
    python = stage / "Scripts/python.exe"
    code = """import importlib,json,sys,torch
from pathlib import Path
root=Path(sys.argv[1]).resolve()
assert sys.version_info[:2]==(3,12),sys.version
assert sys.prefix==sys.base_prefix and Path(sys.prefix).resolve()==root
assert not (root/'pyvenv.cfg').exists() and not (root/'Scripts/pyvenv.cfg').exists()
for module in ('pymss','pymss_core','pymss.graph','av','librosa','cv2','spandrel','propainter'):
    importlib.import_module(module)
assert bool(torch.version.cuda)==(sys.argv[2]=='cuda'),torch.__version__
print(json.dumps({'python':sys.version.split()[0],'torch':torch.__version__,'cuda':torch.version.cuda,'selfContained':True}))
"""
    info = json.loads(run(python, ["-I", "-c", code, str(stage), backend], stage, capture=True))
    if not info["python"].startswith(expected + "."):
        raise ValueError("Runtime Python differs from the declared version")
    return info


def package(stage: Path, asset_id: str, backend: str, info: dict) -> dict:
    output = ROOT / "release/runtime"
    for parent in (output, *output.parents):
        if linked(parent):
            raise ValueError("Runtime archive refuses linked output paths")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{asset_id}.zip"
    if archive.exists():
        raise FileExistsError("Refusing to replace an existing runtime asset")
    handle, name = tempfile.mkstemp(prefix=f".{asset_id}-", suffix=".tmp", dir=output)
    os.close(handle)
    pending = Path(name)
    try:
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as target:
            excluded = {"__pycache__", ".build-data", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
            for directory, names, files in os.walk(stage, followlinks=False):
                names[:] = sorted(name for name in names if name not in excluded)
                for name in names:
                    if linked(Path(directory) / name):
                        raise ValueError("Runtime archive refuses linked directories")
                for name in sorted(files):
                    file = Path(directory) / name
                    if linked(file):
                        raise ValueError("Runtime archive refuses linked files")
                    if name in {OWNER, ".asset-id"} or file.suffix.lower() in {".pyc", ".pyo"}:
                        continue
                    target.write(file, file.relative_to(stage).as_posix())
        with pending.open("r+b") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
            os.fsync(source.fileno())
        # Publish without overwriting a parallel build and without copying gigabytes.
        os.link(pending, archive)
    finally:
        pending.unlink(missing_ok=True)
    descriptor = {"assetId": asset_id, "backend": backend, "python": "3.12",
                  "size": archive.stat().st_size, "sha256": digest, "urls": [
                      f"https://hf-mirror.com/datasets/TLBtianhu/MediaForge-Dist/resolve/main/runtime/{asset_id}.zip",
                      f"https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/runtime/{asset_id}.zip"]}
    (output / f"{asset_id}.json").write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
    (stage / ".asset-id").write_text(json.dumps({"schemaVersion": 2, "assetId": asset_id,
                                                "python": "3.12", "sha256": digest}), encoding="utf-8")
    return descriptor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--backend", choices=("cuda", "cpu"), required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--phase", choices=("base", "install", "verify", "package", "all"), default="all")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "python/runtime-manifest.json").read_text(encoding="utf-8"))
    expected = manifest["python"]
    if expected != "3.12" or not args.asset_id.startswith(f"runtime-{args.backend}-py312-"):
        raise ValueError("Asset backend/Python does not match the current manifest")
    stage = checked_stage(args.asset_id, ROOT / ".dev-data/runtime-envs" / args.asset_id)
    if args.phase in {"base", "all"}:
        copy_base(args.python, stage, expected)
    elif not (stage / OWNER).is_file():
        raise ValueError("Runtime stage lacks this builder's ownership record")
    if args.phase in {"install", "all"}:
        install(stage, args.backend, manifest)
    if args.phase in {"verify", "package", "all"}:
        info = verify(stage, args.backend, expected)
        print(json.dumps(info))
        if args.phase in {"package", "all"}:
            print(json.dumps(package(stage, args.asset_id, args.backend, info)))


if __name__ == "__main__":
    main()
