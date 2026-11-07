"""Explicit paths and a disposable process environment for media verification."""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def _environment_value(*names: str) -> str | None:
    return next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), None)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--python", default=_environment_value("MEDIAFORGE_RUNTIME_PYTHON", "MEDIAFORGE_PYTHON", "PYMSS_STUDIO_PYTHON"), help="Explicit test interpreter; never discovered from an installed application")
    parser.add_argument("--model-dir", default=_environment_value("MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR", "PYMSS_STUDIO_MODEL_DIR"), help="Explicit test model root containing media/; models are never downloaded")
    parser.add_argument("--data-root", default=_environment_value("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT", "PYMSS_STUDIO_DATA_DIR"), help="Explicit isolated test data root; never the user/dogfood profile")
    parser.add_argument("--output-dir", type=Path, help="Preserve outputs here; otherwise outputs use a temporary directory")
    parser.add_argument("--input", type=Path, help="Explicit sample image; otherwise generate a synthetic PNG")


def test_path(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    parts = [part.casefold() for part in path.parts]
    if any(part in {".mediaforge", ".pymss-studio", "studio.mediaforge.desktop"} for part in parts):
        raise ValueError(f"{label} must not use a user/dogfood profile: {path}")
    if any(parts[index:index + 2] == ["programs", "mediaforge"] for index in range(len(parts) - 1)):
        raise ValueError(f"{label} must not use the installed application: {path}")
    return path


def interpreter(value: str | None) -> Path:
    if not value:
        raise ValueError("model smoke tests require --python (or MEDIAFORGE_RUNTIME_PYTHON)")
    path = Path(shutil.which(value) or value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"test interpreter does not exist: {path}")
    return test_path(path, "--python")


def model_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    python = interpreter(args.python)
    if not args.model_dir or not args.data_root:
        raise ValueError("model smoke tests require explicit --model-dir and --data-root (or their test environment variables)")
    models = test_path(args.model_dir, "--model-dir")
    data = test_path(args.data_root, "--data-root")
    if not models.is_dir():
        raise ValueError(f"test model directory does not exist: {models}")
    if data == models or models in data.parents:
        raise ValueError("--data-root must be separate from the model directory")
    return python, models, data


def isolated_environment(data: Path, python: Path, models: Path | None = None, outputs: Path | None = None) -> dict[str, str]:
    data = test_path(data, "--data-root")
    models = test_path(models or data / "models", "--model-dir")
    outputs = test_path(outputs or data / "outputs", "--output-dir")
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("MEDIAFORGE_", "PYMSS_STUDIO_", "PYMSS_MODEL_")) or key in {
            "PYTHONPATH", "PYTHONHOME", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "MODELSCOPE_API_TOKEN", "MODELSCOPE_TOKEN",
        }:
            env.pop(key, None)
    runtime = data / "runtime-envs"
    for name in ("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT", "PYMSS_STUDIO_DATA_DIR"):
        env[name] = str(data)
    for name in ("MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR", "PYMSS_STUDIO_MODEL_DIR"):
        env[name] = str(models)
    for prefix in ("MEDIAFORGE", "PYMSS_STUDIO"):
        env[f"{prefix}_RUNTIME_ENVS_DIR"] = str(runtime)
        env[f"{prefix}_ACTIVE_RUNTIME_FILE"] = str(runtime / "active-runtime.json")
        env[f"{prefix}_PYTHON"] = str(python)
        env[f"{prefix}_DEFAULT_OUTPUT_DIR"] = str(outputs)
    env["MEDIAFORGE_RUNTIME_PYTHON"] = env["PYMSS_STUDIO_BOOTSTRAP_PYTHON"] = str(python)
    profile = data / "test-profile"
    env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(profile))
    for name, path in {
        "HOME": profile, "USERPROFILE": profile, "APPDATA": profile / "AppData/Roaming", "LOCALAPPDATA": profile / "AppData/Local",
        "WEBVIEW2_USER_DATA_FOLDER": data / "webview", "XDG_CACHE_HOME": data / "cache", "XDG_CONFIG_HOME": data / "config", "XDG_DATA_HOME": data / "state",
        "HF_HOME": data / "cache/huggingface", "HUGGINGFACE_HUB_CACHE": data / "cache/huggingface/hub", "HF_HUB_CACHE": data / "cache/huggingface/hub",
        "TRANSFORMERS_CACHE": data / "cache/transformers", "HF_ASSETS_CACHE": data / "cache/huggingface/assets", "HF_XET_CACHE": data / "cache/huggingface/xet",
        "MODELSCOPE_CACHE": data / "cache/modelscope", "PIP_CACHE_DIR": data / "cache/pip",
        "TORCH_HOME": data / "cache/torch", "TORCH_EXTENSIONS_DIR": data / "cache/torch-extensions", "TRITON_CACHE_DIR": data / "cache/triton",
        "MPLCONFIGDIR": data / "cache/matplotlib", "NUMBA_CACHE_DIR": data / "cache/numba",
        "TEMP": data / "tmp", "TMP": data / "tmp", "TMPDIR": data / "tmp",
    }.items():
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MODELSCOPE_OFFLINE="1")
    return env
