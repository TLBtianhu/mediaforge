"""Keep worker trees and their reap fences alive across executor failure."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from functools import lru_cache
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.worker_runtime_lock import runtime_lock
from shared.runtime_lease import RuntimeLeaseError, runtime_lease
from shared.worker_protocol import emit_error
from shared.worker_subprocess import hidden_subprocess_kwargs


READY = "MEDIAFORGE_SUPERVISOR_READY\n"
GO = b"\x01"
POLL_SECONDS = 0.05


@lru_cache(maxsize=1)
def _job_api():
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimit), ("io_counters", ctypes.c_uint64 * 6),
            ("memory_limits_and_peaks", ctypes.c_size_t * 4),
        ]

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("times", ctypes.c_int64 * 4), ("page_faults", wintypes.DWORD),
            ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": (wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR),
        "OpenJobObjectW": (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR),
        "SetInformationJobObject": (wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD),
        "QueryInformationJobObject": (wintypes.BOOL, wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)),
        "AssignProcessToJobObject": (wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE),
        "GetCurrentProcess": (wintypes.HANDLE,),
        "TerminateJobObject": (wintypes.BOOL, wintypes.HANDLE, wintypes.UINT),
        "CloseHandle": (wintypes.BOOL, wintypes.HANDLE),
    }
    for name, (result, *arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype = result
        function.argtypes = arguments
    return ctypes, kernel, ExtendedLimit, Accounting


class _WindowsJob:
    def __init__(self):
        ctypes, kernel, limits_type, _ = _job_api()
        self.name = f"Local\\MediaForgeWorker-{uuid.uuid4().hex}"
        ctypes.set_last_error(0)
        self.handle = kernel.CreateJobObjectW(None, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                raise OSError("Worker job name is already in use")
            limits = limits_type()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def active(self) -> int:
        ctypes, kernel, _, accounting_type = _job_api()
        accounting = accounting_type()
        if not kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.active

    def terminate(self) -> None:
        ctypes, kernel, _, _ = _job_api()
        if not kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        ctypes, kernel, _, _ = _job_api()
        if not kernel.CloseHandle(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _run_windows_worker(name: str, worker: list[str]) -> int:
    # Even the venv launcher can start the real interpreter before Popen returns.
    # The real interpreter must join the job itself before executing worker code.
    if os.read(sys.stdin.fileno(), 1) != GO:
        return 1
    ctypes, kernel, _, _ = _job_api()
    handle = kernel.OpenJobObjectW(0x0001, False, name)  # JOB_OBJECT_ASSIGN_PROCESS
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    # The bootstrap uses -S so environment site hooks and .pth files cannot
    # run before joining the job. Restore normal 3.12 venv/site discovery now.
    import site

    site.main()
    sys.argv = worker
    sys.path[0] = str(Path(worker[0]).resolve().parent)
    runpy.run_path(worker[0], run_name="__main__")
    return 0


def _enable_subreaping() -> None:
    if not sys.platform.startswith("linux"):
        return
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class ProcessTree:
    def __init__(self):
        self.job = _WindowsJob() if os.name == "nt" else None
        self.child: subprocess.Popen[bytes] | None = None
        self._group_signalled = False
        if self.job is None:
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)
            _enable_subreaping()

    def start(self, worker: list[str]) -> None:
        command = [sys.executable, "-B", *worker]
        if self.job is not None:
            command = [sys.executable, "-B", "-S", __file__, "--windows-worker", self.job.name, "--", *worker]
        env = os.environ.copy()
        env.pop("MEDIAFORGE_PARENT_PIPE", None)
        env.pop("PYMSS_STUDIO_AUDIO_TEMP_LAUNCHER_PIDS", None)
        if env.get("PYMSS_STUDIO_AUDIO_TEMP_STATE"):
            # Rust registers its direct child. On Windows a venv launcher may
            # be that child, with this interpreter one level below it.
            env["PYMSS_STUDIO_AUDIO_TEMP_LAUNCHER_PIDS"] = f"{os.getpid()},{os.getppid()}"
        if self.job is None:
            env["MEDIAFORGE_PARENT_PIPE"] = "1"
        self.child = subprocess.Popen(
            command, stdin=subprocess.PIPE, env=env,
            start_new_session=self.job is None, **hidden_subprocess_kwargs(),
        )
        if self.job is not None:
            assert self.child.stdin is not None
            self.child.stdin.write(GO)
            self.child.stdin.flush()

    def exited(self) -> bool:
        assert self.child is not None
        if self.job is None and hasattr(os, "waitid") and hasattr(os, "WNOWAIT"):
            # Keep the session leader's PID reserved until we signal its group.
            state = os.waitid(os.P_PID, self.child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            return state is not None and state.si_pid != 0
        return self.child.poll() is not None

    def terminate(self) -> None:
        child = self.child
        if child is None:
            return
        if child.stdin is not None and not child.stdin.closed:
            try:
                child.stdin.close()
            except BrokenPipeError:
                pass
        if self.job is not None:
            while True:
                exited = child.poll() is not None
                active = self.job.active()
                if exited and not active:
                    return
                if active:
                    self.job.terminate()
                time.sleep(POLL_SECONDS)
        else:
            group = child.pid
            if not self._group_signalled:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._group_signalled = True
            child.wait()
            if sys.platform.startswith("linux"):
                # Orphaned descendants in this group were reparented to the
                # subreaper. Wait on them directly, never signal a reused PGID.
                try:
                    while True:
                        os.waitpid(-group, 0)
                except ChildProcessError:
                    return
            else:
                while True:
                    try:
                        os.killpg(group, 0)
                    except ProcessLookupError:
                        return
                    time.sleep(POLL_SECONDS)

    def close(self) -> None:
        if self.job is not None:
            self.job.close()


def _watch_owner(pipe: int, stopped: threading.Event) -> None:
    try:
        os.read(pipe, 1)
    except OSError:
        pass
    finally:
        stopped.set()


def _finish(tree: ProcessTree) -> None:
    last_error = None
    while True:
        try:
            tree.terminate()
            return
        except (OSError, subprocess.SubprocessError) as error:
            if str(error) != last_error:
                last_error = str(error)
                try:
                    print(f"[worker supervisor] tree cleanup is pending: {error}", file=sys.stderr, flush=True)
                except OSError:
                    pass  # The failed owner may also have closed the diagnostics pipe.
            time.sleep(POLL_SECONDS)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fence", type=Path)
    parser.add_argument("--owner-fence", type=Path, action="append", default=[])
    parser.add_argument("--windows-worker")
    parser.add_argument("worker", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    worker = args.worker[1:] if args.worker[:1] == ["--"] else args.worker
    if not worker:
        parser.error("worker command is required")
    if args.windows_worker is not None:
        return _run_windows_worker(args.windows_worker, worker)

    with ExitStack() as fences:
        if args.fence is not None:
            fences.enter_context(runtime_lock(args.fence))
        for path in args.owner_fence:
            fences.enter_context(runtime_lock(path))
        tree = ProcessTree()
        try:
            sys.stdout.buffer.write(READY.encode("ascii"))
            sys.stdout.buffer.flush()
            pipe = sys.stdin.fileno()
            if os.read(pipe, 1) != GO:
                return 1
            stopped = threading.Event()
            threading.Thread(target=_watch_owner, args=(pipe, stopped), daemon=True).start()
            if stopped.is_set():
                return 1
            try:
                fences.enter_context(runtime_lease(worker[1] if len(worker) > 1 else ""))
            except RuntimeLeaseError as error:
                return emit_error(error.code, str(error))
            tree.start(worker)
            assert tree.child is not None
            while not tree.exited() and not stopped.wait(POLL_SECONDS):
                pass
        finally:
            _finish(tree)
            tree.close()
        code = tree.child.returncode
        assert code is not None
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
