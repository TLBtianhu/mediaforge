"""能力声明与实现必须对得上。

2026-10-29 之前这件事靠一个手写的 `implemented: True/False` 维持：
声明在 `CAPABILITIES` 常量里，适配器在 `video_step.register()` / `image_ops` 里，
**两个地方谁也不会因为对方变了而报错**。删掉一个适配器忘了改布尔 → UI 继续显示"可用"，
用户点下去才在运行时报错。

现在 `implemented` 由注册表算出来，`audit_capabilities()` 列四类漂移。这组测试钉住：
① 当前只剩"计划中"的四条缺口；② 审计真的能发现新漂移（不是永远返回空）；
③ 手写 implemented 会被忽略并计入审计。
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

from media_ops import capabilities  # noqa: E402

# 已经声明、还没适配器的能力。这个集合只能变小（做完一条删一条）——
# 变大的意思是"又画了一张饼"，那是产品决定，不该由代码悄悄接受。
PLANNED_CAPABILITIES = {
    "face_restoration",
    "frame_interpolation",
    "video_inpainting",
    "video_super_resolution",
}

ADAPTER_CAPABILITIES = {"upscale", "watermark_removal"}


class CapabilityManifestTests(unittest.TestCase):
    def test_declarations_are_loaded_from_the_json_manifest(self) -> None:
        self.assertTrue(capabilities.CAPABILITIES_PATH.is_file())
        payload = json.loads(capabilities.CAPABILITIES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("version"), 1)
        declared = [item["id"] for item in payload["capabilities"]]
        self.assertEqual(declared, [item["id"] for item in capabilities.CAPABILITIES])

    def test_implemented_is_never_hand_written_in_the_manifest(self) -> None:
        payload = json.loads(capabilities.CAPABILITIES_PATH.read_text(encoding="utf-8"))
        hand_written = [item["id"] for item in payload["capabilities"] if "implemented" in item]
        self.assertEqual(hand_written, [], "实现与否必须由注册表算出，不能手写")

    def test_adapters_are_exactly_the_two_live_capabilities(self) -> None:
        self.assertEqual(capabilities.adapter_capability_ids(), ADAPTER_CAPABILITIES)

    def test_list_capabilities_computes_the_flag_from_the_registry(self) -> None:
        listed = capabilities.list_capabilities()
        reported = {item["id"]: item["implemented"] for item in listed["capabilities"]}
        self.assertEqual({key for key, value in reported.items() if value}, ADAPTER_CAPABILITIES)
        self.assertEqual({key for key, value in reported.items() if not value}, PLANNED_CAPABILITIES)

    def test_list_capabilities_exposes_the_audit(self) -> None:
        """漂移不能只让测试知道——MCP / UI 的调用方也要能看见。"""
        listed = capabilities.list_capabilities()
        self.assertIn("audit", listed)
        self.assertEqual(set(listed["audit"]["declared_without_adapter"]), PLANNED_CAPABILITIES)

    def test_audit_reports_only_the_planned_gaps(self) -> None:
        audit = capabilities.audit_capabilities()
        self.assertEqual(set(audit["declared_without_adapter"]), PLANNED_CAPABILITIES)
        self.assertEqual(audit["adapter_without_declaration"], [])
        self.assertEqual(audit["declared_model_missing"], [])
        self.assertEqual(audit["hand_written_implemented"], [])

    def test_audit_detects_a_freshly_declared_but_unimplemented_capability(self) -> None:
        """审计必须真的在算，而不是永远返回空列表。"""
        invented = {
            "id": "test_only_capability",
            "label": "仅测试",
            "labelEn": "Test only",
            "modalities": ["image"],
            "roles": {"primary": {"label": "主模型", "task": "super_resolution", "required": True}},
            "modelIds": ["media:real-esrgan-x2"],
            "params": {},
        }
        with mock.patch.object(capabilities, "CAPABILITIES", capabilities.CAPABILITIES + [invented]):
            audit = capabilities.audit_capabilities()
        self.assertIn("test_only_capability", audit["declared_without_adapter"])

    def test_audit_detects_a_model_that_the_catalog_does_not_have(self) -> None:
        broken = {
            "id": "upscale_with_ghost_model",
            "label": "幽灵模型",
            "labelEn": "Ghost model",
            "modalities": ["image"],
            "roles": {"upscaler": {"label": "超分模型", "task": "super_resolution", "required": True}},
            "modelIds": ["media:does-not-exist"],
            "params": {},
        }
        with mock.patch.object(capabilities, "CAPABILITIES", capabilities.CAPABILITIES + [broken]):
            audit = capabilities.audit_capabilities()
        self.assertIn("media:does-not-exist", audit["declared_model_missing"])

    def test_normalize_steps_rejects_a_planned_capability(self) -> None:
        with self.assertRaises(ValueError) as caught:
            capabilities.normalize_steps(
                [{"capability": "face_restoration", "bindings": {"face_restorer": "media:gfpgan-v14"}}]
            )
        self.assertIn("not implemented", str(caught.exception))

    def test_normalize_steps_accepts_the_live_capabilities(self) -> None:
        steps = capabilities.steps_from_mode("watermark_upscale")
        normalized = capabilities.normalize_steps(steps)
        self.assertEqual([step["capability"] for step in normalized], ["watermark_removal", "upscale"])

    def test_hand_written_flag_is_ignored_but_reported(self) -> None:
        """旧写法的 implemented 不清掉也不会生效，但审计要把它点出来。"""
        payload = {
            "version": 1,
            "capabilities": [
                {
                    "id": "upscale",
                    "label": "超分",
                    "labelEn": "Upscale",
                    "modalities": ["image"],
                    "roles": {"upscaler": {"label": "超分模型", "task": "super_resolution", "required": True}},
                    "modelIds": ["media:real-esrgan-x2"],
                    "params": {},
                    "implemented": False,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "capabilities.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(capabilities, "CAPABILITIES_PATH", path):
                declarations, flagged = capabilities._load_declarations()
        self.assertEqual(flagged, ["upscale"])
        self.assertNotIn("implemented", declarations[0])


class DefaultModelTests(unittest.TestCase):
    """默认模型只能来自 `capabilities.json`，不许在别处硬编码 id。"""

    def test_default_bindings_come_from_the_manifest(self) -> None:
        self.assertEqual(capabilities.default_bindings("upscale"), {"upscaler": "media:real-esrgan-x2"})
        self.assertEqual(
            set(capabilities.default_bindings("watermark_removal")), {"detector", "inpainter"}
        )

    def test_default_model_by_scale(self) -> None:
        self.assertEqual(capabilities.default_model_by_scale("upscale", 2), "media:real-esrgan-x2")
        self.assertEqual(capabilities.default_model_by_scale("upscale", 4), "media:real-esrgan-x4")
        with self.assertRaises(KeyError):
            capabilities.default_model_by_scale("upscale", 3)

    def test_steps_from_mode_reuses_the_same_defaults(self) -> None:
        steps = capabilities.steps_from_mode("watermark_upscale")
        bindings = {step["capability"]: step["bindings"] for step in steps}
        self.assertEqual(bindings["upscale"], capabilities.default_bindings("upscale"))
        self.assertEqual(bindings["watermark_removal"], capabilities.default_bindings("watermark_removal"))


class SuffixConsistencyTests(unittest.TestCase):
    def test_video_ops_mode_suffix_matches_capability_suffixes(self) -> None:
        """两套后缀表（mode→suffix / capability→suffix）必须一致，否则文件名会漂。"""
        from media_ops import video_ops, video_step

        pairs = {
            "upscale": ["upscale"],
            "watermark": ["watermark_removal"],
            "watermark_upscale": ["watermark_removal", "upscale"],
        }
        for mode, capability_ids in pairs.items():
            self.assertEqual(
                video_ops._MODE_SUFFIX[mode],
                video_step.suffix_for_steps(capability_ids),
                f"mode {mode} 的后缀两处不一致",
            )


class SharedHelperTests(unittest.TestCase):
    def test_srt_time_is_defined_once(self) -> None:
        """`_srt_time` 原来在 asr.py 与 audio_tools_op_bridge.py 各一份，现已收进 common.py。"""
        hits = [
            path.name
            for path in PYTHON_DIR.rglob("*.py")
            if "def _srt_time(" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(hits, ["common.py"])


if __name__ == "__main__":
    unittest.main()
