"""Headless same-source/stage installer smoke with a unique disposable test identity."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("installer_smoke_candidate", Path(__file__).with_name("smoke-release-candidate.py"))
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)
REQUIRED = (*CANDIDATE.PAYLOAD_FILES, "manifest/execution-quota.json", "python/worker.py", "python/runtime-manifest.json",
            "python-runtime/python.exe", "python-runtime/python312.dll", "bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/aria2c.exe")


def isolated_environment(work: Path) -> dict[str, str]:
    return {key: value for key, value in CANDIDATE.isolated_environment(work).items()
            if not re.search(r"KEY|PASSWORD|TOKEN|SECRET", key, re.I)}


def production_registry_snapshot() -> dict:
    def serializable(value):
        if isinstance(value, bytes):
            return {"binaryHex": value.hex()}
        if isinstance(value, dict):
            return {key: serializable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serializable(item) for item in value]
        return value
    snapshots = {}
    for key in (r"Software\MediaForge", CANDIDATE.UNINSTALL_PREFIX + CANDIDATE.PRODUCTION_APP_ID + "_is1"):
        value = CANDIDATE.registry_values(key)
        snapshots[key] = None if value is None else hashlib.sha256(
            json.dumps(serializable(value), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return snapshots


def file_hashes(directory: Path) -> dict[str, str]:
    CANDIDATE.no_reparse(directory, tree=True)
    return {path.relative_to(directory).as_posix(): CANDIDATE.digest(path)
            for current, _, files in os.walk(directory, followlinks=False) for name in files
            for path in (Path(current) / name,)}


def validate_stage(stage: Path) -> dict[str, str]:
    CANDIDATE.no_reparse(stage)
    if not stage.is_dir():
        raise ValueError("an explicit staged package directory is required")
    for name in ("data", "models", "runtime-envs", "python-runtime/runtime-envs", "mediaforge.portable"):
        if (stage / name).exists():
            raise ValueError(f"installer stage contains mutable/portable state: {name}")
    hashes = file_hashes(stage)
    for name in REQUIRED:
        if name not in hashes:
            raise ValueError(f"installer stage is missing {name}")
    if (stage / "python-runtime/python311.dll").exists():
        raise ValueError("installer stage contains an obsolete Python ABI")
    manifest = json.loads((stage / "python/runtime-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("python") != "3.12":
        raise ValueError("installer smoke requires the current Python 3.12 bootstrap")
    if len(list((stage / "bin/vcrt").glob("*.dll"))) < 6:
        raise ValueError("installer stage is missing its VC runtime DLLs")
    return hashes


def capture_logs(work: Path, report: dict) -> None:
    tails = report.setdefault("logTails", {})
    logs = ("compile.log", "install-process.log", "install.log", "worker-health.log")
    for name in (*logs, *(f"previous-{name}" for name in logs), "uninstall-process.log", "uninstall.log"):
        path = work / name
        CANDIDATE.no_reparse(path)
        if path.is_file():
            with path.open("rb") as log:
                log.seek(max(0, path.stat().st_size - 65536))
                tails[name] = log.read().decode("utf-8", "replace")


def installed_hashes(install: Path) -> dict[str, str]:
    hashes = file_hashes(install / "app")
    if any(name.casefold().startswith("integrations/") for name in hashes):
        raise RuntimeError("installed DSH integrations must be outside app/")
    hashes.update({f"integrations/{name}": digest for name, digest in file_hashes(install / "integrations").items()})
    return hashes


def check_installed(report: dict, expected: dict[str, str]) -> dict[str, str | int]:
    CANDIDATE.owned_work(report)
    install = Path(report["installDir"])
    actual = installed_hashes(install)
    for name, digest in expected.items():
        if actual.get(name) != digest:
            raise RuntimeError(f"installed app/ payload differs or is missing: {name}")
    unexpected = actual.keys() - expected.keys()
    if unexpected:
        raise RuntimeError(f"installed payload contains files outside the stage: {sorted(unexpected)[:8]}")
    if (install / "MediaForge.exe").exists() or (install / "app/mediaforge.portable").exists():
        raise RuntimeError("installer must use only the current install/app layout")
    if len(list((install / "app/bin/vcrt").glob("*.dll"))) < 6:
        raise RuntimeError("installed VC runtime DLLs are missing")
    return {"fileCount": len(actual), "sha256": hashlib.sha256(
        json.dumps(actual, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}


def seed_assets(install: Path, nonce: str) -> dict[str, str]:
    data = install / "data"
    sample = data / "media" / f"candidate-{nonce[:8]}.wav"
    runtime = install / "runtime-envs" / "candidate-preservation-fixture"
    runtime_python = runtime / "Scripts/python.exe"
    for directory in (sample.parent, runtime_python.parent, data / "settings", install / "models/candidate-fixture"):
        directory.mkdir(parents=True, exist_ok=True)
    (data / "settings/app.json").write_text(json.dumps({
        "version": 1, "themeMode": "dark", "locale": "zh-CN", "animationsEnabled": False,
        "maxConcurrentSeparations": 2,
    }), encoding="utf-8")
    (install / "models/candidate-fixture/preservation-only.bin").write_bytes(bytes.fromhex(nonce) * 256)
    with wave.open(str(sample), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 800)

    contract = json.loads((ROOT / "manifest/library-schema.json").read_text(encoding="utf-8"))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    database = sqlite3.connect(data / contract["databaseFile"])
    try:
        with database:
            database.execute(f"PRAGMA user_version = {int(contract['schemaVersion'])}")
            database.execute(f"PRAGMA application_id = {int(contract['applicationId'])}")
            database.execute("PRAGMA journal_mode = DELETE")
            for entry in contract["objects"]:
                if entry["create"]:
                    database.execute(entry["sql"])
            database.execute("INSERT INTO assets (path, name, kind, format, size, duration, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                             (str(sample), sample.name, "audio", "wav", sample.stat().st_size, 0.1, stamp, stamp))
    finally:
        database.close()

    # These exercise install preservation only. The worker health probe always
    # runs the installed bootstrap and never executes this runtime sample.
    shutil.copyfile(install / "app/python-runtime/python.exe", runtime_python)
    runtime_dll = runtime / "python312.dll"
    shutil.copyfile(install / "app/python-runtime/python312.dll", runtime_dll)
    archive = install.parent / "preservation-fixture-runtime.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in (runtime_python, runtime_dll):
            output.write(path, path.relative_to(runtime).as_posix())
    (runtime / ".asset-id").write_text(json.dumps({
        "schemaVersion": 2, "assetId": runtime.name, "sha256": CANDIDATE.digest(archive), "python": "3.12",
    }), encoding="utf-8")
    (runtime.parent / "active-runtime.json").write_text(json.dumps({
        "pythonPath": str(runtime_python), "assetId": runtime.name, "backend": "cuda",
    }), encoding="utf-8")
    return CANDIDATE.asset_snapshot(install)


def check_worker_health(install: Path, env: dict[str, str], log: Path) -> None:
    python, worker = install / "app/python-runtime/python.exe", install / "app/python/worker.py"
    CANDIDATE.run_hidden([str(python), "-B", str(worker), "health"], env=env, log=log, timeout=60)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (isinstance(event, dict) and event.get("type") == "health"
                and isinstance(event.get("payload"), dict) and event["payload"].get("ok") is True):
            return
    raise RuntimeError("installed bootstrap worker health did not succeed")


def cleanup_owned(report_path: Path, report: dict, env: dict[str, str]) -> None:
    """Reuse candidate ownership guards; only completed, verified subprocesses enter here."""
    work = CANDIDATE.owned_work(report)
    if report_path.absolute().is_relative_to(work):
        raise ValueError("smoke report must remain outside the removable work directory")
    if CANDIDATE.running_test_processes(work):
        raise RuntimeError("owned test processes remain; retaining the work directory")
    install = Path(report["installDir"])
    uninstaller = install / "unins000.exe"
    before = CANDIDATE.asset_snapshot(install)
    if report.get("installedVersion"):
        if not uninstaller.is_file() or CANDIDATE.digest(uninstaller) != report.get("uninstallerSha256"):
            raise RuntimeError("verified test uninstaller is missing or changed; retaining work")
        CANDIDATE.verify_identity(report, report["installedVersion"])
        CANDIDATE.run_hidden([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                              f"/LOG={work / 'uninstall.log'}"], env=env, log=work / "uninstall-process.log", timeout=180)
        deadline = time.monotonic() + 10
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        CANDIDATE.owned_work(report)
        if uninstaller.exists() or installed_hashes(install) or (install / "MediaForge.exe").exists():
            raise RuntimeError("test uninstall did not remove the application")
        if CANDIDATE.asset_snapshot(install) != before:
            raise RuntimeError("test uninstall changed preserved fixture assets")
        if CANDIDATE.registry_values(report["registryKey"]) is not None or CANDIDATE.registry_values(
                CANDIDATE.UNINSTALL_PREFIX + report["appId"] + "_is1") is not None:
            raise RuntimeError("test uninstall left its registration")
        report["uninstallPreservedAssets"] = before
    elif uninstaller.exists() or CANDIDATE.registry_values(report["registryKey"]) is not None or CANDIDATE.registry_values(
            CANDIDATE.UNINSTALL_PREFIX + report["appId"] + "_is1") is not None:
        raise RuntimeError("unverified installation state; retaining work for inspection")
    if CANDIDATE.running_test_processes(work):
        raise RuntimeError("owned test processes remain after uninstall")
    if production_registry_snapshot() != report["productionRegistryBefore"]:
        raise RuntimeError("production registry changed; no automatic restoration or work cleanup")
    capture_logs(work, report)
    report["status"] = "uninstalled-pending-temp-cleanup"
    CANDIDATE.write_report(report_path, report)
    CANDIDATE.remove_owned_work(report)
    report["workRootRemoved"] = True


def execute(args: argparse.Namespace) -> dict:
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("installer smoke requires an explicit Windows Python 3.12 interpreter")
    stage, compiler = args.stage.absolute(), args.iscc.absolute()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        raise ValueError("version must be numeric major.minor.patch")
    previous = args.previous_stage.absolute() if args.previous_stage else None
    if bool(previous) != bool(args.previous_version):
        raise ValueError("previous stage and previous version must be provided together")
    if previous is not None and not re.fullmatch(r"\d+\.\d+\.\d+", args.previous_version):
        raise ValueError("previous version must be numeric major.minor.patch")
    expected = validate_stage(stage)
    previous_expected = validate_stage(previous) if previous is not None else None
    if previous_expected == expected:
        raise ValueError("in-place upgrade requires a distinct previous stage payload")
    CANDIDATE.no_reparse(compiler)
    if not compiler.is_file():
        raise ValueError("explicit Inno Setup compiler is missing")
    installer_input = args.installer.absolute() if args.installer else None
    installer_hash = None
    if installer_input:
        CANDIDATE.no_reparse(installer_input)
        installer_hash = CANDIDATE.digest(installer_input)
    report_path = args.report.absolute() if args.report else ROOT / ".tmp" / f"installer-smoke-{uuid.uuid4().hex}.json"
    if not report_path.is_relative_to(ROOT / ".tmp") or report_path.suffix != ".json":
        raise ValueError("report must be a new JSON file inside the repository .tmp directory")
    CANDIDATE.no_reparse(report_path)
    if report_path.exists():
        raise ValueError("report already exists; choose a new report path")
    before_registry = production_registry_snapshot()
    nonce = uuid.uuid4().hex
    work = Path(tempfile.gettempdir()).absolute() / f"MediaForge-candidate-{nonce}"
    CANDIDATE.no_reparse(work)
    work.mkdir()
    report = {"schemaVersion": 1, "nonce": nonce, "workRoot": str(work), "installDir": str(work / "install"),
              "appId": "{" + str(uuid.uuid4()).upper() + "}", "appName": f"MediaForge Candidate Test {nonce[:8]}",
              "registryKey": rf"Software\MediaForgeCandidateTests\{nonce}", "status": "created",
              "applicationLaunched": False, "productionInstallerExecuted": False, "workRootRemoved": False,
              "validationMode": "same source and stage recompiled with unique test identity; bootstrap worker health only",
              "stage": str(stage), "version": args.version, "sourcePayloadHashes": expected,
              "productionInstaller": str(installer_input) if installer_input else None,
              "productionInstallerSha256": installer_hash, "productionRegistryBefore": before_registry}
    if previous is not None:
        report.update(previousStage=str(previous), previousVersion=args.previous_version,
                      previousPayloadHashes=previous_expected)
    (work / CANDIDATE.OWNER_FILE).write_text(json.dumps({key: report[key] for key in
        ("nonce", "workRoot", "installDir", "appId", "appName", "registryKey")}), encoding="utf-8")
    failure, may_cleanup, env = None, True, None
    try:
        CANDIDATE.owned_work(report)
        env = isolated_environment(work)
        if CANDIDATE.registry_values(report["registryKey"]) is not None or CANDIDATE.registry_values(
                CANDIDATE.UNINSTALL_PREFIX + report["appId"] + "_is1") is not None:
            raise RuntimeError("generated test registry identity already exists")
        source = CANDIDATE.test_installer_source((ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8-sig"), report, "smoke")
        install = Path(report["installDir"])
        uninstaller = install / "unins000.exe"
        inputs = [("", stage, args.version, expected)]
        if previous is not None:
            inputs.insert(0, ("previous-", previous, args.previous_version, previous_expected))
        for prefix, input_stage, version, hashes in inputs:
            script, output = work / f"{prefix}candidate-smoke.iss", work / f"{prefix}compiled"
            script.write_text(source, encoding="utf-8-sig")
            output.mkdir()
            compiler_env = dict(env, MEDIAFORGE_VERSION=version, MEDIAFORGE_PORTABLE_DIR=str(input_stage),
                                MEDIAFORGE_INSTALLER_OUTPUT=str(output), MEDIAFORGE_PACKAGE_SUFFIX="windows-x64-smoke-test",
                                MEDIAFORGE_INSTALLER_ICON=str(ROOT / "src-tauri/icons/icon.ico"))
            CANDIDATE.write_report(report_path, report)
            # A failed compiler/installer can leave descendants; only a completed,
            # verified installation may be uninstalled automatically afterwards.
            may_cleanup = False
            CANDIDATE.run_hidden([str(compiler), "/Qp", str(script)], env=compiler_env, log=work / f"{prefix}compile.log")
            installer = output / "candidate-smoke.exe"
            if not installer.is_file():
                raise RuntimeError("compiler did not produce the unique test installer")
            CANDIDATE.no_reparse(installer)
            install_log = work / f"{prefix}install.log"
            CANDIDATE.run_hidden([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                                  "/CURRENTUSER", "/TASKS=dshbridge", f"/DIR={install}", f"/LOG={install_log}"],
                                 env=env, log=work / f"{prefix}install-process.log", timeout=180)
            CANDIDATE.verify_identity(report, version)
            CANDIDATE.no_reparse(uninstaller)
            report.update(installedVersion=version, uninstallerSha256=CANDIDATE.digest(uninstaller))
            may_cleanup = True
            prior = bool(prefix)
            report["previousInstalledPayload" if prior else "installedPayload"] = check_installed(report, hashes)
            check_worker_health(install, env, work / f"{prefix}worker-health.log")
            report["previousWorkerHealth" if prior else "workerHealth"] = "passed"
            if prior:
                report["assetsBeforeUpgrade"] = seed_assets(install, nonce)
            elif previous is not None:
                after = CANDIDATE.asset_snapshot(install)
                if after != report["assetsBeforeUpgrade"]:
                    raise RuntimeError("in-place upgrade changed preserved settings, models, media or runtime samples")
                report.update(upgradePassed=True, assetsAfterUpgrade=after, assetsBeforeUninstall=after)
            else:
                report["assetsBeforeUninstall"] = seed_assets(install, nonce)
        for _, input_stage, _, hashes in inputs:
            if file_hashes(input_stage) != hashes:
                raise RuntimeError(f"compiler or install changed the read-only stage: {input_stage}")
    except Exception as error:
        failure, may_cleanup = error, may_cleanup and not isinstance(error, subprocess.TimeoutExpired)
        report["error"] = str(error)
    finally:
        try:
            report["productionRegistryUnchanged"] = production_registry_snapshot() == before_registry
            if not report["productionRegistryUnchanged"]:
                may_cleanup = False
                raise RuntimeError("production registry changed; preserving evidence without automatic restoration")
            if may_cleanup and env is not None:
                cleanup_owned(report_path, report, env)
                report["status"] = "failed-cleaned" if failure else "cleaned"
            else:
                report["status"] = "failed-retained"
        except Exception as error:
            report["status"], report["cleanupError"] = "failed-retained", str(error)
            failure = failure or error
        if work.exists():
            try:
                capture_logs(work, report)
            except (OSError, ValueError) as error:
                report["status"], report["logCaptureError"] = "failed-retained", str(error)
                failure = failure or error
        if installer_input is not None:
            report["productionInstallerUnchanged"] = False
            try:
                CANDIDATE.no_reparse(installer_input)
                if CANDIDATE.digest(installer_input) != installer_hash:
                    raise RuntimeError("read-only production installer artifact changed during validation")
                report["productionInstallerUnchanged"] = True
            except (OSError, ValueError, RuntimeError) as error:
                report["status"] = "failed-cleaned" if report["workRootRemoved"] else "failed-retained"
                report["productionInstallerError"] = str(error)
                failure = failure or error
        CANDIDATE.write_report(report_path, report)
    if failure:
        raise RuntimeError(f"installer smoke failed; report: {report_path}: {failure}") from failure
    upgrade = " / in-place upgrade" if previous is not None else ""
    print(f"PASS: unique-identity install{upgrade} / full stage / worker health / silent uninstall; report: {report_path}", flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--previous-stage", type=Path)
    parser.add_argument("--previous-version")
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--installer", type=Path, help="Read-only production artifact metadata; never executed")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        execute(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
