"""Install/upgrade/uninstall a unique temporary identity using real staged payloads.

Never executes a production installer, an application, or a staged interpreter.
The two existing stage directories are read-only compiler inputs, not copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
OWNER_FILE = ".mediaforge-candidate-test.json"
PRODUCTION_APP_ID = "{34A687CC-EE1D-401D-8FE1-98E4061C32CB}"
UNINSTALL_PREFIX = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
PAYLOAD_FILES = (
    "MediaForge.exe", "manifest/protocol.json", "manifest/runtime.json", "manifest/library-schema.json",
    "python/plugin-sdk/pymss-sdk.js", "python/plugin-sdk/pymss-sdk.css",
)


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def no_reparse(path: Path, *, tree: bool = False) -> None:
    path = path.absolute()
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"refusing linked test path: {entry}")
    if tree and path.is_dir():
        for current, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                entry = Path(current) / name
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError(f"refusing linked test tree entry: {entry}")


def owned_work(report: dict) -> Path:
    work = Path(report["workRoot"]).absolute()
    nonce = report["nonce"]
    if not re.fullmatch(r"[0-9a-f]{32}", nonce) or work.name != f"MediaForge-candidate-{nonce}":
        raise ValueError("invalid candidate work identity")
    if work.parent != Path(tempfile.gettempdir()).absolute():
        raise ValueError("candidate work directory must be an immediate system TEMP child")
    no_reparse(work, tree=True)
    marker = json.loads((work / OWNER_FILE).read_text(encoding="utf-8"))
    for key in ("nonce", "workRoot", "installDir", "appId", "appName", "registryKey"):
        if marker.get(key) != report.get(key):
            raise ValueError(f"candidate ownership marker mismatch: {key}")
    if Path(report["installDir"]).absolute() != work / "install":
        raise ValueError("candidate install directory escaped its owned work directory")
    if report["registryKey"] != rf"Software\MediaForgeCandidateTests\{nonce}":
        raise ValueError("candidate registry key is not isolated")
    if report["appId"] == PRODUCTION_APP_ID or not re.fullmatch(r"\{[0-9A-F-]{36}\}", report["appId"]):
        raise ValueError("candidate AppId is not isolated")
    return work


def isolated_environment(work: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith(("MEDIAFORGE_", "PYMSS_", "TAURI_SIGNING_", "VERSIONS_SIGNING_", "SIGNPATH_", "HF_", "HUGGING_FACE_", "MODELSCOPE_"))
           and name.upper() not in {"PYTHONPATH", "PYTHONHOME", "WEBVIEW2_USER_DATA_FOLDER"}}
    install = work / "install"
    data, models, runtime = install / "data", install / "models", install / "runtime-envs"
    python = install / "app/python-runtime/python.exe"
    for prefix in ("MEDIAFORGE", "PYMSS_STUDIO"):
        for suffix, path in {
            "DATA_ROOT": data, "DATA_DIR": data, "MODEL_DIR": models, "RUNTIME_ENVS_DIR": runtime,
            "ACTIVE_RUNTIME_FILE": runtime / "active-runtime.json", "PYTHON": python,
            "DEFAULT_OUTPUT_DIR": data / "outputs",
        }.items():
            env[f"{prefix}_{suffix}"] = str(path)
    env["PYMSS_MODEL_DIR"] = str(models)
    env["MEDIAFORGE_RUNTIME_PYTHON"] = env["PYMSS_STUDIO_BOOTSTRAP_PYTHON"] = str(python)
    profile, cache = work / "profile", work / "cache"
    paths = {
        "HOME": profile, "USERPROFILE": profile, "APPDATA": profile / "AppData/Roaming",
        "LOCALAPPDATA": profile / "AppData/Local", "WEBVIEW2_USER_DATA_FOLDER": work / "webview",
        "TEMP": work / "tmp", "TMP": work / "tmp", "TMPDIR": work / "tmp",
        "XDG_CACHE_HOME": cache, "XDG_CONFIG_HOME": work / "config", "XDG_DATA_HOME": work / "state",
        "HF_HOME": cache / "huggingface", "HF_HUB_CACHE": cache / "huggingface/hub",
        "HUGGINGFACE_HUB_CACHE": cache / "huggingface/hub", "HF_ASSETS_CACHE": cache / "huggingface/assets",
        "HF_XET_CACHE": cache / "huggingface/xet", "TRANSFORMERS_CACHE": cache / "transformers",
        "TORCH_HOME": cache / "torch", "TORCH_EXTENSIONS_DIR": cache / "torch-extensions",
        "MODELSCOPE_CACHE": cache / "modelscope", "PIP_CACHE_DIR": cache / "pip",
        "TRITON_CACHE_DIR": cache / "triton", "MPLCONFIGDIR": cache / "matplotlib", "NUMBA_CACHE_DIR": cache / "numba",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path)
    env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(profile))
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", MODELSCOPE_OFFLINE="1")
    return env


def test_installer_source(source: str, identity: dict, label: str) -> str:
    if source.count('#define MyAppName "MediaForge"') != 1:
        raise ValueError("installer product declaration changed; review isolation before running")
    if source.count(f"AppId={{{PRODUCTION_APP_ID}") != 1:
        raise ValueError("installer AppId declaration changed; review isolation before running")
    source = source.replace('#define MyAppName "MediaForge"', f'#define MyAppName "{identity["appName"]}"')
    source = source.replace(f"AppId={{{PRODUCTION_APP_ID}", f'AppId={{{identity["appId"]}')
    source = source.replace(r"Software\MediaForge", identity["registryKey"])
    # Disable every shortcut and post-install executable rather than relying
    # on the user-task choices or the silent flag alone.
    for section in ("Icons", "Run"):
        source, count = re.subn(rf"(?ims)^\[{section}\]\s*\n.*?(?=^\[|\Z)", f"[{section}]\n\n", source)
        if count != 1:
            raise ValueError(f"installer [{section}] boundary changed")
    source = re.sub(r"(?m)^SetupArchitecture=.*\n", "", source)  # Local Inno 6 compatibility.
    source, count = re.subn(r"(?m)^CloseApplications=.*$", "CloseApplications=no", source)
    if count != 1:
        raise ValueError("installer application-close policy changed")
    source = re.sub(r"(?m)^OutputBaseFilename=.*$", f"OutputBaseFilename=candidate-{label}", source)
    if PRODUCTION_APP_ID in source or re.search(r'Subkey:\s*"Software\\MediaForge"', source, re.I):
        raise ValueError("production installation identity leaked into test installer")
    if "LZMADictionarySize=65536" not in source or "LZMANumBlockThreads=2" not in source:
        raise ValueError("installer compression is not memory bounded")
    return source


def run_hidden(command: list[str], *, env: dict[str, str], log: Path, timeout: int = 600) -> None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    print(f"Running {Path(command[0]).name}; log: {log}", flush=True)
    with log.open("wb") as output:
        result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                creationflags=flags, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} exited {result.returncode}; see {log}")


def write_report(path: Path, report: dict) -> None:
    no_reparse(path)
    temporary = path.with_suffix(".writing.json")
    no_reparse(temporary)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def registry_values(key: str) -> dict[str, str] | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            result = {}
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(handle, index)
                except OSError:
                    return result
                result[name] = value
                index += 1
    except FileNotFoundError:
        return None


def verify_identity(report: dict, version: str) -> None:
    pointer = registry_values(report["registryKey"])
    uninstall = registry_values(UNINSTALL_PREFIX + report["appId"] + "_is1")
    if not pointer or Path(pointer.get("InstallDir", "")).absolute() != Path(report["installDir"]) / "app":
        raise RuntimeError("test-only install registry pointer is missing or incorrect")
    if not uninstall or uninstall.get("DisplayName") != report["appName"] + " " + version:
        raise RuntimeError("test-only uninstall display identity is missing or incorrect")
    if uninstall.get("DisplayVersion") != version:
        raise RuntimeError("test-only uninstall version did not advance")
    if Path(uninstall.get("InstallLocation", "")).absolute() != Path(report["installDir"]):
        raise RuntimeError("test-only uninstall install location is incorrect")


def asset_snapshot(install: Path) -> dict[str, str]:
    result = {}
    for name in ("data", "models", "runtime-envs"):
        directory = install / name
        no_reparse(directory, tree=True)
        if directory.exists():
            for current, _, names in os.walk(directory, followlinks=False):
                for filename in names:
                    file = Path(current) / filename
                    result[file.relative_to(install).as_posix()] = digest(file)
    return result


def seed_assets(install: Path, nonce: str) -> dict[str, str]:
    timestamp = int(time.time() * 1000)
    fixtures = {
        "data/settings/app.json": json.dumps({"themeMode": "dark", "locale": "zh-CN", "animationsEnabled": False,
                                                "candidateUpgradeSentinel": nonce}, ensure_ascii=False).encode(),
        "data/settings/media-workflows.json": json.dumps({"version": 1, "presets": [{
            "id": "candidate_upgrade_preserved", "name": "Candidate upgrade preserved",
            "definition": {"version": 1, "modality": "image", "steps": [{"capability": "upscale",
                "bindings": {"upscaler": "media:real-esrgan-x2"}, "params": {"scale": 2, "tile": 0}}]},
            "createdAt": timestamp, "updatedAt": timestamp}]}).encode(),
        "models/candidate-fixture/preservation-only.bin": bytes(range(256)) * 16,
        "runtime-envs/candidate-preservation-marker.json": json.dumps({"fixture": True, "nonce": nonce}).encode(),
    }
    for relative, content in fixtures.items():
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return asset_snapshot(install)


def running_test_processes(install: Path) -> list[int]:
    """Only query image paths; never stop any process, including test processes."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    pids = (wintypes.DWORD * 32768)()
    used = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        raise OSError("cannot check candidate process lifetime")
    prefix = str(install.absolute()).casefold() + os.sep
    active = []
    for pid in pids[:used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) and buffer.value.casefold().startswith(prefix):
                active.append(pid)
        finally:
            kernel.CloseHandle(handle)
    return active


