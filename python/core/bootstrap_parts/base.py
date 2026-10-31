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
from core import worker_bootstrap as wb

def _default_runtime_envs_dir() -> Path:
    executable = Path(sys.executable).resolve()
    if executable.parent.name.lower() in {"scripts", "bin"}:
        env_dir = executable.parent.parent
        if (env_dir / "pyvenv.cfg").is_file():
            return env_dir.parent
        return env_dir / "runtime-envs"
    return executable.parent / "runtime-envs"


def _manifest() -> dict[str, Any]:
    manifest = json.loads(wb.MANIFEST_PATH.read_text(encoding="utf-8"))
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


def _supported_backend(backend: str, manifest: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
    normalized = str(backend or "").strip().lower()
    if not normalized or "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        return None
    manifest = manifest or wb._manifest()
    spec = manifest.get("backends", {}).get(normalized)
    return (normalized, spec) if isinstance(spec, dict) else None


def _emit(event_type: str, payload: dict[str, Any], task_id: str | None = None) -> None:
    from shared.worker_protocol import emit
    emit(event_type, payload, task_id=task_id)


def _serialized_runtime_command(command: Callable[..., int], *, query: bool = False) -> Callable[[dict[str, Any]], int]:
    @wraps(command)
    def run(payload: dict[str, Any]) -> int:
        try:
            # Repairing queries hold the same lock as writers for their entire operation.
            # Read-only installations may only be observed, never repaired or recovered.
            with runtime_lock(wb.RUNTIME_ENVS_DIR / ".runtime-management.lock", allow_read_only=query) as writable:
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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    wb._atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


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
    return wb.RUNTIME_ENVS_DIR / backend


def _env_state_path(backend: str) -> Path:
    return wb._env_dir(backend) / "pymss-runtime-state.json"


def _env_log_path(backend: str) -> Path:
    return wb._env_dir(backend) / "pymss-runtime-install.log"


def _bootstrap_python_path() -> Path:
    python_path = Path(os.environ.get("PYMSS_STUDIO_BOOTSTRAP_PYTHON") or sys.executable)
    return python_path if python_path.is_file() else Path(sys.executable)


def _resolve_pypi_mirror(mirror: str, locale: str) -> tuple[str, str | None]:
    selected = "ustc" if mirror == "auto" and locale.startswith("zh") else "pypi" if mirror == "auto" else mirror
    return selected, wb.PYPI_MIRROR_URLS.get(selected)


def _backend_extra_names(manifest: dict[str, Any], backend: str | None) -> list[str]:
    return [re.split(r"[<>=!~;\[]", str(extra), maxsplit=1)[0].strip() for extra in manifest["backends"].get(backend or "", {}).get("extras", [])]


def _state_matches_backend(backend: str, state: dict[str, Any]) -> bool:
    """Whether the torch build recorded in an environment's state can belong to that environment.

    MLX ships a CPU torch build, so "cpu" is the correct recording for the mlx backend."""
    recorded = str(state.get("torchBackend") or "")
    if not recorded or recorded.startswith("error:"):
        return True  # Nothing concrete to contradict.
    return recorded == ("cpu" if backend == "mlx" else backend)


def _vendor_from_pci_id(value: str) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return wb._PCI_VENDORS.get(text[-4:]) if len(text) >= 4 else None


def _windows_gpu_vendors() -> list[str]:
    import ctypes
    from ctypes import wintypes

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    enum_display_devices = ctypes.windll.user32.EnumDisplayDevicesW
    enum_display_devices.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    enum_display_devices.restype = wintypes.BOOL

    vendors: list[str] = []
    index = 0
    while index < 32:  # Bounded: mirroring drivers can otherwise enumerate indefinitely.
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not enum_display_devices(None, index, ctypes.byref(device), 0):
            break
        index += 1
        match = re.search(r"VEN_([0-9A-Fa-f]{4})", device.DeviceID or "")
        vendor = wb._vendor_from_pci_id(match.group(1)) if match else None
        if vendor and vendor not in vendors:
            vendors.append(vendor)
    return vendors


def _linux_gpu_vendors(drm_root: Path | None = None) -> list[str]:
    vendors: list[str] = []
    for path in sorted((drm_root or wb.LINUX_DRM_ROOT).glob("card*/device/vendor")):
        try:
            vendor = wb._vendor_from_pci_id(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if vendor and vendor not in vendors:
            vendors.append(vendor)
    return vendors


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


def _same_path(left: Any, right: Any) -> bool:
    try:
        left_path = wb._normal_runtime_path(Path(str(left))) if os.name == "nt" else str(left)
        right_path = wb._normal_runtime_path(Path(str(right))) if os.name == "nt" else str(right)
        return os.path.normcase(os.path.normpath(left_path)) == os.path.normcase(os.path.normpath(right_path))
    except Exception:
        return False


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


def _path_is_within(path: Path, root: Path) -> bool:
    """Check containment while accepting mixed regular/extended Windows path spellings."""
    candidates = [(path, root)]
    normalized_path = Path(wb._normal_runtime_path(path))
    normalized_root = Path(wb._normal_runtime_path(root))
    if normalized_path != path or normalized_root != root:
        candidates.append((normalized_path, normalized_root))
    for child, parent in candidates:
        try:
            if child.resolve().is_relative_to(parent.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _bundled_bootstrap_python() -> Path | None:
    if not wb.BUNDLED_RUNTIME_ENVS_DIR:
        return None
    runtime_root = wb.BUNDLED_RUNTIME_ENVS_DIR.parent
    candidates = [
        runtime_root / "python.exe",
        runtime_root / "bin" / "python3",
        runtime_root / "bin" / "python",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _runtime_overlay_root() -> Path:
    return wb.RUNTIME_ENVS_DIR / ".overlays"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _core_update_identity(state: dict[str, Any], versions: dict[str, Any], manifest: dict[str, Any]) -> str:
    identity = {
        "backend": state.get("backend"),
        "python": os.path.normcase(wb._normal_runtime_path(Path(str(state.get("pythonPath") or "")))),
        "overlay": os.path.normcase(wb._normal_runtime_path(Path(str(state.get("overlayPath") or "")))),
        "versions": versions, "manifest": manifest,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _load_core_update_plan(plan_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
        raise ValueError("A confirmed runtime update plan is required")
    plan = wb._read_json_object(wb._runtime_overlay_root() / ".plans" / f"{plan_id}.json")
    if not plan or time.time() - plan.get("createdAt", 0) > 15 * 60:
        raise ValueError("The runtime update plan expired. Check for updates again.")
    return plan


def _overlay_requirement_is_satisfied(requirement: str, installed_version: str | None) -> bool:
    if not installed_version:
        return False
    try:
        parsed = Requirement(requirement)
        return not parsed.specifier or Version(installed_version) in parsed.specifier
    except Exception:
        return False


def _overlay_requirements(
    manifest: dict[str, Any],
    backend: str,
    base_versions: dict[str, str | None],
    target_pymss_version: str,
    target_pymss_core_version: str,
) -> list[str]:
    requirements = [
        wb._pin_manifest_requirement(manifest.get("common", {}).get("pymss"), target_pymss_version),
        f"pymss-core=={target_pymss_core_version}",
    ]
    for name, value in manifest.get("common", {}).items():
        if name in {"pymss", "pymss-core"}:
            continue
        requirement = str(value)
        if not wb._overlay_requirement_is_satisfied(requirement, base_versions.get(name)):
            requirements.append(requirement)
    for value in manifest.get("backends", {}).get(backend, {}).get("extras", []) or []:
        requirement = str(value)
        try:
            name = Requirement(requirement).name
        except Exception:
            name = requirement
        if not wb._overlay_requirement_is_satisfied(requirement, base_versions.get(name)):
            requirements.append(requirement)
    return requirements


def _is_base_accelerator_package(name: str) -> bool:
    name = canonicalize_name(name)
    return name in {"torch", "torchaudio", "torchvision", "triton", "mlx"} or name.startswith(("nvidia-", "rocm-", "mlx-"))


def _resolved_overlay_requirements(requirements: list[str], report: dict[str, Any]) -> list[str]:
    if report.get("version") != "1" or not isinstance(report.get("install"), list):
        raise RuntimeError("The package resolver returned an unsupported installation report")
    resolved = {canonicalize_name(Requirement(value).name): value for value in requirements}
    for item in report["install"]:
        metadata = item["metadata"]
        name = canonicalize_name(metadata["name"])
        if wb._is_base_accelerator_package(name):
            raise RuntimeError(f"The core update requires changing the base accelerator package {name}; the current runtime was preserved")
        version = str(Version(metadata["version"]))
        resolved[name] = wb._pin_manifest_requirement(resolved.get(name, name), version)
    return list(resolved.values())


def _manifest_requirement_names(manifest: dict[str, Any], backend: str) -> list[str]:
    """Return distribution names that the selected backend must provide."""
    return [*manifest.get("common", {}), *wb._backend_extra_names(manifest, backend)]


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


def _manifest_versions_are_satisfied(
    probed: dict[str, Any],
    manifest: dict[str, Any],
    backend: str,
) -> tuple[bool, list[str]]:
    """Validate installed distribution versions against the shipped manifest.

    Pip resolves the requirements during installation, but a successful command alone is not
    enough to justify recording the new manifest version: an old, partially-installed runtime
    can still make pip exit successfully when only one package was updated.  Use the same
    requirement parser as pip when it is available in the runtime, and report missing or
    incompatible distributions to the caller before state is committed.
    """
    versions = probed.get("packageVersions") or {}
    requirements: dict[str, Any] = dict(manifest.get("common", {}))
    for extra in manifest.get("backends", {}).get(backend, {}).get("extras", []) or []:
        name = Requirement(str(extra)).name
        if name:
            requirements[name] = extra
    torch_requirement = manifest.get("backends", {}).get(backend, {}).get("torch", {}).get("requirement")
    if torch_requirement:
        requirements["torch"] = torch_requirement
        versions = {**versions, "torch": probed.get("torchVersion")}
    failures: list[str] = []
    for name, requirement in requirements.items():
        actual = versions.get(name)
        if not actual:
            failures.append(f"{name} is not installed")
            continue
        try:
            parsed = wb._runtime_requirement(name, requirement)
            if parsed.specifier and not parsed.specifier.contains(Version(str(actual)), prereleases=True):
                failures.append(f"{name}=={actual} does not satisfy {parsed.specifier}")
        except Exception as exc:
            failures.append(f"{name}: invalid requirement {requirement!r}: {exc}")
    return not failures, failures


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


def cmd_runtime_core_versions(payload: dict[str, Any]) -> int:
    channel = "prerelease" if payload.get("channel") == "prerelease" else "stable"
    current = payload.get("versions") or {}
    packages: dict[str, dict[str, Any]] = {}
    for name in ("pymss", "pymss-core"):
        try:
            latest = wb._latest_pypi_version(name, channel)
            installed = current.get(name)
            packages[name] = {
                "latestVersion": latest, "currentVersion": installed,
                "updateAvailable": bool(latest and installed and Version(latest) > Version(installed)),
            }
        except Exception as exc:
            packages[name] = {"latestVersion": None, "error": str(exc)}
    wb._emit("runtime_core_versions", {"channel": channel, "packages": packages})
    return 0


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


def _runtime_command_path(path: Path) -> Path:
    """Use a regular Win32 path for short runtime commands, retaining long-path support."""
    normal = wb._normal_runtime_path(path)
    if os.name == "nt" and len(normal) < 240:
        return Path(normal)
    return path


def _create_runtime_venv(env_dir: Path) -> None:
    import venv

    venv.EnvBuilder(with_pip=True, clear=True, symlinks=(os.name != "nt")).create(wb._normal_runtime_path(env_dir))


def _repair_runtime_venv_config(env_dir: Path) -> bool:
    cfg = env_dir / "pyvenv.cfg"
    bootstrap = wb._bootstrap_python_path()
    if not cfg.is_file() or not bootstrap.is_file():
        return False
    content = "\n".join([
        f"home = {wb._normal_runtime_path(bootstrap.parent)}",
        "include-system-site-packages = false",
        f"executable = {wb._normal_runtime_path(bootstrap)}",
        f"command = {wb._normal_runtime_path(bootstrap)} -m venv {wb._normal_runtime_path(env_dir)}",
        "",
    ])
    if cfg.read_text(encoding="utf-8", errors="replace") != content:
        wb._atomic_write_text(cfg, content)
        return True
    return False


def _make_posix_venv_relocatable(env_dir: Path) -> bool:
    if os.name == "nt":
        return False
    bin_dir = env_dir / "bin"
    bootstrap = wb._bootstrap_python_path().resolve()
    if not bin_dir.is_dir() or not bootstrap.is_file():
        return False
    # Resolve both ends: on macOS /var aliases /private/var, and lexical
    # traversal from the alias can otherwise leave a dangling interpreter link.
    relative = os.path.relpath(bootstrap, bin_dir.resolve())
    names = {"python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}"}
    changed = False
    for name in names:
        path = bin_dir / name
        try:
            if path.is_symlink() and path.resolve() == bootstrap:
                continue
        except OSError:
            pass
        temporary = bin_dir / f".{name}.{os.getpid()}.pymss-tmp"
        try:
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(relative)
            os.replace(temporary, path)
            changed = True
        finally:
            temporary.unlink(missing_ok=True)
    return changed



__all__ = ['_default_runtime_envs_dir', '_manifest', '_pin_manifest_requirement', '_manifest_pinned_version', '_runtime_requirement', '_supported_backend', '_emit', '_serialized_runtime_command', '_module_available', '_atomic_write_json', '_atomic_write_text', '_env_dir', '_env_state_path', '_env_log_path', '_bootstrap_python_path', '_resolve_pypi_mirror', '_backend_extra_names', '_state_matches_backend', '_vendor_from_pci_id', '_windows_gpu_vendors', '_linux_gpu_vendors', '_dir_size_bytes', '_same_path', '_latest_pypi_version', '_runtime_env_dir_for_python', '_path_is_within', '_bundled_bootstrap_python', '_runtime_overlay_root', '_read_json_object', '_core_update_identity', '_load_core_update_plan', '_overlay_requirement_is_satisfied', '_overlay_requirements', '_is_base_accelerator_package', '_resolved_overlay_requirements', '_manifest_requirement_names', '_runtime_manifest_status', '_manifest_versions_are_satisfied', '_runtime_probe_failure', '_torch_probe_failure', 'cmd_runtime_core_versions', '_normal_runtime_path', '_runtime_command_path', '_create_runtime_venv', '_repair_runtime_venv_config', '_make_posix_venv_relocatable']
