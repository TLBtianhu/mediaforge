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
from core import bootstrap_parts as _parts

MANIFEST_PATH = Path(__file__).parents[1] / "runtime-manifest.json"

def _default_runtime_envs_dir() -> Path:
    return _parts._default_runtime_envs_dir()

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
    return _parts._manifest()

def _pin_manifest_requirement(requirement: Any, version: str) -> str:
    return _parts._pin_manifest_requirement(requirement, version)

def _manifest_pinned_version(manifest: dict[str, Any], name: str) -> str:
    return _parts._manifest_pinned_version(manifest, name)

def _runtime_requirement(name: str, value: Any) -> Requirement:
    return _parts._runtime_requirement(name, value)

def _supported_backend(backend: str, manifest: dict[str, Any] | None=None) -> tuple[str, dict[str, Any]] | None:
    return _parts._supported_backend(backend, manifest)

def _emit(event_type: str, payload: dict[str, Any], task_id: str | None=None) -> None:
    return _parts._emit(event_type, payload, task_id)

def _serialized_runtime_command(command: Callable[..., int], *, query: bool=False) -> Callable[[dict[str, Any]], int]:
    return _parts._serialized_runtime_command(command, query=query)

def _module_available(name: str) -> bool:
    return _parts._module_available(name)

def _resolve_runtime_state_from(file: Path) -> dict[str, Any] | None:
    return _parts._resolve_runtime_state_from(file)

def _read_runtime_state() -> dict[str, Any] | None:
    return _parts._read_runtime_state()

def _write_runtime_state(state: dict[str, Any]) -> None:
    return _parts._write_runtime_state(state)

def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    return _parts._atomic_write_json(path, value)

def _atomic_write_text(path: Path, content: str) -> None:
    return _parts._atomic_write_text(path, content)

def _env_dir(backend: str) -> Path:
    return _parts._env_dir(backend)

def _env_python_path(backend: str) -> Path:
    return _parts._env_python_path(backend)

def _env_state_path(backend: str) -> Path:
    return _parts._env_state_path(backend)

def _env_log_path(backend: str) -> Path:
    return _parts._env_log_path(backend)

def _recover_reinstall_backups() -> None:
    return _parts._recover_reinstall_backups()

def _recover_core_update_transactions() -> None:
    return _parts._recover_core_update_transactions()

def _recover_runtime_transactions() -> None:
    return _parts._recover_runtime_transactions()

def _bootstrap_python_path() -> Path:
    return _parts._bootstrap_python_path()

ENV_STATE_VERSION = 2

PYPI_MIRROR_URLS = {
    "ustc": "https://mirrors.ustc.edu.cn/pypi/web/simple",
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple",
    "tencent": "https://mirrors.cloud.tencent.com/pypi/simple",
    "pypi": "https://pypi.org/simple",
}

def _resolve_pypi_mirror(mirror: str, locale: str) -> tuple[str, str | None]:
    return _parts._resolve_pypi_mirror(mirror, locale)

def _backend_extra_names(manifest: dict[str, Any], backend: str | None) -> list[str]:
    return _parts._backend_extra_names(manifest, backend)

def _state_matches_backend(backend: str, state: dict[str, Any]) -> bool:
    return _parts._state_matches_backend(backend, state)

def _repaired_env_state(backend: str, state: dict[str, Any], *, persist: bool=True, probed: dict[str, Any] | None=None) -> dict[str, Any]:
    return _parts._repaired_env_state(backend, state, persist=persist, probed=probed)

def _read_installed_env_state(backend: str, *, repair: bool=True, probed: dict[str, Any] | None=None) -> dict[str, Any] | None:
    return _parts._read_installed_env_state(backend, repair=repair, probed=probed)

def _fresh_runtime_state_from_probe(backend: str, manifest: dict[str, Any], probed: dict[str, Any]) -> dict[str, Any]:
    return _parts._fresh_runtime_state_from_probe(backend, manifest, probed)

def _discover_runtime_state(backend: str, python_path: Path, manifest: dict[str, Any], *, persist: bool, probed: dict[str, Any] | None=None) -> dict[str, Any] | None:
    return _parts._discover_runtime_state(backend, python_path, manifest, persist=persist, probed=probed)

_PCI_VENDORS = {"10de": "nvidia", "1002": "amd", "1022": "amd", "8086": "intel"}

def _vendor_from_pci_id(value: str) -> str | None:
    return _parts._vendor_from_pci_id(value)

def _windows_gpu_vendors() -> list[str]:
    return _parts._windows_gpu_vendors()

LINUX_DRM_ROOT = Path("/sys/class/drm")

