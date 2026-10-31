from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from functools import partial, wraps
from pathlib import Path
from typing import Any, Callable

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name
from pip._vendor.packaging.version import Version

from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock

MANIFEST_PATH = Path(__file__).parents[2] / "runtime-manifest.json"

def _default_runtime_envs_dir() -> Path:
    executable = Path(sys.executable).resolve()
    if executable.parent.name.lower() in {"scripts", "bin"}:
        env_dir = executable.parent.parent
        if (env_dir / "pyvenv.cfg").is_file():
            return env_dir.parent
        return env_dir / "runtime-envs"
    return executable.parent / "runtime-envs"


RUNTIME_ENVS_DIR = Path(os.environ.get("MEDIAFORGE_RUNTIME_ENVS_DIR") or _default_runtime_envs_dir())

ACTIVE_RUNTIME_FILE = Path(os.environ.get("MEDIAFORGE_ACTIVE_RUNTIME_FILE") or RUNTIME_ENVS_DIR / "active-runtime.json")

BUNDLED_RUNTIME_ENVS_DIR = Path(os.environ["PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR"]) if os.environ.get("PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR") else None

ALLOW_DEBUG_RUNTIME_OVERRIDE = os.environ.get("PYMSS_STUDIO_ALLOW_DEBUG_RUNTIME_OVERRIDE") == "1"

PACKAGE_IMPORT_NAMES = {
    "pysocks": "socks",
    "pyyaml": "yaml",
    "pymss-core": "pymss_core",
    "typing-extensions": "typing_extensions",
}

def _manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for spec in manifest.get("backends", {}).values():
        override = spec.get("platformOverrides", {}).get(sys.platform)
        if isinstance(override, dict):
            spec.update(override)
    return manifest


def _pin_manifest_requirement(requirement: Any, version: str) -> str:
    """Pin a manifest requirement while retaining any extras it declares."""
    spec = str(requirement or "").strip()
    name_match = re.match(r"([A-Za-z0-9_.-]+)", spec)
    name = name_match.group(1) if name_match else "pymss"
    extras_match = re.search(r"(\[[^\]]+\])", spec)
    extras = extras_match.group(1) if extras_match else ""
    return f"{name}{extras}=={version}"


def _manifest_pinned_version(manifest: dict[str, Any], name: str) -> str:
    try:
        requirement = Requirement(str(manifest.get("common", {}).get(name) or ""))
    except Exception:
        return ""
    for specifier in requirement.specifier:
        if specifier.operator in {"==", "==="} and "*" not in specifier.version:
            return specifier.version
    return ""


def _runtime_requirement(name: str, value: Any) -> Requirement:
    requirement = Requirement(str(value))
    if name in {"pymss", "pymss-core"}:
        # Release pins define the bundled baseline, not a ceiling for core updates.
        requirement.specifier = SpecifierSet(
            ",".join(
                f">={spec.version.removesuffix('.*')}" if spec.operator in {"==", "===", "~="} else str(spec)
                for spec in requirement.specifier if spec.operator not in {"<", "<="}
            )
        )
    return requirement


def _emit(event_type: str, payload: dict[str, Any], task_id: str | None = None) -> None:
    from shared.worker_protocol import emit
    emit(event_type, payload, task_id=task_id)


def _serialized_runtime_command(command: Callable[..., int], *, query: bool = False) -> Callable[[dict[str, Any]], int]:
    @wraps(command)
    def run(payload: dict[str, Any]) -> int:
        try:
            # Repairing queries hold the same lock as writers for their entire operation.
            # Read-only installations may only be observed, never repaired or recovered.
            with runtime_lock(RUNTIME_ENVS_DIR / ".runtime-management.lock", allow_read_only=query) as writable:
                return command(payload, repair=writable) if query else command(payload)
        except RuntimeLockBusy:
            from shared.worker_protocol import emit_error
            return emit_error(
                "RUNTIME_BUSY",
                "Another runtime operation is in progress. Wait for it to finish and retry.",
                task_id=payload.get("taskId"),
                recoverable=True,
            )
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                raise
            from shared.worker_protocol import emit_error
            return emit_error(
                "RUNTIME_PERMISSION_DENIED",
                "The runtime directory does not allow this operation.",
                detail=str(exc),
                task_id=payload.get("taskId"),
                recoverable=True,
            )
    return run


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _atomic_write_text(path: Path, content: str) -> None:
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _env_dir(backend: str) -> Path:
    return RUNTIME_ENVS_DIR / backend


