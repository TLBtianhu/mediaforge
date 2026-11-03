"""视频抽帧必须使用新旧 FFmpeg 都支持的 fps_mode 参数。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class VideoFfmpegOptionTests(unittest.TestCase):
    def test_video_decode_uses_fps_mode_passthrough_not_removed_vsync(self) -> None:
        script = (ROOT / "python/media_ops/video_ops.py").read_text(encoding="utf-8")
        self.assertNotIn('"-vsync"', script)
        self.assertGreaterEqual(script.count('"-fps_mode", "passthrough"'), 4)


if __name__ == "__main__":
    unittest.main()
