"""模型接口：从登记表生成槽位，且**不许携带布局**。

这个测试同时守两条边界：
* 后端接口里不能出现布局字段（order/group/width…），否则解耦就漏回来了
* 前端的槽位词汇表（`src/litegraph/slots.ts`）必须覆盖后端所有槽位，反之亦然
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import interface as iface  # noqa: E402
from media_ops import registry as reg  # noqa: E402

SLOTS_TS = ROOT / "src" / "litegraph" / "slots.ts"


def frontend_slot_ids() -> set[str]:
    """从 TS 词汇表里数出槽位 id（数据在 TS，检查放在 Python，避免两边各自漂移）。"""
    source = SLOTS_TS.read_text(encoding="utf-8")
    start = source.index("export const SLOTS")
    body = source[start:]
    return set(re.findall(r"^  ([a-z_][a-z0-9_]*): \{", body, flags=re.MULTILINE))


class VocabularyParityTests(unittest.TestCase):
    def test_backend_and_frontend_slot_vocabularies_match(self) -> None:
        backend = set(iface.SLOT_DEFAULTS)
        frontend = frontend_slot_ids()
        self.assertEqual(frontend, backend, f"前端缺 {backend - frontend}，后端多 {frontend - backend}")

    def test_every_registry_slot_is_known_to_the_backend(self) -> None:
        used = {slot for model in reg.models() for slot in model.get("slots") or []}
        missing = used - set(iface.SLOT_DEFAULTS)
        self.assertEqual(missing, set(), f"登记表用了后端不认识的槽位：{missing}")

    def test_every_registry_slot_exists_in_the_frontend_vocabulary(self) -> None:
        used = {slot for model in reg.models() for slot in model.get("slots") or []}
        self.assertEqual(used - frontend_slot_ids(), set())


class InterfaceGenerationTests(unittest.TestCase):
    def _audio_model(self) -> str:
        model = next(
            item for item in reg.models(task="separation") if item.get("architecture") == "mel_band_roformer"
        )
        return model["id"]

    def test_interface_uses_task_slots_and_arch_overrides(self) -> None:
        model_id = self._audio_model()
        interface = iface.interface_for(model_id)
        self.assertIsNotNone(interface)
        self.assertEqual(interface["task"], "separation")
        self.assertEqual(set(interface["slots"]), {"batch_size", "chunk_size", "overlap", "stems"})
        # mel_band_roformer 的覆盖生效
        self.assertEqual(interface["slots"]["chunk_size"]["step"], 128)
        self.assertEqual(interface["slots"]["chunk_size"]["default"], 512)
        self.assertEqual(interface["slots"]["chunk_size"]["derive"], "chunk_size_constraint")

    def test_demucs_has_no_chunk_size(self) -> None:
        model = next(
            (item for item in reg.models(task="separation") if item.get("architecture") in {"demucs", "htdemucs"}),
            None,
        )
        if model is None:
            self.skipTest("登记表里没有 demucs 条目")
        interface = iface.interface_for(model["id"])
        self.assertNotIn("chunk_size", interface["slots"])

    def test_vr_models_expose_window_and_aggression(self) -> None:
        model = next((item for item in reg.models(task="separation") if item.get("architecture") == "vr"), None)
        if model is None:
            self.skipTest("登记表里没有 vr 条目")
        slots = set(iface.interface_for(model["id"])["slots"])
        self.assertIn("window_size", slots)
        self.assertIn("aggression", slots)
        # VR 用重叠次数而不是重叠样本数
        self.assertIn("overlap_count", slots)
        self.assertNotIn("overlap", slots)

    def test_plain_models_have_no_vr_slots(self) -> None:
        model = next(
            item for item in reg.models(task="separation")
            if item.get("architecture") == "mel_band_roformer"
        )
        slots = set(iface.interface_for(model["id"])["slots"])
        for slot in ("window_size", "aggression", "overlap_count", "post_process"):
            self.assertNotIn(slot, slots, f"普通模型不该有 {slot}")

    def test_apollo_keeps_post_process_but_not_vr_window(self) -> None:
        model = next((item for item in reg.models(task="separation") if item.get("architecture") == "apollo"), None)
        if model is None:
            self.skipTest("登记表里没有 apollo 条目")
        slots = set(iface.interface_for(model["id"])["slots"])
        self.assertIn("post_process", slots)
        self.assertIn("overlap_count", slots)
        self.assertNotIn("window_size", slots)
        self.assertNotIn("aggression", slots)

    def test_unknown_model_returns_none(self) -> None:
        self.assertIsNone(iface.interface_for("nope:nope"))

    def test_generated_interfaces_are_valid(self) -> None:
        samples = reg.models()[:40]
        for model in samples:
            interface = iface.interface_for(model["id"])
            with self.subTest(model=model["id"]):
                self.assertIsNotNone(interface)
                self.assertEqual(iface.validate_interface(interface), [])


class GuardTests(unittest.TestCase):
    def test_layout_keys_are_rejected(self) -> None:
        broken = {
            "modelId": "test:broken",
            "task": "separation",
            "slots": {"chunk_size": {"bind": "chunk_size", "order": 1}},
        }
        problems = iface.validate_interface(broken)
        self.assertTrue(any("layout key order" in problem for problem in problems), problems)

    def test_unknown_keys_are_rejected(self) -> None:
        broken = {
            "modelId": "test:broken",
            "task": "separation",
            "slots": {"chunk_size": {"bind": "chunk_size", "widget": "slider"}},
        }
        problems = iface.validate_interface(broken)
        self.assertTrue(any("unknown key widget" in problem for problem in problems), problems)

    def test_unknown_slot_is_rejected(self) -> None:
        broken = {"modelId": "test:broken", "task": "x", "slots": {"mystery": {"bind": "m"}}}
        self.assertTrue(any("unknown slot" in problem for problem in iface.validate_interface(broken)))

    def test_missing_bind_is_rejected(self) -> None:
        broken = {"modelId": "test:broken", "task": "x", "slots": {"chunk_size": {"bind": ""}}}
        self.assertTrue(any("needs a bind target" in problem for problem in iface.validate_interface(broken)))


if __name__ == "__main__":
    unittest.main()
