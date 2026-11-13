"""Bounded lifecycle and stderr draining for binary media subprocess pipes."""
from __future__ import annotations

import os
import subprocess
import threading

from shared.worker_subprocess import hidden_subprocess_kwargs

REAP_TIMEOUT = 5.0
DRAIN_TIMEOUT = 1.0


class StreamProcess:
    def __init__(self, command: list[str], label: str, *, want_stdin: bool = False, want_stdout: bool = False) -> None:
        self.label = label
        self._tail: list[str] = []
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._stop_drain = threading.Event()
        self._closed = False
        self._drain: threading.Thread | None = None
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if want_stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if want_stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            **hidden_subprocess_kwargs(),
        )
        try:
            assert self.proc.stderr is not None
            # Python 3.12 supports nonblocking pipes on Windows too. Polling a raw
            # pipe makes shutdown bounded even if another process retains a writer.
            os.set_blocking(self.proc.stderr.fileno(), False)
            self._drain = threading.Thread(target=self._drain_stderr, name=f"mf-{label}", daemon=True)
            self._drain.start()
        except BaseException:
            self.close()
            raise

    def _record(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            with self._lock:
                self._tail.append(line)
                del self._tail[:-40]

    def _drain_stderr(self) -> None:
        stream = self.proc.stderr
        if stream is None:
            return
        pending = b""
        try:
            descriptor = stream.fileno()
            while not self._stop_drain.is_set():
                try:
                    chunk = os.read(descriptor, 8192)
                except BlockingIOError:
                    self._stop_drain.wait(0.01)
                    continue
                if not chunk:
                    break
                lines = (pending + chunk).split(b"\n")
                pending = lines.pop()[-65536:]
                for line in lines:
                    self._record(line)
        except (OSError, ValueError):
            pass
        finally:
            self._record(pending)

    def tail(self) -> str:
        with self._lock:
            return "\n".join(self._tail[-12:])

    def error(self) -> str:
        return f"{self.label} failed:\n{self.tail() or '(no ffmpeg output)'}"

    @staticmethod
    def _close_pipe(stream) -> None:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _release_resources(self) -> None:
        with self._cleanup_lock:
            if self._closed:
                return
            self._close_pipe(self.proc.stdin)
            self._close_pipe(self.proc.stdout)
            drain = self._drain
            try:
                if drain is not None and drain.ident is not None:
                    # First let an exited child's remaining diagnostics reach EOF.
                    drain.join(timeout=DRAIN_TIMEOUT)
                    if drain.is_alive():
                        self._stop_drain.set()
                        drain.join(timeout=DRAIN_TIMEOUT)
            except RuntimeError:
                pass
            finally:
                self._stop_drain.set()
                self._close_pipe(self.proc.stderr)
                self._closed = True

    def finish(self, timeout: float = 30.0) -> int:
        """Send stdin EOF, reap, drain and close; return even a nonzero exit code."""
        self._close_pipe(self.proc.stdin)
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self.close()
            raise RuntimeError(f"{self.label} timed out after {timeout:g}s:\n{self.tail() or '(no ffmpeg output)'}") from error
        except BaseException:
            self.close()
            raise
        self._release_resources()
        return code

    def close(self) -> None:
        """Abort and release owned resources; never replace an in-flight error."""
        if self._closed:
            return
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=REAP_TIMEOUT)
        except Exception:
            pass
        self._release_resources()

    def kill(self) -> None:
        self.close()
