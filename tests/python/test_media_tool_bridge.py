"""audio_tools 桥必须把媒体操作交给 media_ops，而不是音频 operations。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from core import worker_tools  # noqa: E402


class MediaToolBridgeTests(unittest.TestCase):
    def test_media_operations_use_the_media_bridge_before_audio_operations(self) -> None:
        for operation in ("media_image", "media_video", "media_workflow", "media_probe", "media_convert"):
            with self.subTest(operation=operation):
                with mock.patch("media_ops.worker.cmd_media_tool", return_value=0) as media, \
                     mock.patch("core.audio_tools_ops.run_audio_tool_via_operations") as audio:
                    code = worker_tools.cmd_audio_tools({"operation": operation, "requestId": "media-test"})
                self.assertEqual(code, 0)
                media.assert_called_once_with({"operation": operation, "requestId": "media-test"})
                audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
