"""Real hidden release children use only tiny disposable programs, never builds."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
NODE = shutil.which("node")


def ps(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL and NODE, "Windows release subprocess fixtures")
class HiddenReleaseProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mediaforge-release-process-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {key:value for key,value in os.environ.items()
                    if not any(part in key.upper() for part in ("KEY","PASSWORD","TOKEN","SECRET"))
                    and not key.upper().startswith(("MEDIAFORGE_","PYMSS_","TAURI_","VERSIONS_SIGNING_","SIGNPATH_"))
                    and key.upper() not in {"PYTHONHOME","PYTHONPATH"}}
        for name in ("HOME","USERPROFILE","APPDATA","LOCALAPPDATA","TEMP","TMP","TMPDIR",
                     "MEDIAFORGE_DATA_ROOT","MEDIAFORGE_MODEL_DIR","MEDIAFORGE_RUNTIME_ENVS_DIR","WEBVIEW2_USER_DATA_FOLDER"):
            path = self.root / name.lower()
            path.mkdir()
            self.env[name] = str(path)

    def invoke(self, body, *, ok=True):
        script = self.root / "fixture.ps1"
        script.write_text("$ErrorActionPreference='Stop'\n. " + ps(ROOT / "scripts/release-signing.ps1") + "\n" + body, encoding="utf-8-sig")
        result = subprocess.run([POWERSHELL,"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
                                cwd=self.root,env=self.env,capture_output=True,text=True,timeout=30,
                                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.assertEqual(result.returncode == 0,ok,result.stdout + result.stderr)
        return result

    @staticmethod
    def assert_reaped(pid):
        kernel = ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=(ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=(ctypes.c_void_p,ctypes.c_ulong)
        kernel.WaitForSingleObject.restype=ctypes.c_ulong
        kernel.CloseHandle.argtypes=(ctypes.c_void_p,)
        handle = kernel.OpenProcess(0x00100000,False,pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # The explicitly owned PID no longer exists.
                return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.WaitForSingleObject(handle,5000) != 0:
                raise AssertionError(f"owned fixture child {pid} was not reaped")
        finally:
            kernel.CloseHandle(handle)

    def project(self, javascript):
        root = self.root / "project"
        (root / "scripts").mkdir(parents=True)
        (root / "python/shared").mkdir(parents=True)
        (root / "python/shared/worker_subprocess.py").write_bytes((ROOT / "python/shared/worker_subprocess.py").read_bytes())
        (root / "scripts/tauri.mjs").write_text(javascript,encoding="utf-8")
        return root

    def test_leaf_has_no_console_and_preserves_quoted_arguments_full_dual_streams_and_exit_code(self):
        arguments = ["space and & $ % chars", 'quote"inside', "trailing\\"]
        code = """import ctypes,json,sys
kernel=ctypes.WinDLL('kernel32');kernel.GetConsoleWindow.restype=ctypes.c_void_p
assert not kernel.GetConsoleWindow(), 'leaf created a console'
assert sys.argv[1:] == json.loads(sys.argv[0]), sys.argv
sys.stdout.write('o'*2097152);sys.stderr.write('e'*2097152)
raise SystemExit(17)
"""
        # -c puts the supplied first argument at argv[1], so use a separate assertion payload.
        code = code.replace("sys.argv[1:] == json.loads(sys.argv[0])", "sys.argv[2:] == json.loads(sys.argv[1])")
        values = ["-I","-B","-c",code,json.dumps(arguments),*arguments]
        result = self.invoke("$result=Invoke-ReleaseProcess -FilePath " + ps(sys.executable) + " -Arguments @(" + ",".join(map(ps,values)) + ")\n"
                             "if ($result.ExitCode -ne 17 -or $result.Stdout.Length -ne 2097152 -or $result.Stderr.Length -ne 2097152) { throw 'process output/exit changed' }\n"
                             "'round-trip-ok'")
        self.assertIn("round-trip-ok",result.stdout)

    def test_bootstrap_probe_uses_the_hidden_process_helper(self):
        result = self.invoke("Get-ReleasePythonVersion -PythonPath " + ps(sys.executable))
        self.assertEqual(result.stdout.strip(),"3.12")

    def test_timeout_reaps_only_its_owned_leaf_and_keeps_diagnostics(self):
        ready = self.root / "leaf.json"
        code = "import json,os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()}));print('leaf-stdout',flush=True);print('leaf-stderr',file=sys.stderr,flush=True);time.sleep(20)"
        result = self.invoke("Invoke-ReleaseProcess -FilePath " + ps(sys.executable) + " -Arguments @('-I','-B','-c'," + ps(code) + "," + ps(ready) + ") -TimeoutMilliseconds 1500",ok=False)
        self.assertIn("timed out",result.stderr)
        self.assertIn("leaf-stdout",result.stdout)
        self.assertIn("leaf-stderr",result.stderr)
        self.assert_reaped(json.loads(ready.read_text())["pid"])

    def test_build_supervisor_uses_exact_entry_and_grandchildren_inherit_a_hidden_console(self):
        child = """import ctypes
