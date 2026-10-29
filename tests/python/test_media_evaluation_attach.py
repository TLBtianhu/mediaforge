"""产物自带评价：跑完就有 `eval`，不需要再问一次。

这是"自评价"的**推**模式（拉模式是显式调 `media.evaluate`）。
两条硬约束：只做加法（评价失败不影响主结果）、可整体关闭（批量处理省 IO）。
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

try:
    from PIL import Image

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    PIL_AVAILABLE = False

from media_ops import evaluation  # noqa: E402


def write_pattern(path: Path, size: tuple[int, int] = (48, 32)) -> Path:
    width, height = size
    grid = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            grid[y, x] = (x * 5 % 256, y * 7 % 256, (x * y) % 256)
    Image.fromarray(grid).save(path)
    return path


@unittest.skipUnless(PIL_AVAILABLE, "需要 numpy + Pillow")
class AttachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-attach-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = write_pattern(self.root / "source.png", (32, 24))
        self.output = write_pattern(self.root / "out.png", (64, 48))

    def test_attach_adds_metrics_and_notes(self) -> None:
        outputs = [{"path": str(self.output), "source": str(self.source)}]
        evaluation.attach(outputs)
        entry = outputs[0]["eval"]
        self.assertEqual(entry["kind"], "image")
        self.assertEqual(entry["metrics"]["width"], 64)
        self.assertTrue(any("尺寸放大" in note for note in entry["notes"]))

    def test_attach_without_reference_still_reports_metrics(self) -> None:
        outputs = [{"path": str(self.output)}]
        evaluation.attach(outputs)
        self.assertIn("metrics", outputs[0]["eval"])
        self.assertEqual(outputs[0]["eval"]["notes"], [])

    def test_attachment_can_be_disabled(self) -> None:
        outputs = [{"path": str(self.output), "source": str(self.source)}]
        with mock.patch.dict("os.environ", {"MEDIAFORGE_AUTO_EVALUATE": "0"}, clear=False):
            evaluation.attach(outputs)
        self.assertNotIn("eval", outputs[0])

    def test_missing_file_does_not_break_the_result(self) -> None:
        outputs = [{"path": str(self.root / "gone.png")}]
        evaluation.attach(outputs)  # 不应抛异常
        self.assertIn("eval", outputs[0])
        self.assertEqual(outputs[0]["eval"].get("error"), "file not found")

    def test_entries_without_path_are_skipped(self) -> None:
        outputs = [{"source": str(self.source)}, "not-a-dict"]
        evaluation.attach(outputs)  # type: ignore[arg-type]
        self.assertNotIn("eval", outputs[0])

    def test_enabled_flag_follows_the_environment(self) -> None:
        with mock.patch.dict("os.environ", {"MEDIAFORGE_AUTO_EVALUATE": "off"}, clear=False):
            self.assertFalse(evaluation.enabled())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertTrue(evaluation.enabled())


if __name__ == "__main__":
    unittest.main()