def _linux_gpu_vendors(drm_root: Path | None=None) -> list[str]:
    return _parts._linux_gpu_vendors(drm_root)

def _detect_gpu_vendors() -> list[str]:
    return _parts._detect_gpu_vendors()

def _dir_size_bytes(path: Path) -> int:
    return _parts._dir_size_bytes(path)

def _incomplete_env_backends(manifest: dict[str, Any], *, repair: bool=True) -> list[str]:
    return _parts._incomplete_env_backends(manifest, repair=repair)

def _env_size_targets(manifest: dict[str, Any]) -> dict[str, Path]:
    return _parts._env_size_targets(manifest)

def _probe_environment(overlay_path: Path | None=None, *, python_path: Path | None=None) -> dict[str, str]:
    return _parts._probe_environment(overlay_path, python_path=python_path)

def _verify_runtime_audio(python_path: Path, overlay_path: Path | None=None, *, core_api: bool=False) -> None:
    return _parts._verify_runtime_audio(python_path, overlay_path, core_api=core_api)

def _probe_python_runtime(python_path: Path, extras: list[str] | None=None, overlay_path: Path | None=None) -> dict[str, Any]:
    return _parts._probe_python_runtime(python_path, extras, overlay_path)

def _probe_python_package_versions(python_path: Path, package_names: list[str], overlay_path: Path | None=None) -> dict[str, str | None]:
    return _parts._probe_python_package_versions(python_path, package_names, overlay_path)

def _installed_envs(manifest: dict[str, Any], *, repair: bool=True, active_python: Path | None=None, active_probe: dict[str, Any] | None=None, active_state: dict[str, Any] | None=None) -> list[dict[str, Any]]:
    return _parts._installed_envs(manifest, repair=repair, active_python=active_python, active_probe=active_probe, active_state=active_state)

def _same_path(left: Any, right: Any) -> bool:
    return _parts._same_path(left, right)

def _envs_with_live_active(manifest: dict[str, Any], active_python: Path | None, active_probe: dict[str, Any] | None, active_state: dict[str, Any] | None=None, *, repair: bool=True) -> list[dict[str, Any]]:
    return _parts._envs_with_live_active(manifest, active_python, active_probe, active_state, repair=repair)

def _latest_pypi_version(distribution: str, channel: str='stable') -> str | None:
    return _parts._latest_pypi_version(distribution, channel)

def _runtime_env_dir_for_python(python_path: Path) -> Path:
    return _parts._runtime_env_dir_for_python(python_path)

def _path_is_within(path: Path, root: Path) -> bool:
    return _parts._path_is_within(path, root)

def _is_user_runtime_env(env_dir: Path) -> bool:
    return _parts._is_user_runtime_env(env_dir)

def _is_bundled_runtime_env(env_dir: Path) -> bool:
    return _parts._is_bundled_runtime_env(env_dir)

def _bundled_bootstrap_python() -> Path | None:
    return _parts._bundled_bootstrap_python()

def _is_bundled_bootstrap_python(path: Path) -> bool:
    return _parts._is_bundled_bootstrap_python(path)

def _runtime_overlay_root() -> Path:
    return _parts._runtime_overlay_root()

def _runtime_overlay_selection_path(backend: str, python_path: Path | None=None) -> Path:
    return _parts._runtime_overlay_selection_path(backend, python_path)

def _read_json_object(path: Path) -> dict[str, Any] | None:
    return _parts._read_json_object(path)

def _recover_overlay_activation() -> None:
    return _parts._recover_overlay_activation()

def _commit_runtime_overlay(state: dict[str, Any]) -> None:
    return _parts._commit_runtime_overlay(state)

def _cache_core_runtime_state(env_dir: Path, state_path: Path, python_path: Path, state: dict[str, Any], append_log: Callable[[str, str], None]) -> None:
    return _parts._cache_core_runtime_state(env_dir, state_path, python_path, state, append_log)

def _core_update_identity(state: dict[str, Any], versions: dict[str, Any], manifest: dict[str, Any]) -> str:
    return _parts._core_update_identity(state, versions, manifest)

def _core_rollback_available(state: dict[str, Any] | None, manifest: dict[str, Any]) -> bool:
    return _parts._core_rollback_available(state, manifest)

def _load_core_update_plan(plan_id: str) -> dict[str, Any]:
    return _parts._load_core_update_plan(plan_id)

def _save_core_update_plan(plan: dict[str, Any], task_id: str) -> None:
    return _parts._save_core_update_plan(plan, task_id)

def _overlay_path_from_state(state: dict[str, Any] | None) -> Path | None:
    return _parts._overlay_path_from_state(state)