k=ctypes.WinDLL('kernel32');k.GetConsoleWindow.restype=ctypes.c_void_p
user=ctypes.WinDLL('user32');user.IsWindowVisible.argtypes=(ctypes.c_void_p,);user.IsWindowVisible.restype=ctypes.c_int
window=k.GetConsoleWindow()
assert window and not user.IsWindowVisible(window), 'grandchild console was not inherited hidden'
print('hidden-grandchild-ok')
"""
        root = self.project("import {spawnSync} from 'node:child_process';\n"
                            "if(JSON.stringify(process.argv.slice(2))!==JSON.stringify(['build','--no-bundle','--ci']))throw Error('wrong CLI args');\n"
                            "if(process.env.CARGO_NET_OFFLINE!=='true')throw Error('network not disabled');\n"
                            f"const child=spawnSync({json.dumps(sys.executable)},['-I','-B','-c',{json.dumps(child)}],{{stdio:'inherit'}});process.exit(child.status??1);\n")
        result = self.invoke("Invoke-ReleaseNativeBuild -Root " + ps(root) + " -PythonPath " + ps(sys.executable) + " -Node " + ps(NODE) + " -TimeoutMilliseconds 15000")
        self.assertIn("hidden-grandchild-ok",result.stdout)

    def test_build_timeout_closes_its_job_and_reaps_node_and_unflagged_grandchild(self):
        child = "import json,os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid()}));time.sleep(25)"
        root = self.project("import fs from 'node:fs';import {spawn} from 'node:child_process';\n"
                            "fs.writeFileSync('node.json',JSON.stringify({pid:process.pid}));\n"
                            f"spawn({json.dumps(sys.executable)},['-I','-B','-c',{json.dumps(child)},'grandchild.json'],{{stdio:'inherit'}});\n"
                            "console.log('build-tree-started');setInterval(()=>{},1000);\n")
        result = self.invoke("Invoke-ReleaseNativeBuild -Root " + ps(root) + " -PythonPath " + ps(sys.executable) + " -Node " + ps(NODE) + " -TimeoutMilliseconds 3000",ok=False)
        self.assertIn("timed out",result.stderr)
        self.assertIn("build-tree-started",result.stdout)
        for name in ("node.json","grandchild.json"):
            self.assert_reaped(json.loads((root / name).read_text())["pid"])

    def test_default_node_resolution_selects_one_application_and_keeps_descendants_hidden(self):
        probe = """import ctypes
kernel=ctypes.WinDLL('kernel32');kernel.GetConsoleWindow.restype=ctypes.c_void_p
user=ctypes.WinDLL('user32');user.IsWindowVisible.argtypes=(ctypes.c_void_p,);user.IsWindowVisible.restype=ctypes.c_int
window=kernel.GetConsoleWindow()
assert window and not user.IsWindowVisible(window), 'default build child console was not hidden'
print('default-node-ok')
"""
        root = self.project("import {spawnSync} from 'node:child_process';\n"
                            f"const child=spawnSync({json.dumps(sys.executable)},['-I','-B','-c',{json.dumps(probe)}],{{stdio:'inherit'}});process.exit(child.status??1);\n")
        body = "function Get-Command { param($Name,$CommandType,$ErrorAction)\n"
        body += "if ($Name -ne 'node') { throw 'Unexpected executable lookup' };\n"
        body += "[PSCustomObject]@{Source=" + ps(NODE) + "};[PSCustomObject]@{Source=" + ps(self.root / "must-not-select-node.exe") + "}\n}\n"
        body += "Invoke-ReleaseNativeBuild -Root " + ps(root) + " -PythonPath " + ps(sys.executable) + " -TimeoutMilliseconds 15000"
        result = self.invoke(body)
        self.assertIn("default-node-ok",result.stdout)


if __name__ == "__main__":
    unittest.main()
