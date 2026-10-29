"""产物自评价：agent 要能自己判断"做好了没有"。

测三件事：指标算得对、对比能看出变化、判断保守（不把正常差异报成问题）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

try:
    import numpy as np
    from PIL import Image, ImageFilter

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - 取决于解释器里装了什么
    PIL_AVAILABLE = False

import media_eval  # noqa: E402


def _write_pattern(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    """造一张有结构（边缘）的图——纯色图锐度恒为 0，测不出东西。"""
    width, height = size
    grid = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            grid[y, x] = (x * 4 % 256, y * 5 % 256, (x + y) * 3 % 256)
    Image.fromarray(grid).save(path)
    return path


@unittest.skipUnless(PIL_AVAILABLE, "需要 numpy + Pillow")
class ImageMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-eval-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_metrics_report_size_and_sharpness(self) -> None:
        path = _write_pattern(self.root / "a.png")
        result = media_eval.evaluate_file(path)
        self.assertEqual(result["kind"], "image")
        self.assertFalse(result.get("error"))
        metrics = result["metrics"]
        self.assertEqual((metrics["width"], metrics["height"]), (64, 48))
        self.assertGreater(metrics["sharpness"], 0.0)
        self.assertGreater(metrics["sharpnessNorm"], 0.0)

    def test_identical_file_is_called_out(self) -> None:
        path = _write_pattern(self.root / "a.png")
        payload = media_eval.evaluate([str(path)], reference=str(path))
        notes = payload["evaluations"][0]["notes"]
        self.assertTrue(any("字节完全相同" in note for note in notes), notes)

    def test_upscale_is_recognised(self) -> None:
        small = _write_pattern(self.root / "small.png", (32, 24))
        big = _write_pattern(self.root / "big.png", (64, 48))
        payload = media_eval.evaluate([str(big)], reference=str(small))
        notes = payload["evaluations"][0]["notes"]
        self.assertTrue(any("尺寸放大" in note for note in notes), notes)
        self.assertTrue(any("32x24" in note and "64x48" in note for note in notes), notes)

    def test_blurred_output_is_flagged_as_softer(self) -> None:
        source = _write_pattern(self.root / "source.png", (96, 72))
        blurred = self.root / "blurred.png"
        Image.open(source).filter(ImageFilter.GaussianBlur(radius=3)).save(blurred)
        payload = media_eval.evaluate([str(blurred)], reference=str(source))
        notes = payload["evaluations"][0]["notes"]
        self.assertTrue(any("锐度比" in note for note in notes), notes)
        self.assertTrue(any("变糊" in note for note in notes), notes)

    def test_flat_reference_skips_the_sharpness_ratio_explicitly(self) -> None:
        """纯色参考图没有高频细节，比值无意义——必须说"已跳过"，不能静默。"""
        flat = self.root / "flat.png"
        Image.new("RGB", (64, 48), (128, 128, 128)).save(flat)
        detailed = _write_pattern(self.root / "detailed.png", (64, 48))
        payload = media_eval.evaluate([str(detailed)], reference=str(flat))
        notes = payload["evaluations"][0]["notes"]
        self.assertTrue(any("锐度对比已跳过" in note for note in notes), notes)

    def test_missing_file_does_not_raise(self) -> None:
        result = media_eval.evaluate_file(self.root / "nope.png")
        self.assertFalse(result["exists"])
        self.assertEqual(result["error"], "file not found")


class VideoMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-eval-video-"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_video_metrics_via_ffprobe(self) -> None:
        try:
            from media_ops import ffmpeg

            ffmpeg.ffmpeg_path()
        except Exception:
            self.skipTest("需要 ffmpeg")
        import subprocess

        from media_ops import ffmpeg

        clip = self.root / "clip.mp4"
        subprocess.run(
            [
                ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
            capture_output=True,
        )
        result = media_eval.evaluate_file(clip)
        self.assertEqual(result["kind"], "video")
        self.assertFalse(result.get("error"))
        metrics = result["metrics"]
        self.assertEqual((metrics["width"], metrics["height"]), (64, 48))
        self.assertGreaterEqual(metrics.get("frames", 0), 9)


if __name__ == "__main__":
    unittest.main()