def _registered_runtime_overlay(backend: str, python_path: Path) -> dict[str, Any] | None:
    return _parts._registered_runtime_overlay(backend, python_path)

def _overlay_requirement_is_satisfied(requirement: str, installed_version: str | None) -> bool:
    return _parts._overlay_requirement_is_satisfied(requirement, installed_version)

def _overlay_requirements(manifest: dict[str, Any], backend: str, base_versions: dict[str, str | None], target_pymss_version: str, target_pymss_core_version: str) -> list[str]:
    return _parts._overlay_requirements(manifest, backend, base_versions, target_pymss_version, target_pymss_core_version)

def _is_base_accelerator_package(name: str) -> bool:
    return _parts._is_base_accelerator_package(name)

def _resolved_overlay_requirements(requirements: list[str], report: dict[str, Any]) -> list[str]:
    return _parts._resolved_overlay_requirements(requirements, report)

def _cleanup_runtime_overlays(backend: str, keep: set[Path]) -> None:
    return _parts._cleanup_runtime_overlays(backend, keep)

def _target_runtime_from_payload(payload: dict[str, Any], backend: str) -> tuple[dict[str, Any] | None, Path, Path, Path] | None:
    return _parts._target_runtime_from_payload(payload, backend)

def _runtime_probe_is_ready(backend: str, probed: dict[str, Any], manifest: dict[str, Any]) -> bool:
    return _parts._runtime_probe_is_ready(backend, probed, manifest)

def _runtime_health(backend: str, state: dict[str, Any] | None, manifest: dict[str, Any]) -> str:
    return _parts._runtime_health(backend, state, manifest)

def _manifest_requirement_names(manifest: dict[str, Any], backend: str) -> list[str]:
    return _parts._manifest_requirement_names(manifest, backend)

def _runtime_manifest_status(actual: Any, expected: Any) -> str:
    return _parts._runtime_manifest_status(actual, expected)

def _manifest_versions_are_satisfied(probed: dict[str, Any], manifest: dict[str, Any], backend: str) -> tuple[bool, list[str]]:
    return _parts._manifest_versions_are_satisfied(probed, manifest, backend)

def _runtime_probe_failure(error: Exception) -> dict[str, str]:
    return _parts._runtime_probe_failure(error)

def _torch_probe_failure(torch_backend: str) -> dict[str, str]:
    return _parts._torch_probe_failure(torch_backend)

def _runtime_info_payload(payload: dict[str, Any], *, repair: bool=True) -> dict[str, Any]:
    return _parts._runtime_info_payload(payload, repair=repair)

@partial(_serialized_runtime_command, query=True)
def cmd_runtime_info(payload: dict[str, Any], *, repair: bool=True) -> int:
    return _parts.cmd_runtime_info(payload, repair=repair)

def cmd_runtime_core_versions(payload: dict[str, Any]) -> int:
    return _parts.cmd_runtime_core_versions(payload)

@partial(_serialized_runtime_command, query=True)
def cmd_runtime_env_sizes(payload: dict[str, Any], *, repair: bool=True) -> int:
    return _parts.cmd_runtime_env_sizes(payload, repair=repair)

@_serialized_runtime_command
def cmd_activate_runtime(payload: dict[str, Any]) -> int:
    return _parts.cmd_activate_runtime(payload)

@_serialized_runtime_command
def cmd_delete_runtime(payload: dict[str, Any]) -> int:
    return _parts.cmd_delete_runtime(payload)

def _normal_runtime_path(path: Path) -> str:
    return _parts._normal_runtime_path(path)

def _runtime_command_path(path: Path) -> Path:
    return _parts._runtime_command_path(path)

def _create_runtime_venv(env_dir: Path) -> None:
    return _parts._create_runtime_venv(env_dir)

def _runtime_python_works(python_path: Path) -> bool:
    return _parts._runtime_python_works(python_path)

def _runtime_pip_works(python_path: Path) -> bool:
    return _parts._runtime_pip_works(python_path)

def _ensure_runtime_pip(python_path: Path, task_id: str, append_log: Any) -> None:
    return _parts._ensure_runtime_pip(python_path, task_id, append_log)

def _repair_runtime_venv_config(env_dir: Path) -> bool:
    return _parts._repair_runtime_venv_config(env_dir)

def _make_posix_venv_relocatable(env_dir: Path) -> bool:
    return _parts._make_posix_venv_relocatable(env_dir)

@_serialized_runtime_command
def cmd_install_runtime(payload: dict[str, Any]) -> int:
    return _parts.cmd_install_runtime(payload)

@_serialized_runtime_command
def cmd_update_runtime_core(payload: dict[str, Any]) -> int:
    return _parts.cmd_update_runtime_core(payload)

