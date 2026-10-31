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

from core.bootstrap import base, operations, primitives
def _recover_runtime_transactions() -> None:
    primitives._recover_overlay_activation()
    operations._recover_reinstall_backups()
    operations._recover_core_update_transactions()


def _envs_with_live_active(
    manifest: dict[str, Any],
    active_python: Path | None,
    active_probe: dict[str, Any] | None,
    active_state: dict[str, Any] | None = None,
    *,
    repair: bool = True,
) -> list[dict[str, Any]]:
    """Installed environments, with the active one's torch facts taken from the live probe.

    A packaged environment records its state on the build machine, which has no GPU, so a
    CUDA environment may claim acceleratorAvailable=false forever. The active
    environment was just probed for real, so prefer that answer over the recording. Idle
    environments keep their recorded state — probing each one would spawn an interpreter per
    environment on every refresh."""
    environments = operations._installed_envs(
        manifest,
        repair=repair,
        active_python=active_python,
        active_probe=active_probe,
        active_state=active_state,
    )
    if not active_probe or not active_python:
        return environments
    for entry in environments:
        if not primitives._same_path(entry.get("pythonPath"), active_python):
            continue
        active_state = {
            **entry,
            "packages": {**(entry.get("packages") or {}), **(active_probe.get("packages") or {})},
            "packageVersions": {**(entry.get("packageVersions") or {}), **(active_probe.get("packageVersions") or {})},
            "torchBackend": active_probe.get("torchBackend"),
        }
        entry.update({
            "torchVersion": active_probe.get("torchVersion"),
            "torchBackend": active_probe.get("torchBackend"),
            "acceleratorAvailable": bool(active_probe.get("acceleratorAvailable")),
            "health": primitives._runtime_health(str(entry.get("backend") or ""), active_state, manifest),
            # Merged, not replaced: the probe's extras follow the requested backend, so a
            # replace could drop a key (mlx) the recording legitimately carries.
            "packages": {**(entry.get("packages") or {}), **(active_probe.get("packages") or {})},
            "packageVersions": {**(entry.get("packageVersions") or {}), **(active_probe.get("packageVersions") or {})},
            "pymssVersion": active_probe.get("pymssVersion") or entry.get("pymssVersion"),
            "pymssCoreVersion": active_probe.get("pymssCoreVersion") or entry.get("pymssCoreVersion"),
        })
        if "pymssGraphAvailable" in active_probe:
            entry["pymssGraphAvailable"] = bool(active_probe["pymssGraphAvailable"])
        break
    return environments


def _runtime_info_payload(payload: dict[str, Any], *, repair: bool = True) -> dict[str, Any]:
    manifest = base._manifest()
    backend = str(payload.get("backend") or "").strip() or None
    install_state = operations._read_runtime_state()
    packages = {name: base._module_available(base.PACKAGE_IMPORT_NAMES.get(name, name)) for name in manifest["common"]}
    package_versions = None
    pymss_version = None
    pymss_core_version = None
    pymss_graph_available = None
    extra_names = base._backend_extra_names(manifest, backend)
    # With no explicit backend the caller is asking "what am I running on"; on macOS that answer
    # depends on whether MLX is present, so probe for it even though no backend was requested.
    if not backend and sys.platform == "darwin" and "mlx" not in extra_names:
        extra_names = [*extra_names, "mlx"]
    for name in extra_names:
        packages[name] = base._module_available(base.PACKAGE_IMPORT_NAMES.get(name, name))
    torch_version = None
    torch_backend = "missing"
    accelerator_available = False
    live_probe_error: dict[str, str] | None = None
    active_probe: dict[str, Any] | None = None
    active_python = primitives._runtime_command_path(Path(str(install_state.get("pythonPath")))) if install_state and install_state.get("pythonPath") else None
    active_overlay = primitives._overlay_path_from_state(install_state)
    if active_python and active_python.is_file():
        try:
            probed = (
                operations._probe_python_runtime(active_python, extra_names, active_overlay)
                if active_overlay
                else operations._probe_python_runtime(active_python, extra_names)
            )
            active_probe = probed
            packages = dict(probed.get("packages") or packages)
            package_versions = probed.get("packageVersions")
            pymss_version = probed.get("pymssVersion")
            pymss_core_version = probed.get("pymssCoreVersion")
            pymss_graph_available = probed.get("pymssGraphAvailable")
            torch_version = probed.get("torchVersion")
            torch_backend = str(probed.get("torchBackend") or torch_backend)
            accelerator_available = bool(probed.get("acceleratorAvailable"))
        except Exception as exc:
            live_probe_error = base._runtime_probe_failure(exc)
            package_versions = install_state.get("packageVersions")
            pymss_version = install_state.get("pymssVersion")
            pymss_core_version = install_state.get("pymssCoreVersion")
            torch_version = install_state.get("torchVersion")
    elif base._module_available("torch"):
        try:
            probed = operations._probe_python_runtime(Path(sys.executable), extra_names)
            active_probe = probed
            packages = dict(probed.get("packages") or packages)
            package_versions = probed.get("packageVersions")
            pymss_version = probed.get("pymssVersion")
            pymss_core_version = probed.get("pymssCoreVersion")
            pymss_graph_available = probed.get("pymssGraphAvailable")
            torch_version = probed.get("torchVersion")
            torch_backend = str(probed.get("torchBackend") or torch_backend)
            accelerator_available = bool(probed.get("acceleratorAvailable"))
        except Exception as exc:
            live_probe_error = base._runtime_probe_failure(exc)
    if live_probe_error is None and torch_backend.startswith("error:"):
        live_probe_error = base._torch_probe_failure(torch_backend)
    if live_probe_error:
        # Cached versions remain useful for display, but not as evidence that
        # dependencies, devices or graph execution are currently available.
        packages = {name: False for name in packages}
        torch_backend = f"error:{live_probe_error['code']}"
        accelerator_available = False
        pymss_graph_available = None
        active_probe = None
    if not pymss_version and package_versions:
        pymss_version = package_versions.get("pymss")
    if not pymss_core_version and package_versions:
        pymss_core_version = package_versions.get("pymss-core")
    environments = _envs_with_live_active(
        manifest,
        active_python,
        active_probe,
        active_state=install_state,
        repair=repair,
    )
    if live_probe_error and active_python:
        for entry in environments:
            if primitives._same_path(entry.get("pythonPath"), active_python):
                entry.update({
                    "health": "broken", "torchBackend": torch_backend,
                    "acceleratorAvailable": False, "pymssGraphAvailable": None,
                    "packages": {name: False for name in (entry.get("packages") or {})},
                })
    return {
        "manifestVersion": manifest["manifestVersion"],
        "pythonVersion": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "bootstrapPython": str(base._bootstrap_python_path()),
        "runtimeEnvsDir": str(base.RUNTIME_ENVS_DIR),
        "activeRuntimeFile": str(base.ACTIVE_RUNTIME_FILE),
        "backend": backend,
        "installedBackend": install_state.get("backend") if install_state else None,
        "installState": install_state,
        "rollbackAvailable": primitives._core_rollback_available(install_state, manifest),
        "statePath": str(base.ACTIVE_RUNTIME_FILE),
        "logPath": str(install_state.get("logPath")) if install_state and install_state.get("logPath") else None,
        "installedEnvironments": environments,
        "gpuVendors": primitives._detect_gpu_vendors(),
        "torchVersion": torch_version,
        "torchBackend": torch_backend,
        "acceleratorAvailable": accelerator_available,
        "packages": packages,
        "packageVersions": package_versions,
        "pymssVersion": pymss_version,
        "pymssCoreVersion": pymss_core_version,
        "pymssGraphAvailable": pymss_graph_available,
        "liveProbeError": live_probe_error,
        # mlx is an optional extra: it must not drag readiness down when it was probed
        # speculatively (no backend requested). An explicit mlx backend still requires it below.
        "ready": live_probe_error is None and all(v for k, v in packages.items() if k != "mlx" or backend == "mlx") and torch_backend != "missing" and not torch_backend.startswith("error:") and (
            not backend or backend == "cpu" and torch_backend == "cpu"
            or backend == "cuda" and torch_backend == "cuda" and accelerator_available
            or backend == "rocm" and torch_backend == "rocm" and accelerator_available
            or backend == "mlx" and packages.get("mlx", False)
        ),
    }


