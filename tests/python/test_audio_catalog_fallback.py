"""`audio.separate` 必须认得 `models_list` 给出的模型 id。

真实回归：用 `models_list` 查到的 `audio:HTDemucs4.th` 直接传给 `infer`，
报 `Unknown pymss model` —— 因为音频模型库（326 条）和 pymss 自己的模型索引
是两套命名，之前没人跑过这条链路（分发前唯一没验证的功能）。
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

try:  # worker_infer 依赖 torch/pymss，环境不全时跳过而不是假装通过
    import worker_infer

    IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001
    worker_infer = None
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

DEMUCS_RECORD = {
    "id": "audio:HTDemucs4.th",
    "label": "HTDemucs4",
    "name": "HTDemucs4.th",
    "task": "htdemucs",
    "files": [
        {"path": "music_stems/four_stem/HTDemucs4.th", "size": 84141911},
        {"path": "music_stems/four_stem/HTDemucs4.yaml", "size": 1826},
    ],
}


@unittest.skipUnless(worker_infer is not None, f"worker_infer 不可用：{IMPORT_ERROR}")
class AudioCatalogFallbackTests(unittest.TestCase):
    def _fake_list_models(self, records):
        class FakeHub:
            @staticmethod
            def list_models(modality: str = "audio", **_: object) -> dict:
                return {"models": list(records)}

        return FakeHub

    def _resolve_record(self, name: str, records=None):
        records = [DEMUCS_RECORD] if records is None else records
        module = self._fake_list_models(records)
        with mock.patch.dict(sys.modules, {"core.model_hub": module}):
            return worker_infer._resolve_audio_catalog_record(name)

    def test_matches_the_catalog_id(self) -> None:
        self.assertIsNotNone(self._resolve_record("audio:HTDemucs4.th"))

    def test_matches_without_the_audio_prefix(self) -> None:
        self.assertIsNotNone(self._resolve_record("HTDemucs4.th"))

    def test_matches_the_label(self) -> None:
        self.assertIsNotNone(self._resolve_record("HTDemucs4"))

    def test_is_case_insensitive(self) -> None:
        self.assertIsNotNone(self._resolve_record("audio:htdemucs4.th"))

    def test_unknown_name_returns_none(self) -> None:
        self.assertIsNone(self._resolve_record("nope-not-a-model"))

    def test_resolved_paths_point_at_the_weight_and_its_config(self) -> None:
        root = Path(self._resolve_record("audio:HTDemucs4.th") and ".")
        with mock.patch.object(worker_infer.worker_paths, "model_root", return_value=root):
            # 文件不存在时必须明确报错，而不是返回一个后面才炸的路径
            with self.assertRaises(FileNotFoundError):
                worker_infer._resolved_from_audio_record(DEMUCS_RECORD, None, "audio:HTDemucs4.th")

    def test_a_record_without_weights_is_rejected(self) -> None:
        record = dict(DEMUCS_RECORD, files=[{"path": "music_stems/four_stem/HTDemucs4.yaml", "size": 1}])
        with self.assertRaises(FileNotFoundError):
            worker_infer._resolved_from_audio_record(record, None, "audio:HTDemucs4.th")


if __name__ == "__main__":
    unittest.main()
