"""Verified marketplace installs and isolated pymss plugin discovery."""
from __future__ import annotations

import atexit
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from core.plugin_marketplace_trust import Package, catalog, dependency_tree_digest, stage_package, verify_source
from shared.asset_references import ensure_plain_path
from shared.worker_protocol import emit, emit_error, isolate_protocol_stdout
from core.worker_runtime_lock import RuntimeLockBusy, runtime_lock
from shared.worker_subprocess import hidden_subprocess_kwargs

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_PROBE_MARKER = "__STUDIO_PLUGIN_PROBE__"
_RECEIPT = ".pymss-install.json"
_SIDECAR = ".studio-deps"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PLUGIN_BOOTSTRAP = threading.RLock()


def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid plugin name")
    return name


def _root() -> Path:
    from core.worker_optional_packages import _runtime_context

    runtime = _runtime_context()
    # Preserve the venv path: resolving its python symlink would merge distinct venvs.
    digest = hashlib.sha256(os.path.normcase(os.path.abspath(sys.executable)).encode()).hexdigest()[:12]
    return runtime.packages_dir.parent.parent / "plugins" / f"{runtime.identity}-{digest}"


@contextmanager
def _plugin_dir(path: Path) -> Iterator[None]:
    with _PLUGIN_BOOTSTRAP:
        previous = os.environ.get("PYMSS_PLUGINS_DIR")
        os.environ["PYMSS_PLUGINS_DIR"] = str(path)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("PYMSS_PLUGINS_DIR", None)
            else:
                os.environ["PYMSS_PLUGINS_DIR"] = previous


def _recover(root: Path) -> None:
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    for backup in backups.iterdir():
        if not backup.is_dir() or not _NAME.fullmatch(backup.name):
            continue
        target = root / "installed" / backup.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(backup)
        else:
            backup.rename(target)
    transactions = root / "transactions"
    if transactions.exists():
        for transaction in transactions.iterdir():
            if transaction.is_dir() and transaction.name.startswith(("install-", "removed-")):
                shutil.rmtree(transaction, ignore_errors=True)


def _recover_for_read(root: Path) -> None:
    if not root.exists():
        return
    try:
        with runtime_lock(root / ".lock", timeout=0, allow_read_only=True) as writable:
            if writable:
                _recover(root)
    except (RuntimeLockBusy, OSError):
        # A live writer owns its transaction; readers must never clean it up.
        # Read-only installations can still be inspected and executed.
        pass


def _installed(root: Path) -> list[Path]:
    directory = root / "installed"
    ensure_plain_path(directory)
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.is_dir() and _NAME.fullmatch(p.name))


def _receipt_fields(package: Package) -> dict[str, Any]:
    return {
        "schemaVersion": 1, "name": package.name, "source": "catalog",
        "version": package.version, "url": package.repository_url,
        "ref": package.commit, "subpath": package.subpath, "sourceSha256": package.tree_sha256,
    }


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate fields in plugin installation receipt")
    return value


def _manifest(plugin: Path) -> dict[str, Any]:
    path = plugin / _RECEIPT
    ensure_plain_path(path)
    if not path.is_file():
        raise ValueError("Missing plugin installation receipt")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("Unsupported plugin installation receipt schema")
    return value


def _sidecar_digest(plugin: Path) -> str | None:
    path = plugin / _SIDECAR
    if not path.exists() and not path.is_symlink():
        return None
    return dependency_tree_digest(path)


