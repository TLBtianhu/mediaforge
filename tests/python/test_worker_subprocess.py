"""Noninteractive worker children must not open Windows console windows."""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

if __package__:
    from . import _bootstrap as _worker_test_bootstrap
else:
    import _bootstrap as _worker_test_bootstrap

from shared import worker_subprocess

NO_WINDOW = 0x08000000


class HiddenSubprocessOptionsTests(unittest.TestCase):
    def test_windows_uses_the_native_no_console_flag(self):
        with mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(worker_subprocess.subprocess, "CREATE_NO_WINDOW", NO_WINDOW, create=True):
            self.assertEqual(worker_subprocess.hidden_subprocess_kwargs(), {"creationflags": NO_WINDOW})

    def test_other_platforms_keep_native_process_options(self):
        with mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="posix")):
            self.assertEqual(worker_subprocess.hidden_subprocess_kwargs(), {})
            self.assertEqual(worker_subprocess.hidden_subprocess_kwargs(inherit_console=True), {})

    def test_direct_ensurepip_mode_gives_descendants_a_hidden_console(self):
        startup = SimpleNamespace(dwFlags=0, wShowWindow=-1)
        with mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(worker_subprocess.subprocess, "STARTUPINFO", return_value=startup, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "STARTF_USESHOWWINDOW", 1, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "SW_HIDE", 0, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "CREATE_NEW_CONSOLE", 16, create=True):
            options = worker_subprocess.hidden_subprocess_kwargs(inherit_console=True)
        self.assertEqual(options["creationflags"], 16)
        self.assertIs(options["startupinfo"], startup)
        self.assertEqual(startup.dwFlags, 1)
        self.assertEqual(startup.wShowWindow, 0)

    def test_all_worker_subprocess_call_sites_use_the_explicit_options(self):
        # Include paths that need models or platform SDKs to execute: auditing the actual
        # call nodes prevents a less common ffmpeg/pip/probe branch from escaping the rule.
        root = Path(__file__).resolve().parents[2] / "python"
        calls = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
                    continue
                if node.func.attr not in {"run", "Popen", "call", "check_call", "check_output"}:
                    continue
                calls.append((path.relative_to(root), node.lineno))
                with self.subTest(path=path.relative_to(root), line=node.lineno):
                    self.assertTrue(any(
                        keyword.arg is None and isinstance(keyword.value, ast.Call)
                        and isinstance(keyword.value.func, ast.Name)
                        and keyword.value.func.id == "hidden_subprocess_kwargs"
                        for keyword in node.keywords
                    ), "Child process does not apply the worker's Windows console options")
        self.assertGreater(len(calls), 20)


class HiddenVenvBuilderTests(unittest.TestCase):
    def test_windows_delegates_to_stdlib_with_a_hidden_inheritable_console(self):
        startup = SimpleNamespace(dwFlags=0, wShowWindow=-1)
        context = SimpleNamespace()
        with mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="nt")), \
             mock.patch.object(worker_subprocess.subprocess, "STARTUPINFO", return_value=startup, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "STARTF_USESHOWWINDOW", 1, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "SW_HIDE", 0, create=True), \
             mock.patch.object(worker_subprocess.subprocess, "CREATE_NEW_CONSOLE", 16, create=True), \
             mock.patch.object(venv.EnvBuilder, "_call_new_python", return_value="stdlib result") as call:
            result = worker_subprocess.HiddenVenvBuilder()._call_new_python(
                context, "-m", "ensurepip", stderr=subprocess.STDOUT, timeout=15,
            )
        self.assertEqual(result, "stdlib result")
        self.assertEqual(call.call_args.args, (context, "-m", "ensurepip"))
        self.assertEqual(call.call_args.kwargs["creationflags"], 16)
        self.assertIs(call.call_args.kwargs["startupinfo"], startup)
        self.assertEqual(startup.dwFlags, 1)
        self.assertEqual(startup.wShowWindow, 0)
        self.assertEqual(call.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(call.call_args.kwargs["timeout"], 15)

    def test_other_platforms_delegate_unchanged(self):
        context = SimpleNamespace()
        with mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="posix")), \
             mock.patch.object(venv.EnvBuilder, "_call_new_python") as call:
            worker_subprocess.HiddenVenvBuilder()._call_new_python(context, "-m", "ensurepip", stderr=subprocess.STDOUT)
        call.assert_called_once_with(context, "-m", "ensurepip", stderr=subprocess.STDOUT)

    @unittest.skipUnless(os.name == "nt", "Windows console visibility contract")
    def test_own_child_and_unflagged_grandchild_are_hidden_with_output_and_exit_codes(self):
        # Query only the console assigned to these children. No desktop enumeration,
        # window activation, visibility changes, or user-process interaction is used.
        visibility = """
import ctypes
from ctypes import wintypes
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetConsoleWindow.restype = wintypes.HWND
user = ctypes.WinDLL('user32', use_last_error=True)
user.IsWindowVisible.argtypes = [wintypes.HWND]
user.IsWindowVisible.restype = wintypes.BOOL
window = kernel.GetConsoleWindow()
console = {'attached': bool(window), 'visible': bool(user.IsWindowVisible(window))}
"""
        grandchild = visibility + "\nimport json, sys\nprint(json.dumps(console))\nsys.exit(9)\n"
        with tempfile.TemporaryDirectory(prefix="mediaforge-hidden-venv-") as raw:
            root = Path(raw)
            context = SimpleNamespace(env_exec_cmd=sys.executable, env_dir=str(root))
            for exit_code in (0, 7):
                with self.subTest(exit_code=exit_code):
                    report = root / f"console-{exit_code}.json"
                    child = visibility + f"""
import json, subprocess, sys
from pathlib import Path
# Deliberately no creationflags/startupinfo: ensurepip starts pip this way too.
result = subprocess.run([sys.executable, '-I', '-B', '-c', {grandchild!r}], capture_output=True, text=True, timeout=15)
payload = {{'child': console, 'grandchild': json.loads(result.stdout), 'grandchildExit': result.returncode}}
Path({str(report)!r}).write_text(json.dumps(payload), encoding='utf-8')
print(json.dumps(payload))
sys.exit({exit_code})
"""
                    builder = worker_subprocess.HiddenVenvBuilder()
                    if exit_code:
                        with self.assertRaises(subprocess.CalledProcessError) as caught:
                            builder._call_new_python(context, "-I", "-B", "-c", child, stderr=subprocess.STDOUT, timeout=25)
                        self.assertEqual(caught.exception.returncode, exit_code)
                        self.assertEqual(json.loads(caught.exception.output), json.loads(report.read_text(encoding="utf-8")))
                    else:
                        self.assertIsNone(builder._call_new_python(
                            context, "-I", "-B", "-c", child, stderr=subprocess.STDOUT, timeout=25,
                        ))
                    payload = json.loads(report.read_text(encoding="utf-8"))
                    self.assertEqual(payload["child"], {"attached": True, "visible": False})
                    self.assertEqual(payload["grandchild"], {"attached": True, "visible": False})
                    self.assertEqual(payload["grandchildExit"], 9)


class WorkerChildInvocationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(worker_subprocess, "os", SimpleNamespace(name="nt")))
        self.enterContext(mock.patch.object(worker_subprocess.subprocess, "CREATE_NO_WINDOW", NO_WINDOW, create=True))
        self.enterContext(mock.patch.object(worker_subprocess.subprocess, "CREATE_NEW_CONSOLE", 16, create=True))
        self.enterContext(mock.patch.object(worker_subprocess.subprocess, "STARTF_USESHOWWINDOW", 1, create=True))
        self.enterContext(mock.patch.object(worker_subprocess.subprocess, "SW_HIDE", 0, create=True))
        self.enterContext(mock.patch.object(worker_subprocess.subprocess, "STARTUPINFO", create=True,
                                          side_effect=lambda: SimpleNamespace(dwFlags=0, wShowWindow=-1)))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="mediaforge-child-options-")))

    def assert_hidden(self, call):
        self.assertEqual(call.kwargs["creationflags"], NO_WINDOW)
        self.assertNotIn("shell", call.kwargs)

    def test_audio_helpers_preserve_capture_and_file_output(self):
        from audio_tools import common, loudness, slicer
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="measurement")
        with mock.patch.object(common, "_ffmpeg_path", return_value="ffmpeg"), \
             mock.patch.object(common.subprocess, "run", return_value=completed) as run:
            common._run_ffmpeg(["-version"])
            common.run_process(["ffprobe", "input.wav"])
        for call in run.call_args_list:
            self.assert_hidden(call)
            self.assertTrue(call.kwargs["check"])
            self.assertTrue(call.kwargs["capture_output"])
        with mock.patch.object(loudness, "_ffmpeg_path", return_value="ffmpeg"), \
             mock.patch.object(loudness.subprocess, "run", return_value=completed) as run:
            self.assertEqual(loudness._run_measurement(Path("input.wav"), "volumedetect"), "measurement")
        self.assert_hidden(run.call_args)
        self.assertFalse(run.call_args.kwargs["check"])
        with mock.patch.object(slicer, "_ffmpeg_path", return_value="ffmpeg"), \
             mock.patch.object(slicer.subprocess, "run", return_value=completed) as run:
            slicer._decode_analysis_audio(Path("input.wav"), self.root / "samples.raw")
        self.assert_hidden(run.call_args)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stdout"].name, str(self.root / "samples.raw"))

    def test_media_probes_keep_json_stderr_and_failure_codes(self):
        from media_ops import ffmpeg, video_ops
        completed = subprocess.CompletedProcess([], 0, stdout='{"streams": []}', stderr="")
        with mock.patch.object(ffmpeg.subprocess, "run", return_value=completed) as run:
            self.assertEqual(ffmpeg._run_json(["ffprobe"]), {"streams": []})
            self.assertIs(video_ops._run(["ffmpeg"], "decode"), completed)
        for call in run.call_args_list:
            self.assert_hidden(call)
            self.assertEqual(call.kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(call.kwargs["stderr"], subprocess.PIPE)
        with mock.patch.object(video_ops.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 9, stdout="", stderr="bad frame")):
            with self.assertRaisesRegex(RuntimeError, "bad frame"):
                video_ops._run(["ffmpeg"], "decode")

    def test_thumbnail_process_keeps_output_suppression_and_timeout(self):
        from core import library
        with mock.patch.object(library, "thumbnail_dir", return_value=self.root), \
             mock.patch.object(library.ffmpeg, "ffmpeg_path", return_value="ffmpeg"), \
             mock.patch.object(library.subprocess, "run") as run:
            library._thumbnail(self.root / "input.mp4", {"duration": 1}, "video")
        self.assert_hidden(run.call_args)
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_runtime_probes_and_pip_bootstrap_preserve_streams_and_timeouts(self):
        from core import worker_bootstrap as bootstrap
        with mock.patch.object(bootstrap.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, stdout="", stderr="")) as run:
            self.assertTrue(bootstrap._runtime_python_works(Path("python")))
            self.assertTrue(bootstrap._runtime_pip_works(Path("python")))
            bootstrap._verify_runtime_audio(Path("python"))
            with mock.patch.object(bootstrap, "_runtime_pip_works", side_effect=[False, True]), \
                 mock.patch.object(bootstrap, "_emit"):
                bootstrap._ensure_runtime_pip(Path("python"), "fixture", lambda *args: None)
        for call in run.call_args_list[:-1]:
            self.assert_hidden(call)
            self.assertGreater(call.kwargs["timeout"], 0)
        self.assertEqual(run.call_args.kwargs["creationflags"], 16)
        self.assertEqual(run.call_args.kwargs["startupinfo"].dwFlags, 1)
        self.assertEqual(run.call_args.kwargs["startupinfo"].wShowWindow, 0)
        self.assertGreater(run.call_args.kwargs["timeout"], 0)
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        python = self.root / "python"
        python.write_bytes(b"fixture executable")
        with mock.patch.object(bootstrap.subprocess, "check_output", return_value='{"pymss": "1"}') as check:
            self.assertEqual(bootstrap._probe_python_package_versions(python, ["pymss"]), {"pymss": "1"})
        self.assert_hidden(check.call_args)

    def test_plugin_and_optional_installers_keep_logging_and_exit_failures(self):
        from core import worker_optional_packages as optional, worker_plugins as plugins
        for module, invoke in (
            (optional, lambda: optional._run_process(["python", "-m", "pip"], "funasr", "install")),
            (plugins, lambda: plugins._run_process(["python", "-m", "pip"], "fixture")),
        ):
            for code in (0, 7):
                with self.subTest(module=module.__name__, code=code):
                    process = mock.Mock(stdout=io.StringIO("install log\n"))
                    process.wait.return_value = code
                    process.poll.return_value = code
                    with mock.patch.object(module.subprocess, "Popen", return_value=process) as popen, \
                         mock.patch.object(module, "emit"):
                        if code:
                            with self.assertRaisesRegex(RuntimeError, "install log"):
                                invoke()
                        else:
                            invoke()
                    self.assert_hidden(popen.call_args)
                    self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
                    self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
                    process.wait.assert_called()

    def test_video_stream_process_keeps_pipes_drain_and_cancel(self):
        from media_ops import video_ops
        read_fd, write_fd = os.pipe()
        stderr = os.fdopen(read_fd, "rb", buffering=0)
        try:
            os.write(write_fd, b"diagnostic\n")
        finally:
            os.close(write_fd)
        process = mock.Mock(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=stderr)
        process.poll.return_value = None
        process.wait.return_value = 0
        try:
            with mock.patch.object(video_ops.subprocess, "Popen", return_value=process) as popen:
                stream = video_ops._StreamProcess(["ffmpeg"], "fixture", want_stdin=True, want_stdout=True)
                stream._drain.join(timeout=2)
                self.assertFalse(stream._drain.is_alive())
                self.assertEqual(stream.tail(), "diagnostic")
                stream.kill()
                stream.close()
        finally:
            stderr.close()
        self.assert_hidden(popen.call_args)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.PIPE)
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_kernel_child_keeps_protocol_pipe_and_wait(self):
        from core import operation_kernel as kernel_module
        kernel = kernel_module.OperationKernel(self.root)
        work = self.root / "fixture-work"
        work.mkdir()
        job = kernel_module.Job("job_hidden_child", "audio_tools", {}, work)
        process = mock.Mock(stdout=io.StringIO('{"type":"task.progress","payload":{"fraction":0.5}}\n'))
        process.wait.return_value = 0
        with mock.patch.object(kernel_module.subprocess, "Popen", return_value=process) as popen, \
             mock.patch.object(kernel_module.threading, "Thread"):
            kernel._run_worker(job)
        self.assert_hidden(popen.call_args)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertTrue(popen.call_args.kwargs["text"])
        self.assertEqual(job.status, "completed")
        process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
