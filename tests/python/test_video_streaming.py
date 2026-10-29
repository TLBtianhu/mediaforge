"""视频管线：帧不再落盘。

2026-10-29 改造前：抽出全部帧成 PNG（`frames_in/`）→ 逐帧写回另一套 PNG（`frames_out/`）→ 编码。
1080p 十分钟 ≈ 18000 帧 × 1.5–3MB × 2 ≈ 50–100GB，全在 `%TEMP%`。

这组测试用**真实 ffmpeg** 造一段合成视频，然后断言三件事：
① 产物尺寸/帧数/音频正确；② 中间目录里**没有任何帧文件**；③ 尺寸中途变化时给出可读的错误。

需要 ffmpeg；没有就跳过（`media_ops.ffmpeg` 找不到时会抛 RuntimeError）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import ffmpeg, video_ops  # noqa: E402


def ffmpeg_available() -> bool:
    try:
        ffmpeg.ffmpeg_path()
    except RuntimeError:
        return False
    return True


FFMPEG = ffmpeg_available()


class FakeUpscaler:
    def __init__(self, factor: int = 2, switch_after: int | None = None) -> None:
        self.factor = factor
        self.switch_after = switch_after
        self.calls = 0

    def upscale(self, rgb, *, scale, target, fit, preferred_scale):
        self.calls += 1
        factor = self.factor
        if self.switch_after is not None and self.calls > self.switch_after:
            factor = self.factor + 1
        return np.repeat(np.repeat(rgb, factor, axis=0), factor, axis=1), float(factor)


class FakeRestorer:
    def __init__(self, upscaler: FakeUpscaler | None = None) -> None:
        self.upscaler = upscaler or FakeUpscaler()
        self.detector = None
        self.inpainter = None


@unittest.skipUnless(FFMPEG, "需要 ffmpeg")
class StreamingPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_dir = Path(tempfile.mkdtemp(prefix="mf-video-src-"))
        cls.source = cls.source_dir / "clip.mp4"
        command = [
            ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x64:rate=15:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(cls.source),
        ]
        import subprocess

        subprocess.run(command, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.source_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="mf-video-work-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.out = Path(tempfile.mkdtemp(prefix="mf-video-out-"))
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_pipeline(self, restorer, **kwargs) -> dict:
        patcher = mock.patch.object(video_ops.tempfile, "mkdtemp", return_value=str(self.work))
        keep = mock.patch.object(video_ops.shutil, "rmtree", return_value=None)
        patcher.start()
        keep.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(keep.stop)
        return video_ops.process_video(
            self.source, self.out, restorer=restorer, mode="upscale", scale=2.0, **kwargs
        )

    def frame_files(self) -> list[Path]:
        """中间目录里任何"帧文件"都算回归。"""
        return [path for path in self.work.rglob("*") if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}]

    def test_upscale_keeps_size_frame_count_and_audio(self) -> None:
        result = self.run_pipeline(FakeRestorer())
        produced = Path(result["path"])
        self.assertTrue(produced.is_file())
        meta = ffmpeg.probe(produced)
        video = meta["video"]
        self.assertEqual((video["width"], video["height"]), (128, 128))
        self.assertEqual(result["info"]["frames"], 15)
        self.assertEqual(result["info"]["output_size"], [128, 128])
        self.assertTrue(result["info"]["audio"], "源有音频，产物也应保留音频")
        self.assertTrue(meta.get("audio"))

    def test_no_frame_files_are_written(self) -> None:
        self.run_pipeline(FakeRestorer())
        leftovers = self.frame_files()
        self.assertEqual(leftovers, [], f"中间目录不该有帧文件：{leftovers}")
        # 只允许输入副本、音频和最终视频
        names = sorted(path.name for path in self.work.iterdir() if path.is_file())
        self.assertIn("output.mp4", names)
        self.assertFalse((self.work / "frames_in").exists())
        self.assertFalse((self.work / "frames_out").exists())

    def test_preview_seconds_limits_frames(self) -> None:
        result = self.run_pipeline(FakeRestorer(), preview_seconds=0.5)
        self.assertLessEqual(result["info"]["frames"], 9, "0.5 秒 @15fps 不该超过 9 帧")
        self.assertGreaterEqual(result["info"]["frames"], 6)

    def test_progress_is_monotonic_and_finishes(self) -> None:
        seen: list[float] = []

        def progress(fraction: float, message: str) -> None:
            seen.append(float(fraction))

        self.run_pipeline(FakeRestorer(), progress=progress)
        self.assertTrue(seen)
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen), "进度不能倒退")

    def test_size_change_mid_stream_reports_a_readable_error(self) -> None:
        """帧尺寸若中途变化，编码器无法接受——必须给出人话，而不是 BrokenPipe。"""
        restorer = FakeRestorer(FakeUpscaler(factor=2, switch_after=3))
        with self.assertRaises(RuntimeError) as caught:
            self.run_pipeline(restorer)
        self.assertIn("尺寸", str(caught.exception))

    def test_missing_input_is_reported(self) -> None:
        with self.assertRaises(FileNotFoundError):
            video_ops.process_video(
                self.source_dir / "nope.mp4", self.out, restorer=FakeRestorer(), mode="upscale", scale=2.0
            )


class ScaleUpscaler:
    """按 `scale` 参数决定倍率的替身，用来验证多步顺序。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def upscale(self, rgb, *, scale, target, fit, preferred_scale):
        factor = max(1, int(round(float(scale))))
        self.calls.append(factor)
        return np.repeat(np.repeat(rgb, factor, axis=0), factor, axis=1), float(factor)


