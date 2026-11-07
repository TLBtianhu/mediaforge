"""Exercise conversion's real hidden child, pipe pressure and exceptional cleanup."""
from __future__ import annotations

import contextlib
import math
import struct
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import ffmpeg  # noqa: E402


CHILD = r"""
import ctypes, os, sys, time
from pathlib import Path
if os.name == 'nt' and ctypes.windll.kernel32.GetConsoleWindow():
    raise RuntimeError('conversion child acquired a console')
mode, destination = sys.argv[1:]
print('out_time_us=100000', flush=True)
if mode == 'callback':
    while True:
        print('waiting-for-cancellation-' + 'x' * 8192, file=sys.stderr, flush=True)
        time.sleep(0.01)
for index in range(256):
    print('stderr-pressure-' + str(index) + '-' + 'x' * 8192, file=sys.stderr, flush=True)
    if index % 8 == 0:
        print('out_time_us=' + str(100000 + index * 2000), flush=True)
if mode == 'error':
    print('FINAL-CONVERSION-FAILURE: codec fixture refused input', file=sys.stderr, flush=True)
    raise SystemExit(7)
Path(destination).write_bytes(b'conversion-fixture-output')
print('out_time_us=1000000', flush=True)
"""


class ConversionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="mf-conversion-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "tiny.wav"
        with wave.open(str(self.source), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            samples = (int(math.sin(index * math.tau * 440 / 8000) * 1000) for index in range(1600))
            audio.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
        self.output = self.root / "output"

    def _assert_released(self, instance) -> None:
        self.assertIsNotNone(instance.proc.returncode, "child was not waited/reaped")
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(instance.proc, name)
            if stream is not None:
                self.assertTrue(stream.closed, f"{name} pipe stayed open")
        drain = instance._drain
        if drain is not None:
            self.assertFalse(drain.is_alive(), "stderr drain stayed alive after conversion")

    def _conversion(self, mode: str | None, progress=None):
        instances = []
        outcome = {}
        original = ffmpeg.StreamProcess

        class ObservedProcess(original):
            def __init__(self, *args, **kwargs):
                # Track even constructor failures, so a deadline always owns its cleanup.
                instances.append(self)
                super().__init__(*args, **kwargs)

        def convert() -> None:
            try:
                outcome["value"] = ffmpeg.convert_one(self.source, self.output, "flac", progress=progress)
            except BaseException as error:
                outcome["error"] = error

        def command(_src, destination, *_args):
            return [sys.executable, "-I", "-B", "-u", "-c", CHILD, mode, str(destination)]

        thread = threading.Thread(target=convert, name="conversion-fixture", daemon=True)
        with contextlib.ExitStack() as patches:
            patches.enter_context(mock.patch.object(ffmpeg, "StreamProcess", ObservedProcess))
            # Avoid ffprobe and models; only the conversion child is real.
            patches.enter_context(mock.patch.object(ffmpeg, "probe", return_value={
                "duration": 1.0, "format": "wav", "video": None, "audio": {"sample_rate": 8000},
            }))
            if mode is not None:
                patches.enter_context(mock.patch.object(ffmpeg, "_build_command", side_effect=command))
            try:
                thread.start()
                thread.join(timeout=8)
                self.assertFalse(thread.is_alive(), "conversion blocked on child pipes")
                self.assertEqual(len(instances), 1)
                self._assert_released(instances[0])
                return outcome
            finally:
                # Never leave a deliberately blocked fixture behind, even when assertions fail.
                for instance in instances:
                    proc = getattr(instance, "proc", None)
                    if proc is None:
                        continue
                    try:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait(timeout=3)
                    finally:
                        for name in ("stdin", "stdout", "stderr"):
                            stream = getattr(proc, name)
                            if stream is not None:
                                stream.close()
                thread.join(timeout=3)
                for instance in instances:
                    if hasattr(instance, "proc"):
                        instance.close()

    def test_large_stderr_and_stdout_progress_complete_without_deadlock(self) -> None:
        reports = []
        outcome = self._conversion("success", lambda fraction, message: reports.append((fraction, message)))
        self.assertNotIn("error", outcome)
        result = outcome["value"]
        self.assertEqual(Path(result["path"]).read_bytes(), b"conversion-fixture-output")
        self.assertGreater(len(reports), 16)
        self.assertEqual(reports[-1][0], 1.0)
        self.assertTrue(all(first[0] <= second[0] for first, second in zip(reports, reports[1:])))

    def test_nonzero_exit_preserves_the_final_stderr_after_pipe_pressure(self) -> None:
        outcome = self._conversion("error")
        self.assertIsInstance(outcome.get("error"), RuntimeError)
        self.assertIn("FINAL-CONVERSION-FAILURE: codec fixture refused input", str(outcome["error"]))
        self.assertIn(self.source.name, str(outcome["error"]))

    def test_progress_callback_failure_reaps_and_closes_the_child_without_replacing_the_error(self) -> None:
        original = RuntimeError("progress consumer failed")

        def fail(_fraction, _message):
            raise original

        outcome = self._conversion("callback", fail)
        self.assertIs(outcome.get("error"), original)

    def test_callback_cancellation_reaps_and_closes_the_child_without_replacing_the_exception(self) -> None:
        original = KeyboardInterrupt("conversion cancelled")

        def cancel(_fraction, _message):
            raise original

        outcome = self._conversion("callback", cancel)
        self.assertIs(outcome.get("error"), original)

    def test_real_ffmpeg_converts_the_tiny_wave_without_ffprobe_or_models(self) -> None:
        try:
            ffmpeg.ffmpeg_path()
        except RuntimeError:
            self.skipTest("FFmpeg is unavailable")
        outcome = self._conversion(None)
        self.assertNotIn("error", outcome)
        result = outcome["value"]
        destination = Path(result["path"])
        self.assertEqual(destination.read_bytes()[:4], b"fLaC")
        self.assertGreater(result["size"], 0)
        self.assertEqual(result["kind"], "audio")


if __name__ == "__main__":
    unittest.main()