def _bootstrap_python_path() -> Path:
    python_path = Path(os.environ.get("PYMSS_STUDIO_BOOTSTRAP_PYTHON") or sys.executable)
    return python_path if python_path.is_file() else Path(sys.executable)


ENV_STATE_VERSION = 2

PYPI_MIRROR_URLS = {
    "ustc": "https://mirrors.ustc.edu.cn/pypi/web/simple",
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "pypi": "https://pypi.org/simple",
}

def _resolve_pypi_mirror(mirror: str, locale: str) -> tuple[str, str | None]:
    selected = "ustc" if mirror == "auto" and locale.startswith("zh") else "pypi" if mirror == "auto" else mirror
    return selected, PYPI_MIRROR_URLS.get(selected)


def _backend_extra_names(manifest: dict[str, Any], backend: str | None) -> list[str]:
    return [re.split(r"[<>=!~;\[]", str(extra), maxsplit=1)[0].strip() for extra in manifest["backends"].get(backend or "", {}).get("extras", [])]


def _state_matches_backend(backend: str, state: dict[str, Any]) -> bool:
    """Whether the torch build recorded in an environment's state can belong to that environment.

    MLX ships a CPU torch build, so "cpu" is the correct recording for the mlx backend."""
    recorded = str(state.get("torchBackend") or "")
    if not recorded or recorded.startswith("error:"):
        return True  # Nothing concrete to contradict.
    return recorded == ("cpu" if backend == "mlx" else backend)


_PCI_VENDORS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}

def _vendor_from_pci_id(value: str) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return _PCI_VENDORS.get(text[-4:]) if len(text) >= 4 else None


LINUX_DRM_ROOT = Path("/sys/class/drm")

