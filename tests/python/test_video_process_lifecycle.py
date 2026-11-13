"""Exercise streaming-process ownership with small, hidden, real child processes."""
from __future__ import annotations

import gc
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings

import numpy as np

if __package__:
    from . import _bootstrap as _worker_test_bootstrap
else:
    import _bootstrap as _worker_test_bootstrap

from media_ops import ffmpeg, video_ops
from shared.worker_subprocess import hidden_subprocess_kwargs


class _OwnedProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owned = []
        temporary = tempfile.TemporaryDirectory(prefix="mf-video-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(self.cleanup_owned)
        self.root = Path(temporary.name)
        environment = {}
        for name in (
            "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
            "MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT",
            "PYMSS_STUDIO_DATA_DIR", "MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR",
            "MEDIAFORGE_RUNTIME_ENVS_DIR", "WEBVIEW2_USER_DATA_FOLDER", "HF_HOME",
            "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "XDG_CACHE_HOME",
        ):
            directory = self.root / name.lower()
            directory.mkdir()
            environment[name] = str(directory)
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                           HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PIP_NO_INDEX="1")
        self.enterContext(mock.patch.dict(os.environ, environment))
        # tempfile caches its first resolved root; environment changes alone do not redirect it.
        self.enterContext(mock.patch.object(tempfile, "tempdir", str(self.root / "tmp")))
        self.resource_warnings = self.enterContext(warnings.catch_warnings(record=True))
        warnings.simplefilter("always", ResourceWarning)

    def force_reap(self, stream) -> None:
        """Last-resort cleanup of this test's own Popen only, even after a failed assertion."""
        process = stream.proc
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()
        stream._drain.join(timeout=2)

    def cleanup_owned(self) -> None:
        for stream in reversed(self.owned):
            try:
                stream.close()
            finally:
                self.force_reap(stream)
        self.owned.clear()

    def tearDown(self) -> None:
        self.cleanup_owned()
        gc.collect()
        leaked = [str(item.message) for item in self.resource_warnings
                  if issubclass(item.category, ResourceWarning)]
        self.assertEqual(leaked, [], "owned subprocesses or streams leaked resources")

    def start_python(self, code: str, label: str, **options):
        stream = video_ops._StreamProcess([sys.executable, "-I", "-B", "-c", code], label, **options)
        self.owned.append(stream)
        return stream

    def bounded(self, stream, action, timeout: float = 8):
        """A drain regression must fail promptly rather than hang the entire test runner."""
        results = []
        errors = []

        def invoke():
            try:
                results.append(action())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=invoke, name="video-lifecycle-test", daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            self.force_reap(stream)
            thread.join(timeout=2)
            self.fail("owned process lifecycle operation blocked beyond its deadline")
        if errors:
            raise errors[0]
        return results[0]

    def assert_released(self, stream) -> None:
        # Do not poll first: that would reap a process the lifecycle method forgot to wait for.
        self.assertIsNotNone(stream.proc.returncode, "the owned process was not waited for")
        self.assertFalse(stream._drain.is_alive(), "stderr drain thread is still running")
        for pipe in (stream.proc.stdin, stream.proc.stdout, stream.proc.stderr):
            if pipe is not None:
                self.assertTrue(pipe.closed, "an owned pipe remains open")


class StreamProcessLifecycleTests(_OwnedProcessTests):
    def test_finish_delivers_stdin_eof_and_releases_encoder_pipes(self) -> None:
        stream = self.start_python(
            "import sys; data=sys.stdin.buffer.read(); "
            "sys.stderr.write('received:%d\\n' % len(data))",
            "stdin fixture", want_stdin=True,
        )
        stream.proc.stdin.write(b"small frame")
        self.assertEqual(self.bounded(stream, lambda: stream.finish(timeout=3)), 0)
        self.assertIn("received:11", stream.tail())
        self.assert_released(stream)

    def test_finish_releases_consumed_stdout_and_can_be_repeated(self) -> None:
        stream = self.start_python(
            "import sys; sys.stdout.buffer.write(b'frame'); sys.stdout.flush(); "
            "sys.stderr.write('decode complete\\n')",
            "stdout fixture", want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: stream.proc.stdout.read(5)), b"frame")
        self.assertEqual(self.bounded(stream, lambda: stream.finish(timeout=3)), 0)
        self.assertEqual(stream.finish(timeout=0.1), 0)
        stream.close()
        stream.kill()
        self.assertIn("decode complete", stream.tail())
        self.assert_released(stream)

    def test_nonzero_exit_retains_diagnostics_after_reaping(self) -> None:
        stream = self.start_python(
            "import sys,time; time.sleep(0.1); "
            "sys.stderr.write('encoder rejected fixture without newline'); sys.exit(7)",
            "failed fixture", want_stdin=True, want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: stream.finish(timeout=3)), 7)
        self.assertIn("failed fixture failed", stream.error())
        self.assertIn("encoder rejected fixture", stream.error())
        self.assert_released(stream)

    def test_short_pipe_reads_are_combined_until_the_complete_frame_arrives(self) -> None:
        stream = self.start_python(
            "import os,time; "
            "[(os.write(1, part), time.sleep(0.02)) for part in (b'a', b'bc', b'def')]",
            "short reads fixture", want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: video_ops._read_exact(stream.proc.stdout, 6)), b"abcdef")
        self.assertEqual(self.bounded(stream, lambda: stream.finish(timeout=3)), 0)
        self.assert_released(stream)

    def test_stderr_pressure_does_not_deadlock_stdout_or_wait(self) -> None:
        stream = self.start_python(
            "import os; "
            "[os.write(2, b'x'*4096+b'\\n') for _ in range(512)]; "
            "os.write(2, b'pressure complete\\n'); os.write(1, b'done')",
            "pressure fixture", want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: stream.proc.stdout.read(4)), b"done")
        self.assertEqual(self.bounded(stream, lambda: stream.finish(timeout=3)), 0)
        self.assertIn("pressure complete", stream.tail())
        self.assertLessEqual(len(stream.tail().splitlines()), 12)
        self.assert_released(stream)

    def test_early_close_and_kill_are_idempotent_and_reap_the_live_child(self) -> None:
        stream = self.start_python(
            "import os,time; os.write(1,b'ready'); time.sleep(30)",
            "early stop fixture", want_stdin=True, want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: stream.proc.stdout.read(5)), b"ready")
        self.assertIsNone(stream.proc.returncode)
        self.bounded(stream, stream.close)
        stream.close()
        stream.kill()
        self.assert_released(stream)

    def test_finish_timeout_reaps_the_child_before_raising(self) -> None:
        stream = self.start_python(
            "import os,time; os.write(2,b'before timeout\\n'); "
            "os.write(1,b'ready'); time.sleep(30)",
            "timeout fixture", want_stdout=True,
        )
        self.assertEqual(self.bounded(stream, lambda: stream.proc.stdout.read(5)), b"ready")
        with self.assertRaisesRegex(RuntimeError, "timeout fixture"):
            self.bounded(stream, lambda: stream.finish(timeout=0.05))
        self.assertIn("before timeout", stream.tail())
        self.assert_released(stream)