def verify_installed_plugin(plugin: Path, packages: dict[str, Package] | None = None) -> dict[str, Any]:
    package = (catalog() if packages is None else packages).get(plugin.name)
    if package is None:
        raise ValueError(f"Plugin {plugin.name!r} is not authorized by the bundled marketplace catalog")
    # The receipt is installation metadata. Only the bundled pin authenticates the source tree.
    verify_source(package, plugin, installed=True)
    receipt = _manifest(plugin)
    expected = _receipt_fields(package)
    if receipt.keys() != expected.keys() | {"sidecarSha256"} or any(receipt[key] != value for key, value in expected.items()):
        raise ValueError("Plugin installation receipt does not match the bundled marketplace pin")
    digest = receipt["sidecarSha256"]
    if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        raise ValueError("Invalid plugin dependency integrity digest")
    if _sidecar_digest(plugin) != digest:
        raise ValueError("Plugin dependency sidecar integrity check failed")
    return receipt


def _copy_verified_plugin(plugin: Path, destination: Path, packages: dict[str, Package],
                          *, copy_sidecar: bool = True) -> str | None:
    receipt = verify_installed_plugin(plugin, packages)
    ignore = None if copy_sidecar else shutil.ignore_patterns(_SIDECAR)
    shutil.copytree(plugin, destination, symlinks=True, ignore=ignore)
    if copy_sidecar:
        copied_receipt = verify_installed_plugin(destination, packages)
    else:
        verify_source(packages[plugin.name], destination, installed=True)
        copied_receipt = _manifest(destination)
    if copied_receipt != receipt:
        raise ValueError("Plugin installation changed while preparing its private load directory")
    return receipt["sidecarSha256"]


def _verified_snapshot(root: Path, packages: dict[str, Package] | None = None, *, copy_sidecars: bool = True):
    # Some OS temp paths themselves use aliases (for example macOS /var).
    temporary = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve(), prefix="mediaforge-plugin-load-")
    installed = Path(temporary.name) / "installed"
    installed.mkdir()
    failures = []
    sidecars = {}
    try:
        plugins = _installed(root)
        if plugins and packages is None:
            packages = catalog()
        for plugin in plugins:
            destination = installed / plugin.name
            try:
                digest = _copy_verified_plugin(plugin, destination, packages, copy_sidecar=copy_sidecars)
                if not copy_sidecars and digest is not None:
                    sidecars[plugin.name] = (plugin, digest)
            except (OSError, ValueError) as error:
                if destination.exists():
                    shutil.rmtree(destination)
                failures.append({"name": plugin.name, "managed": True, "loaded": False, "error": str(error)})
        return temporary, failures, sidecars
    except BaseException:
        temporary.cleanup()
        raise


def _activate_dependencies(paths: Iterator[Path]) -> None:
    for deps in paths:
        if deps.is_dir() and str(deps) not in sys.path:
            # The host wins if a later core update adds a formerly missing package.
            sys.path.append(str(deps))
    importlib.invalidate_caches()


def _manual_directory(root: Path) -> Path:
    from pymss.plugins.loader import get_plugins_dir

    with _PLUGIN_BOOTSTRAP:
        directory = get_plugins_dir().resolve()
        managed = root.parent.resolve()

        def overlaps(path: Path) -> bool:
            return path.is_relative_to(managed) or managed.is_relative_to(path)

        if overlaps(directory) or (directory.is_dir() and any(
            overlaps(child.resolve()) for child in directory.iterdir()
            if not child.name.startswith((".", "_")) and child.is_dir()
        )):
            raise ValueError("Managed marketplace paths cannot be loaded through the manual plugin directory")
        return directory


def _load_manual_plugins(root: Path) -> str | None:
    from pymss.plugins import ensure_plugins_loaded

    try:
        directory = _manual_directory(root)
    except (OSError, ValueError, RuntimeError) as error:
        return str(error)
    with _plugin_dir(directory):
        ensure_plugins_loaded()
    return None


def _load_snapshot(snapshot: Path, root: Path, dependencies: Iterator[Path] | None = None):
    from pymss.operations import default_runtime

    # Keep manually installed plugins and entry points separate from managed dependency search paths.
    sys.dont_write_bytecode = True
    manual_error = _load_manual_plugins(root)
    runtime = default_runtime()
    runtime._studio_manual_error = manual_error
    if dependencies is None:
        dependencies = (plugin / _SIDECAR for plugin in _installed(snapshot))
    _activate_dependencies(dependencies)
    with _plugin_dir(snapshot / "installed"):
        runtime.load_plugins()
    return runtime


