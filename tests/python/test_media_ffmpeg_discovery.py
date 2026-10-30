"""ffmpeg / ffprobe 的定位规则。

2026-10-29 的真实事故：安装包把工具放在 `<install>/bin/`，而 `_find_tool` 只找
`<root>/tools/...`，从不看 `bin/`。开发机 PATH 上通常有系统 ffmpeg，所以本地一切正常；
干净机器上则直接 `ffmpeg not found` —— 视频抽帧、回封、探测全废，而这正是"发给群友"
第一天才发现的那类问题。

这组测试用**临时 root** 模拟打包形态，不依赖本机是否装了 ffmpeg。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import ffmpeg  # noqa: E402


def make_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


class BundledToolTests(unittest.TestCase):
    def test_installer_bin_directory_is_found(self) -> None:
        """打包版：<root>/bin/ffmpeg.exe（安装器整目录拷进 {app}）。"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundled = make_exe(root / "bin" / "ffmpeg.exe")
            self.assertEqual(Path(ffmpeg._find_tool("ffmpeg", root=root)), bundled)

    def test_bundled_tool_wins_over_system_path(self) -> None:
        """自带工具优先于 PATH：装了什么版本就用什么版本。"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundled = make_exe(root / "bin" / "ffmpeg.exe")
            with mock.patch.object(ffmpeg.shutil, "which", return_value=r"C:\somewhere\else\ffmpeg.exe"):
                self.assertEqual(Path(ffmpeg._find_tool("ffmpeg", root=root)), bundled)

    def test_legacy_tools_layout_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            legacy = make_exe(root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe")
            self.assertEqual(Path(ffmpeg._find_tool("ffprobe", root=root)), legacy)

    def test_env_override_wins_over_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_exe(root / "bin" / "ffmpeg.exe")
            chosen = make_exe(Path(raw) / "custom" / "ffmpeg.exe")
            with mock.patch.dict("os.environ", {"MEDIAFORGE_FFMPEG": str(chosen)}):
                self.assertEqual(Path(ffmpeg._find_tool("ffmpeg", root=root)), chosen)

    def test_missing_tool_names_the_bundled_location(self) -> None:
        """找不到时必须指出"安装包里应该有的那个位置"，而不是让用户去猜。"""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.object(ffmpeg.shutil, "which", return_value=None):
                with self.assertRaises(RuntimeError) as caught:
                    ffmpeg._find_tool("ffmpeg", root=root)
        message = str(caught.exception)
        self.assertIn(str(root / "bin" / "ffmpeg.exe"), message)
        self.assertIn("MEDIAFORGE_FFMPEG", message)

    def test_system_path_is_still_the_last_resort(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.object(ffmpeg.shutil, "which", return_value=r"C:\sys\ffmpeg.exe"):
                self.assertEqual(ffmpeg._find_tool("ffmpeg", root=root), r"C:\sys\ffmpeg.exe")


if __name__ == "__main__":
    unittest.main()
