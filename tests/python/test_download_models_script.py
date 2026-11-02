"""历史媒体下载脚本只能选择 catalog 条目，下载与校验必须走 model_hub。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mediaforge_download_models", ROOT / "download_models.py")
assert SPEC and SPEC.loader
script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(script)


class DownloadModelsScriptTests(unittest.TestCase):
    def test_default_selection_is_ordered_by_catalog_ids(self) -> None:
        records = [{"id": model_id} for model_id in reversed(script.DEFAULT_MODEL_IDS)]
        with tempfile.TemporaryDirectory(prefix="mf-download-script-") as root:
            with mock.patch.object(script.model_hub, "list_models", return_value={"models": records}) as list_models:
                selected = script.default_records(Path(root))
        self.assertEqual([item["id"] for item in selected], list(script.DEFAULT_MODEL_IDS))
        list_models.assert_called_once_with(modality="image", root=root)

    def test_download_delegates_to_shared_verified_downloader(self) -> None:
        item = {
            "id": "media:lama", "label": "LaMa", "name": "big-lama.pt",
            "files": [{"path": "media/big-lama.pt"}],
        }
        with tempfile.TemporaryDirectory(prefix="mf-download-script-") as root_name:
            root = Path(root_name)
            target = root / "media/big-lama.pt"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old")
            result = {"files": [{"path": str(target), "size": 0, "skipped": False}]}
            with mock.patch.object(script.model_hub, "download_model", return_value=result) as download_model:
                self.assertTrue(script.download(item, root, force=True))
            self.assertFalse(target.exists(), "force 必须先移除该 catalog 条目的旧文件")
            self.assertEqual(download_model.call_args.args[0], "media:lama")
            self.assertEqual(download_model.call_args.kwargs["root"], str(root))
            self.assertTrue(callable(download_model.call_args.kwargs["detailed_progress"]))

    def test_asset_metadata_is_not_duplicated_in_the_script(self) -> None:
        source = (ROOT / "download_models.py").read_text(encoding="utf-8")
        self.assertIn("from core import model_hub", source)
        for duplicated in ("huggingface.co", "github.com", "114_434_898", "205_669_692", "67_061_725", "67_040_989"):
            self.assertNotIn(duplicated, source)


if __name__ == "__main__":
    unittest.main()