def operation_runtime():
    """Load both existing user plugins and runtime-owned marketplace plugins."""
    # Bridge graph queries can bootstrap pymss concurrently in one process.
    with _PLUGIN_BOOTSTRAP, isolate_protocol_stdout():
        return _operation_runtime()


def _operation_runtime():
    from pymss.operations import default_runtime

    root = _root()
    runtime = default_runtime()
    if getattr(runtime, "_studio_root", None) == str(root):
        return runtime
    _recover_for_read(root)
    # Native wheels can stay mapped until Windows exits the worker. Use the
    # verified installed sidecars in place under the worker's runtime lease.
    snapshot, failures, sidecars = _verified_snapshot(root, copy_sidecars=False)
    snapshot_path = Path(snapshot.name)

    def dependencies() -> Iterator[Path]:
        for name, (plugin, digest) in sidecars.items():
            try:
                if _sidecar_digest(plugin) != digest:
                    raise ValueError("Plugin dependency sidecar integrity check failed")
            except (OSError, ValueError) as error:
                shutil.rmtree(snapshot_path / "installed" / name)
                failures.append({"name": name, "managed": True, "loaded": False, "error": str(error)})
            else:
                yield plugin / _SIDECAR

    try:
        runtime = _load_snapshot(snapshot_path, root, dependencies())
    except BaseException:
        snapshot.cleanup()
        raise
    atexit.register(snapshot.cleanup)
    runtime._studio_snapshot = snapshot  # Relative imports and sidecars may be used lazily.
    runtime._studio_failures = failures
    runtime._studio_root = str(root)
    return runtime


def plugin_reports(runtime, installed_dir: Path | None = None) -> list[dict[str, Any]]:
    from pymss.plugins import get_last_report

    report = get_last_report()
    manual = [
        {"name": item.name, "loaded": item.loaded, "error": item.error,
         "source": item.source, "path": item.path, "managed": False}
        for item in (report.results if report else [])
    ]
    if error := getattr(runtime, "_studio_manual_error", None):
        manual.append({"name": "<manual plugin directory>", "managed": False, "loaded": False, "error": error})
    managed = list(getattr(runtime, "_studio_failures", ()))
    manual_names = {item["name"] for item in manual}
    if installed_dir is None:
        snapshot = getattr(runtime, "_studio_snapshot", None)
        installed_dir = Path(snapshot.name) / "installed" if snapshot is not None else _root() / "installed"
    for item in runtime.plugin_report():
        item = {**item, "error": _describe_error(item.get("error"), manual_names), "managed": True}
        if item.get("loaded"):
            try:
                _validate_requirements(installed_dir / item["name"])
            except Exception as error:
                item.update(loaded=False, error=str(error))
        managed.append(item)
    return manual + managed


def _probe(root: Path) -> list[dict[str, Any]]:
    snapshot, failures, _ = _verified_snapshot(root)
    with snapshot:
        return _probe_snapshot(Path(snapshot.name), root) + failures


def _probe_snapshot(snapshot: Path, root: Path) -> list[dict[str, Any]]:
    """Import only a verified, private snapshot in the bounded probe child."""
    code = f"""
import json, sys
from pathlib import Path
from core import worker_plugins as p
from shared.worker_protocol import isolate_protocol_stdout
with isolate_protocol_stdout():
    snapshot = Path(sys.argv[1])
    runtime = p._load_snapshot(snapshot, Path(sys.argv[2]))
    reports = p.plugin_reports(runtime, snapshot / "installed")
print({_PROBE_MARKER!r} + json.dumps(reports))
"""
    with _PLUGIN_BOOTSTRAP:
        env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", ""),
    ]))
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(snapshot), str(root)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45, env=env,
        **hidden_subprocess_kwargs(),
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(_PROBE_MARKER):
            return json.loads(line[len(_PROBE_MARKER):])
    raise RuntimeError((result.stderr or "Plugin verification produced no report")[-3000:])


