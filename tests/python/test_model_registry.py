"""统一模型登记表：音视频共用一份记录格式。

样本量是这个结论的前提——只看 8 个本地模型时，"怎么分类"只能靠猜；
现在有 77 条（42 个 spandrel 架构 + 27 个已核实仓库 + 8 个本地）才能看出规律。
"""
from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core import registry as reg  # noqa: E402


class ShapeTests(unittest.TestCase):
    def test_registry_passes_validation(self) -> None:
        self.assertEqual(reg.validate(), [])

    def test_sample_is_large_enough_for_taxonomy(self) -> None:
        """样本太少时分类结论不可信；这条测试防止有人把表删回个位数。"""
        self.assertGreaterEqual(len(reg.load_registry()["models"]), 70)

    def test_every_task_has_at_least_one_model(self) -> None:
        counts = Counter(item["task"] for item in reg.models())
        for task, spec in reg.tasks().items():
            if spec.get("planned"):
                continue
            with self.subTest(task=task):
                self.assertGreaterEqual(counts.get(task, 0), 1, f"{task} 没有任何模型")

    def test_planned_tasks_are_explicitly_marked(self) -> None:
        """还没模型的任务必须标 planned——不能悄悄混在"支持"里。"""
        counts = Counter(item["task"] for item in reg.models())
        for task, spec in reg.tasks().items():
            with self.subTest(task=task):
                self.assertEqual(
                    bool(spec.get("planned")), counts.get(task, 0) == 0, f"{task} 的 planned 标记不对"
                )

    def test_signatures_come_from_a_small_vocabulary(self) -> None:
        """类型签名必须是小集合——这正是"统一 schema"能成立的前提。"""
        signatures = {reg.signature(item) for item in reg.models()}
        # 词表在 0.0.16 扩过一次：新增 depth / mask / json 三种媒体输出，
        # 因为去噪、深度、抠图、检测这些能力的输出不再都是"媒体->媒体"。
        self.assertLessEqual(len(signatures), 9, f"签名种类过多：{signatures}")
        self.assertTrue({"image->image", "video->video"} <= signatures)


class SourceHonestyTests(unittest.TestCase):
    def test_verified_entries_name_a_real_file(self) -> None:
        for item in reg.models(status="verified"):
            with self.subTest(model=item["id"]):
                self.assertTrue(item["source"]["repo"].count("/") == 1)
                self.assertTrue(item["source"]["file"])

    def test_arch_only_entries_do_not_claim_a_checkpoint(self) -> None:
        for item in reg.models(status="arch-only"):
            with self.subTest(model=item["id"]):
                self.assertIsNone(item["source"]["repo"])
                self.assertIsNone(item["source"]["file"])

    def test_local_entries_match_the_media_catalog(self) -> None:
        import json

        catalog = json.loads(
            (PYTHON_DIR / "media_ops" / "media_catalog.json").read_text(encoding="utf-8")
        )
        expected = {f"media:{item['id']}" for item in catalog["models"]}
        actual = {item["id"] for item in reg.models(status="local")}
        self.assertEqual(actual, expected)


class LookupTests(unittest.TestCase):
    def test_models_can_be_filtered_by_task(self) -> None:
        upscalers = reg.models(task="upscale")
        self.assertTrue(upscalers)
        self.assertTrue(all(item["task"] == "upscale" for item in upscalers))

    def test_find_by_id(self) -> None:
        sample = reg.models()[0]
        self.assertEqual(reg.find(sample["id"])["id"], sample["id"])
        self.assertIsNone(reg.find("nope:nope"))

    def test_video_tasks_use_video_signatures(self) -> None:
        for task in ("frame_interpolation", "video_super_resolution", "video_inpainting"):
            for item in reg.models(task=task):
                with self.subTest(model=item["id"]):
                    self.assertEqual(item["io"]["in"], "video")
                    self.assertEqual(item["io"]["out"], "video")


if __name__ == "__main__":
    unittest.main()