def wait_test_processes(install: Path, timeout: float = 30) -> list[int]:
    deadline = time.monotonic() + timeout
    while True:
        active = running_test_processes(install)
        if not active or time.monotonic() >= deadline:
            return active
        time.sleep(0.1)


def remove_owned_work(report: dict) -> None:
    work = owned_work(report)

    def readonly_retry(function, path, error):
        target = Path(path).absolute()
        if not target.is_relative_to(work) or function not in (os.unlink, os.remove, os.rmdir):
            raise error[1]
        no_reparse(target)
        info = target.lstat()
        if not isinstance(error[1], PermissionError) or not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
            raise error[1]
        # Inno may mark temporary Windows shell folders read-only. Clear only
        # that attribute, never alter ACLs or retry an unrelated access failure.
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        function(path)

    # Keep the ownership marker until all children have been removed, so a
    # locked/readonly subtree cannot leave an unverifiable partial work root.
    for entry in list(work.iterdir()):
        if entry.name == OWNER_FILE:
            continue
        no_reparse(entry, tree=entry.is_dir())
        if entry.is_dir():
            shutil.rmtree(entry, onerror=readonly_retry)
        else:
            try:
                entry.unlink()
            except PermissionError:
                readonly_retry(os.unlink, str(entry), sys.exc_info())
    marker = work / OWNER_FILE
    marker.unlink()
    try:
        work.rmdir()
    except OSError:
        marker.write_text(json.dumps({key: report[key] for key in
            ("nonce", "workRoot", "installDir", "appId", "appName", "registryKey")}), encoding="utf-8")
        raise