def _describe_error(error: str | None, manual_names: set[str] | None = None) -> str | None:
    if not error:
        return None
    return re.sub(
        r"already registered by [\'\"]_pymss_(runtime_)?plugin_([^\'\"]+)[\'\"]",
        lambda match: "already provided by " + (
            "manually installed plugin " if not match[1] or match[2] in (manual_names or set())
            else "marketplace plugin "
        ) + match[2],
        error,
    )


def _validate_requirements(plugin: Path) -> None:
    from pip._vendor.packaging.requirements import Requirement
    path = plugin / "pyproject.toml"
    if not path.exists():
        return
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    for text in project.get("dependencies", []):
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        try:
            version = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f"Missing plugin dependency: {requirement.name}") from None
        if requirement.specifier and version not in requirement.specifier:
            raise ValueError(f"Plugin requires {requirement}; current runtime provides {version}")


def validate_operation(runtime, operation: str) -> None:
    snapshot = getattr(runtime, "_studio_snapshot", None)
    if snapshot is None:
        return
    for item in runtime.plugin_report():
        if operation in item.get("operations", []):
            _validate_requirements(Path(snapshot.name) / "installed" / item["name"])


def _project_version(directory: Path) -> str | None:
    project = directory / "pyproject.toml"
    if not project.exists():
        return None
    return tomllib.loads(project.read_text(encoding="utf-8")).get("project", {}).get("version")


def _manual_plugin_fallback(root: Path) -> list[dict]:
    rows = []
    try:
        directory = _manual_directory(root)
    except (OSError, ValueError, RuntimeError) as error:
        return [{"name": "<manual plugin directory>", "managed": False, "loaded": False, "error": str(error)}]
    try:
        for child in sorted(directory.iterdir()) if directory.exists() else []:
            if child.is_dir() and not child.name.startswith((".", "_")):
                entry = next((child / name for name in ("__init__.py", "plugin.py") if (child / name).exists()), child / "__init__.py")
                rows.append({"name": child.name, "managed": False, "loaded": None, "path": str(entry)})
    except OSError:
        pass
    for entry in importlib.metadata.entry_points(group="pymss.plugins"):
        rows.append({"name": entry.name, "managed": False, "loaded": None, "source": "entrypoint"})
    return rows


def _manifest_contributions(plugin: Path) -> dict[str, Any]:
    """Views/declared contributions from the pymss protocol manifest, if any.

    Studio consumes these to offer plugin-provided pages; graphs and player
    entries are surfaced for diagnostics even before their Studio UI lands.
    """
    try:
        from pymss.plugins.manifest import read_manifest
    except ImportError:
        return {}
    try:
        manifest = read_manifest(plugin)
    except Exception:
        return {}
    if manifest is None:
        return {}
    contributions = manifest.to_dict()["contributions"]
    for entry in contributions.get("nodes", []):
        entry["pluginId"] = manifest.plugin_id
    for entry in contributions.get("views", []):
        entry["pluginId"] = manifest.plugin_id
        entry["entryPath"] = str((plugin / entry["entry"]).resolve())
    return contributions