@partial(base._serialized_runtime_command, query=True)
def cmd_runtime_info(payload: dict[str, Any], *, repair: bool = True) -> int:
    if repair:
        _recover_runtime_transactions()
    base._emit("runtime_info", _runtime_info_payload(payload, repair=repair))
    return 0


@partial(base._serialized_runtime_command, query=True)
def cmd_runtime_env_sizes(payload: dict[str, Any], *, repair: bool = True) -> int:
    """Disk usage per installed environment. Split out of runtime_info on purpose: walking a
    multi-GB venv takes long enough that it would slow down every startup and every refresh."""
    del payload
    sizes: dict[str, int] = {}
    incomplete: list[str] = []
    if repair:
        _recover_runtime_transactions()
    manifest = base._manifest()
    try:
        targets = primitives._env_size_targets(manifest)
        incomplete = operations._incomplete_env_backends(manifest, repair=repair)
    except Exception:
        # Probing the directories can fail outright (permissions, a vanished data root).
        # Sizes are supplementary, so degrade to "unknown" instead of failing the command.
        targets = {}
    for backend, env_dir in targets.items():
        try:
            sizes[backend] = base._dir_size_bytes(env_dir)
        except Exception:
            continue
    base._emit("runtime_env_sizes", {
        "sizes": sizes,
        "totalBytes": sum(sizes.values()),
        "incompleteBackends": incomplete,
    })
    return 0