def cleanup(report_path: Path, report: dict) -> None:
    no_reparse(report_path)
    if report.get("status") == "failed":
        # Windows subprocess timeouts can leave compiler/installer descendants.
        # A failed run needs inspection; never guess that its process tree ended.
        raise ValueError("failed candidate runs require inspection; automatic uninstall/work cleanup is disabled")
    work = owned_work(report)
    if report_path.absolute().is_relative_to(work):
        raise ValueError("candidate cleanup report must remain outside the removable work directory")
    install = Path(report["installDir"])
    active = running_test_processes(work)
    if active:
        raise RuntimeError(f"close candidate processes before cleanup: {active}")
    uninstaller = install / "unins000.exe"
    before = asset_snapshot(install)
    if uninstaller.is_file():
        if digest(uninstaller) != report.get("uninstallerSha256"):
            raise ValueError("candidate uninstaller does not match the retained test identity")
        verify_identity(report, report["installedVersion"])
        env = isolated_environment(work)
        run_hidden([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={work / 'uninstall.log'}"],
                   env=env, log=work / "uninstall-process.log", timeout=180)
        deadline = time.monotonic() + 10
        while uninstaller.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if (install / "app").exists():
            raise RuntimeError("candidate uninstaller left application payload")
        if asset_snapshot(install) != before:
            raise RuntimeError("candidate silent uninstall changed preserved data/model/runtime assets")
        if registry_values(report["registryKey"]) is not None or registry_values(UNINSTALL_PREFIX + report["appId"] + "_is1") is not None:
            raise RuntimeError("candidate uninstall left its test registration")
        report["uninstallPreservedAssets"] = before
    elif report.get("installedVersion"):
        if (report.get("uninstallPreservedAssets") != before or uninstaller.exists() or (install / "app").exists()
                or registry_values(report["registryKey"]) is not None
                or registry_values(UNINSTALL_PREFIX + report["appId"] + "_is1") is not None):
            raise RuntimeError("completed candidate uninstall is not verified; refusing automatic work cleanup")
    active = wait_test_processes(work)
    if active:
        raise RuntimeError(f"candidate processes remain after uninstall: {active}")
    # Validate the exact owned root again after the subprocess has exited.
    owned_work(report)
    report["status"] = "uninstalled-pending-temp-cleanup"
    write_report(report_path, report)
    remove_owned_work(report)
    report["status"] = "cleaned"
    report["cleanedAt"] = time.time()
    write_report(report_path, report)
    print(f"Uninstalled test identity and removed owned TEMP root; report: {report_path}", flush=True)


def execute(args: argparse.Namespace) -> dict:
    if os.name != "nt":
        raise RuntimeError("real candidate installation requires Windows")
    stages = {"A": args.stage_a.absolute(), "B": args.stage_b.absolute()}
    for label, stage in stages.items():
        no_reparse(stage, tree=True)
        # The previous release predates SDK staging. Verify its available
        # contract without modifying it; B must supply the complete new one.
        required = PAYLOAD_FILES if label == "B" else PAYLOAD_FILES[:3]
        for relative in required:
            if not (stage / relative).is_file():
                raise ValueError(f"missing real staged input: {stage / relative}")
        for forbidden in ("data", "models", "python-runtime/runtime-envs", "mediaforge.portable"):
            if (stage / forbidden).exists():
                raise ValueError(f"stage contains forbidden mutable input: {stage / forbidden}")
    if not args.iscc.is_file():
        raise ValueError(f"Inno Setup compiler does not exist: {args.iscc}")
    for version in (args.version_a, args.version_b):
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("candidate test versions must be numeric major.minor.patch")
    report_path = args.report.absolute() if args.report else None
    if report_path:
        if not report_path.is_relative_to(ROOT / ".tmp") or report_path.suffix != ".json":
            raise ValueError("candidate reports must be JSON files inside the repository .tmp directory")
        no_reparse(report_path)
        if report_path.exists():
            raise ValueError("candidate report already exists; retain it and choose a new report path")
    nonce = uuid.uuid4().hex
    work = Path(tempfile.gettempdir()).absolute() / f"MediaForge-candidate-{nonce}"
    no_reparse(work)
    work.mkdir()
    report_path = report_path or ROOT / ".tmp" / f"release-candidate-upgrade-{time.strftime('%Y%m%d')}-{nonce[:8]}.json"
    no_reparse(report_path)
    report = {"schemaVersion": 1, "nonce": nonce, "workRoot": str(work), "installDir": str(work / "install"),
              "appId": "{" + str(uuid.uuid4()).upper() + "}", "appName": f"MediaForge Candidate Test {nonce[:8]}",
              "registryKey": rf"Software\MediaForgeCandidateTests\{nonce}", "status": "created", "reportPath": str(report_path),
              "applicationLaunched": False, "keepInstalled": bool(args.keep_installed),
              "stageA": str(stages["A"]), "stageB": str(stages["B"]), "versionA": args.version_a, "versionB": args.version_b,
              "sourcePayloadHashes": {label: {name: digest(stage / name) for name in PAYLOAD_FILES if (stage / name).is_file()} for label, stage in stages.items()}}
    (work / OWNER_FILE).write_text(json.dumps({key: report[key] for key in
        ("nonce", "workRoot", "installDir", "appId", "appName", "registryKey")}), encoding="utf-8")
    write_report(report_path, report)
    try:
        owned_work(report)
        if registry_values(report["registryKey"]) is not None or registry_values(UNINSTALL_PREFIX + report["appId"] + "_is1") is not None:
            raise RuntimeError("generated test registry identity already exists")
        env = isolated_environment(work)
        # Persist only the isolated path/flag overlay, never inherited secrets.
        overlay = {key: value for key, value in env.items() if key not in os.environ or os.environ[key] != value}
        report["environmentFile"] = str(work / "launch-environment.json")
        report["environmentClearPrefixes"] = ["MEDIAFORGE_", "PYMSS_", "TAURI_SIGNING_", "VERSIONS_SIGNING_", "SIGNPATH_", "HF_", "HUGGING_FACE_", "MODELSCOPE_"]
        report["environmentClearNames"] = ["PYTHONPATH", "PYTHONHOME", "WEBVIEW2_USER_DATA_FOLDER"]
        Path(report["environmentFile"]).write_text(json.dumps(overlay, indent=2), encoding="utf-8")
        source = (ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8-sig")
        for label, version in (("A", args.version_a), ("B", args.version_b)):
            out = work / f"compiled-{label}"
            out.mkdir()
            script = work / f"candidate-{label}.iss"
            script.write_text(test_installer_source(source, report, label), encoding="utf-8-sig")
            compiler_env = dict(env, MEDIAFORGE_VERSION=version, MEDIAFORGE_PORTABLE_DIR=str(stages[label]),
                                MEDIAFORGE_INSTALLER_OUTPUT=str(out), MEDIAFORGE_PACKAGE_SUFFIX="windows-x64-candidate-test",
                                MEDIAFORGE_INSTALLER_ICON=str(ROOT / "src-tauri/icons/icon.ico"))
            run_hidden([str(args.iscc), "/Qp", str(script)], env=compiler_env, log=work / f"compile-{label}.log")
            installer = out / f"candidate-{label}.exe"
            if not installer.is_file():
                raise RuntimeError("isolated compiler did not produce the expected installer")
            run_hidden([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER",
                        f'/DIR={report["installDir"]}', f"/LOG={work / ('install-' + label + '.log')}"],
                       env=env, log=work / f"install-{label}-process.log", timeout=240)
            report["installedVersion"] = version
            report["uninstallerSha256"] = digest(Path(report["installDir"]) / "unins000.exe")
            report["status"] = f"{label.lower()}-installed"
            write_report(report_path, report)
            verify_identity(report, version)
            actual = digest(Path(report["installDir"]) / "app/MediaForge.exe")
            if actual != report["sourcePayloadHashes"][label]["MediaForge.exe"]:
                raise RuntimeError(f"installed {label} executable does not match its real staged payload")
            if label == "A":
                report["assetsBeforeUpgrade"] = seed_assets(Path(report["installDir"]), nonce)
            else:
                after = asset_snapshot(Path(report["installDir"]))
                if after != report["assetsBeforeUpgrade"]:
                    raise RuntimeError("upgrade changed existing configuration/model/runtime fixture assets")
                report["assetsAfterUpgrade"] = after
                for name, expected in report["sourcePayloadHashes"]["B"].items():
                    if digest(Path(report["installDir"]) / "app" / name) != expected:
                        raise RuntimeError(f"installed candidate contract differs from B stage: {name}")
            write_report(report_path, report)
        for label, stage in stages.items():
            if {name: digest(stage / name) for name in report["sourcePayloadHashes"][label]} != report["sourcePayloadHashes"][label]:
                raise RuntimeError("installer compilation modified a read-only source stage")
        report["upgradePassed"] = True
        report["installedExe"] = str(Path(report["installDir"]) / "app/MediaForge.exe")
        write_report(report_path, report)
        print(f"A -> B upgrade passed; report: {report_path}", flush=True)
        if args.keep_installed:
            print(f"Retained candidate: {report['installedExe']}", flush=True)
        else:
            cleanup(report_path, report)
        return report
    except Exception as error:
        report["failure"] = str(error)
        report["statusAtFailure"] = report["status"]
        report["status"] = "failed"
        write_report(report_path, report)
        print(f"Retained failed test for inspection; report: {report_path}", file=sys.stderr, flush=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-a", type=Path)
    parser.add_argument("--stage-b", type=Path)
    parser.add_argument("--version-a")
    parser.add_argument("--version-b")
    parser.add_argument("--iscc", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe")
    parser.add_argument("--report", type=Path, help="Small JSON evidence file; defaults to the repository .tmp directory")
    parser.add_argument("--keep-installed", action="store_true", help="Retain the B test identity for isolated UI checks")
    parser.add_argument("--cleanup-report", type=Path, help="Uninstall only the retained test identity, verify asset preservation, then remove its owned TEMP root")
    args = parser.parse_args(argv)
    if args.cleanup_report:
        if os.name != "nt":
            parser.error("candidate uninstallation requires Windows")
        no_reparse(args.cleanup_report)
        report = json.loads(args.cleanup_report.read_text(encoding="utf-8"))
        cleanup(args.cleanup_report, report)
    else:
        if not all((args.stage_a, args.stage_b, args.version_a, args.version_b)):
            parser.error("Provide both explicit source stages and their versions")
        execute(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