def _dir_size_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(str(path), onerror=lambda _exc: None):
        for name in files:
            try:
                entry = Path(root) / name
                # Symlinks (common in POSIX venvs) point outside the env; don't double count.
                if entry.is_symlink():
                    continue
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def _latest_pypi_version(distribution: str, channel: str = "stable") -> str | None:
    url = f"https://pypi.org/pypi/{distribution}/json"
    with urllib.request.urlopen(url, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    versions = []
    for version, files in payload.get("releases", {}).items():
        try:
            parsed = Version(version)
        except ValueError:
            continue
        if not files or all(file.get("yanked", False) for file in files):
            continue
        if channel != "prerelease" and parsed.is_prerelease:
            continue
        versions.append(parsed)
    return str(max(versions)) if versions else None


def _runtime_env_dir_for_python(python_path: Path) -> Path:
    return python_path.parent.parent if python_path.parent.name.lower() in {"scripts", "bin"} else python_path.parent


def _bundled_bootstrap_python() -> Path | None:
    if not BUNDLED_RUNTIME_ENVS_DIR:
        return None
    runtime_root = BUNDLED_RUNTIME_ENVS_DIR.parent
    candidates = [
        runtime_root / "python.exe",
        runtime_root / "bin" / "python3",
        runtime_root / "bin" / "python",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _runtime_overlay_root() -> Path:
    return RUNTIME_ENVS_DIR / ".overlays"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _overlay_requirement_is_satisfied(requirement: str, installed_version: str | None) -> bool:
    if not installed_version:
        return False
    try:
        parsed = Requirement(requirement)
        return not parsed.specifier or Version(installed_version) in parsed.specifier
    except Exception:
        return False


def _is_base_accelerator_package(name: str) -> bool:
    name = canonicalize_name(name)
    return name in {"torch", "torchaudio", "torchvision", "triton", "mlx"} or name.startswith(("nvidia-", "rocm-", "mlx-"))


def _runtime_manifest_status(actual: Any, expected: Any) -> str:
    """Compare numeric manifest markers without treating malformed prefixes as versions."""
    actual_text = str(actual or "").strip()
    expected_text = str(expected or "").strip()
    if not all(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) for value in (actual_text, expected_text)):
        return "unknown"
    actual_parts = [int(part) for part in actual_text.split(".")]
    expected_parts = [int(part) for part in expected_text.split(".")]
    length = max(len(actual_parts), len(expected_parts))
    actual_parts.extend([0] * (length - len(actual_parts)))
    expected_parts.extend([0] * (length - len(expected_parts)))
    if actual_parts == expected_parts:
        return "current"
    return "older" if actual_parts < expected_parts else "newer"


def _runtime_probe_failure(error: Exception) -> dict[str, str]:
    # Exception strings can contain the entire probe command, subprocess output
    # or credential-bearing paths. Report only the failure category and codes.
    if isinstance(error, ValueError):
        code, reason = "PYTHON_PROBE_INVALID_RESPONSE", "Python runtime probe returned an invalid response"
    elif isinstance(error, OSError):
        code, reason = "PYTHON_START_FAILED", "Python interpreter could not be started"
    else:
        code, reason = "PYTHON_PROBE_FAILED", "Python runtime probe did not complete"
    details = type(error).__name__
    if isinstance(error, subprocess.CalledProcessError):
        details += f", exit code {error.returncode}"
    elif isinstance(error, OSError):
        winerror = getattr(error, "winerror", None)
        if isinstance(winerror, int):
            details += f", WinError {winerror}"
        elif isinstance(error.errno, int):
            details += f", errno {error.errno}"
    return {"code": code, "message": f"{reason} ({details})."[:240]}


def _torch_probe_failure(torch_backend: str) -> dict[str, str]:
    details = re.search(r"\b(?:ImportError|ModuleNotFoundError|OSError|RuntimeError)\b", torch_backend)
    winerror = re.search(r"\bwinerror\s+(\d{1,8})\b", torch_backend, re.IGNORECASE)
    parts = [details.group(0)] if details else []
    if winerror:
        parts.append(f"WinError {winerror.group(1)}")
    suffix = f" ({', '.join(parts)})" if parts else ""
    return {"code": "TORCH_IMPORT_FAILED", "message": f"Torch import failed{suffix}."}


def _normal_runtime_path(path: Path) -> str:
    """Return a normal Win32 path before handing a runtime directory to ``venv``.

    Rust's ``canonicalize`` returns a ``\\\\?\\`` long-path prefix on Windows.  The prefix is
    valid for ordinary file I/O, but Python 3.12's venv/ensurepip bootstrap intermittently fails
    when its destination (and the child interpreter it launches) keeps that prefix, especially
    for repository paths containing non-ASCII characters.
    """
    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[len("\\\\?\\UNC\\"):]
    if value.startswith("\\\\?\\"):
        return value[len("\\\\?\\"):]
    return value



__all__ = ['MANIFEST_PATH', 'RUNTIME_ENVS_DIR', 'ACTIVE_RUNTIME_FILE', 'BUNDLED_RUNTIME_ENVS_DIR', 'ALLOW_DEBUG_RUNTIME_OVERRIDE', 'PACKAGE_IMPORT_NAMES', 'ENV_STATE_VERSION', 'PYPI_MIRROR_URLS', '_PCI_VENDORS', 'LINUX_DRM_ROOT', '_default_runtime_envs_dir', '_manifest', '_pin_manifest_requirement', '_manifest_pinned_version', '_runtime_requirement', '_emit', '_serialized_runtime_command', '_module_available', '_atomic_write_text', '_env_dir', '_bootstrap_python_path', '_resolve_pypi_mirror', '_backend_extra_names', '_state_matches_backend', '_vendor_from_pci_id', '_dir_size_bytes', '_latest_pypi_version', '_runtime_env_dir_for_python', '_bundled_bootstrap_python', '_runtime_overlay_root', '_read_json_object', '_overlay_requirement_is_satisfied', '_is_base_accelerator_package', '_runtime_manifest_status', '_runtime_probe_failure', '_torch_probe_failure', '_normal_runtime_path']
