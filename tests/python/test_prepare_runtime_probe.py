import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest.mock import patch


class PrepareRuntimeProbeTests(unittest.TestCase):
    def test_macos_bundled_state_records_torch_and_rejects_an_absent_installation(self):
        script = (Path(__file__).resolve().parents[2] / "scripts/prepare-python-runtime.sh").read_text(encoding="utf-8")
        recorder = re.search(r'runtime_python - "\$RUNTIME_ENVS_DIR" .*? <<\'PY\'\n(.*?)\nPY', script, re.S)
        self.assertIsNotNone(recorder)
        for torch_installed in (True, False):
            with self.subTest(torch_installed=torch_installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.json"
                manifest.write_text(json.dumps({"manifestVersion": "2026.10.2", "common": {"pymss": "pymss==2.1.8"}}), encoding="utf-8")

                def version(name):
                    if name == "torch" and not torch_installed:
                        raise metadata.PackageNotFoundError(name)
                    return {"pymss": "2.1.8", "torch": "2.7.1", "mlx": "0.32.4"}[name]

                with patch.object(sys, "argv", ["-", str(root), str(manifest)]), \
                        patch.object(metadata, "version", side_effect=version), \
                        patch.object(importlib.util, "find_spec", return_value=object()):
                    if not torch_installed:
                        with self.assertRaisesRegex(SystemExit, "torch"):
                            exec(compile(recorder.group(1), "bundled-state-recorder", "exec"), {})
                        self.assertFalse((root / "active-runtime.json").exists())
                        continue
                    exec(compile(recorder.group(1), "bundled-state-recorder", "exec"), {})
                state = json.loads((root / "active-runtime.json").read_text(encoding="utf-8"))
                self.assertEqual(state["torchVersion"], "2.7.1")
                self.assertEqual(state["packageVersions"]["torch"], "2.7.1")
                self.assertTrue(state["packages"]["torch"])

    def test_windows_preparation_only_builds_current_minimal_bootstrap(self):
        script = (Path(__file__).resolve().parents[2] / "scripts/prepare-python-runtime.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[CmdletBinding()]", script)
        self.assertIn("Assert-ReleasePython", script)
        self.assertIn("EXTERNALLY-MANAGED", script)
        for removed in ("InitialBackend", "RewriteRuntimeEnvConfigs", "TemplateRuntimeEnvConfigs", "Get-ManifestBackendExtras", "-m', 'venv'"):
            self.assertNotIn(removed, script)
        self.assertLess(script.index("Assert-ReleasePython"), script.index("robocopy"))
        self.assertNotIn("Remove-Item", script)
        self.assertNotIn("prune-python-runtime.ps1", script)

    def test_shell_runtime_python_sets_home_only_for_standalone_macos_python(self):
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is required for runtime preparation tests")
        script = (Path(__file__).resolve().parents[2] / "scripts/prepare-python-runtime.sh").read_text(encoding="utf-8")
        helper = re.search(r"^runtime_python\(\) \{\n.*?^\}", script, re.M | re.S)
        self.assertIsNotNone(helper)
        with tempfile.TemporaryDirectory(prefix="pymss-runtime-home-") as directory:
            interpreter = Path(directory) / "python stub.sh"
            interpreter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "${PYTHONHOME-unset}" "${PYTHONDONTWRITEBYTECODE-unset}" "$@"\n', encoding="utf-8")
            interpreter.chmod(0o755)
            for platform, expected_home in [("linux-gnu", "unset"), ("darwin23", "/standalone runtime")]:
                with self.subTest(platform=platform):
                    invocation = helper.group(0) + '\nPYTHONDONTWRITEBYTECODE=1 runtime_python "argument with spaces"\n'
                    result = subprocess.run(
                        [bash, "-c", invocation], capture_output=True, text=True, check=True,
                        env={**os.environ, "OSTYPE": platform, "RUNTIME_HOME": "/standalone runtime", "PY": interpreter.as_posix(), "PYTHONHOME": "/inherited home"},
                    )
                    self.assertEqual(result.stdout.splitlines(), [expected_home, "1", "argument with spaces"])

    def _run_shell_bootstrap_probe(self, *, missing_xmlrpc=False, broken_installer=False, final_prune=False):
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is required for runtime preparation tests")
        script = (Path(__file__).resolve().parents[2] / "scripts/prepare-python-runtime.sh").read_text(encoding="utf-8")
        version_helper = re.search(r"^verify_python_version\(\) \{\n.*?^\}", script, re.M | re.S)
        helper = re.search(r"^verify_bootstrap_runtime\(\) \{\n.*?^\}", script, re.M | re.S)
        self.assertIsNotNone(helper, "execute only the verification helper, never the runtime rebuild script")
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="pymss-bootstrap-probe-"))).resolve()
        library = root / "lib"
        library.mkdir()
        for name in ("requests", "socks"):
            (library / f"{name}.py").touch()
        pip = library / "pip"
        (pip / "_vendor").mkdir(parents=True)
        (pip / "__init__.py").touch()
        (pip / "_vendor/__init__.py").touch()
        packaging = importlib.util.find_spec("pip._vendor.packaging")
        self.assertIsNotNone(packaging)
        shutil.copytree(
            Path(packaging.origin).parent, pip / "_vendor/packaging",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Older pip vendors packaging's parser separately. Preserve that small
        # dependency so this fixture also works in the existing CPU test env.
        parser = importlib.util.find_spec("pip._vendor.pyparsing")
        if parser:
            origin = Path(parser.origin)
            if origin.name == "__init__.py":
                shutil.copytree(origin.parent, pip / "_vendor/pyparsing", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copyfile(origin, pip / "_vendor/pyparsing.py")
        if not missing_xmlrpc:
            shutil.copytree(
                Path(sysconfig.get_path("stdlib")) / "xmlrpc", library / "xmlrpc",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        (pip / "__main__.py").write_text('''
import os, sys
if sys.argv[1:] == ['--version']:
    print('pip 26.2.1')
elif sys.argv[1:] == ['install', '--help']:
    if os.environ.get('PREPARE_TEST_BROKEN_INSTALLER') == '1':
        raise RuntimeError('installer command is unavailable')
    import xmlrpc.client
    print('Usage: pip install [options]')
else:
    raise SystemExit('Unexpected package installer arguments')
''', encoding="utf-8")
        runner = root / "isolated-runtime.py"
        runner.write_text('''
import json, os, runpy, sys
from importlib.machinery import PathFinder
from pathlib import Path
library = Path(sys.argv[1]).resolve()
arguments = sys.argv[2:]
sys.path.insert(0, str(library))
class FixturePackages:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in {'requests', 'socks', 'pip', 'xmlrpc'}:
            return None
        spec = PathFinder.find_spec(fullname, path if path is not None else [str(library)])
        if spec is None or not Path(spec.origin).resolve().is_relative_to(library):
            raise ModuleNotFoundError('No module named ' + fullname)
        return spec
sys.meta_path.insert(0, FixturePackages())
try:
    if arguments == ['-']:
        exec(compile(sys.stdin.read(), '<bootstrap-probe>', 'exec'), {'__name__': '__main__'})
    elif arguments[:3] == ['-I', '-B', '-c'] and 'sys.version_info[:2]' in arguments[3]:
        print('3.12')
    elif arguments[:2] == ['-m', 'pip']:
        sys.argv = ['pip', *arguments[2:]]
        runpy.run_module('pip', run_name='__main__', alter_sys=True)
    else:
        raise SystemExit('Unexpected runtime invocation')
finally:
    module = sys.modules.get('xmlrpc.client')
    with open(os.environ['PREPARE_TEST_CALLS'], 'a', encoding='utf-8') as output:
        output.write(json.dumps({'arguments': arguments,
            'dontWriteBytecode': os.environ.get('PYTHONDONTWRITEBYTECODE'),
            'xmlrpcOrigin': getattr(module, '__file__', None)}) + '\\n')
''', encoding="utf-8")
        calls = root / "calls.jsonl"
        environment = {
            **os.environ, "PREPARE_TEST_PYTHON": Path(sys.executable).as_posix(),
            "PYTHONDONTWRITEBYTECODE": "0",
            "PREPARE_TEST_RUNNER": runner.as_posix(), "PREPARE_TEST_LIBRARY": library.as_posix(),
            "PREPARE_TEST_CALLS": str(calls),
            "PREPARE_TEST_BROKEN_INSTALLER": "1" if broken_installer else "0",
        }
        version = subprocess.run(
            [sys.executable, "-I", "-B", "-S", str(runner), str(library), "-m", "pip", "--version"],
            capture_output=True, text=True, env=environment,
        )
        wrapper = '''
runtime_python() {
  "$PREPARE_TEST_PYTHON" -I -B -S "$PREPARE_TEST_RUNNER" "$PREPARE_TEST_LIBRARY" "$@"
}
'''
        invocation = "\nverify_bootstrap_runtime\n"
        if final_prune:
            pruning = list(re.finditer(r"^bash [^\n]*prune-python-runtime\.sh[^\n]*\n", script, re.M))
            self.assertTrue(pruning)
            # Run only the final post-build tail. A faulty final pruning pass must
            # be detected even if bootstrap verification succeeded earlier.
            invocation = '''
RUNTIME_DIR="$PREPARE_TEST_LIBRARY"
bash() {
  rm -rf -- "$PREPARE_TEST_LIBRARY/xmlrpc"
}
''' + script[pruning[-1].start():]
        result = subprocess.run(
            [bash, "-c", "set -euo pipefail\n" + wrapper + "\nEXPECTED_PYTHON=3.12\n" + version_helper.group(0) + "\n" + helper.group(0) + invocation],
            capture_output=True, text=True, env=environment,
        )
        observations = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        return version, result, observations, library

    def test_shell_bootstrap_probe_rejects_missing_xmlrpc_even_when_pip_version_works(self):
        version, result, _calls, _library = self._run_shell_bootstrap_probe(missing_xmlrpc=True)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertNotEqual(result.returncode, 0, "a usable pip version cannot compensate for missing standard-library modules")
        self.assertIn("xmlrpc", result.stderr)

    def test_shell_bootstrap_probe_rejects_a_broken_install_command(self):
        version, result, _calls, _library = self._run_shell_bootstrap_probe(broken_installer=True)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertNotEqual(result.returncode, 0, "probe the installer entry point rather than only pip --version")
        self.assertIn("installer command is unavailable", result.stderr)

    def test_shell_bootstrap_probe_accepts_complete_fixture_and_checks_the_installer(self):
        version, result, calls, library = self._run_shell_bootstrap_probe()
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        installer = [call for call in calls if call["arguments"] == ["-m", "pip", "install", "--help"]]
        self.assertEqual(len(installer), 1, "successful validation must exercise pip install")
        self.assertEqual(installer[0]["dontWriteBytecode"], "1")
        imports = next(call for call in calls if call["arguments"] == ["-"])
        self.assertEqual(Path(imports["xmlrpcOrigin"]).resolve(), library / "xmlrpc/client.py")

    def test_shell_final_pruning_is_followed_by_bootstrap_validation(self):
        version, result, calls, _library = self._run_shell_bootstrap_probe(final_prune=True)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertNotEqual(result.returncode, 0, "the final pruning pass must not invalidate an earlier bootstrap check")
        self.assertIn("xmlrpc", result.stderr)
        self.assertTrue(any(call["arguments"] == ["-"] for call in calls))


if __name__ == "__main__":
    unittest.main()
