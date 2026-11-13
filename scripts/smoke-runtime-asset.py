"""Headless installation of a real runtime ZIP with isolated data and local HTTP."""
from __future__ import annotations

import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile

from media_smoke_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-exe", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    app = args.app_exe.resolve(strict=True)
    descriptor_file = args.descriptor.resolve(strict=True)
    release = (ROOT / "release").resolve(strict=True)
    if not app.is_relative_to(release) or not descriptor_file.is_relative_to(release / "runtime"):
        raise ValueError("Use the packaged release EXE and a local release/runtime asset descriptor")
    descriptor = json.loads(descriptor_file.read_text(encoding="utf-8"))
    if (set(descriptor) != {"assetId", "backend", "platform", "arch", "python", "sha256", "size", "urls"}
            or (descriptor["platform"], descriptor["arch"], descriptor["backend"], descriptor["python"])
            != ("windows", "x86_64", "cuda", "3.12")):
        raise ValueError("The runtime descriptor must declare the current Windows x64 CUDA Python 3.12 asset")
    archive = descriptor_file.with_suffix(".zip")
    if archive.stat().st_size != descriptor["size"] or digest(archive) != descriptor["sha256"]:
        raise ValueError("The local archive differs from its descriptor")
    with zipfile.ZipFile(archive) as source:
        interpreter_hash = hashlib.sha256(source.read("Scripts/python.exe")).hexdigest()
    parent = ROOT / ".tmp"
    parent.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="runtime-asset-", dir=parent))
    python = app.parent / "python-runtime/python.exe"
    env = isolated_environment(work / "data", python)
    for key in list(env):
        if re.search(r"KEY|PASSWORD|TOKEN|SECRET", key, re.I) or key.startswith(("TAURI_", "VERSIONS_SIGNING_", "SIGNPATH_")):
            env.pop(key, None)
    report = {"version": 1, "assetId": descriptor["assetId"], "archiveSha256": descriptor["sha256"],
              "workRoot": str(work), "guiLaunched": False, "mode": "headless-cli", "checks": []}
    corrupt = work / "corrupt.zip"
    corrupt.write_bytes(b"invalid-runtime")

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, request: str) -> str:
            return str({"/asset.zip": archive, "/corrupt.zip": corrupt}.get(request, work / "missing"))

        def log_message(self, *_args) -> None:
            pass

        def copyfile(self, source, outputfile) -> None:
            try:
                super().copyfile(source, outputfile)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # aria2 closes unused connections when the server has no Range support.
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    local = {**descriptor, "urls": [f"http://127.0.0.1:{server.server_port}/asset.zip"]}

    def invoke(mode: str, value: dict, success: bool) -> None:
        current = {**env, "MEDIAFORGE_SMOKE_RUNTIME_DESCRIPTOR": json.dumps(value)}
        result = subprocess.run([str(app), mode], cwd=work, env=current, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report["checks"].append({"mode": mode, "python": value["python"], "exitCode": result.returncode,
                                  "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]})
        if (result.returncode == 0) != success:
            raise AssertionError(f"Unexpected runtime smoke exit {result.returncode}: {result.stderr[-2000:]}")

    try:
        invoke("--smoke-install-runtime", local, True)
        runtime = work / "data/runtime-envs" / descriptor["assetId"]
        active = work / "data/runtime-envs/active-runtime.json"
        desired = work / "data/runtime-envs/desired-runtime.json"
        marker = json.loads((runtime / ".asset-id").read_text(encoding="utf-8"))
        assert marker["schemaVersion"] == 2 and marker["python"] == "3.12"
        assert marker["sha256"] == descriptor["sha256"]
        assert json.loads(active.read_text(encoding="utf-8"))["assetId"] == descriptor["assetId"]
        assert digest(runtime / "Scripts/python.exe") == interpreter_hash
        pointer_hash, desired_hash = digest(active), digest(desired)
        invoke("--smoke-install-runtime", {**local, "python": "3.11"}, False)
        assert digest(active) == pointer_hash and digest(desired) == desired_hash
        invoke("--smoke-install-runtime", {**local, "platform": "linux"}, False)
        invoke("--smoke-activate-runtime", {**local, "arch": "aarch64"}, False)
        assert digest(active) == pointer_hash and digest(desired) == desired_hash
        bad = {**local, "assetId": descriptor["assetId"] + "-bad", "size": corrupt.stat().st_size,
               "sha256": "0" * 64, "urls": [f"http://127.0.0.1:{server.server_port}/corrupt.zip"]}
        invoke("--smoke-install-runtime", bad, False)
        assert digest(active) == pointer_hash
        assert not (work / "data/runtime-envs" / bad["assetId"] / "Scripts/python.exe").exists()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        invoke("--smoke-activate-runtime", local, True)  # HTTP is now unavailable; reuse must not download.
        assert digest(active) == pointer_hash
        code = """import json,sys,torch
from pathlib import Path
assert sys.version_info[:2]==(3,12)
assert sys.prefix==sys.base_prefix and Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve()
print(json.dumps({'python':sys.version.split()[0],'torch':torch.__version__,'cuda':torch.version.cuda,'selfContained':True}))
"""
        probe = subprocess.run([str(runtime / "Scripts/python.exe"), "-I", "-B", "-c", code, str(runtime)],
                               env=env, cwd=work, capture_output=True, text=True, check=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report["installedRuntime"] = json.loads(probe.stdout)
        report["interpreterUnchanged"] = True
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        # The only recursive cleanup target was freshly created by this script.
        resolved = work.resolve(strict=True)
        if resolved.parent != parent.resolve(strict=True) or not resolved.name.startswith("runtime-asset-"):
            raise RuntimeError("Refusing unexpected runtime verification cleanup target")
        shutil.rmtree(resolved)
        report["workRootRemoved"] = not work.exists()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["installedRuntime"]))
    print(f"PASS: headless runtime install, ABI/hash rejection and offline reuse; report {args.report}")


if __name__ == "__main__":
    main()