class ScaleRestorer:
    def __init__(self, upscaler: ScaleUpscaler | None = None) -> None:
        self.upscaler = upscaler or ScaleUpscaler()
        self.detector = None
        self.inpainter = None


class MultiStepPipelineTests(unittest.TestCase):
    """多步工作流：一次解码、一次编码。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not FFMPEG:
            raise unittest.SkipTest("需要 ffmpeg")
        cls.source_dir = Path(tempfile.mkdtemp(prefix="mf-video-src-"))
        cls.source = cls.source_dir / "clip.mp4"
        import subprocess

        subprocess.run(
            [
                ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=64x64:rate=15:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(cls.source),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.source_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="mf-video-work-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.out = Path(tempfile.mkdtemp(prefix="mf-video-out-"))
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_steps(self, steps, restorer, **kwargs) -> tuple[dict, list[str]]:
        labels: list[str] = []
        real = video_ops._StreamProcess

        class Spy(real):  # type: ignore[misc, valid-type]
            def __init__(self, command, label, **options):
                labels.append(label)
                super().__init__(command, label, **options)

        with mock.patch.object(video_ops.tempfile, "mkdtemp", return_value=str(self.work)), \
                mock.patch.object(video_ops.shutil, "rmtree", return_value=None), \
                mock.patch.object(video_ops, "_StreamProcess", Spy):
            result = video_ops.process_video_steps(
                self.source, self.out, steps=steps, restorer=restorer, **kwargs
            )
        return result, labels

    def test_two_steps_share_one_decode_and_one_encode(self) -> None:
        restorer = ScaleRestorer()
        steps = [
            video_ops.FrameStep(mode="upscale", scale=2.0),
            video_ops.FrameStep(mode="upscale", scale=2.0),
        ]
        result, labels = self.run_steps(steps, restorer)
        self.assertEqual(labels.count("frame decoding"), 1, "解码只能一次")
        self.assertEqual(labels.count("video encode"), 1, "编码只能一次（多步不再逐步重编码）")
        # 15 帧 × 2 步
        self.assertEqual(len(restorer.upscaler.calls), 30)
        self.assertEqual(result["info"]["output_size"], [256, 256])
        self.assertEqual(len(result["info"]["steps"]), 2)

    def test_steps_are_applied_in_declared_order(self) -> None:
        restorer = ScaleRestorer()
        steps = [
            video_ops.FrameStep(mode="upscale", scale=2.0),
            video_ops.FrameStep(mode="upscale", scale=3.0),
        ]
        result, _ = self.run_steps(steps, restorer)
        # 64 -> 128 -> 384
        self.assertEqual(result["info"]["output_size"], [384, 384])
        self.assertEqual(restorer.upscaler.calls[:2], [2, 3])

    def test_no_frame_files_for_multi_step(self) -> None:
        restorer = ScaleRestorer()
        steps = [
            video_ops.FrameStep(mode="upscale", scale=2.0),
            video_ops.FrameStep(mode="upscale", scale=2.0),
        ]
        self.run_steps(steps, restorer)
        leftovers = [
            path
            for path in self.work.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ]
        self.assertEqual(leftovers, [])
        self.assertFalse((self.work / "step1").exists())


if __name__ == "__main__":
    unittest.main()
