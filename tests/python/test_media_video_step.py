"""视频工作流"步骤计划"注册表的契约。

2026-10-29：`workflow_worker._run_video` 里那串 `if capability == ...` 被收成注册表。
这里钉住两件事：
  1. 计划是纯计算（不加载模型），所以能在没有 torch 的解释器里测；
  2. 加载哪个权重由 catalog 声明决定，不看模型 id 里有没有 x4 字样。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import video_step  # noqa: E402


def step_inputs(capability, params=None, bindings=None, payload=None):
    return video_step.StepInputs(
        capability=capability,
        params=dict(params or {}),
        bindings=dict(bindings or {}),
        payload=dict(payload or {}),
    )


WATERMARK_BINDINGS = {"detector": "media:watermark-yolo11x", "inpainter": "media:lama"}


class RegistryTests(unittest.TestCase):
    def test_implemented_video_capabilities_are_registered(self) -> None:
        self.assertEqual(video_step.registered(), ("upscale", "watermark_removal"))

    def test_unknown_capability_lists_what_is_available(self) -> None:
        with self.assertRaises(ValueError) as caught:
            video_step.plan(step_inputs("face_restoration"))
        message = str(caught.exception)
        self.assertIn("face_restoration", message)
        self.assertIn("upscale", message)


class WatermarkPlanTests(unittest.TestCase):
    def test_builds_detector_and_inpainter_paths(self) -> None:
        plan = video_step.plan(step_inputs("watermark_removal", bindings=WATERMARK_BINDINGS))
        self.assertEqual(plan.mode, "watermark")
        self.assertEqual(plan.scale, 1.0)
        self.assertIsNone(plan.preferred_scale)
        self.assertEqual(set(plan.model_paths), {"detector", "inpainter"})
        self.assertTrue(plan.model_paths["detector"].endswith("watermark_yolo11x.pt"))
        self.assertTrue(plan.model_paths["inpainter"].endswith("big-lama.pt"))
        self.assertEqual(plan.suffix, "clean")

    def test_step_params_win_over_payload_and_payload_is_the_fallback(self) -> None:
        plan = video_step.plan(
            step_inputs(
                "watermark_removal",
                params={"conf": 0.5},
                payload={"conf": 0.9, "expand": 12},
                bindings=WATERMARK_BINDINGS,
            )
        )
        self.assertEqual(plan.conf, 0.5)  # 步骤级参数优先
        self.assertEqual(plan.expand, 12)  # 步骤没给 -> 用 payload

    def test_detect_mode_defaults_to_fixed(self) -> None:
        plan = video_step.plan(step_inputs("watermark_removal", bindings=WATERMARK_BINDINGS))
        self.assertEqual(plan.detect_mode, "fixed")


class UpscalePlanTests(unittest.TestCase):
    def test_x4_model_loads_the_x4_slot(self) -> None:
        plan = video_step.plan(step_inputs("upscale", bindings={"upscaler": "media:real-esrgan-x4"}))
        self.assertEqual(plan.mode, "upscale")
        self.assertEqual(plan.preferred_scale, 4)
        self.assertEqual(list(plan.model_paths), ["upscaler_x4"])
        self.assertTrue(plan.model_paths["upscaler_x4"].endswith("RealESRGAN_x4plus.pth"))
        self.assertEqual(plan.suffix, "sr")

    def test_x2_model_loads_the_x2_slot(self) -> None:
        plan = video_step.plan(step_inputs("upscale", bindings={"upscaler": "media:real-esrgan-x2"}))
        self.assertEqual(plan.preferred_scale, 2)
        self.assertEqual(list(plan.model_paths), ["upscaler_x2"])
        self.assertTrue(plan.model_paths["upscaler_x2"].endswith("RealESRGAN_x2plus.pth"))

    def test_requested_scale_is_independent_of_model_scale(self) -> None:
        """用户要 3×，但只有一个 2× 权重：两者必须分开记，不能互相覆盖。"""
        plan = video_step.plan(
            step_inputs("upscale", params={"scale": 3}, bindings={"upscaler": "media:real-esrgan-x2"})
        )
        self.assertEqual(plan.scale, 3.0)
        self.assertEqual(plan.preferred_scale, 2)


class SuffixTests(unittest.TestCase):
    def test_suffix_chain_follows_step_order(self) -> None:
        self.assertEqual(video_step.suffix_for_steps(["watermark_removal", "upscale"]), "clean_sr")
        self.assertEqual(video_step.suffix_for_steps(["upscale"]), "sr")

    def test_unknown_capability_still_yields_a_readable_suffix(self) -> None:
        self.assertEqual(video_step.suffix_for_steps(["frame_interpolation"]), "frame_interpolation")

    def test_empty_steps_fall_back_to_workflow(self) -> None:
        self.assertEqual(video_step.suffix_for_steps([]), "workflow")


if __name__ == "__main__":
    unittest.main()