def _inventory(root: Path, *, verify: bool = True) -> dict[str, Any]:
    _recover_for_read(root)
    installed = _installed(root)
    # Queries can overlap with other operation readers. Only recovery needs the lock.
    report_list: list[dict] = []
    verification_error = None
    if verify:
        try:
            report_list = _probe(root)
        except Exception as error:
            verification_error = str(error)
    reports = {item["name"]: item for item in report_list if item.get("managed")}
    rows = []
    try:
        packages = catalog() if installed else {}
    except (OSError, ValueError) as error:
        packages = {}
        verification_error = verification_error or str(error)
    for plugin in installed:
        try:
            meta, row_error = verify_installed_plugin(plugin, packages), None
        except Exception as error:
            meta, row_error = {}, str(error)
        report = reports.get(plugin.name, {})
        rows.append({
            "name": plugin.name, "version": meta.get("version"), "managed": True,
            "source": meta.get("source", "unknown"), "url": meta.get("url"),
            "path": str(plugin), "verified": row_error is None,
            "loaded": report.get("loaded") if row_error is None else False,
            "error": row_error or _describe_error(report.get("error")),
            "operations": report.get("operations", []) if row_error is None else [],
            "contributions": _manifest_contributions(plugin) if row_error is None else {},
        })
    if verification_error:
        report_list.extend(_manual_plugin_fallback(root))
    for report in report_list:
        if report.get("managed"):
            continue
        path = report.get("path")
        try:
            version = _project_version(Path(path).parent) if path else None
        except Exception:
            version = None
        rows.append({
            "name": report["name"], "version": version, "managed": False,
            "source": "manual", "url": None, "path": path or report.get("source", ""),
            "loaded": report.get("loaded"), "error": _describe_error(report.get("error")),
            "operations": None,
        })
    return {
        "installed": rows, "pluginsDir": str(root / "installed"),
        "verificationError": verification_error,
        "runtime": {"identity": root.name, "pythonPath": sys.executable},
    }


def cmd_plugin_marketplace(payload: dict[str, Any]) -> int:
    try:
        snapshot = _inventory(_root())
        try:
            snapshot["catalog"] = [
                {"name": package.name, "url": package.repository_url, "subpath": package.subpath,
                 "description": package.description, "tags": list(package.tags)}
                for package in catalog().values()
            ]
            snapshot["catalogError"] = None
        except Exception as error:
            snapshot["catalog"], snapshot["catalogError"] = [], str(error)
        emit("plugin_marketplace", snapshot)
        return 0
    except Exception as error:
        return emit_error("PLUGIN_MARKETPLACE_FAILED", str(error), recoverable=True)


def _stage(task_id: str | None, message: str) -> None:
    emit("plugin_stage", {"message": message}, task_id=task_id)


