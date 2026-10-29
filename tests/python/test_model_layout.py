"""模型目录布局：媒体权重在 `media/` 下，音频保持 pymss 的分类相对路径。

2026-10-29 改动：媒体权重原来和音频的分类目录平铺在同一个根下（`models/big-lama.pt`）。
按"一个根、分格子"的约定，媒体统一进 `models/media/`。

难点不在搬目录，在**升级**：0.0.13 之前下载的模型就躺在根目录，布局一改，
老用户会看到"模型未下载"——重下代价是几百 MB，而且他会以为是自己的问题。
所以这组测试的重点是迁移：搬一次、只搬点名的、搬完不重复搬、失败不致命。
"""
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

import model_hub  # noqa: E402
import worker_paths  # noqa: E402
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


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        worker_paths.reset_migration_cache()
        self.addCleanup(worker_paths.reset_migration_cache)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)

    def test_flat_weights_are_moved_into_the_subdirectory(self) -> None:
        (self.root / "big-lama.pt").write_bytes(b"a")
        moved = worker_paths.migrate_media_models(self.root, ["big-lama.pt"])
        self.assertEqual(moved, ["big-lama.pt"])
        self.assertTrue((self.root / "media" / "big-lama.pt").is_file())
        self.assertFalse((self.root / "big-lama.pt").exists())

    def test_migration_is_idempotent(self) -> None:
        (self.root / "big-lama.pt").write_bytes(b"a")
        worker_paths.migrate_media_models(self.root, ["big-lama.pt"])
        worker_paths.reset_migration_cache()
        self.assertEqual(worker_paths.migrate_media_models(self.root, ["big-lama.pt"]), [])
        self.assertTrue((self.root / "media" / "big-lama.pt").is_file())

    def test_unknown_files_are_left_alone(self) -> None:
        """模型根是应用管理的，但用户可能放过别的东西——不点名的绝不搬。"""
        (self.root / "my-notes.txt").write_bytes(b"x")
        (self.root / "big-lama.pt").write_bytes(b"a")
        worker_paths.migrate_media_models(self.root, ["big-lama.pt"])
        self.assertTrue((self.root / "my-notes.txt").is_file())
        self.assertFalse((self.root / "media" / "my-notes.txt").exists())

    def test_existing_target_wins(self) -> None:
        """目标已存在时不覆盖（可能是刚下载好的那份）。"""
        (self.root / "media").mkdir()
        (self.root / "media" / "big-lama.pt").write_bytes(b"new")
        (self.root / "big-lama.pt").write_bytes(b"old")
        moved = worker_paths.migrate_media_models(self.root, ["big-lama.pt"])
        self.assertEqual(moved, [])
        self.assertEqual((self.root / "media" / "big-lama.pt").read_bytes(), b"new")

    def test_audio_style_relative_names_are_skipped(self) -> None:
        moved = worker_paths.migrate_media_models(self.root, ["cleanup/audio/x.ckpt"])
        self.assertEqual(moved, [])

    def test_per_root_flag_is_reset_for_tests(self) -> None:
        (self.root / "big-lama.pt").write_bytes(b"a")
        worker_paths.migrate_media_models(self.root, ["big-lama.pt"])
        # 同一进程内第二次调用直接返回空——这正是性能保护
        self.assertEqual(worker_paths.migrate_media_models(self.root, ["big-lama.pt"]), [])


class CapabilityPathTests(unittest.TestCase):
    def setUp(self) -> None:
        worker_paths.reset_migration_cache()
        self.addCleanup(worker_paths.reset_migration_cache)
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

    def test_model_path_triggers_migration_of_an_old_layout(self) -> None:
        (self.models / "big-lama.pt").write_bytes(b"old")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            resolved = capabilities.model_path("media:lama")
        self.assertEqual(resolved, self.models / "media" / "big-lama.pt")
        self.assertTrue(resolved.is_file(), "老布局的权重应在解析路径时被搬过去")

    def test_listing_reports_downloaded_from_the_new_location(self) -> None:
        (self.models / "media").mkdir()
        (self.models / "media" / "big-lama.pt").write_bytes(b"new")
        with mock.patch.object(capabilities, "CATALOG_PATH", self.catalog_path), \
                mock.patch.object(worker_paths, "model_root", return_value=self.models):
            listed = capabilities.list_capabilities()
        lama = next(item for item in listed["capabilities"] if item["id"] == "watermark_removal")
        model = next(entry for entry in lama["models"] if entry["id"] == "media:lama")
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
        worker_paths.reset_migration_cache()
        self.addCleanup(worker_paths.reset_migration_cache)

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