class _FakeUpscaler:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at

    def upscale(self, rgb, **_options):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("fixture model exploded")
        return np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1), 2.0


class VideoPipelineProcessLifecycleTests(_OwnedProcessTests):
    def setUp(self) -> None:
        super().setUp()
        try:
            self.ffmpeg = ffmpeg.ffmpeg_path()
        except RuntimeError:
            self.skipTest("bundled or PATH FFmpeg is unavailable")
        self.source = self.root / "source.mkv"
        subprocess.run(
            [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=32x24:rate=40:duration=3",
             "-c:v", "ffv1", str(self.source)],
            check=True, capture_output=True, timeout=15, **hidden_subprocess_kwargs(),
        )
        real_stream = video_ops._StreamProcess
        self.decoder_command = None

        def track(command, label, **options):
            if label == "frame decoding" and self.decoder_command is not None:
                command = self.decoder_command
            stream = real_stream(command, label, **options)
            self.owned.append(stream)
            return stream

        self.enterContext(mock.patch.object(video_ops, "_StreamProcess", side_effect=track))

    def run_pipeline(self, fail_at: int | None = None, **options):
        restorer = SimpleNamespace(upscaler=_FakeUpscaler(fail_at), detector=None, inpainter=None)
        return video_ops.process_video_steps(
            self.source, self.root / "output", steps=[video_ops.FrameStep(mode="upscale", scale=2)],
            restorer=restorer, **options,
        )

    def assert_pipeline_released(self, expected_count: int) -> None:
        self.assertEqual(len(self.owned), expected_count)
        for stream in self.owned:
            self.assert_released(stream)
        self.assertEqual(list((self.root / "tmp").glob("mfvideo_*")), [])

    def test_success_reaps_both_ffmpeg_children_and_closes_their_pipes(self) -> None:
        result = self.run_pipeline()
        self.assertEqual(result["info"]["frames"], 120)
        self.assertTrue(Path(result["path"]).is_file())
        self.assert_pipeline_released(2)

    def test_first_frame_model_exception_reaps_decoder_without_starting_encoder(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "fixture model exploded"):
            self.run_pipeline(fail_at=1)
        self.assert_pipeline_released(1)

    def test_later_model_exception_reaps_both_active_ffmpeg_children(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "fixture model exploded"):
            self.run_pipeline(fail_at=2)
        self.assert_pipeline_released(2)

    def test_encoder_failure_keeps_ffmpeg_diagnostic_and_releases_both_children(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "video encode failed") as caught:
            self.run_pipeline(preset="fixture-invalid-preset")
        self.assertIn("fixture-invalid-preset", str(caught.exception))
        self.assert_pipeline_released(2)

    def test_decoder_failure_before_first_frame_retains_stderr_and_reaps(self) -> None:
        self.decoder_command = [sys.executable, "-I", "-B", "-c",
                                "import sys; sys.stderr.write('fixture decoder corrupt input\\n'); sys.exit(7)"]
        with self.assertRaisesRegex(RuntimeError, "frame decoding failed") as caught:
            self.run_pipeline()
        self.assertIn("fixture decoder corrupt input", str(caught.exception))
        self.assert_pipeline_released(1)

    def test_empty_successful_decoder_reports_no_frames_and_reaps(self) -> None:
        self.decoder_command = [sys.executable, "-I", "-B", "-c", "pass"]
        with self.assertRaisesRegex(RuntimeError, "no video frames"):
            self.run_pipeline()
        self.assert_pipeline_released(1)

    def test_partial_frame_eof_fails_instead_of_returning_a_truncated_video(self) -> None:
        frame_size = 32 * 24 * 3
        self.decoder_command = [sys.executable, "-I", "-B", "-c",
                                f"import os; os.write(1, b'\\x00'*{frame_size + 5})"]
        with self.assertRaisesRegex(RuntimeError, "truncated video frame"):
            self.run_pipeline()
        self.assert_pipeline_released(2)
        self.assertEqual(list((self.root / "output").glob("*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