def _run_process(command: list[str], task_id: str | None, *, env: dict[str, str] | None = None) -> None:
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        **hidden_subprocess_kwargs(),
    )
    recent: list[str] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            message = line.strip()[-2000:]
            if message:
                recent = (recent + [message])[-8:]
                emit("plugin_log", {"message": message}, task_id=task_id)
        code = process.wait()
        if code:
            raise RuntimeError("\n".join(recent) or f"Dependency installer exited with code {code}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout:
            process.stdout.close()


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _install_dependencies(plugin: Path, root: Path, task_id: str | None, payload: dict) -> None:
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.version import Version

    project_file = plugin / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8")).get("project", {}) if project_file.exists() else {}
    python_constraint = project.get("requires-python")
    if python_constraint and Version(".".join(map(str, sys.version_info[:3]))) not in SpecifierSet(python_constraint):
        raise ValueError(f"Plugin requires Python {python_constraint}")
    requirements = project.get("dependencies", [])
    if not isinstance(requirements, list) or not all(isinstance(item, str) for item in requirements):
        raise ValueError("Plugin dependencies must be a list of Python requirements")
    requirements = [Requirement(item) for item in requirements]
    if any(item.url for item in requirements):
        raise ValueError("Plugin dependencies must use named wheel packages from the selected package index")
    if not requirements:
        return
    # Resolve against the selected interpreter, freezing host packages. Other installed
    # sidecars constrain versions but are not borrowed: uninstalling one cannot break another.
    versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            versions[_canonical(name)] = distribution.version
    for other in _installed(root):
        if other.name == plugin.name:
            continue
        for distribution in importlib.metadata.distributions(path=[str(other / _SIDECAR)]):
            name = _canonical(distribution.metadata["Name"])
            if name in versions and versions[name] != distribution.version:
                raise ValueError(f"Installed plugin dependency conflict: {name}")
            versions[name] = distribution.version
    constraints = plugin.parent / "constraints.txt"
    constraints.write_text("".join(f"{name}=={version}\n" for name, version in sorted(versions.items())), encoding="utf-8")
    report = plugin.parent / "pip-report.json"
    from core.worker_bootstrap import _resolve_pypi_mirror
    _, index = _resolve_pypi_mirror(str(payload.get("mirror") or "auto").strip().lower(), str(payload.get("locale") or ""))
    if not index:
        raise ValueError("Unrecognized plugin dependency package index")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = os.devnull
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
            "--no-compile", "--index-url", index]
    # dependency_mode = "declared-only" (in [tool.pymss.plugin]): transitive
    # metadata is ignored — the plugin's dependency list IS the full closure.
    # Used when an upstream wheel pins a host-incompatible version of a package
    # that is actually compatible (e.g. gamemlx's conservative numpy<2 pin).
    plugin_meta = tomllib.loads(project_file.read_text(encoding="utf-8")).get("tool", {}).get("pymss", {}).get("plugin", {}) if project_file.exists() else {}
    declared_only = str(plugin_meta.get("dependency_mode") or "").strip().lower() == "declared-only"
    _stage(task_id, "Resolving plugin dependencies against the active runtime")
    resolve_cmd = base + ["--dry-run", "--report", str(report), "--constraint", str(constraints)]
    if declared_only:
        resolve_cmd.append("--no-deps")
    _run_process(resolve_cmd + list(map(str, requirements)), task_id, env=env)
    resolved = json.loads(report.read_text(encoding="utf-8")).get("install", [])
    if any(item.get("is_direct") for item in resolved):
        raise ValueError("Plugin dependency resolution selected an unauthorized direct package source")
    selected = []
    for item in resolved:
        name = Requirement(item["metadata"]["name"])
        version = Version(item["metadata"]["version"])
        if name.extras or name.specifier or name.url or name.marker:
            raise ValueError("Invalid dependency name in the resolver report")
        if declared_only and _canonical(name.name) in versions:
            continue
        download = item.get("download_info", {})
        digest = download.get("archive_info", {}).get("hashes", {}).get("sha256", "")
        if (not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or not urlsplit(download.get("url", "")).path.lower().endswith(".whl")):
            raise ValueError(f"Dependency resolver did not identify a hashed wheel for {name.name}")
        selected.append(f"{name.name}=={version} --hash=sha256:{digest}")
    if selected:
        locked = plugin.parent / "verified-dependencies.txt"
        locked.write_text("\n".join(selected) + "\n", encoding="utf-8")
        _stage(task_id, "Installing additional dependencies")
        _run_process(base + ["--no-deps", "--require-hashes", "--target", str(plugin / _SIDECAR),
                             "--requirement", str(locked)], task_id, env=env)


def _publish(plugin: Path, root: Path, name: str) -> None:
    target = root / "installed" / name
    backup = root / "backups" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(backup)
    try:
        plugin.rename(target)
    except BaseException:
        if backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        # Publication has committed. A cleanup failure is retried on the next query.
        shutil.rmtree(backup, ignore_errors=True)


def _select_package(payload: dict, packages: dict[str, Package]) -> Package:
    name = payload.get("name")
    if not isinstance(name, str) or name not in packages:
        raise ValueError("Plugin source is not authorized by the bundled marketplace catalog")
    return packages[name]