@base._serialized_runtime_command
def cmd_activate_runtime(payload: dict[str, Any]) -> int:
    backend = str(payload.get("backend") or "").strip().lower()
    supported = primitives._supported_backend(backend)
    if not supported:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_BACKEND_UNSUPPORTED", f"Unsupported runtime backend: {backend or 'missing'}")
    backend, _spec = supported
    target = operations._target_runtime_from_payload(payload, backend)
    if not target:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_NOT_INSTALLED", f"Backend {backend} is not installed")
    state, env_dir, env_state_path, python_path = target
    manifest = base._manifest()

    def preserve_existing_pointer() -> bool:
        if not payload.get("onlyIfNoActive"):
            return False
        try:
            # The startup caller uses this guard to avoid replacing a pointer that a user
            # created while the probe was in flight.  A file that cannot be parsed or whose
            # interpreter disappeared is not an active environment, though, and must not block
            # recovery of the only usable runtime.
            state = primitives._resolve_runtime_state_from(base.ACTIVE_RUNTIME_FILE)
            if not state or not state.get("pythonPath"):
                return False
            pointer_backend = str(state.get("backend") or "").strip().lower()
            if not primitives._supported_backend(pointer_backend):
                return False
            pointer_python = primitives._runtime_command_path(Path(str(state["pythonPath"])))
            if not pointer_python.is_file():
                return False
            if primitives._is_bundled_bootstrap_python(pointer_python):
                return True
            pointer_env = base._runtime_env_dir_for_python(pointer_python)
            return (
                pointer_env.name == pointer_backend
                and (primitives._is_user_runtime_env(pointer_env) or primitives._is_bundled_runtime_env(pointer_env))
            )
        except OSError:
            # Startup recovery must not replace a pointer it cannot safely inspect.
            return True

    # The startup probe and this command are separate operations. A user action can create an
    # active pointer between them, so honour the guard for both bundled and managed runtimes.
    if preserve_existing_pointer():
        return 0
    registered_overlay = primitives._registered_runtime_overlay(backend, python_path)
    overlay_path = primitives._overlay_path_from_state(state) or primitives._overlay_path_from_state(registered_overlay)
    if primitives._is_bundled_runtime_env(env_dir) or primitives._is_bundled_bootstrap_python(python_path):
        try:
            probed = (
                operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend), overlay_path)
                if overlay_path
                else operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend))
            )
        except Exception as exc:
            from shared.worker_protocol import emit_error
            return emit_error("RUNTIME_ACTIVATION_FAILED", f"Bundled runtime {backend} failed validation: {exc}", recoverable=True)
        if not primitives._runtime_probe_is_ready(backend, probed, manifest):
            from shared.worker_protocol import emit_error
            return emit_error(
                "RUNTIME_ACTIVATION_FAILED",
                f"Bundled runtime {backend} failed validation: torchBackend={probed.get('torchBackend')}, "
                f"acceleratorAvailable={probed.get('acceleratorAvailable')}",
                recoverable=True,
            )
        active = {
            **(state or {}),
            **(registered_overlay or {}),
            "backend": backend,
            "pythonPath": str(python_path),
            "source": "bundled",
            "activatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        primitives._write_runtime_state(active)
        base._emit("runtime_activated", active)
        return 0
    try:
        probed = (
            operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend), overlay_path)
            if overlay_path
            else operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend))
        )
    except Exception as exc:
        from shared.worker_protocol import emit_error
        return emit_error(
            "RUNTIME_ACTIVATION_FAILED",
            f"Runtime {backend} failed validation: {exc}",
            recoverable=True,
        )
    if not primitives._runtime_probe_is_ready(backend, probed, manifest):
        from shared.worker_protocol import emit_error
        return emit_error(
            "RUNTIME_ACTIVATION_FAILED",
            f"Runtime {backend} failed validation: torchBackend={probed.get('torchBackend')}, "
            f"acceleratorAvailable={probed.get('acceleratorAvailable')}",
            recoverable=True,
        )
    if not state:
        state = primitives._fresh_runtime_state_from_probe(backend, manifest, probed)
    else:
        state = {
            **state,
            **(registered_overlay or {}),
            "stateVersion": max(int(state.get("stateVersion") or 1), base.ENV_STATE_VERSION),
            "pythonVersion": probed.get("pythonVersion") or state.get("pythonVersion"),
            "torchVersion": probed.get("torchVersion"),
            "torchBackend": probed.get("torchBackend"),
            "acceleratorAvailable": bool(probed.get("acceleratorAvailable")),
            "packages": probed.get("packages") or state.get("packages"),
            "packageVersions": probed.get("packageVersions") or state.get("packageVersions"),
            "pymssVersion": probed.get("pymssVersion") or state.get("pymssVersion"),
            "pymssCoreVersion": probed.get("pymssCoreVersion") or state.get("pymssCoreVersion"),
            "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
        }
    if env_state_path:
        try:
            primitives._atomic_write_json(env_state_path, state)
        except OSError:
            # Activation remains valid when state persistence is unavailable; the next probe can
            # rebuild the cache again from the interpreter.
            pass
    active = {
        **state,
        "backend": backend,
        "pythonPath": str(python_path),
        "logPath": str(env_dir / "pymss-runtime-install.log"),
        "activatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Close the remaining race window while the live probe was running. Per-environment cache
    # refreshes above are harmless, but an active pointer created meanwhile belongs to the user.
    if preserve_existing_pointer():
        return 0
    primitives._write_runtime_state(active)
    base._emit("runtime_activated", active)
    return 0


@base._serialized_runtime_command
def cmd_delete_runtime(payload: dict[str, Any]) -> int:
    backend = str(payload.get("backend") or "").strip().lower()
    supported = primitives._supported_backend(backend)
    if not supported:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_BACKEND_UNSUPPORTED", f"Unsupported runtime backend: {backend or 'missing'}")
    backend, _spec = supported
    target = operations._target_runtime_from_payload(payload, backend)
    if not target:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_NOT_INSTALLED", f"Backend {backend} is not installed")
    _state, env_dir, _env_state_path_value, python_path = target
    if primitives._is_bundled_runtime_env(env_dir) or primitives._is_bundled_bootstrap_python(python_path):
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_BUNDLED_READ_ONLY", f"Bundled runtime {backend} cannot be deleted.")
    active = operations._read_runtime_state()
    active_python = primitives._runtime_command_path(Path(str(active.get("pythonPath")))) if active and active.get("pythonPath") else None
    if active and active_python and primitives._same_path(active_python, python_path):
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_DELETE_ACTIVE", f"Cannot delete the currently active runtime ({backend}). Switch to another environment first.")
    import shutil
    try:
        if env_dir.is_dir():
            shutil.rmtree(str(env_dir))
        for selection in (primitives._runtime_overlay_selection_path(backend), primitives._runtime_overlay_selection_path(backend, python_path)):
            selected = base._read_json_object(selection)
            if selected and primitives._same_path(selected.get("pythonPath"), python_path):
                selection.unlink(missing_ok=True)
        retained = set()
        for record in (active, (active or {}).get("previousRuntime")):
            overlay = primitives._overlay_path_from_state(record)
            if overlay:
                retained.add(overlay.parent)
        primitives._cleanup_runtime_overlays(backend, retained)
        base._emit("runtime_deleted", {"backend": backend})
        return 0
    except Exception as exc:
        from shared.worker_protocol import emit_error
        return emit_error(
            "RUNTIME_PERMISSION_DENIED" if isinstance(exc, PermissionError) else "RUNTIME_DELETE_FAILED",
            str(exc),
        )


@base._serialized_runtime_command
def cmd_install_runtime(payload: dict[str, Any]) -> int:
    """Build and activate an environment for `backend`.

    Cancellation is not handled here: the desktop shell kills this process and its whole tree
    (see cancel_task in app_cmd.rs), which takes pip down with it. The interrupted venv is left
    on disk on purpose — _incomplete_env_backends() reports it so the space can be reclaimed."""
    task_id = str(payload.get("taskId") or f"runtime_install_{int(time.time() * 1000)}")
    backend = str(payload.get("backend") or "cpu").strip().lower()
    mirror = str(payload.get("mirror") or "auto").strip().lower()
    locale = str(payload.get("locale") or "").strip().lower()
    manifest = base._manifest()
    _recover_runtime_transactions()
    supported = primitives._supported_backend(backend, manifest)
    if not supported:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_BACKEND_UNSUPPORTED", f"Unsupported runtime backend: {backend}", task_id=task_id)
    backend, spec = supported
    if sys.platform not in spec.get("platforms", []):
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_PLATFORM_UNSUPPORTED", f"Backend {backend} is not supported on {sys.platform}", task_id=task_id)
    index_url = None
    mirror, index_url = base._resolve_pypi_mirror(mirror, locale)
    env_dir = base._env_dir(backend)
    try:
        had_existing_environment = env_dir.is_dir() and any(env_dir.iterdir())
    except OSError:
        had_existing_environment = env_dir.exists()
    env_dir.mkdir(parents=True, exist_ok=True)
    env_python = primitives._env_python_path(backend)
    install_log_path = primitives._env_log_path(backend)
    reinstall_backup: Path | None = None
    if had_existing_environment:
        reinstall_backup = base.RUNTIME_ENVS_DIR / f".{backend}.reinstalling"
        if reinstall_backup.exists():
            shutil.rmtree(reinstall_backup)
        env_dir.rename(reinstall_backup)
        # The install log is written before venv creation, so recreate the destination root after
        # moving the previous environment aside. Without this, every real reinstall fails before
        # the first pip command because the log's parent directory no longer exists.
        env_dir.mkdir(parents=True, exist_ok=True)
    def append_log(stage: str, message: str) -> None:
        with install_log_path.open("a", encoding="utf-8", errors="replace") as file:
            file.write(f"[{stage}] {message}\n")

    def run_pip(args: list[str], stage: str, package_index: str | None = None) -> None:
        command = [str(env_python), "-m", "pip", "install", "--no-cache-dir"]
        if stage == "pymss":
            command.append("--upgrade")
        if stage in {"common", "pymss"}:
            command.extend(["--only-binary=:all:", "--prefer-binary"])
        if package_index or (index_url and stage != "torch"):
            command.extend(["--index-url", package_index or index_url])
        command.extend(args)
        append_log(stage, "pip install " + " ".join(args))
        base._emit("runtime_install_stage", {"stage": stage, "command": "pip install " + " ".join(args)}, task_id)
        process = base.subprocess.Popen(command, stdout=base.subprocess.PIPE, stderr=base.subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=primitives._probe_environment())
        assert process.stdout is not None
        try:
            for line in process.stdout:
                message = line.rstrip()
                append_log(stage, message)
                base._emit("runtime_install_log", {"stage": stage, "message": message}, task_id)
        except Exception:
            # If the protocol pipe is gone, do not leave pip orphaned while the worker reports
            # a failure.  An orphaned install can keep files locked and make the next click look
            # like a successful retry only because the first attempt continued in the background.
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
        if process.wait() != 0:
            raise RuntimeError(f"pip failed during {stage} with exit code {process.returncode}")

    def run_pip_with_pypi_fallback(args: list[str], stage: str) -> None:
        try:
            run_pip(args, stage)
        except RuntimeError:
            if mirror == "pypi":
                raise
            append_log(stage, "selected mirror failed, retrying with PyPI")
            base._emit("runtime_install_stage", {"stage": stage, "message": "Selected mirror failed, retrying with PyPI"}, task_id)
            run_pip(args, stage, "https://pypi.org/simple")

    try:
        install_log_path.write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] install backend={backend} mirror={mirror} manifest={manifest['manifestVersion']}\n",
            encoding="utf-8",
        )
        rebuild_message = ""
        primitives._repair_runtime_venv_config(env_dir)
        if not env_python.is_file():
            primitives._create_runtime_venv(env_dir)
            # A newly created venv writes pyvenv.cfg from the bootstrap interpreter.  When
            # that interpreter was launched through a Windows extended path, venv copies the
            # ``\\\\?\\`` prefix into the config.  Normalize it before the first pip process
            # starts so fresh installs and repaired installs follow the same path rules.
            primitives._repair_runtime_venv_config(env_dir)
        elif not operations._runtime_python_works(env_python):
            rebuild_message = "existing environment interpreter is unusable; rebuilding"
            base._emit("runtime_install_log", {"stage": "venv", "message": rebuild_message}, task_id)
            import shutil
            shutil.rmtree(env_dir, ignore_errors=True)
            env_dir.mkdir(parents=True, exist_ok=True)
            primitives._create_runtime_venv(env_dir)
            primitives._repair_runtime_venv_config(env_dir)
        elif not operations._runtime_pip_works(env_python):
            # A cancelled or older portable install can leave a runnable venv without pip. The
            # venv's ensurepip module may have been pruned already, so recreate it from the
            # bootstrap runtime instead of failing the first package install.
            rebuild_message = "existing environment has no pip; rebuilding the virtual environment"
            base._emit("runtime_install_log", {
                "stage": "bootstrap",
                "message": rebuild_message,
            }, task_id)
            import shutil
            shutil.rmtree(env_dir, ignore_errors=True)
            env_dir.mkdir(parents=True, exist_ok=True)
            primitives._create_runtime_venv(env_dir)
            primitives._repair_runtime_venv_config(env_dir)
        if rebuild_message:
            append_log("bootstrap", rebuild_message)
        primitives._make_posix_venv_relocatable(env_dir)
        base._emit("runtime_install_started", {"backend": backend, "manifestVersion": manifest["manifestVersion"], "logPath": str(install_log_path)}, task_id)
        operations._ensure_runtime_pip(env_python, task_id, append_log)
        torch = spec.get("torch", {})
        if torch.get("rocmRequirements"):
            run_pip(list(torch["rocmRequirements"]), "rocm-sdk", None)
        torch_args = list(torch.get("requirements", [])) if torch.get("requirements") else [torch["requirement"]]
        run_pip((["--no-deps"] if torch.get("noDeps") else []) + torch_args, "torch", torch.get("indexUrl"))
        common = [value for name, value in manifest["common"].items() if name not in {"pymss", "pymss-core"}]
        pymss_requirement = manifest["common"]["pymss"]
        pymss_core_requirement = manifest["common"]["pymss-core"]
        run_pip_with_pypi_fallback(common, "common")
        # Install the declared pymss extras with dependency resolution enabled.  The previous
        # --no-deps path silently skipped PySocks required by pymss[proxy], so a fresh runtime
        # passed the basic import probe but failed when a SOCKS proxy was used.
        run_pip_with_pypi_fallback([pymss_requirement, pymss_core_requirement], "pymss")
        if spec.get("extras"):
            run_pip_with_pypi_fallback(list(spec["extras"]), "extras")
        # Probe the interpreter that was just built, not _runtime_info_payload(): that one reads
        # the *active* runtime, which at this point is still the previously activated environment
        # — recording its torch build here is what made a CPU env report cu128.
        probed = operations._probe_python_runtime(env_python, base._backend_extra_names(manifest, backend))
        if not primitives._runtime_probe_is_ready(backend, probed, manifest):
            missing = [
                name for name in [*manifest.get("common", {}), *base._backend_extra_names(manifest, backend)]
                if (probed.get("packages") or {}).get(name) is not True
            ]
            raise RuntimeError(
                f"Runtime probe failed for {backend}: missing packages={missing}, "
                f"torchBackend={probed.get('torchBackend')}, "
                f"acceleratorAvailable={probed.get('acceleratorAvailable')}"
            )
        manifest_ok, manifest_failures = primitives._manifest_versions_are_satisfied(probed, manifest, backend)
        if not manifest_ok:
            raise RuntimeError(
                "Runtime manifest verification failed: " + "; ".join(manifest_failures)
            )
        operations._verify_runtime_audio(env_python)
        state = {
            "backend": backend,
            "manifestVersion": manifest["manifestVersion"],
            "stateVersion": base.ENV_STATE_VERSION,
            "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pythonVersion": probed.get("pythonVersion") or platform.python_version(),
            "torchVersion": probed.get("torchVersion"),
            "torchBackend": probed.get("torchBackend"),
            "acceleratorAvailable": bool(probed.get("acceleratorAvailable")),
            "packages": probed.get("packages"),
            "packageVersions": probed.get("packageVersions"),
            "pymssVersion": probed.get("pymssVersion"),
            "pymssCoreVersion": probed.get("pymssCoreVersion"),
            "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
        }
        primitives._atomic_write_json(primitives._env_state_path(backend), state)
        active = {
            **state,
            "pythonPath": str(env_python),
            "logPath": str(install_log_path),
            "activatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        primitives._write_runtime_state(active)
        if reinstall_backup and reinstall_backup.exists():
            import shutil
            try:
                shutil.rmtree(reinstall_backup)
            except OSError as backup_error:
                append_log("warning", f"failed to remove reinstall backup: {backup_error}")
        base._emit("runtime_install_finished", {"backend": backend, "state": active, "logPath": str(install_log_path)}, task_id)
        return 0
    except Exception as exc:
        from shared.worker_protocol import emit_error
        if reinstall_backup and reinstall_backup.exists():
            import shutil
            if env_dir.exists():
                shutil.rmtree(env_dir, ignore_errors=True)
            reinstall_backup.rename(env_dir)
        append_log("error", str(exc))
        return emit_error(
            "RUNTIME_PERMISSION_DENIED" if isinstance(exc, PermissionError) else "RUNTIME_INSTALL_FAILED",
            str(exc),
            detail=f"详细安装日志：{install_log_path}",
            task_id=task_id,
            recoverable=True,
            extra={"backend": backend, "logPath": str(install_log_path)},
        )


@base._serialized_runtime_command
def cmd_update_runtime_core(payload: dict[str, Any]) -> int:
    task_id = str(payload.get("taskId") or f"runtime_core_update_{int(time.time() * 1000)}")
    backend = str(payload.get("backend") or "").strip().lower()
    mirror = str(payload.get("mirror") or "auto").strip().lower()
    locale = str(payload.get("locale") or "").strip().lower()
    prepare_only = payload.get("prepareOnly") is True
    mode = str(payload.get("mode") or ("repair" if payload.get("repairDependencies") else "update"))
    channel = "prerelease" if payload.get("channel") == "prerelease" else "stable"
    manifest = base._manifest()
    _recover_runtime_transactions()
    supported = primitives._supported_backend(backend)
    if not supported:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_BACKEND_UNSUPPORTED", f"Unsupported runtime backend: {backend or 'missing'}", task_id=task_id)
    backend, _spec = supported

    target = operations._target_runtime_from_payload(payload, backend)
    if not target:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_NOT_INSTALLED", f"Backend {backend} is not installed", task_id=task_id)
    state, env_dir, env_state_path, python_path = target
    if not state:
        from shared.worker_protocol import emit_error
        return emit_error(
            "RUNTIME_NOT_INSTALLED",
            f"Backend {backend} has no completed installation state",
            task_id=task_id,
            recoverable=True,
        )
    if not python_path.is_file():
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_NOT_INSTALLED", f"Active runtime Python not found: {python_path}", task_id=task_id)
    # Overlay activation changes the process import path, so only the active runtime may be
    # migrated. The base environment itself remains immutable, including bundled runtimes.
    active = operations._read_runtime_state()
    active_python = primitives._runtime_command_path(Path(str(active.get("pythonPath")))) if active and active.get("pythonPath") else None
    if not active_python or not primitives._same_path(active_python, python_path):
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_CORE_UPDATE_INACTIVE", "Core update is only available for the currently active runtime. Please switch to this environment first.", task_id=task_id, recoverable=True)
    if active.get("overlayPath"):
        state = {**state, **active}
    try:
        plan = None if prepare_only else primitives._load_core_update_plan(str(payload.get("planId") or ""))
        if plan:
            if plan.get("backend") != backend or not primitives._same_path(plan.get("pythonPath"), python_path):
                raise ValueError("The runtime update plan belongs to another environment")
            mode, channel, mirror = plan["mode"], plan["channel"], plan["mirror"]
        if mode not in {"update", "sync", "repair", "rollback"}:
            raise ValueError("Unsupported runtime core operation")
    except (OSError, ValueError, KeyError) as exc:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_UPDATE_PLAN_INVALID", str(exc), task_id=task_id, recoverable=True)
    repair_dependencies = mode in {"sync", "repair"}
    manifest_status = base._runtime_manifest_status(state.get("manifestVersion"), manifest.get("manifestVersion"))
    if manifest_status not in {"current", "older"} and not (
        repair_dependencies and manifest_status == "unknown"
    ):
        from shared.worker_protocol import emit_error
        return emit_error(
            "RUNTIME_MANIFEST_INCOMPATIBLE",
            "The runtime manifest is newer than this application or has an unknown version. "
            "Use a compatible application version or reinstall the runtime before updating core packages.",
            task_id=task_id,
            recoverable=True,
        )
    mirror, index_url = ("pypi", base.PYPI_MIRROR_URLS["pypi"]) if mirror == "auto" else base._resolve_pypi_mirror(mirror, locale)
    package_versions = state.get("packageVersions") if isinstance(state.get("packageVersions"), dict) else {}
    package_versions = {
        **package_versions,
        **operations._probe_python_package_versions(python_path, ["pymss", "pymss-core"], primitives._overlay_path_from_state(active)),
    }
    identity = primitives._core_update_identity(active, package_versions, manifest)
    if plan and plan.get("identity") != identity:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_UPDATE_PLAN_STALE", "The active runtime changed. Prepare and confirm a new update plan.", task_id=task_id, recoverable=True)

    def target_core_version(name: str, direct_key: str) -> str:
        actual = str(package_versions.get(name) or state.get(direct_key) or "").strip()
        requirement_value = manifest.get("common", {}).get(name)
        try:
            requirement = base._runtime_requirement(name, requirement_value)
            installed = actual if requirement.specifier.contains(Version(actual), prereleases=True) else ""
        except Exception:
            installed = ""
        if repair_dependencies:
            baseline = installed or base._manifest_pinned_version(manifest, name)
            if not baseline:
                lower = [Version(spec.version) for spec in requirement.specifier if spec.operator == ">="]
                baseline = str(max(lower)) if lower else ""
            if baseline:
                return baseline
        latest = base._latest_pypi_version(name, channel)
        if not latest:
            raise RuntimeError(f"PyPI did not return a version for {name}")
        # A synchronization or a stale package index must not downgrade an updated core.
        return installed if installed and Version(installed) > Version(latest) else str(Version(latest))

    try:
        previous_runtime = (plan.get("rollbackState") if plan else active.get("previousRuntime")) if mode == "rollback" else None
        if mode == "rollback":
            if not isinstance(previous_runtime, dict) or not primitives._same_path(previous_runtime.get("pythonPath"), python_path):
                raise ValueError("No verified previous runtime is available")
            previous_versions = previous_runtime.get("packageVersions") or {}
            targets = {
                "pymss": previous_runtime.get("pymssVersion") or previous_versions.get("pymss"),
                "pymss-core": previous_runtime.get("pymssCoreVersion") or previous_versions.get("pymss-core"),
            }
        elif plan:
            targets = plan["targetVersions"]
        else:
            targets = {
                "pymss": target_core_version("pymss", "pymssVersion"),
                "pymss-core": target_core_version("pymss-core", "pymssCoreVersion"),
            }
        target_pymss_version, target_pymss_core_version = targets["pymss"], targets["pymss-core"]
    except Exception as exc:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_CORE_UPDATE_FAILED", f"Failed to resolve runtime core versions: {exc}", task_id=task_id, recoverable=True)
    overlay_backend_root = base._runtime_overlay_root() / backend
    overlay_backend_root.mkdir(parents=True, exist_ok=True)
    log_path = overlay_backend_root / "pymss-core-update.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    operation = "repair runtime dependencies" if repair_dependencies else "update pymss core"
    log_path.write_text(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {operation} mirror={mirror}\n", encoding="utf-8")
    base._emit(
        "runtime_core_update_started",
        {
            "backend": backend,
            "logPath": str(log_path),
            "mode": mode,
        },
        task_id,
    )

    log_writable = True

    def append_log(stage: str, message: str) -> None:
        nonlocal log_writable
        if not log_writable:
            return
        try:
            with log_path.open("a", encoding="utf-8", errors="replace") as file:
                file.write(f"[{stage}] {message}\n")
        except OSError:
            log_writable = False
            base._emit("runtime_core_update_log", {"stage": "warning", "message": "The runtime log is unavailable. The operation will continue."}, task_id)

    try:
        base_probe = operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend))
    except Exception:
        base_probe = {}
    torch_version = str(base_probe.get("torchVersion") or (state or {}).get("torchVersion") or "").strip()
    if not torch_version:
        from shared.worker_protocol import emit_error
        return emit_error("RUNTIME_CORE_UPDATE_FAILED", "Unable to determine the installed Torch version; refusing to update the runtime core.", task_id=task_id, recoverable=True)
    # Older bundled state omitted Torch's version. Preserve the live base facts
    # in both generations so a healthy previous core remains eligible for rollback.
    runtime_facts = {
        key: base_probe[key]
        for key in ("pythonVersion", "torchBackend", "acceleratorAvailable")
        if key in base_probe
    }
    runtime_facts["torchVersion"] = torch_version
    staging_generation: Path | None = None
    promoted_generation: Path | None = None
    overlay_committed = False
    try:
        if plan and (plan.get("torchVersion") != torch_version or plan.get("baseVersions") != base_probe.get("packageVersions")):
            raise ValueError("The base runtime changed. Prepare and confirm a new update plan.")
        if mode == "rollback":
            rollback_overlay = primitives._overlay_path_from_state(previous_runtime)
            if previous_runtime.get("overlayPath") and rollback_overlay is None:
                raise ValueError("The previous runtime files are no longer available")
            if prepare_only:
                primitives._save_core_update_plan({
                    "backend": backend, "pythonPath": str(python_path), "mode": mode,
                    "channel": channel, "mirror": mirror, "identity": identity,
                    "torchVersion": torch_version, "baseVersions": base_probe.get("packageVersions"),
                    "currentVersions": {name: package_versions.get(name) for name in targets},
                    "targetVersions": targets, "dependencyCount": 0, "rollbackState": previous_runtime,
                }, task_id)
                return 0
            probed = operations._probe_python_runtime(python_path, base._backend_extra_names(manifest, backend), rollback_overlay)
            manifest_ok, failures = primitives._manifest_versions_are_satisfied(probed, manifest, backend)
            if not manifest_ok or any(probed.get("packageVersions", {}).get(name) != version for name, version in targets.items()):
                raise RuntimeError("The previous runtime failed compatibility verification: " + "; ".join(failures))
            if probed.get("torchVersion") != torch_version or probed.get("torchBackend") != base_probe.get("torchBackend") or probed.get("pymssGraphAvailable") is not True:
                raise RuntimeError("The previous runtime is incompatible with the current application")
            operations._verify_runtime_audio(python_path, rollback_overlay, core_api=True)
            restored = {key: value for key, value in previous_runtime.items() if key not in {"previousRuntime", "activationId"}}
            restored["previousRuntime"] = {key: value for key, value in active.items() if key not in {"previousRuntime", "activationId"}}
            restored["previousRuntime"].update({
                "packageVersions": package_versions, "pymssVersion": package_versions.get("pymss"),
                "pymssCoreVersion": package_versions.get("pymss-core"),
            })
            base._emit("runtime_core_update_stage", {"stage": "activate", "message": "Restoring the previous runtime"}, task_id)
            operations._commit_runtime_overlay(restored)
            primitives._cache_core_runtime_state(env_dir, env_state_path, python_path, restored, append_log)
            base._emit("runtime_core_update_finished", {"backend": backend, "state": restored, "mode": mode, "rollbackAvailable": primitives._core_rollback_available(restored, manifest)}, task_id)
            return 0
        base._emit(
            "runtime_core_update_stage",
            {
                "stage": "prepare",
                "message": "Preparing versioned runtime overlay",
            },
            task_id,
        )
        if not operations._runtime_pip_works(python_path):
            raise RuntimeError("The runtime package installer is unavailable. Repair the runtime before updating core packages.")
        base_versions = dict(base_probe.get("packageVersions") or {})
        requirements = primitives._overlay_requirements(
            manifest,
            backend,
            base_versions,
            target_pymss_version,
            target_pymss_core_version,
        )
        if prepare_only and mode == "update":
            for index, name in enumerate(("pymss", "pymss-core")):
                requirement = base._runtime_requirement(name, manifest["common"][name])
                requirement.specifier &= SpecifierSet(f"<={targets[name]}")
                installed = package_versions.get(name)
                if installed:
                    requirement.specifier &= SpecifierSet(f">={installed}")
                requirements[index] = str(requirement)
        # Cancellation can terminate this bootstrap process before its finally block runs.
        # Runtime operations are serialized, so any staging directory left from an earlier
        # process is safe to reclaim before starting the next generation.
        for stale_staging in overlay_backend_root.glob(".*.staging"):
            if stale_staging.is_dir():
                shutil.rmtree(stale_staging, ignore_errors=True)
        generation = f"{manifest.get('manifestVersion')}-{time.time_ns()}"
        staging_generation = overlay_backend_root / f".{generation}.staging"
        final_generation = overlay_backend_root / generation
        staging_site_packages = staging_generation / "site-packages"
        shutil.rmtree(staging_generation, ignore_errors=True)
        staging_site_packages.mkdir(parents=True)
        wheel_cache = base._runtime_overlay_root() / ".wheel-cache"
        wheel_cache.mkdir(parents=True, exist_ok=True)

        pip_command = [
            str(python_path), "-m", "pip", "install", "--upgrade",
            "--only-binary=:all:", "--prefer-binary", "--cache-dir", str(wheel_cache),
        ]
        if index_url:
            pip_command.extend(["--index-url", index_url])
        if channel == "prerelease":
            pip_command.append("--pre")

        def run_pip(command: list[str], stage: str) -> None:
            process = base.subprocess.Popen(
                command,
                stdout=base.subprocess.PIPE,
                stderr=base.subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=primitives._probe_environment(python_path=python_path),
            )
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    message = line.rstrip()
                    append_log(stage, message)
                    base._emit("runtime_core_update_log", {"stage": stage, "message": message}, task_id)
            except Exception:
                if process.poll() is None:
                    process.kill()
                process.wait()
                raise
            finally:
                close_stdout = getattr(process.stdout, "close", None)
                if close_stdout:
                    close_stdout()
            if process.wait() != 0:
                raise RuntimeError(f"pip failed during {stage} with exit code {process.returncode}")

        stage = "resolve" if prepare_only else mode
        base._emit(
            "runtime_core_update_stage",
            {
                "stage": stage,
                "message": "Building isolated runtime overlay",
            },
            task_id,
        )
        # Resolve against the immutable base, without --target (which ignores installed
        # packages). Install only the resulting delta into the new overlay afterwards.
        if prepare_only:
            constraints = staging_generation / "base-constraints.txt"
            constraints.write_text(f"torch=={torch_version}\n", encoding="utf-8")
            report_path = staging_generation / "resolve-report.json"
            run_pip([
                *pip_command, "--dry-run", "--report", str(report_path),
                "--constraint", str(constraints), *requirements,
            ], "resolve")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            requirements = primitives._resolved_overlay_requirements(requirements, report)
            selected = {canonicalize_name(item["metadata"]["name"]): item["metadata"]["version"] for item in report["install"]}
            targets = {name: selected.get(name) or base_versions.get(name) for name in targets}
            for index, name in enumerate(("pymss", "pymss-core")):
                if not targets[name]:
                    raise RuntimeError(f"Unable to resolve a compatible version of {name}")
                if channel == "stable" and Version(targets[name]).is_prerelease and targets[name] != package_versions.get(name):
                    raise RuntimeError("No stable core version is compatible with the current runtime")
                requirements[index] = base._pin_manifest_requirement(manifest["common"][name], targets[name])
            if mode == "update" and not any(
                not package_versions.get(name) or Version(version) > Version(package_versions[name])
                for name, version in targets.items()
            ):
                raise RuntimeError("No newer core is compatible with the current runtime. Check the package mirror or upgrade Python/PyTorch before retrying.")
            primitives._save_core_update_plan({
                "backend": backend, "pythonPath": str(python_path), "mode": mode,
                "channel": channel, "mirror": mirror, "identity": identity,
                "torchVersion": torch_version, "baseVersions": base_probe.get("packageVersions"),
                "currentVersions": {name: package_versions.get(name) for name in targets},
                "targetVersions": targets, "dependencyCount": max(0, len(requirements) - 2),
                "requirements": requirements,
            }, task_id)
            return 0
        requirements = plan["requirements"]
        run_pip([*pip_command, "--target", str(staging_site_packages), "--no-deps", *requirements], stage)
        base._emit("runtime_core_update_stage", {"stage": "verify", "message": "Verifying runtime packages"}, task_id)
        probed = operations._probe_python_runtime(
            python_path,
            base._backend_extra_names(manifest, backend),
            staging_site_packages,
        )
        manifest_ok, manifest_failures = primitives._manifest_versions_are_satisfied(probed, manifest, backend)
        if not manifest_ok:
            raise RuntimeError(
                "Runtime manifest verification failed: " + "; ".join(manifest_failures)
            )
        if probed.get("pymssVersion") != target_pymss_version:
            raise RuntimeError(f"pymss stayed at {probed.get('pymssVersion') or 'unknown'} after update; expected {target_pymss_version}")
        if probed.get("pymssCoreVersion") != target_pymss_core_version:
            raise RuntimeError(f"pymss-core stayed at {probed.get('pymssCoreVersion') or 'unknown'} after update; expected {target_pymss_core_version}")
        if probed.get("pymssGraphAvailable") is False:
            raise RuntimeError("pymss.graph is unavailable after the core update; retry the update or reinstall the runtime")
        expected_torch_backend = "cpu" if backend == "mlx" else backend
        if probed.get("torchBackend") != expected_torch_backend:
            raise RuntimeError(
                f"Torch backend changed to {probed.get('torchBackend') or 'unknown'} during update; expected {expected_torch_backend}"
            )
        if probed.get("torchVersion") != torch_version:
            raise RuntimeError(
                f"Torch version changed to {probed.get('torchVersion') or 'unknown'} during update; expected {torch_version}"
            )
        operations._verify_runtime_audio(python_path, staging_site_packages, core_api=True)
        staging_generation.rename(final_generation)
        promoted_generation = final_generation
        overlay_path = final_generation / "site-packages"
        updated = {
            **(state or {}),
            **runtime_facts,
            "backend": backend,
            "manifestVersion": manifest.get("manifestVersion"),
            "pythonPath": str(python_path),
            "overlayPath": str(overlay_path),
            "logPath": str(log_path),
            "packages": probed.get("packages") or (state.get("packages") if state else None),
            "packageVersions": probed.get("packageVersions") or (state.get("packageVersions") if state else None),
            "pymssVersion": probed.get("pymssVersion") or (state.get("pymssVersion") if state else None),
            "pymssCoreVersion": probed.get("pymssCoreVersion") or (state.get("pymssCoreVersion") if state else None),
            "pymssGraphAvailable": bool(probed.get("pymssGraphAvailable")),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "previousRuntime": {key: value for key, value in active.items() if key not in {"previousRuntime", "activationId"}},
        }
        updated["previousRuntime"].update({
            **runtime_facts,
            "packageVersions": package_versions,
            "pymssVersion": package_versions.get("pymss"),
            "pymssCoreVersion": package_versions.get("pymss-core"),
        })
        base._emit("runtime_core_update_stage", {"stage": "activate", "message": "Activating runtime overlay"}, task_id)
        primitives._atomic_write_json(final_generation / "runtime-state.json", updated)
        operations._commit_runtime_overlay(updated)
        overlay_committed = True
        primitives._cache_core_runtime_state(env_dir, env_state_path, python_path, updated, append_log)
        retained = {final_generation}
        previous_overlay = primitives._overlay_path_from_state(active)
        if previous_overlay:
            retained.add(previous_overlay.parent)
        try:
            primitives._cleanup_runtime_overlays(backend, retained)
            (base._runtime_overlay_root() / ".plans" / f"{plan['id']}.json").unlink(missing_ok=True)
        except OSError as cleanup_error:
            append_log("warning", f"Runtime activated; deferred cleanup: {cleanup_error}")
        append_log(
            "complete",
            "Runtime dependency reinstall completed" if repair_dependencies else "Core package update completed",
        )
        base._emit(
            "runtime_core_update_finished",
            {
                "backend": backend,
                "state": updated,
                "rollbackAvailable": primitives._core_rollback_available(updated, manifest),
                "logPath": str(log_path),
                "mode": mode,
            },
            task_id,
        )
        return 0
    except Exception as exc:
        if not overlay_committed and promoted_generation and promoted_generation.is_dir() and not (base.RUNTIME_ENVS_DIR / ".overlay-activation.json").exists():
            shutil.rmtree(promoted_generation, ignore_errors=True)
        from shared.worker_protocol import emit_error
        append_log("error", str(exc))
        return emit_error(
            "RUNTIME_CORE_UPDATE_FAILED",
            str(exc),
            detail=f"详细更新日志：{log_path}",
            task_id=task_id,
            recoverable=True,
            extra={"backend": backend, "logPath": str(log_path)},
        )
    finally:
        if staging_generation and staging_generation.is_dir():
            shutil.rmtree(staging_generation, ignore_errors=True)



__all__ = ['_recover_runtime_transactions', '_envs_with_live_active', '_runtime_info_payload', 'cmd_runtime_info', 'cmd_runtime_env_sizes', 'cmd_activate_runtime', 'cmd_delete_runtime', 'cmd_install_runtime', 'cmd_update_runtime_core']
