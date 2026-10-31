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

from core.bootstrap import base, primitives
def _read_runtime_state() -> dict[str, Any] | None:
    state = primitives._resolve_runtime_state_from(base.ACTIVE_RUNTIME_FILE)
    if state and state.get("pythonPath"):
        supported = primitives._supported_backend(str(state.get("backend") or ""))
        if supported:
            # Rust may persist the active interpreter using the Windows extended-path
            # prefix (``\\?\\``), while the configured runtime root can use a regular
            # drive path.  Normalize the interpreter before deriving its environment
            # directory so an otherwise valid active environment is not discarded.
            state["pythonPath"] = str(primitives._runtime_command_path(Path(str(state["pythonPath"]))))
            try:
                env_dir = base._runtime_env_dir_for_python(Path(str(state["pythonPath"])))
            except (OSError, ValueError):
                env_dir = None
            python_path = Path(str(state["pythonPath"]))
            debug_override = bool(state.get("debugOverride")) and base.ALLOW_DEBUG_RUNTIME_OVERRIDE
            if env_dir and (
                (env_dir.name == supported[0] and (
                    primitives._is_user_runtime_env(env_dir)
                    or primitives._is_bundled_runtime_env(env_dir)
                    or debug_override
                ))
                or primitives._is_bundled_bootstrap_python(python_path)
            ):
                if primitives._is_bundled_runtime_env(env_dir) or primitives._is_bundled_bootstrap_python(python_path):
                    state["source"] = "bundled"
                elif debug_override:
                    state["source"] = "debug"
                return state
    if base.BUNDLED_RUNTIME_ENVS_DIR:
        bundled = primitives._resolve_runtime_state_from(base.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json")
        if bundled and bundled.get("pythonPath"):
            bundled["source"] = "bundled"
            return bundled
    return None


def _recover_reinstall_backups() -> None:
    import shutil

    if not base.RUNTIME_ENVS_DIR.is_dir():
        return
    for backup in base.RUNTIME_ENVS_DIR.glob(".*.reinstalling"):
        backend = backup.name.removeprefix(".").removesuffix(".reinstalling")
        if not primitives._supported_backend(backend):
            continue
        target = base._env_dir(backend)
        try:
            state = _read_installed_env_state(backend)
            python_path = primitives._env_python_path(backend)
            if state and python_path.is_file():
                shutil.rmtree(backup)
                continue
            if target.exists():
                shutil.rmtree(target)
            backup.rename(target)
        except OSError:
            continue


def _recover_core_update_transactions() -> None:
    """Recover directory swaps left by releases with transactional core updates.

    New updates run in place, but an older process may have stopped between its two renames.
    Never leave a hidden backup to be mistaken for an installed environment:
    validate the visible directory first, otherwise restore the original copy.
    """
    if not base.RUNTIME_ENVS_DIR.is_dir():
        return
    for backup in base.RUNTIME_ENVS_DIR.glob(".*.core-backup"):
        backend = backup.name.removeprefix(".").removesuffix(".core-backup")
        if not primitives._supported_backend(backend):
            continue
        target = base._env_dir(backend)
        staging = base.RUNTIME_ENVS_DIR / f".{backend}.core-updating"
        try:
            target_is_valid = False
            if target.is_dir():
                target_python = primitives._env_python_path(backend)
                if target_python.is_file():
                    try:
                        probed = _probe_python_runtime(target_python, base._backend_extra_names(base._manifest(), backend))
                        target_is_valid = primitives._runtime_probe_is_ready(backend, probed, base._manifest())
                    except Exception:
                        target_is_valid = False
            if target_is_valid:
                # The process may have stopped after promoting the staged directory but before
                # committing its state files. Rebuild the cache from the promoted interpreter so
                # an otherwise successful transaction does not keep advertising the old manifest.
                recovered_state = _discover_runtime_state(
                    backend,
                    target_python,
                    base._manifest(),
                    persist=True,
                )
                active_state = primitives._resolve_runtime_state_from(base.ACTIVE_RUNTIME_FILE)
                if recovered_state and active_state and primitives._same_path(active_state.get("pythonPath"), target_python):
                    primitives._write_runtime_state({
                        **active_state,
                        **recovered_state,
                        "pythonPath": str(target_python),
                    })
                shutil.rmtree(backup, ignore_errors=True)
            else:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                backup.rename(target)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except OSError:
            # Leave the directories in place for the next startup; deleting either copy here
            # would turn a recoverable interrupted update into data loss.
            continue

    # A staging copy without its backup is never safe to activate: it may have been created
    # before pip completed.  The next update can recreate it from the visible environment.
    for staging in base.RUNTIME_ENVS_DIR.glob(".*.core-updating"):
        backend = staging.name.removeprefix(".").removesuffix(".core-updating")
        if primitives._supported_backend(backend):
            shutil.rmtree(staging, ignore_errors=True)


def _repaired_env_state(
    backend: str, state: dict[str, Any], *, persist: bool = True, probed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-derive an environment's torch facts from its own interpreter.

    Installs used to record whatever the *active* runtime reported, so a CPU environment
    installed while a CUDA runtime was active kept "2.7.1+cu128" and acceleratorAvailable=true
    forever, and the UI faithfully showed it. Nothing rewrites the file on its own, so the lie
    has to be corrected on read."""
    repaired = dict(state)
    try:
        if probed is None:
            probed = _probe_python_runtime(primitives._env_python_path(backend), base._backend_extra_names(base._manifest(), backend))
        repaired.update({
            "pythonVersion": probed.get("pythonVersion") or repaired.get("pythonVersion"),
            "torchVersion": probed.get("torchVersion"),
            "torchBackend": probed.get("torchBackend"),
            "acceleratorAvailable": bool(probed.get("acceleratorAvailable")),
            "packages": probed.get("packages") or repaired.get("packages"),
            "packageVersions": probed.get("packageVersions") or repaired.get("packageVersions"),
            "pymssVersion": probed.get("pymssVersion") or repaired.get("pymssVersion"),
            "pymssCoreVersion": probed.get("pymssCoreVersion") or repaired.get("pymssCoreVersion"),
            "stateVersion": base.ENV_STATE_VERSION,
        })
        if "pymssGraphAvailable" in probed:
            repaired["pymssGraphAvailable"] = bool(probed["pymssGraphAvailable"])
    except Exception:
        # The truth is unavailable (broken or vanished interpreter). Drop the claim rather than
        # repeat a false one, and leave the file untouched so the next read tries again.
        repaired.update({"torchVersion": None, "torchBackend": None, "acceleratorAvailable": False})
        return repaired
    if persist:
        try:
            primitives._atomic_write_json(primitives._env_state_path(backend), repaired)
        except Exception:
            pass
    return repaired


def _read_installed_env_state(
    backend: str, *, repair: bool = True, probed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    path = primitives._env_state_path(backend)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    if int(state.get("stateVersion") or 1) < base.ENV_STATE_VERSION and not base._state_matches_backend(backend, state):
        state = _repaired_env_state(backend, state, persist=repair, probed=probed)
    state.pop("source", None)
    return state


def _discover_runtime_state(
    backend: str,
    python_path: Path,
    manifest: dict[str, Any],
    *,
    persist: bool,
    probed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Rebuild the per-environment record from the interpreter when its cache is absent.

    The directory and interpreter are the durable installation.  ``pymss-runtime-state.json``
    is a cache written after a successful install, so losing it during an overwrite must not
    make an otherwise runnable environment disappear from the environment list.
    """
    try:
        if probed is None:
            probed = _probe_python_runtime(python_path, base._backend_extra_names(manifest, backend))
    except Exception:
        return None
    if not primitives._runtime_probe_is_ready(backend, probed, manifest):
        return None
    state = primitives._fresh_runtime_state_from_probe(backend, manifest, probed)
    if persist:
        try:
            primitives._atomic_write_json(primitives._env_state_path(backend), state)
        except OSError:
            pass
    return state


def _incomplete_env_backends(manifest: dict[str, Any], *, repair: bool = True) -> list[str]:
    """Backends whose venv exists but never recorded an install state.

    An interrupted or failed install leaves the venv behind (state is only written on
    success), so these directories can hold gigabytes while not counting as installed.
    Reporting them is what lets the UI offer to reclaim the space."""
    incomplete: list[str] = []
    for backend in manifest.get("backends", {}):
        python_path = primitives._env_python_path(backend)
        if not python_path.is_file():
            continue
        state = _read_installed_env_state(backend, repair=repair)
        if state and str(state.get("backend") or "").strip().lower() in {"", backend}:
            continue
        if _discover_runtime_state(backend, python_path, manifest, persist=repair) is None:
            incomplete.append(backend)
    return incomplete


def _verify_runtime_audio(python_path: Path, overlay_path: Path | None = None, *, core_api: bool = False) -> None:
    """Exercise lazy imports and decoding before committing an installed environment."""
    completed = base.subprocess.run(
        [
            str(primitives._runtime_command_path(python_path)),
            str(primitives._runtime_command_path(Path(__file__).parents[1] / "runtime_audio_probe.py")),
            *(["--check-core"] if core_api else []),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**primitives._probe_environment(overlay_path, python_path=python_path), "PYTHONDONTWRITEBYTECODE": "1"},
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
    package_names = list(base._manifest()["common"].keys()) + extras
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
   .replace("%MAPPING%", repr(json.dumps(base.PACKAGE_IMPORT_NAMES)))
    output = base.subprocess.check_output(
        [str(python_path), "-c", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        stderr=base.subprocess.PIPE,
        env=primitives._probe_environment(overlay_path, python_path=python_path),
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
        output = base.subprocess.check_output(
            [str(python_path), "-c", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            env=primitives._probe_environment(overlay_path, python_path=python_path),
        )
        data = json.loads(output.strip() or "{}")
        return {name: (str(data.get(name)) if data.get(name) else None) for name in package_names}
    except Exception:
        return {}


def _installed_envs(
    manifest: dict[str, Any], *, repair: bool = True,
    active_python: Path | None = None, active_probe: dict[str, Any] | None = None,
    active_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    def live_probe_for(python_path: Path, names: list[str]) -> dict[str, Any] | None:
        # A request may ask about a different backend than the active one. Reuse only
        # facts from this interpreter that cover every package required by the caller.
        if active_python and active_probe and primitives._same_path(python_path, active_python):
            packages = active_probe.get("packages") or {}
            versions = active_probe.get("packageVersions") or {}
            if all(name in packages and name in versions for name in names):
                # Match the caller's own probe shape, so unrelated extras are not
                # added to a discovered or repaired environment's persisted cache.
                return {
                    **active_probe,
                    "packages": {name: packages[name] for name in names},
                    "packageVersions": {name: versions[name] for name in names},
                }
        return None

    def core_versions(python_path: Path, state: dict[str, Any] | None = None) -> dict[str, str | None]:
        names = ["pymss", "pymss-core"]
        probed = live_probe_for(python_path, names)
        if probed is not None:
            versions = probed["packageVersions"]
            return {name: str(versions[name]) if versions[name] else None for name in names}
        overlay = primitives._overlay_path_from_state(state)
        return (
            _probe_python_package_versions(python_path, names, overlay)
            if overlay
            else _probe_python_package_versions(python_path, names)
        )

    items: list[dict[str, Any]] = []
    seen_backends: set[str] = set()
    # User-managed environments (highest priority)
    for backend in manifest.get("backends", {}):
        env_dir = base._env_dir(backend)
        if repair and env_dir.is_dir() and not primitives._is_bundled_runtime_env(env_dir):
            try:
                configuration_changed = primitives._repair_runtime_venv_config(env_dir)
                configuration_changed = primitives._make_posix_venv_relocatable(env_dir) or configuration_changed
            except OSError:
                configuration_changed = True
            if configuration_changed and active_python and primitives._same_path(primitives._env_python_path(backend), active_python):
                # Repair may replace the base interpreter or its configuration. A
                # probe from before that change is not valid for subsequent reads.
                active_probe = None
        python_path = primitives._env_python_path(backend)
        probed = live_probe_for(python_path, primitives._manifest_requirement_names(manifest, backend))
        state = _read_installed_env_state(backend, repair=repair, probed=probed)
        if state and str(state.get("backend") or "").strip().lower() not in {"", backend}:
            state = None
        if python_path.is_file() and state is None:
            state = _discover_runtime_state(backend, python_path, manifest, persist=repair, probed=probed)
        if state and python_path.is_file():
            package_versions = core_versions(python_path, state)
            items.append({
                **state,
                "backend": backend,
                "pythonPath": str(python_path),
                "logPath": str(primitives._env_log_path(backend)),
                "health": primitives._runtime_health(backend, state, manifest),
                "coreUpdateSupported": repair,
                "packageVersions": {**(state.get("packageVersions") or {}), **package_versions},
                "pymssVersion": (package_versions.get("pymss") or state.get("pymssVersion")),
                "pymssCoreVersion": (package_versions.get("pymss-core") or state.get("pymssCoreVersion")),
            })
            seen_backends.add(backend)
    bundled_state = primitives._resolve_runtime_state_from(base.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if base.BUNDLED_RUNTIME_ENVS_DIR else None
    if bundled_state:
        bundled_state["source"] = "bundled"
    bundled_python = base._bundled_bootstrap_python()
    bundled_backend = str(bundled_state.get("backend") or "") if bundled_state else ""
    if (
        bundled_state
        and bundled_state.get("source") == "bundled"
        and bundled_python
        and bundled_backend in manifest.get("backends", {})
        and bundled_backend not in seen_backends
    ):
        try:
            probed = live_probe_for(bundled_python, primitives._manifest_requirement_names(manifest, bundled_backend))
            if probed is None:
                overlay = primitives._overlay_path_from_state(active_state) if active_state and primitives._same_path(active_state.get("pythonPath"), bundled_python) else None
                probed = (
                    _probe_python_runtime(bundled_python, base._backend_extra_names(manifest, bundled_backend), overlay)
                    if overlay
                    else _probe_python_runtime(bundled_python, base._backend_extra_names(manifest, bundled_backend))
                )
            if primitives._runtime_probe_is_ready(bundled_backend, probed, manifest):
                bundled_live_state = {
                    **bundled_state,
                    **(active_state if active_state and primitives._same_path(active_state.get("pythonPath"), bundled_python) else {}),
                    "packages": probed.get("packages"),
                    "packageVersions": probed.get("packageVersions"),
                    "torchBackend": probed.get("torchBackend"),
                }
                effective_state = {
                    **bundled_state,
                    **(active_state if active_state and primitives._same_path(active_state.get("pythonPath"), bundled_python) else {}),
                }
                items.append({
                    **effective_state,
                    "backend": bundled_backend,
                    "pythonPath": str(bundled_python),
                    "source": "bundled",
                    "health": primitives._runtime_health(bundled_backend, bundled_live_state, manifest),
                    "coreUpdateSupported": repair,
                    "packages": probed.get("packages"),
                    "packageVersions": probed.get("packageVersions"),
                    "pymssVersion": probed.get("pymssVersion"),
                    "pymssCoreVersion": probed.get("pymssCoreVersion"),
                    "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
                })
        except Exception:
            pass
    if base.BUNDLED_RUNTIME_ENVS_DIR and base.BUNDLED_RUNTIME_ENVS_DIR.is_dir():
        for backend in manifest.get("backends", {}):
            if backend in seen_backends:
                continue
            bundled_env = base.BUNDLED_RUNTIME_ENVS_DIR / backend
            bundled_state_path = bundled_env / "pymss-runtime-state.json"
            bundled_python = bundled_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if not bundled_python.is_file():
                continue
            try:
                state = json.loads(bundled_state_path.read_text(encoding="utf-8")) if bundled_state_path.is_file() else None
            except Exception:
                state = None
            if isinstance(state, dict) and str(state.get("backend") or "").strip().lower() not in {"", backend}:
                state = None
            if not isinstance(state, dict):
                probed = live_probe_for(bundled_python, primitives._manifest_requirement_names(manifest, backend))
                state = _discover_runtime_state(backend, bundled_python, manifest, persist=False, probed=probed)
            if not state:
                continue
            effective_state = {
                **state,
                **(active_state if active_state and primitives._same_path(active_state.get("pythonPath"), bundled_python) else {}),
            }
            package_versions = core_versions(bundled_python, effective_state)
            items.append({
                **effective_state,
                "backend": backend,
                "pythonPath": str(bundled_python),
                "source": "bundled",
                "health": primitives._runtime_health(backend, effective_state, manifest),
                "coreUpdateSupported": repair,
                "packageVersions": {**(effective_state.get("packageVersions") or {}), **package_versions},
                "pymssVersion": package_versions.get("pymss") or effective_state.get("pymssVersion"),
                "pymssCoreVersion": package_versions.get("pymss-core") or effective_state.get("pymssCoreVersion"),
                "pymssGraphAvailable": effective_state.get("pymssGraphAvailable"),
            })
            seen_backends.add(backend)
    return items


def _commit_runtime_overlay(state: dict[str, Any]) -> None:
    journal = base.RUNTIME_ENVS_DIR / ".overlay-activation.json"
    state["activationId"] = uuid.uuid4().hex
    selection = primitives._runtime_overlay_selection_path(str(state["backend"]))
    previous_selection = base._read_json_object(selection)
    if previous_selection and previous_selection.get("pythonPath") and state.get("pythonPath") and not primitives._same_path(previous_selection["pythonPath"], state["pythonPath"]):
        # Keep the latest verified selection for the other installation source
        # before the backend-wide index starts referring to the active source.
        primitives._atomic_write_json(primitives._runtime_overlay_selection_path(state["backend"], Path(previous_selection["pythonPath"])), previous_selection)
    primitives._atomic_write_json(journal, {
        "backend": state["backend"], "nextState": state,
        "previousSelection": previous_selection,
    })
    try:
        primitives._atomic_write_json(selection, state)
        # This single atomic replacement is the commit point. Recovery completes
        # or undoes the secondary index according to this authoritative record.
        primitives._write_runtime_state(state)
    except Exception:
        primitives._recover_overlay_activation()
        if (base._read_json_object(base.ACTIVE_RUNTIME_FILE) or {}).get("activationId") != state["activationId"]:
            raise
    else:
        try:
            primitives._recover_overlay_activation()
        except OSError:
            # The authoritative state is committed. The durable journal repairs
            # the secondary index on the next management operation.
            pass


def _target_runtime_from_payload(payload: dict[str, Any], backend: str) -> tuple[dict[str, Any] | None, Path, Path, Path] | None:
    if not primitives._supported_backend(backend):
        return None
    target_python = primitives._runtime_command_path(Path(str(payload.get("pythonPath")))) if payload.get("pythonPath") else None

    if target_python:
        if not target_python.is_file():
            return None
        if primitives._is_bundled_bootstrap_python(target_python):
            state = primitives._resolve_runtime_state_from(base.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if base.BUNDLED_RUNTIME_ENVS_DIR else None
            if state and str(state.get("backend") or "").strip().lower() == backend:
                state["source"] = "bundled"
                return state, target_python.parent, Path(), target_python
            return None
        env_dir = base._runtime_env_dir_for_python(target_python)
        if (not primitives._is_user_runtime_env(env_dir) and not primitives._is_bundled_runtime_env(env_dir)) or env_dir.name != backend:
            return None
        env_state_path = env_dir / "pymss-runtime-state.json"
        state = None
        if env_state_path.is_file():
            try:
                state = json.loads(env_state_path.read_text(encoding="utf-8"))
            except Exception:
                state = None
            if state and str(state.get("backend") or "").strip().lower() not in {"", backend}:
                return None
        if state is None:
            state = _discover_runtime_state(
                backend,
                target_python,
                base._manifest(),
                persist=not primitives._is_bundled_runtime_env(env_dir),
            )
        if state is not None and primitives._is_bundled_runtime_env(env_dir):
            state["source"] = "bundled"
        return state, env_dir, env_state_path, target_python

    active = _read_runtime_state()
    active_python = primitives._runtime_command_path(Path(str(active.get("pythonPath")))) if active and active.get("pythonPath") else None
    if active and str(active.get("backend") or "").strip().lower() == backend and active_python and active_python.is_file():
        env_dir = base._runtime_env_dir_for_python(active_python)
        env_state_path = env_dir / "pymss-runtime-state.json"
        state = dict(active)
        if env_state_path.is_file():
            try:
                state = json.loads(env_state_path.read_text(encoding="utf-8"))
            except Exception:
                state = dict(active)
        if primitives._is_bundled_runtime_env(env_dir):
            state["source"] = "bundled"
        return state, env_dir, env_state_path, active_python

    state = _read_installed_env_state(backend)
    env_dir = base._env_dir(backend)
    env_state_path = primitives._env_state_path(backend)
    python_path = primitives._env_python_path(backend)
    if state and python_path.is_file():
        return state, env_dir, env_state_path, python_path

    if python_path.is_file():
        return (
            _discover_runtime_state(backend, python_path, base._manifest(), persist=True),
            env_dir,
            env_state_path,
            python_path,
        )

    if base.BUNDLED_RUNTIME_ENVS_DIR:
        bundled_env = base.BUNDLED_RUNTIME_ENVS_DIR / backend
        bundled_python = bundled_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        bundled_state_path = bundled_env / "pymss-runtime-state.json"
        if bundled_python.is_file():
            bundled_state = None
            if bundled_state_path.is_file():
                try:
                    bundled_state = json.loads(bundled_state_path.read_text(encoding="utf-8"))
                except Exception:
                    bundled_state = None
            if bundled_state is not None:
                bundled_state["source"] = "bundled"
            return bundled_state, bundled_env, bundled_state_path, bundled_python

    bundled_state = primitives._resolve_runtime_state_from(base.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if base.BUNDLED_RUNTIME_ENVS_DIR else None
    bundled_python = base._bundled_bootstrap_python()
    if (
        bundled_state
        and str(bundled_state.get("backend") or "") == backend
        and bundled_python
    ):
        bundled_state["source"] = "bundled"
        return bundled_state, bundled_python.parent, Path(), bundled_python

    return None


def _runtime_python_works(python_path: Path) -> bool:
    try:
        return base.subprocess.run(
            [str(python_path), "-c", "import sys; print(sys.prefix)"],
            capture_output=True,
            text=True,
            timeout=20,
            env=primitives._probe_environment(python_path=python_path),
        ).returncode == 0
    except (OSError, base.subprocess.SubprocessError):
        return False


def _runtime_pip_works(python_path: Path) -> bool:
    try:
        return base.subprocess.run(
            [str(python_path), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=primitives._probe_environment(python_path=python_path),
        ).returncode == 0
    except (OSError, base.subprocess.SubprocessError):
        return False


def _ensure_runtime_pip(
    python_path: Path,
    task_id: str,
    append_log: Any,
) -> None:
    """Repair an interrupted venv before the first pip operation.

    A killed install can leave Scripts/python.exe in place while ensurepip never ran. Treat that
    directory as recoverable instead of reusing it until every later pip command fails.
    """
    if _runtime_pip_works(python_path):
        return

    message = "pip is unavailable; bootstrapping it with ensurepip"
    append_log("bootstrap", message)
    base._emit("runtime_install_log", {"stage": "bootstrap", "message": message}, task_id)
    result = base.subprocess.run(
        [str(python_path), "-m", "ensurepip", "--upgrade", "--default-pip"],
        stdout=base.subprocess.PIPE,
        stderr=base.subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=primitives._probe_environment(python_path=python_path),
    )
    for line in result.stdout.splitlines():
        message = line.rstrip()
        append_log("bootstrap", message)
        base._emit("runtime_install_log", {"stage": "bootstrap", "message": message}, task_id)
    if result.returncode != 0:
        raise RuntimeError(f"pip bootstrap failed with exit code {result.returncode}")

    if not _runtime_pip_works(python_path):
        raise RuntimeError("pip bootstrap completed but pip is still unavailable")



__all__ = ['_read_runtime_state', '_recover_reinstall_backups', '_recover_core_update_transactions', '_repaired_env_state', '_read_installed_env_state', '_discover_runtime_state', '_incomplete_env_backends', '_verify_runtime_audio', '_probe_python_runtime', '_probe_python_package_versions', '_installed_envs', '_commit_runtime_overlay', '_target_runtime_from_payload', '_runtime_python_works', '_runtime_pip_works', '_ensure_runtime_pip']
