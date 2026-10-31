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
        resolved_overlay = wb._overlay_path_from_state({"overlayPath": str(overlay)})
        if resolved_overlay is None:
            state.pop("overlayPath", None)
        else:
            state["overlayPath"] = str(resolved_overlay)
    state.pop("source", None)
    return state


def _read_runtime_state() -> dict[str, Any] | None:
    state = wb._resolve_runtime_state_from(wb.ACTIVE_RUNTIME_FILE)
    if state and state.get("pythonPath"):
        supported = wb._supported_backend(str(state.get("backend") or ""))
        if supported:
            # Rust may persist the active interpreter using the Windows extended-path
            # prefix (``\\?\\``), while the configured runtime root can use a regular
            # drive path.  Normalize the interpreter before deriving its environment
            # directory so an otherwise valid active environment is not discarded.
            state["pythonPath"] = str(wb._runtime_command_path(Path(str(state["pythonPath"]))))
            try:
                env_dir = wb._runtime_env_dir_for_python(Path(str(state["pythonPath"])))
            except (OSError, ValueError):
                env_dir = None
            python_path = Path(str(state["pythonPath"]))
            debug_override = bool(state.get("debugOverride")) and wb.ALLOW_DEBUG_RUNTIME_OVERRIDE
            if env_dir and (
                (env_dir.name == supported[0] and (
                    wb._is_user_runtime_env(env_dir)
                    or wb._is_bundled_runtime_env(env_dir)
                    or debug_override
                ))
                or wb._is_bundled_bootstrap_python(python_path)
            ):
                if wb._is_bundled_runtime_env(env_dir) or wb._is_bundled_bootstrap_python(python_path):
                    state["source"] = "bundled"
                elif debug_override:
                    state["source"] = "debug"
                return state
    if wb.BUNDLED_RUNTIME_ENVS_DIR:
        bundled = wb._resolve_runtime_state_from(wb.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json")
        if bundled and bundled.get("pythonPath"):
            bundled["source"] = "bundled"
            return bundled
    return None


def _write_runtime_state(state: dict[str, Any]) -> None:
    wb.ACTIVE_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    wb._atomic_write_json(wb.ACTIVE_RUNTIME_FILE, state)


def _env_python_path(backend: str) -> Path:
    env_dir = wb._env_dir(backend)
    # ``runtime_envs_dir`` can originate from Rust's ``canonicalize`` and therefore carry
    # Windows' ``\\\\?\\`` extended-path prefix.  Passing that path to a venv's Python makes
    # pip build script destinations such as ``Lib\\site-packages\\..\\..\\Scripts``.  The
    # Win32 path parser rejects the ``..`` components under the extended prefix and reports
    # ``[Errno 22] Invalid argument`` while installing entry points (for example ``numba``).
    # Use a normal path for the interpreter whenever it is representable by Win32; keep the
    # extended form for unusually long paths so those installations still retain long-path
    # support.
    candidate = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return wb._runtime_command_path(candidate)


def _fresh_runtime_state_from_probe(
    backend: str, manifest: dict[str, Any], probed: dict[str, Any],
) -> dict[str, Any]:
    """Project a validated probe into a new cache record, without merging or writing state."""
    manifest_version = manifest.get("manifestVersion")
    if not wb._manifest_versions_are_satisfied(probed, manifest, backend)[0]:
        # A directory can be runnable while still carrying an older or incomplete dependency
        # set. Do not stamp a newly discovered cache as current in that case.
        manifest_version = None
    return {
        "backend": backend,
        "manifestVersion": manifest_version,
        "stateVersion": wb.ENV_STATE_VERSION,
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


def _detect_gpu_vendors() -> list[str]:
    """Best-effort GPU vendor list, never raising. macOS is deliberately absent: the backend
    there follows from the CPU architecture, which platform.machine() already answers."""
    try:
        if sys.platform == "win32":
            return wb._windows_gpu_vendors()
        if sys.platform.startswith("linux"):
            return wb._linux_gpu_vendors()
    except Exception:
        return []
    return []


def _env_size_targets(manifest: dict[str, Any]) -> dict[str, Path]:
    """Backends that own a dedicated directory on disk, in the same priority order as
    _installed_envs(). The bootstrap interpreter is deliberately absent: it is the app's own
    runtime, not a removable environment, so reporting a size for it would be misleading."""
    targets: dict[str, Path] = {}
    for backend in manifest.get("backends", {}):
        if wb._env_python_path(backend).is_file():
            targets[backend] = wb._env_dir(backend)
    if wb.BUNDLED_RUNTIME_ENVS_DIR and wb.BUNDLED_RUNTIME_ENVS_DIR.is_dir():
        for backend in manifest.get("backends", {}):
            if backend in targets:
                continue
            bundled_env = wb.BUNDLED_RUNTIME_ENVS_DIR / backend
            bundled_python = bundled_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if bundled_python.is_file():
                targets[backend] = bundled_env
    return targets


def _probe_environment(overlay_path: Path | None = None, *, python_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    if sys.platform == "darwin" and python_path and wb._is_bundled_bootstrap_python(python_path):
        env["PYTHONHOME"] = str(wb.BUNDLED_RUNTIME_ENVS_DIR.parent)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if overlay_path:
        env["PYTHONPATH"] = str(overlay_path)
    else:
        env.pop("PYTHONPATH", None)
    return env


def _verify_runtime_audio(python_path: Path, overlay_path: Path | None = None, *, core_api: bool = False) -> None:
    """Exercise lazy imports and decoding before committing an installed environment."""
    completed = subprocess.run(
        [
            str(wb._runtime_command_path(python_path)),
            str(wb._runtime_command_path(Path(wb.__file__).with_name("runtime_audio_probe.py"))),
            *(["--check-core"] if core_api else []),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**wb._probe_environment(overlay_path, python_path=python_path), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode:
        lines = completed.stderr.strip().splitlines()
        reason = lines[-1][:500] if lines else f"exit code {completed.returncode}"
        label = "Runtime compatibility verification" if core_api else "Runtime audio decoder verification"
        raise RuntimeError(f"{label} failed: {reason}")


def _probe_python_runtime(
    python_path: Path,
    extras: list[str] | None = None,
    overlay_path: Path | None = None,
) -> dict[str, Any]:
    extras = extras or []
    package_names = list(wb._manifest()["common"].keys()) + extras
    script = """
import importlib.util, json, platform
from importlib import metadata
packages = json.loads(%PACKAGES%)
mapping = json.loads(%MAPPING%)
result = {'pythonVersion': platform.python_version(), 'torchVersion': None, 'torchBackend': 'missing', 'acceleratorAvailable': False, 'packages': {}, 'packageVersions': {}, 'pymssVersion': None, 'pymssCoreVersion': None, 'pymssGraphAvailable': False}
for name in packages:
    result['packages'][name] = importlib.util.find_spec(mapping.get(name, name)) is not None
    try:
        result['packageVersions'][name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        result['packageVersions'][name] = None
result['pymssVersion'] = result['packageVersions'].get('pymss')
result['pymssCoreVersion'] = result['packageVersions'].get('pymss-core')
try:
    result['pymssGraphAvailable'] = importlib.util.find_spec('pymss.graph') is not None
except Exception:
    result['pymssGraphAvailable'] = False
if importlib.util.find_spec('torch') is not None:
    try:
        import torch
        result['torchVersion'] = torch.__version__
        result['torchBackend'] = 'rocm' if getattr(torch.version, 'hip', None) else 'cuda' if getattr(torch.version, 'cuda', None) else 'cpu'
        result['acceleratorAvailable'] = bool(torch.cuda.is_available())
    except Exception as exc:
        result['torchBackend'] = f'error:{exc}'
print(json.dumps(result, ensure_ascii=False))
""".replace("%PACKAGES%", repr(json.dumps(package_names))) \
   .replace("%MAPPING%", repr(json.dumps(wb.PACKAGE_IMPORT_NAMES)))
    output = subprocess.check_output(
        [str(python_path), "-c", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        stderr=subprocess.PIPE,
        env=wb._probe_environment(overlay_path, python_path=python_path),
    )
    result = json.loads(output)
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("pythonVersion"), str)
        or not isinstance(result.get("packages"), dict)
        or not isinstance(result.get("packageVersions"), dict)
        or not isinstance(result.get("torchBackend"), str)
        or not isinstance(result.get("acceleratorAvailable"), bool)
        or not isinstance(result.get("pymssGraphAvailable"), bool)
        or any(not isinstance(result["packages"].get(name), bool) for name in package_names)
        or any(
            name not in result["packageVersions"]
            or not isinstance(result["packageVersions"][name], (str, type(None)))
            for name in package_names
        )
    ):
        raise ValueError("Python runtime probe returned an incomplete response")
    return result


def _probe_python_package_versions(
    python_path: Path,
    package_names: list[str],
    overlay_path: Path | None = None,
) -> dict[str, str | None]:
    if not python_path.is_file() or not package_names:
        return {}
    script = """
import json
from importlib import metadata
names = json.loads(%NAMES%)
result = {}
for name in names:
    try:
        result[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        result[name] = None
print(json.dumps(result, ensure_ascii=False))
""".replace("%NAMES%", repr(json.dumps(package_names)))
    try:
        output = subprocess.check_output(
            [str(python_path), "-c", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            env=wb._probe_environment(overlay_path, python_path=python_path),
        )
        data = json.loads(output.strip() or "{}")
        return {name: (str(data.get(name)) if data.get(name) else None) for name in package_names}
    except Exception:
        return {}


def _is_user_runtime_env(env_dir: Path) -> bool:
    # ``canonicalize`` on Windows can add ``\\?\\`` to one side only.  Compare both
    # spellings so a short-path command path still belongs to the configured runtime root
    # and is not rejected as an outside interpreter.  The original spelling is retained as
    # the first candidate so genuinely long paths keep their extended-path support.
    return wb._path_is_within(env_dir, wb.RUNTIME_ENVS_DIR)


def _is_bundled_runtime_env(env_dir: Path) -> bool:
    if not wb.BUNDLED_RUNTIME_ENVS_DIR:
        return False
    return wb._path_is_within(env_dir, wb.BUNDLED_RUNTIME_ENVS_DIR)


def _is_bundled_bootstrap_python(path: Path) -> bool:
    bundled = wb._bundled_bootstrap_python()
    try:
        return bool(
            bundled
            and wb._same_path(path.parent.resolve(), bundled.parent.resolve())
            and wb._same_path(path.resolve(), bundled.resolve())
        )
    except OSError:
        return False


def _runtime_overlay_selection_path(backend: str, python_path: Path | None = None) -> Path:
    suffix = ""
    if python_path is not None:
        identity = os.path.normcase(wb._normal_runtime_path(wb._runtime_command_path(python_path)))
        suffix = "-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    return wb._runtime_overlay_root() / backend / f"active-overlay{suffix}.json"


def _recover_overlay_activation() -> None:
    journal = wb.RUNTIME_ENVS_DIR / ".overlay-activation.json"
    transaction = wb._read_json_object(journal)
    if not transaction:
        return
    backend = str(transaction.get("backend") or "")
    if not wb._supported_backend(backend):
        raise ValueError("Invalid runtime activation transaction")
    active = wb._read_json_object(wb.ACTIVE_RUNTIME_FILE) or {}
    committed = active.get("activationId") == transaction["nextState"]["activationId"]
    selection = transaction["nextState"] if committed else transaction.get("previousSelection")
    path = wb._runtime_overlay_selection_path(backend)
    if selection is None:
        path.unlink(missing_ok=True)
    else:
        wb._atomic_write_json(path, selection)
    journal.unlink()


def _commit_runtime_overlay(state: dict[str, Any]) -> None:
    journal = wb.RUNTIME_ENVS_DIR / ".overlay-activation.json"
    state["activationId"] = uuid.uuid4().hex
    selection = wb._runtime_overlay_selection_path(str(state["backend"]))
    previous_selection = wb._read_json_object(selection)
    if previous_selection and previous_selection.get("pythonPath") and state.get("pythonPath") and not wb._same_path(previous_selection["pythonPath"], state["pythonPath"]):
        # Keep the latest verified selection for the other installation source
        # before the backend-wide index starts referring to the active source.
        wb._atomic_write_json(wb._runtime_overlay_selection_path(state["backend"], Path(previous_selection["pythonPath"])), previous_selection)
    wb._atomic_write_json(journal, {
        "backend": state["backend"], "nextState": state,
        "previousSelection": previous_selection,
    })
    try:
        wb._atomic_write_json(selection, state)
        # This single atomic replacement is the commit point. Recovery completes
        # or undoes the secondary index according to this authoritative record.
        wb._write_runtime_state(state)
    except Exception:
        wb._recover_overlay_activation()
        if (wb._read_json_object(wb.ACTIVE_RUNTIME_FILE) or {}).get("activationId") != state["activationId"]:
            raise
    else:
        try:
            wb._recover_overlay_activation()
        except OSError:
            # The authoritative state is committed. The durable journal repairs
            # the secondary index on the next management operation.
            pass


def _cache_core_runtime_state(env_dir: Path, state_path: Path, python_path: Path, state: dict[str, Any], append_log: Callable[[str, str], None]) -> None:
    if wb._is_bundled_runtime_env(env_dir) or wb._is_bundled_bootstrap_python(python_path):
        return
    cached = {key: value for key, value in state.items() if key not in {"pythonPath", "logPath", "activatedAt", "source"}}
    try:
        wb._atomic_write_json(state_path, cached)
    except OSError as error:
        append_log("warning", f"Runtime activated; environment cache could not be updated: {error}")


def _core_rollback_available(state: dict[str, Any] | None, manifest: dict[str, Any]) -> bool:
    previous = (state or {}).get("previousRuntime")
    return bool(
        isinstance(previous, dict)
        and wb._same_path(previous.get("pythonPath"), state.get("pythonPath"))
        and (not previous.get("overlayPath") or wb._overlay_path_from_state(previous) is not None)
        and previous.get("pymssGraphAvailable") is not False
        and wb._runtime_health(str(state.get("backend") or ""), previous, manifest) == "ready"
    )


def _save_core_update_plan(plan: dict[str, Any], task_id: str) -> None:
    plan.update({"id": uuid.uuid4().hex, "createdAt": time.time()})
    root = wb._runtime_overlay_root() / ".plans"
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob("*.json"):
        if time.time() - path.stat().st_mtime > 15 * 60:
            path.unlink(missing_ok=True)
    wb._atomic_write_json(root / f"{plan['id']}.json", plan)
    wb._emit("runtime_core_update_plan", {
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
    roots = [wb._runtime_overlay_root()]
    if wb.BUNDLED_RUNTIME_ENVS_DIR:
        roots.append(wb.BUNDLED_RUNTIME_ENVS_DIR / ".overlays")
    return resolved if resolved.is_dir() and any(wb._path_is_within(resolved, root) for root in roots) else None


def _registered_runtime_overlay(backend: str, python_path: Path) -> dict[str, Any] | None:
    for path in (wb._runtime_overlay_selection_path(backend), wb._runtime_overlay_selection_path(backend, python_path)):
        state = wb._read_json_object(path)
        if not state or not wb._same_path(state.get("pythonPath"), python_path):
            continue
        overlay = wb._overlay_path_from_state(state)
        # A matching record without an overlay is an explicit restoration of
        # the base; do not revive an older per-source cache behind it.
        return {**state, "overlayPath": str(overlay)} if overlay else None
    return None


def _cleanup_runtime_overlays(backend: str, keep: set[Path]) -> None:
    root = wb._runtime_overlay_root() / backend
    if not root.is_dir():
        return
    try:
        kept = {path.resolve() for path in keep}
        current = wb._read_json_object(wb._runtime_overlay_selection_path(backend)) or {}
        for path in root.glob("active-overlay*.json"):
            selected = wb._read_json_object(path) or {}
            if path.name != "active-overlay.json" and wb._same_path(selected.get("pythonPath"), current.get("pythonPath")):
                continue
            for record in (selected, selected.get("previousRuntime") or {}):
                if not isinstance(record, dict):
                    continue
                overlay = wb._overlay_path_from_state(record)
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
    required = wb._manifest_requirement_names(manifest, backend)
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
    required = wb._manifest_requirement_names(manifest, backend)
    if any(packages.get(name) is False for name in required):
        return "broken"
    expected_torch_backend = "cpu" if backend == "mlx" else backend
    recorded_torch_backend = str(state.get("torchBackend") or "")
    if recorded_torch_backend and recorded_torch_backend != expected_torch_backend:
        return "broken"
    if all(name in packages for name in required) and all(packages.get(name) is True for name in required):
        versions = state.get("packageVersions")
        if isinstance(versions, dict) and versions:
            manifest_ok, _failures = wb._manifest_versions_are_satisfied(state, manifest, backend)
            if not manifest_ok:
                return "degraded"
        return "ready"
    return "unknown"


def _runtime_python_works(python_path: Path) -> bool:
    try:
        return subprocess.run(
            [str(python_path), "-c", "import sys; print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=20,
            env=wb._probe_environment(python_path=python_path),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _runtime_pip_works(python_path: Path) -> bool:
    try:
        return subprocess.run(
            [str(python_path), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=wb._probe_environment(python_path=python_path),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False



__all__ = ['_resolve_runtime_state_from', '_read_runtime_state', '_write_runtime_state', '_env_python_path', '_fresh_runtime_state_from_probe', '_detect_gpu_vendors', '_env_size_targets', '_probe_environment', '_verify_runtime_audio', '_probe_python_runtime', '_probe_python_package_versions', '_is_user_runtime_env', '_is_bundled_runtime_env', '_is_bundled_bootstrap_python', '_runtime_overlay_selection_path', '_recover_overlay_activation', '_commit_runtime_overlay', '_cache_core_runtime_state', '_core_rollback_available', '_save_core_update_plan', '_overlay_path_from_state', '_registered_runtime_overlay', '_cleanup_runtime_overlays', '_runtime_probe_is_ready', '_runtime_health', '_runtime_python_works', '_runtime_pip_works']
