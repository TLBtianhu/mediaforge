from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
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

from core.bootstrap import base
def _supported_backend(backend: str, manifest: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
    normalized = str(backend or "").strip().lower()
    if not normalized or "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        return None
    manifest = manifest or base._manifest()
    spec = manifest.get("backends", {}).get(normalized)
    return (normalized, spec) if isinstance(spec, dict) else None


def _resolve_runtime_state_from(file: Path) -> dict[str, Any] | None:
    """Read active-runtime.json and resolve relative pythonPath against its parent dir."""
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    python_path = state.get("pythonPath")
    if python_path:
        if not isinstance(python_path, str):
            return None
        p = Path(python_path)
        if not p.is_absolute():
            resolved = file.parent / p
            if resolved.is_file():
                # Keep the executable link: POSIX venvs locate pyvenv.cfg through argv[0].
                state["pythonPath"] = str(resolved.parent.resolve() / resolved.name)
            else:
                return None
        elif not p.is_file():
            return None
    overlay_path = state.get("overlayPath")
    if overlay_path:
        overlay = Path(str(overlay_path))
        if not overlay.is_absolute():
            overlay = file.parent / overlay
        resolved_overlay = _overlay_path_from_state({"overlayPath": str(overlay)})
        if resolved_overlay is None:
            state.pop("overlayPath", None)
        else:
            state["overlayPath"] = str(resolved_overlay)
    state.pop("source", None)
    return state


def _write_runtime_state(state: dict[str, Any]) -> None:
    base.ACTIVE_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(base.ACTIVE_RUNTIME_FILE, state)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    base._atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _env_python_path(backend: str) -> Path:
    env_dir = base._env_dir(backend)
    # ``runtime_envs_dir`` can originate from Rust's ``canonicalize`` and therefore carry
    # Windows' ``\\\\?\\`` extended-path prefix.  Passing that path to a venv's Python makes
    # pip build script destinations such as ``Lib\\site-packages\\..\\..\\Scripts``.  The
    # Win32 path parser rejects the ``..`` components under the extended prefix and reports
    # ``[Errno 22] Invalid argument`` while installing entry points (for example ``numba``).
    # Use a normal path for the interpreter whenever it is representable by Win32; keep the
    # extended form for unusually long paths so those installations still retain long-path
    # support.
    candidate = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return _runtime_command_path(candidate)


def _env_state_path(backend: str) -> Path:
    return base._env_dir(backend) / "pymss-runtime-state.json"


def _env_log_path(backend: str) -> Path:
    return base._env_dir(backend) / "pymss-runtime-install.log"


def _fresh_runtime_state_from_probe(
    backend: str, manifest: dict[str, Any], probed: dict[str, Any],
) -> dict[str, Any]:
    """Project a validated probe into a new cache record, without merging or writing state."""
    manifest_version = manifest.get("manifestVersion")
    if not _manifest_versions_are_satisfied(probed, manifest, backend)[0]:
        # A directory can be runnable while still carrying an older or incomplete dependency
        # set. Do not stamp a newly discovered cache as current in that case.
        manifest_version = None
    return {
        "backend": backend,
        "manifestVersion": manifest_version,
        "stateVersion": base.ENV_STATE_VERSION,
        "pythonVersion": probed.get("pythonVersion"),
        "torchVersion": probed.get("torchVersion"),
        "torchBackend": probed.get("torchBackend"),
        "acceleratorAvailable": bool(probed.get("acceleratorAvailable")),
        "packages": probed.get("packages"),
        "packageVersions": probed.get("packageVersions"),
        "pymssVersion": probed.get("pymssVersion"),
        "pymssCoreVersion": probed.get("pymssCoreVersion"),
        "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
    }


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
        vendor = base._vendor_from_pci_id(match.group(1)) if match else None
        if vendor and vendor not in vendors:
            vendors.append(vendor)
    return vendors


def _linux_gpu_vendors(drm_root: Path | None = None) -> list[str]:
    vendors: list[str] = []
    for path in sorted((drm_root or base.LINUX_DRM_ROOT).glob("card*/device/vendor")):
        try:
            vendor = base._vendor_from_pci_id(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if vendor and vendor not in vendors:
            vendors.append(vendor)
    return vendors


def _detect_gpu_vendors() -> list[str]:
    """Best-effort GPU vendor list, never raising. macOS is deliberately absent: the backend
    there follows from the CPU architecture, which platform.machine() already answers."""
    try:
        if sys.platform == "win32":
            return _windows_gpu_vendors()
        if sys.platform.startswith("linux"):
            return _linux_gpu_vendors()
    except Exception:
        return []
    return []


def _env_size_targets(manifest: dict[str, Any]) -> dict[str, Path]:
    """Backends that own a dedicated directory on disk, in the same priority order as
    _installed_envs(). The bootstrap interpreter is deliberately absent: it is the app's own
    runtime, not a removable environment, so reporting a size for it would be misleading."""
    targets: dict[str, Path] = {}
    for backend in manifest.get("backends", {}):
        if _env_python_path(backend).is_file():
            targets[backend] = base._env_dir(backend)
    if base.BUNDLED_RUNTIME_ENVS_DIR and base.BUNDLED_RUNTIME_ENVS_DIR.is_dir():
        for backend in manifest.get("backends", {}):
            if backend in targets:
                continue
            bundled_env = base.BUNDLED_RUNTIME_ENVS_DIR / backend
            bundled_python = bundled_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if bundled_python.is_file():
                targets[backend] = bundled_env
    return targets


def _probe_environment(overlay_path: Path | None = None, *, python_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    if sys.platform == "darwin" and python_path and _is_bundled_bootstrap_python(python_path):
        env["PYTHONHOME"] = str(base.BUNDLED_RUNTIME_ENVS_DIR.parent)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if overlay_path:
        env["PYTHONPATH"] = str(overlay_path)
    else:
        env.pop("PYTHONPATH", None)
    return env


def _same_path(left: Any, right: Any) -> bool:
    try:
        left_path = base._normal_runtime_path(Path(str(left))) if os.name == "nt" else str(left)
        right_path = base._normal_runtime_path(Path(str(right))) if os.name == "nt" else str(right)
        return os.path.normcase(os.path.normpath(left_path)) == os.path.normcase(os.path.normpath(right_path))
    except Exception:
        return False


def _path_is_within(path: Path, root: Path) -> bool:
    """Check containment while accepting mixed regular/extended Windows path spellings."""
    candidates = [(path, root)]
    normalized_path = Path(base._normal_runtime_path(path))
    normalized_root = Path(base._normal_runtime_path(root))
    if normalized_path != path or normalized_root != root:
        candidates.append((normalized_path, normalized_root))
    for child, parent in candidates:
        try:
            if child.resolve().is_relative_to(parent.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _is_user_runtime_env(env_dir: Path) -> bool:
    # ``canonicalize`` on Windows can add ``\\?\\`` to one side only.  Compare both
    # spellings so a short-path command path still belongs to the configured runtime root
    # and is not rejected as an outside interpreter.  The original spelling is retained as
    # the first candidate so genuinely long paths keep their extended-path support.
    return _path_is_within(env_dir, base.RUNTIME_ENVS_DIR)


def _is_bundled_runtime_env(env_dir: Path) -> bool:
    if not base.BUNDLED_RUNTIME_ENVS_DIR:
        return False
    return _path_is_within(env_dir, base.BUNDLED_RUNTIME_ENVS_DIR)


def _is_bundled_bootstrap_python(path: Path) -> bool:
    bundled = base._bundled_bootstrap_python()
    try:
        return bool(
            bundled
            and _same_path(path.parent.resolve(), bundled.parent.resolve())
            and _same_path(path.resolve(), bundled.resolve())
        )
    except OSError:
        return False


def _runtime_overlay_selection_path(backend: str, python_path: Path | None = None) -> Path:
    suffix = ""
    if python_path is not None:
        identity = os.path.normcase(base._normal_runtime_path(_runtime_command_path(python_path)))
        suffix = "-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return base._runtime_overlay_root() / backend / f"active-overlay{suffix}.json"


def _recover_overlay_activation() -> None:
    journal = base.RUNTIME_ENVS_DIR / ".overlay-activation.json"
    transaction = base._read_json_object(journal)
    if not transaction:
        return
    backend = str(transaction.get("backend") or "")
    if not _supported_backend(backend):
        raise ValueError("Invalid runtime activation transaction")
    active = base._read_json_object(base.ACTIVE_RUNTIME_FILE) or {}
    committed = active.get("activationId") == transaction["nextState"]["activationId"]
    selection = transaction["nextState"] if committed else transaction.get("previousSelection")
    path = _runtime_overlay_selection_path(backend)
    if selection is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_json(path, selection)
    journal.unlink()


def _cache_core_runtime_state(env_dir: Path, state_path: Path, python_path: Path, state: dict[str, Any], append_log: Callable[[str, str], None]) -> None:
    if _is_bundled_runtime_env(env_dir) or _is_bundled_bootstrap_python(python_path):
        return
    cached = {key: value for key, value in state.items() if key not in {"pythonPath", "logPath", "activatedAt", "source"}}
    try:
        _atomic_write_json(state_path, cached)
    except OSError as error:
        append_log("warning", f"Runtime activated; environment cache could not be updated: {error}")


def _core_update_identity(state: dict[str, Any], versions: dict[str, Any], manifest: dict[str, Any]) -> str:
    identity = {
        "backend": state.get("backend"),
        "python": os.path.normcase(base._normal_runtime_path(Path(str(state.get("pythonPath") or "")))),
        "overlay": os.path.normcase(base._normal_runtime_path(Path(str(state.get("overlayPath") or "")))),
        "versions": versions, "manifest": manifest,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _core_rollback_available(state: dict[str, Any] | None, manifest: dict[str, Any]) -> bool:
    previous = (state or {}).get("previousRuntime")
    return bool(
        isinstance(previous, dict)
        and _same_path(previous.get("pythonPath"), state.get("pythonPath"))
        and (not previous.get("overlayPath") or _overlay_path_from_state(previous) is not None)
        and previous.get("pymssGraphAvailable") is not False
        and _runtime_health(str(state.get("backend") or ""), previous, manifest) == "ready"
    )


def _load_core_update_plan(plan_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
        raise ValueError("A confirmed runtime update plan is required")
    plan = base._read_json_object(base._runtime_overlay_root() / ".plans" / f"{plan_id}.json")
    if not plan or time.time() - plan.get("createdAt", 0) > 15 * 60:
        raise ValueError("The runtime update plan expired. Check for updates again.")
    return plan


def _save_core_update_plan(plan: dict[str, Any], task_id: str) -> None:
    plan.update({"id": uuid.uuid4().hex, "createdAt": time.time()})
    root = base._runtime_overlay_root() / ".plans"
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob("*.json"):
        if time.time() - path.stat().st_mtime > 15 * 60:
            path.unlink(missing_ok=True)
    _atomic_write_json(root / f"{plan['id']}.json", plan)
    base._emit("runtime_core_update_plan", {
        key: plan[key] for key in (
            "id", "backend", "pythonPath", "mode", "channel", "mirror",
            "currentVersions", "targetVersions", "dependencyCount",
        )
    }, task_id)


def _overlay_path_from_state(state: dict[str, Any] | None) -> Path | None:
    value = str((state or {}).get("overlayPath") or "").strip()
    if not value:
        return None
    path = Path(value)
    try:
        resolved = path.resolve()
    except OSError:
        return None
    # Match Rust's allowed roots when reading an installation created before
    # overlays moved to user storage. New generations still use the user root.
    roots = [base._runtime_overlay_root()]
    if base.BUNDLED_RUNTIME_ENVS_DIR:
        roots.append(base.BUNDLED_RUNTIME_ENVS_DIR / ".overlays")
    return resolved if resolved.is_dir() and any(_path_is_within(resolved, root) for root in roots) else None


def _registered_runtime_overlay(backend: str, python_path: Path) -> dict[str, Any] | None:
    for path in (_runtime_overlay_selection_path(backend), _runtime_overlay_selection_path(backend, python_path)):
        state = base._read_json_object(path)
        if not state or not _same_path(state.get("pythonPath"), python_path):
            continue
        overlay = _overlay_path_from_state(state)
        # A matching record without an overlay is an explicit restoration of
        # the base; do not revive an older per-source cache behind it.
        return {**state, "overlayPath": str(overlay)} if overlay else None
    return None


def _overlay_requirements(
    manifest: dict[str, Any],
    backend: str,
    base_versions: dict[str, str | None],
    target_pymss_version: str,
    target_pymss_core_version: str,
) -> list[str]:
    requirements = [
        base._pin_manifest_requirement(manifest.get("common", {}).get("pymss"), target_pymss_version),
        f"pymss-core=={target_pymss_core_version}",
    ]
    for name, value in manifest.get("common", {}).items():
        if name in {"pymss", "pymss-core"}:
            continue
        requirement = str(value)
        if not base._overlay_requirement_is_satisfied(requirement, base_versions.get(name)):
            requirements.append(requirement)
    for value in manifest.get("backends", {}).get(backend, {}).get("extras", []) or []:
        requirement = str(value)
        try:
            name = Requirement(requirement).name
        except Exception:
            name = requirement
        if not base._overlay_requirement_is_satisfied(requirement, base_versions.get(name)):
            requirements.append(requirement)
    return requirements


def _resolved_overlay_requirements(requirements: list[str], report: dict[str, Any]) -> list[str]:
    if report.get("version") != "1" or not isinstance(report.get("install"), list):
        raise RuntimeError("The package resolver returned an unsupported installation report")
    resolved = {canonicalize_name(Requirement(value).name): value for value in requirements}
    for item in report["install"]:
        metadata = item["metadata"]
        name = canonicalize_name(metadata["name"])
        if base._is_base_accelerator_package(name):
            raise RuntimeError(f"The core update requires changing the base accelerator package {name}; the current runtime was preserved")
        version = str(Version(metadata["version"]))
        resolved[name] = base._pin_manifest_requirement(resolved.get(name, name), version)
    return list(resolved.values())


def _cleanup_runtime_overlays(backend: str, keep: set[Path]) -> None:
    root = base._runtime_overlay_root() / backend
    if not root.is_dir():
        return
    try:
        kept = {path.resolve() for path in keep}
        current = base._read_json_object(_runtime_overlay_selection_path(backend)) or {}
        for path in root.glob("active-overlay*.json"):
            selected = base._read_json_object(path) or {}
            if path.name != "active-overlay.json" and _same_path(selected.get("pythonPath"), current.get("pythonPath")):
                continue
            for record in (selected, selected.get("previousRuntime") or {}):
                if not isinstance(record, dict):
                    continue
                overlay = _overlay_path_from_state(record)
                if overlay:
                    kept.add(overlay.parent)
        candidates = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in kept:
            continue
        shutil.rmtree(candidate, ignore_errors=True)


def _runtime_probe_is_ready(backend: str, probed: dict[str, Any], manifest: dict[str, Any]) -> bool:
    packages = probed.get("packages") or {}
    required = _manifest_requirement_names(manifest, backend)
    if not all(packages.get(name) is True for name in required):
        return False
    expected_torch_backend = "cpu" if backend == "mlx" else backend
    if probed.get("torchBackend") != expected_torch_backend:
        return False
    return True


def _runtime_health(backend: str, state: dict[str, Any] | None, manifest: dict[str, Any]) -> str:
    """Classify a cached environment without spawning another interpreter."""
    if not isinstance(state, dict):
        return "unknown"
    packages = state.get("packages")
    if not isinstance(packages, dict):
        return "unknown"
    required = _manifest_requirement_names(manifest, backend)
    if any(packages.get(name) is False for name in required):
        return "broken"
    expected_torch_backend = "cpu" if backend == "mlx" else backend
    recorded_torch_backend = str(state.get("torchBackend") or "")
    if recorded_torch_backend and recorded_torch_backend != expected_torch_backend:
        return "broken"
    if all(name in packages for name in required) and all(packages.get(name) is True for name in required):
        versions = state.get("packageVersions")
        if isinstance(versions, dict) and versions:
            manifest_ok, _failures = _manifest_versions_are_satisfied(state, manifest, backend)
            if not manifest_ok:
                return "degraded"
        return "ready"
    return "unknown"


def _manifest_requirement_names(manifest: dict[str, Any], backend: str) -> list[str]:
    """Return distribution names that the selected backend must provide."""
    return [*manifest.get("common", {}), *base._backend_extra_names(manifest, backend)]


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
            parsed = base._runtime_requirement(name, requirement)
            if parsed.specifier and not parsed.specifier.contains(Version(str(actual)), prereleases=True):
                failures.append(f"{name}=={actual} does not satisfy {parsed.specifier}")
        except Exception as exc:
            failures.append(f"{name}: invalid requirement {requirement!r}: {exc}")
    return not failures, failures


def cmd_runtime_core_versions(payload: dict[str, Any]) -> int:
    channel = "prerelease" if payload.get("channel") == "prerelease" else "stable"
    current = payload.get("versions") or {}
    packages: dict[str, dict[str, Any]] = {}
    for name in ("pymss", "pymss-core"):
        try:
            latest = base._latest_pypi_version(name, channel)
            installed = current.get(name)
            packages[name] = {
                "latestVersion": latest, "currentVersion": installed,
                "updateAvailable": bool(latest and installed and Version(latest) > Version(installed)),
            }
        except Exception as exc:
            packages[name] = {"latestVersion": None, "error": str(exc)}
    base._emit("runtime_core_versions", {"channel": channel, "packages": packages})
    return 0


def _runtime_command_path(path: Path) -> Path:
    """Use a regular Win32 path for short runtime commands, retaining long-path support."""
    normal = base._normal_runtime_path(path)
    if os.name == "nt" and len(normal) < 240:
        return Path(normal)
    return path


def _create_runtime_venv(env_dir: Path) -> None:
    import venv

    venv.EnvBuilder(with_pip=True, clear=True, symlinks=(os.name != "nt")).create(base._normal_runtime_path(env_dir))


def _repair_runtime_venv_config(env_dir: Path) -> bool:
    cfg = env_dir / "pyvenv.cfg"
    bootstrap = base._bootstrap_python_path()
    if not cfg.is_file() or not bootstrap.is_file():
        return False
    content = "\n".join([
        f"home = {base._normal_runtime_path(bootstrap.parent)}",
        "include-system-site-packages = false",
        f"executable = {base._normal_runtime_path(bootstrap)}",
        f"command = {base._normal_runtime_path(bootstrap)} -m venv {base._normal_runtime_path(env_dir)}",
        "",
    ])
    if cfg.read_text(encoding="utf-8", errors="replace") != content:
        base._atomic_write_text(cfg, content)
        return True
    return False


def _make_posix_venv_relocatable(env_dir: Path) -> bool:
    if os.name == "nt":
        return False
    bin_dir = env_dir / "bin"
    bootstrap = base._bootstrap_python_path().resolve()
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



__all__ = ['_supported_backend', '_resolve_runtime_state_from', '_write_runtime_state', '_atomic_write_json', '_env_python_path', '_env_state_path', '_env_log_path', '_fresh_runtime_state_from_probe', '_windows_gpu_vendors', '_linux_gpu_vendors', '_detect_gpu_vendors', '_env_size_targets', '_probe_environment', '_same_path', '_path_is_within', '_is_user_runtime_env', '_is_bundled_runtime_env', '_is_bundled_bootstrap_python', '_runtime_overlay_selection_path', '_recover_overlay_activation', '_cache_core_runtime_state', '_core_update_identity', '_core_rollback_available', '_load_core_update_plan', '_save_core_update_plan', '_overlay_path_from_state', '_registered_runtime_overlay', '_overlay_requirements', '_resolved_overlay_requirements', '_cleanup_runtime_overlays', '_runtime_probe_is_ready', '_runtime_health', '_manifest_requirement_names', '_manifest_versions_are_satisfied', 'cmd_runtime_core_versions', '_runtime_command_path', '_create_runtime_venv', '_repair_runtime_venv_config', '_make_posix_venv_relocatable']
