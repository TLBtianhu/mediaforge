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

def _recover_reinstall_backups() -> None:
    import shutil

    if not wb.RUNTIME_ENVS_DIR.is_dir():
        return
    for backup in wb.RUNTIME_ENVS_DIR.glob(".*.reinstalling"):
        backend = backup.name.removeprefix(".").removesuffix(".reinstalling")
        if not wb._supported_backend(backend):
            continue
        target = wb._env_dir(backend)
        try:
            state = wb._read_installed_env_state(backend)
            python_path = wb._env_python_path(backend)
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
    if not wb.RUNTIME_ENVS_DIR.is_dir():
        return
    for backup in wb.RUNTIME_ENVS_DIR.glob(".*.core-backup"):
        backend = backup.name.removeprefix(".").removesuffix(".core-backup")
        if not wb._supported_backend(backend):
            continue
        target = wb._env_dir(backend)
        staging = wb.RUNTIME_ENVS_DIR / f".{backend}.core-updating"
        try:
            target_is_valid = False
            if target.is_dir():
                target_python = wb._env_python_path(backend)
                if target_python.is_file():
                    try:
                        probed = wb._probe_python_runtime(target_python, wb._backend_extra_names(wb._manifest(), backend))
                        target_is_valid = wb._runtime_probe_is_ready(backend, probed, wb._manifest())
                    except Exception:
                        target_is_valid = False
            if target_is_valid:
                # The process may have stopped after promoting the staged directory but before
                # committing its state files. Rebuild the cache from the promoted interpreter so
                # an otherwise successful transaction does not keep advertising the old manifest.
                recovered_state = wb._discover_runtime_state(
                    backend,
                    target_python,
                    wb._manifest(),
                    persist=True,
                )
                active_state = wb._resolve_runtime_state_from(wb.ACTIVE_RUNTIME_FILE)
                if recovered_state and active_state and wb._same_path(active_state.get("pythonPath"), target_python):
                    wb._write_runtime_state({
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
    for staging in wb.RUNTIME_ENVS_DIR.glob(".*.core-updating"):
        backend = staging.name.removeprefix(".").removesuffix(".core-updating")
        if wb._supported_backend(backend):
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
            probed = wb._probe_python_runtime(wb._env_python_path(backend), wb._backend_extra_names(wb._manifest(), backend))
        repaired.update({
            "pythonVersion": probed.get("pythonVersion") or repaired.get("pythonVersion"),
            "torchVersion": probed.get("torchVersion"),
            "torchBackend": probed.get("torchBackend"),
            "acceleratorAvailable": bool(probed.get("acceleratorAvailable")),
            "packages": probed.get("packages") or repaired.get("packages"),
            "packageVersions": probed.get("packageVersions") or repaired.get("packageVersions"),
            "pymssVersion": probed.get("pymssVersion") or repaired.get("pymssVersion"),
            "pymssCoreVersion": probed.get("pymssCoreVersion") or repaired.get("pymssCoreVersion"),
            "stateVersion": wb.ENV_STATE_VERSION,
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
            wb._atomic_write_json(wb._env_state_path(backend), repaired)
        except Exception:
            pass
    return repaired


def _read_installed_env_state(
    backend: str, *, repair: bool = True, probed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    path = wb._env_state_path(backend)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception:
        return None
    if not isinstance(state, dict):
        return None
    if int(state.get("stateVersion") or 1) < wb.ENV_STATE_VERSION and not wb._state_matches_backend(backend, state):
        state = wb._repaired_env_state(backend, state, persist=repair, probed=probed)
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
            probed = wb._probe_python_runtime(python_path, wb._backend_extra_names(manifest, backend))
    except Exception:
        return None
    if not wb._runtime_probe_is_ready(backend, probed, manifest):
        return None
    state = wb._fresh_runtime_state_from_probe(backend, manifest, probed)
    if persist:
        try:
            wb._atomic_write_json(wb._env_state_path(backend), state)
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
        python_path = wb._env_python_path(backend)
        if not python_path.is_file():
            continue
        state = wb._read_installed_env_state(backend, repair=repair)
        if state and str(state.get("backend") or "").strip().lower() in {"", backend}:
            continue
        if wb._discover_runtime_state(backend, python_path, manifest, persist=repair) is None:
            incomplete.append(backend)
    return incomplete


def _installed_envs(
    manifest: dict[str, Any], *, repair: bool = True,
    active_python: Path | None = None, active_probe: dict[str, Any] | None = None,
    active_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    def live_probe_for(python_path: Path, names: list[str]) -> dict[str, Any] | None:
        # A request may ask about a different backend than the active one. Reuse only
        # facts from this interpreter that cover every package required by the caller.
        if active_python and active_probe and wb._same_path(python_path, active_python):
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
        overlay = wb._overlay_path_from_state(state)
        return (
            wb._probe_python_package_versions(python_path, names, overlay)
            if overlay
            else wb._probe_python_package_versions(python_path, names)
        )

    items: list[dict[str, Any]] = []
    seen_backends: set[str] = set()
    # User-managed environments (highest priority)
    for backend in manifest.get("backends", {}):
        env_dir = wb._env_dir(backend)
        if repair and env_dir.is_dir() and not wb._is_bundled_runtime_env(env_dir):
            try:
                configuration_changed = wb._repair_runtime_venv_config(env_dir)
                configuration_changed = wb._make_posix_venv_relocatable(env_dir) or configuration_changed
            except OSError:
                configuration_changed = True
            if configuration_changed and active_python and wb._same_path(wb._env_python_path(backend), active_python):
                # Repair may replace the base interpreter or its configuration. A
                # probe from before that change is not valid for subsequent reads.
                active_probe = None
        python_path = wb._env_python_path(backend)
        probed = live_probe_for(python_path, wb._manifest_requirement_names(manifest, backend))
        state = wb._read_installed_env_state(backend, repair=repair, probed=probed)
        if state and str(state.get("backend") or "").strip().lower() not in {"", backend}:
            state = None
        if python_path.is_file() and state is None:
            state = wb._discover_runtime_state(backend, python_path, manifest, persist=repair, probed=probed)
        if state and python_path.is_file():
            package_versions = core_versions(python_path, state)
            items.append({
                **state,
                "backend": backend,
                "pythonPath": str(python_path),
                "logPath": str(wb._env_log_path(backend)),
                "health": wb._runtime_health(backend, state, manifest),
                "coreUpdateSupported": repair,
                "packageVersions": {**(state.get("packageVersions") or {}), **package_versions},
                "pymssVersion": (package_versions.get("pymss") or state.get("pymssVersion")),
                "pymssCoreVersion": (package_versions.get("pymss-core") or state.get("pymssCoreVersion")),
            })
            seen_backends.add(backend)
    bundled_state = wb._resolve_runtime_state_from(wb.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if wb.BUNDLED_RUNTIME_ENVS_DIR else None
    if bundled_state:
        bundled_state["source"] = "bundled"
    bundled_python = wb._bundled_bootstrap_python()
    bundled_backend = str(bundled_state.get("backend") or "") if bundled_state else ""
    if (
        bundled_state
        and bundled_state.get("source") == "bundled"
        and bundled_python
        and bundled_backend in manifest.get("backends", {})
        and bundled_backend not in seen_backends
    ):
        try:
            probed = live_probe_for(bundled_python, wb._manifest_requirement_names(manifest, bundled_backend))
            if probed is None:
                overlay = wb._overlay_path_from_state(active_state) if active_state and wb._same_path(active_state.get("pythonPath"), bundled_python) else None
                probed = (
                    wb._probe_python_runtime(bundled_python, wb._backend_extra_names(manifest, bundled_backend), overlay)
                    if overlay
                    else wb._probe_python_runtime(bundled_python, wb._backend_extra_names(manifest, bundled_backend))
                )
            if wb._runtime_probe_is_ready(bundled_backend, probed, manifest):
                bundled_live_state = {
                    **bundled_state,
                    **(active_state if active_state and wb._same_path(active_state.get("pythonPath"), bundled_python) else {}),
                    "packages": probed.get("packages"),
                    "packageVersions": probed.get("packageVersions"),
                    "torchBackend": probed.get("torchBackend"),
                }
                effective_state = {
                    **bundled_state,
                    **(active_state if active_state and wb._same_path(active_state.get("pythonPath"), bundled_python) else {}),
                }
                items.append({
                    **effective_state,
                    "backend": bundled_backend,
                    "pythonPath": str(bundled_python),
                    "source": "bundled",
                    "health": wb._runtime_health(bundled_backend, bundled_live_state, manifest),
                    "coreUpdateSupported": repair,
                    "packages": probed.get("packages"),
                    "packageVersions": probed.get("packageVersions"),
                    "pymssVersion": probed.get("pymssVersion"),
                    "pymssCoreVersion": probed.get("pymssCoreVersion"),
                    "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
                })
        except Exception:
            pass
    if wb.BUNDLED_RUNTIME_ENVS_DIR and wb.BUNDLED_RUNTIME_ENVS_DIR.is_dir():
        for backend in manifest.get("backends", {}):
            if backend in seen_backends:
                continue
            bundled_env = wb.BUNDLED_RUNTIME_ENVS_DIR / backend
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
                probed = live_probe_for(bundled_python, wb._manifest_requirement_names(manifest, backend))
                state = wb._discover_runtime_state(backend, bundled_python, manifest, persist=False, probed=probed)
            if not state:
                continue
            effective_state = {
                **state,
                **(active_state if active_state and wb._same_path(active_state.get("pythonPath"), bundled_python) else {}),
            }
            package_versions = core_versions(bundled_python, effective_state)
            items.append({
                **effective_state,
                "backend": backend,
                "pythonPath": str(bundled_python),
                "source": "bundled",
                "health": wb._runtime_health(backend, effective_state, manifest),
                "coreUpdateSupported": repair,
                "packageVersions": {**(effective_state.get("packageVersions") or {}), **package_versions},
                "pymssVersion": package_versions.get("pymss") or effective_state.get("pymssVersion"),
                "pymssCoreVersion": package_versions.get("pymss-core") or effective_state.get("pymssCoreVersion"),
                "pymssGraphAvailable": effective_state.get("pymssGraphAvailable"),
            })
            seen_backends.add(backend)
    return items


def _target_runtime_from_payload(payload: dict[str, Any], backend: str) -> tuple[dict[str, Any] | None, Path, Path, Path] | None:
    if not wb._supported_backend(backend):
        return None
    target_python = wb._runtime_command_path(Path(str(payload.get("pythonPath")))) if payload.get("pythonPath") else None

    if target_python:
        if not target_python.is_file():
            return None
        if wb._is_bundled_bootstrap_python(target_python):
            state = wb._resolve_runtime_state_from(wb.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if wb.BUNDLED_RUNTIME_ENVS_DIR else None
            if state and str(state.get("backend") or "").strip().lower() == backend:
                state["source"] = "bundled"
                return state, target_python.parent, Path(), target_python
            return None
        env_dir = wb._runtime_env_dir_for_python(target_python)
        if (not wb._is_user_runtime_env(env_dir) and not wb._is_bundled_runtime_env(env_dir)) or env_dir.name != backend:
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
            state = wb._discover_runtime_state(
                backend,
                target_python,
                wb._manifest(),
                persist=not wb._is_bundled_runtime_env(env_dir),
            )
        if state is not None and wb._is_bundled_runtime_env(env_dir):
            state["source"] = "bundled"
        return state, env_dir, env_state_path, target_python

    active = wb._read_runtime_state()
    active_python = wb._runtime_command_path(Path(str(active.get("pythonPath")))) if active and active.get("pythonPath") else None
    if active and str(active.get("backend") or "").strip().lower() == backend and active_python and active_python.is_file():
        env_dir = wb._runtime_env_dir_for_python(active_python)
        env_state_path = env_dir / "pymss-runtime-state.json"
        state = dict(active)
        if env_state_path.is_file():
            try:
                state = json.loads(env_state_path.read_text(encoding="utf-8"))
            except Exception:
                state = dict(active)
        if wb._is_bundled_runtime_env(env_dir):
            state["source"] = "bundled"
        return state, env_dir, env_state_path, active_python

    state = wb._read_installed_env_state(backend)
    env_dir = wb._env_dir(backend)
    env_state_path = wb._env_state_path(backend)
    python_path = wb._env_python_path(backend)
    if state and python_path.is_file():
        return state, env_dir, env_state_path, python_path

    if python_path.is_file():
        return (
            wb._discover_runtime_state(backend, python_path, wb._manifest(), persist=True),
            env_dir,
            env_state_path,
            python_path,
        )

    if wb.BUNDLED_RUNTIME_ENVS_DIR:
        bundled_env = wb.BUNDLED_RUNTIME_ENVS_DIR / backend
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

    bundled_state = wb._resolve_runtime_state_from(wb.BUNDLED_RUNTIME_ENVS_DIR / "active-runtime.json") if wb.BUNDLED_RUNTIME_ENVS_DIR else None
    bundled_python = wb._bundled_bootstrap_python()
    if (
        bundled_state
        and str(bundled_state.get("backend") or "") == backend
        and bundled_python
    ):
        bundled_state["source"] = "bundled"
        return bundled_state, bundled_python.parent, Path(), bundled_python

    return None


def _ensure_runtime_pip(
    python_path: Path,
    task_id: str,
    append_log: Any,
) -> None:
    """Repair an interrupted venv before the first pip operation.

    A killed install can leave Scripts/python.exe in place while ensurepip never ran. Treat that
    directory as recoverable instead of reusing it until every later pip command fails.
    """
    if wb._runtime_pip_works(python_path):
        return

    message = "pip is unavailable; bootstrapping it with ensurepip"
    append_log("bootstrap", message)
    wb._emit("runtime_install_log", {"stage": "bootstrap", "message": message}, task_id)
    result = subprocess.run(
        [str(python_path), "-m", "ensurepip", "--upgrade", "--default-pip"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=wb._probe_environment(python_path=python_path),
    )
    for line in result.stdout.splitlines():
        message = line.rstrip()
        append_log("bootstrap", message)
        wb._emit("runtime_install_log", {"stage": "bootstrap", "message": message}, task_id)
    if result.returncode != 0:
        raise RuntimeError(f"pip bootstrap failed with exit code {result.returncode}")

    if not wb._runtime_pip_works(python_path):
        raise RuntimeError("pip bootstrap completed but pip is still unavailable")



__all__ = ['_recover_reinstall_backups', '_recover_core_update_transactions', '_repaired_env_state', '_read_installed_env_state', '_discover_runtime_state', '_incomplete_env_backends', '_installed_envs', '_target_runtime_from_payload', '_ensure_runtime_pip']
