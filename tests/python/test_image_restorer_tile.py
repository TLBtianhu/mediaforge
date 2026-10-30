"""ImageRestorer 的分块参数：真实调用才会暴露的那条路径。

2026-10-29：给 worker 加了 `payload.tile`，但 `ImageRestorer` 并没有这个参数，
单测全是 mock 所以全绿；真跑一次超分才炸出
`ImageRestorer.__init__() got an unexpected keyword argument 'tile'`。
这组测试钉住三件事：能接收、会钳制、能传到 upscaler。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import image_ops  # noqa: E402


class TileTests(unittest.TestCase):
    def _restorer(self, **kwargs):
        # device 显式给值，避免 pick_device() 去 import torch
        return image_ops.ImageRestorer(device="cpu", **kwargs)

    def test_accepts_a_tile_argument(self) -> None:
        restorer = self._restorer(tile=128)
        self.assertEqual(restorer.tile, 128)

    def test_defaults_to_512(self) -> None:
        self.assertEqual(self._restorer().tile, 512)

    def test_zero_and_negative_are_clamped(self) -> None:
        # tile=0 会让分块循环步长为 0（死循环），必须兜底
        self.assertEqual(self._restorer(tile=0).tile, 512)
        self.assertEqual(self._restorer(tile=-64).tile, 64)

    def test_tile_reaches_the_upscaler(self) -> None:
        restorer = self._restorer(tile=256, context=8)
        fake = mock.Mock()
        with mock.patch.object(image_ops, "RealESRGANUpscaler", return_value=fake) as factory:
            built = restorer.upscaler
        self.assertIs(built, fake)
        _, kwargs = factory.call_args
        self.assertEqual(kwargs["tile"], 256)
        self.assertEqual(kwargs["context"], 8)
        self.assertEqual(kwargs["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