def _install(root: Path, payload: dict, task_id: str | None) -> str:
    action = payload["action"]
    packages = catalog()
    package = _select_package(payload, packages)
    name = package.name
    current = root / "installed" / name
    if action in {"update", "repair"}:
        if not current.is_dir():
            raise ValueError("Plugin is not installed in this runtime")
    elif current.exists():
        raise ValueError("Plugin is already installed; use update or repair")

    transactions = root / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=transactions, prefix="install-") as transaction:
        plugin = Path(transaction) / name
        _stage(task_id, "Downloading and verifying the pinned plugin package")
        stage_package(package, plugin)
        verify_source(package, plugin)

        previous_failures = {
            item["name"] for item in _probe(root) if item.get("managed") and not item.get("loaded")
        } if _installed(root) else set()
        snapshot, _, _ = _verified_snapshot(root, packages)
        with snapshot:
            probe_root = Path(snapshot.name)
            # Resolve against verified peer metadata. Never read constraints from an invalid sidecar.
            _install_dependencies(plugin, probe_root, task_id, payload)
            verify_source(package, plugin, installed=True)
            receipt = {**_receipt_fields(package), "sidecarSha256": _sidecar_digest(plugin)}
            (plugin / _RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")

            _stage(task_id, "Verifying plugin registration")
            candidate = probe_root / "installed" / name
            if candidate.exists():
                shutil.rmtree(candidate)
            _copy_verified_plugin(plugin, candidate, packages)
            reports = {item["name"]: item for item in _probe_snapshot(probe_root, root) if item.get("managed")}
            if name not in reports:
                raise RuntimeError(f"Verified plugin {name!r} produced no registration report")
            failed = [item for item in reports.values() if not item.get("loaded")
                      and (item["name"] == name or item["name"] not in previous_failures)]
            if failed:
                raise RuntimeError("; ".join(f"{item['name']}: {item.get('error')}" for item in failed))
        verify_installed_plugin(plugin, packages)
        _stage(task_id, "Activating plugin")
        _publish(plugin, root, name)
        return name


def _check_updates(root: Path, task_id: str | None) -> dict:
    packages = catalog()
    updates = {}
    with runtime_lock(root / ".lock"):
        _recover(root)
        for plugin in _installed(root):
            _stage(task_id, f"Checking {plugin.name}")
            package = packages.get(plugin.name)
            if package is None:
                updates[plugin.name] = {"error": "No authorized source in the bundled marketplace catalog"}
                continue
            try:
                installed = verify_installed_plugin(plugin, packages)
            except (OSError, ValueError):
                updates[plugin.name] = {"local_version": None, "remote_version": package.version, "up_to_date": False}
            else:
                updates[plugin.name] = {"local_version": installed["version"], "remote_version": package.version, "up_to_date": True}
    return updates


def cmd_manage_plugin(payload: dict[str, Any]) -> int:
    task_id = str(payload.get("taskId") or "").strip() or None
    action = str(payload.get("action") or "").strip()
    try:
        if set(payload) - {"action", "name", "locale", "mirror", "requestId", "taskId"}:
            raise ValueError("Plugin source and content pins are selected by the bundled marketplace catalog")
        if action not in {"install", "update", "repair", "uninstall", "check_updates"}:
            raise ValueError("Unsupported plugin action")
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        updates = {}
        if action == "check_updates":
            updates = _check_updates(root, task_id)
        else:
            with runtime_lock(root / ".lock"):
                _recover(root)
                if action == "uninstall":
                    name = _name(payload.get("name"))
                    target = root / "installed" / name
                    if not target.is_dir():
                        raise ValueError("Plugin is not installed in this runtime")
                    _stage(task_id, "Removing plugin and its additional dependencies")
                    # Rename first so interrupted cleanup never leaves a half-loaded plugin.
                    trash = root / "transactions" / f"removed-{name}"
                    trash.parent.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(trash, ignore_errors=True)
                    target.rename(trash)
                    shutil.rmtree(trash)
                else:
                    _install(root, {**payload, "action": action}, task_id)
        snapshot = _inventory(root)
        snapshot["updates"] = updates
        emit("plugin_manage_result", snapshot, task_id=task_id)
        return 0
    except Exception as error:
        return emit_error("PLUGIN_MANAGE_FAILED", str(error), task_id=task_id, recoverable=True)
