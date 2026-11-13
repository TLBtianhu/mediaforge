"""Current model layout resolves media/ paths without moving existing assets."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core import model_hub  # noqa: E402
from shared import worker_paths  # noqa: E402
from media_ops import capabilities  # noqa: E402


class MediaRelpathTests(unittest.TestCase):
    def test_relative_path_is_prefixed(self) -> None:
        self.assertEqual(worker_paths.media_model_relpath("big-lama.pt"), "media/big-lama.pt")

    def test_backslashes_are_normalised(self) -> None:
        self.assertEqual(worker_paths.media_model_relpath(r"sub\Big.pt"), "media/sub/Big.pt")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            worker_paths.media_model_relpath("")

    def test_media_files_use_the_subdirectory(self) -> None:
        files = model_hub._media_files({"name": "big-lama.pt", "size": 1, "url": "http://x/y"})
        self.assertEqual(files[0]["path"], "media/big-lama.pt")
    def test_directory_model_files_keep_the_model_directory_and_propagate_hashes(self) -> None:
        files = model_hub._media_files({
            "name": "depth-demo",
            "files": [
                {"path": "config.json", "size": 10, "url": "https://example.invalid/config"},
                {"path": "model.safetensors", "size": 20, "url": "https://example.invalid/model"},
            ],
            "file_hashes": {"config.json": "abc123", "model.safetensors": "def456"},
        })
        self.assertEqual([item["path"] for item in files], ["media/depth-demo/config.json", "media/depth-demo/model.safetensors"])
        self.assertEqual([item["sha256"] for item in files], ["abc123", "def456"])

    def test_directory_model_file_paths_cannot_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid media model file path"):
            model_hub._media_files({"name": "depth-demo", "files": [{"path": "../secret"}]})



class CapabilityPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        catalog = {
            "models": [
                {
                    "id": "lama",
                    "name": "big-lama.pt",
                    "label": "LaMa",
                    "task": "inpainting",
                    "size": 4,
                }
            ]
        }
        self.catalog_path = self.root / "media_catalog.json"
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        self.models = self.root / "models"
        self.models.mkdir()

    def test_model_path_resolves_under_media(self) -> None:
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            resolved = capabilities.model_path("media:lama")
        self.assertEqual(resolved, self.models / "media" / "big-lama.pt")

    def test_model_path_does_not_move_flat_assets(self) -> None:
        (self.models / "big-lama.pt").write_bytes(b"old")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            resolved = capabilities.model_path("media:lama")
        self.assertEqual(resolved, self.models / "media" / "big-lama.pt")
        self.assertFalse(resolved.exists())
        self.assertEqual((self.models / "big-lama.pt").read_bytes(), b"old")

    def test_listing_ignores_flat_assets_without_moving_or_deleting_them(self) -> None:
        (self.models / "big-lama.pt").write_bytes(b"flat")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            listed = capabilities.list_capabilities()
        lama = next(item for item in listed["capabilities"] if item["id"] == "watermark_removal")
        model = next(entry for entry in lama["models"] if entry["id"] == "media:lama")
        self.assertFalse(model["downloaded"])
        self.assertEqual((self.models / "big-lama.pt").read_bytes(), b"flat")
        self.assertFalse((self.models / "media").exists())

    def test_model_hub_listing_uses_current_layout_and_leaves_flat_and_current_assets_unchanged(self) -> None:
        (self.models / "big-lama.pt").write_bytes(b"flat")
        (self.models / "media").mkdir()
        (self.models / "media/big-lama.pt").write_bytes(b"new")
        empty_audio = self.root / "audio.json"
        empty_audio.write_text('{"models": []}', encoding="utf-8")
        with mock.patch.object(model_hub, "MEDIA_CATALOG", self.catalog_path), \
                mock.patch.object(model_hub, "AUDIO_CATALOG", empty_audio), \
                mock.patch.object(model_hub.importlib.util, "find_spec", return_value=None):
            listing = model_hub.list_models(root=str(self.models))
        self.assertEqual((self.models / "big-lama.pt").read_bytes(), b"flat")
        self.assertEqual((self.models / "media/big-lama.pt").read_bytes(), b"new")
        self.assertTrue(listing["models"][0]["downloaded"])

    def test_listing_reports_downloaded_from_the_new_location(self) -> None:
        (self.models / "media").mkdir()
        (self.models / "media" / "big-lama.pt").write_bytes(b"new")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            listed = capabilities.list_capabilities()
        lama = next(item for item in listed["capabilities"] if item["id"] == "watermark_removal")
        model = next(entry for entry in lama["models"] if entry["id"] == "media:lama")
        self.assertTrue(model["downloaded"])
    def test_multifile_model_directory_counts_as_downloaded(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["models"].append({
            "id": "propainter",
            "name": "ProPainter.pth",
            "label": "ProPainter",
            "task": "video_inpainting",
            "implemented": True,
            "files": [
                {"path": "ProPainter.pth", "size": 1},
                {"path": "raft-things.pth", "size": 1},
                {"path": "recurrent_flow_completion.pth", "size": 1},
            ],
        })
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        model_dir = self.models / "media" / "ProPainter.pth"
        model_dir.mkdir(parents=True)
        for name in ("ProPainter.pth", "raft-things.pth", "recurrent_flow_completion.pth"):
            (model_dir / name).write_bytes(b"x")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            listed = capabilities.list_capabilities()
        capability = next(item for item in listed["capabilities"] if item["id"] == "video_inpainting")
        model = next(entry for entry in capability["models"] if entry["id"] == "media:propainter")
        self.assertTrue(model["downloaded"])


class ImageRestorerDefaultPathTests(unittest.TestCase):
    """`ImageRestorer` 的默认模型路径也必须走 catalog + 唯一解析。

    2026-10-29 之前这里硬编码四个文件名，并且用 `image_ops.model_dir()`——
    那个函数还留着"仓库根"兜底（打包后 = 安装目录）。这是同一个病**第四处**，
    而且它才是真正把一次图像任务跑挂的地方。
    """

    def setUp(self) -> None:
        try:
            from media_ops import image_ops
        except Exception as exc:  # pragma: no cover - 取决于解释器里装了什么
            self.skipTest(f"无法导入 image_ops：{exc}")
        self.image_ops = image_ops

    def test_defaults_resolve_through_capabilities(self) -> None:
        recorded: list[str] = []

        def fake_model_path(model_id: str) -> Path:
            recorded.append(model_id)
            return Path("models-root") / "media" / model_id.split(":", 1)[1]

        with mock.patch.object(self.image_ops.capabilities, "model_path", side_effect=fake_model_path), \
                mock.patch.object(self.image_ops, "pick_device", return_value="cpu"):
            restorer = self.image_ops.ImageRestorer()

        self.assertEqual(
            set(recorded),
            {"media:watermark-yolo11x", "media:lama", "media:real-esrgan-x2", "media:real-esrgan-x4"},
        )
        self.assertEqual(restorer.model_paths["upscaler_x2"], str(Path("models-root") / "media" / "real-esrgan-x2"))

    def test_explicit_paths_still_win(self) -> None:
        """调用方显式传的路径优先（`ImageRestorer` 会先算默认值再合并）。"""
        with mock.patch.object(self.image_ops, "pick_device", return_value="cpu"):
            restorer = self.image_ops.ImageRestorer(
                model_paths={"upscaler_x2": "custom/x2.pth"}
            )
        self.assertEqual(restorer.model_paths["upscaler_x2"], "custom/x2.pth")
        # 没覆盖的键仍然来自 catalog + media/（用 as_posix 避开 Windows 分隔符）
        self.assertTrue(Path(restorer.model_paths["inpainter"]).as_posix().endswith("media/big-lama.pt"))


if __name__ == "__main__":
    unittest.main()
