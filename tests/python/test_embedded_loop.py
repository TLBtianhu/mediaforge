"""内嵌 loop：计划 → 审批 → 执行 → 自评价 → 收敛判断。

这个 loop 不调用大模型，所以可以逐条断言它的判断：
能跑的直接跑，需要同意的挡回去，长任务交回台账，产物不对就说不对。
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

from surfaces import embedded_loop  # noqa: E402


def write_pattern(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    width, height = size
    grid = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        for x in range(width):
            grid[y, x] = (x * 4 % 256, y * 3 % 256, (x + y) % 256)
    Image.fromarray(grid).save(path)
    return path


def ok_outcome(outputs: list[str], *, attempts: list | None = None) -> dict:
    return {
        "tool": "media.image.upscale",
        "code": 0,
        "ok": True,
        "events": [],
        "result": {
            "type": "task.result",
            "payload": {"outputs": [{"path": path} for path in outputs], "completed": len(outputs)},
        },
        **({"recoveryAttempts": attempts} if attempts else {}),
    }


class PlanTests(unittest.TestCase):
    def test_media_goal_becomes_a_capability_step(self) -> None:
        plan = embedded_loop.plan_for(
            {"kind": "upscale", "inputs": ["a.png"], "outputDir": "out", "modality": "image"}
        )
        self.assertEqual(plan["tool"], "media.image.upscale")
        step = plan["arguments"]["steps"][0]
        self.assertEqual(step["capability"], "upscale")
        self.assertTrue(step["bindings"], "默认绑定应来自 capabilities.json")

    def test_explicit_tool_passthrough(self) -> None:
        plan = embedded_loop.plan_for({"tool": "system.health", "arguments": {}})
        self.assertEqual(plan["tool"], "system.health")

    def test_missing_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            embedded_loop.plan_for({"kind": "upscale", "outputDir": "out"})
        with self.assertRaises(ValueError):
            embedded_loop.plan_for({"kind": "upscale", "inputs": ["a.png"]})

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            embedded_loop.plan_for({"kind": "make_coffee", "inputs": ["a.png"], "outputDir": "out"})


class MultiStepPlanTests(unittest.TestCase):
    def test_two_capabilities_switch_to_the_workflow_tool(self) -> None:
        plan = embedded_loop.plan_for(
            {
                "modality": "image",
                "inputs": ["a.png"],
                "outputDir": "out",
                "steps": [{"capability": "watermark_removal"}, {"capability": "upscale", "params": {"scale": 2}}],
            }
        )
        self.assertEqual(plan["tool"], "media.image.workflow")
        self.assertEqual([step["capability"] for step in plan["arguments"]["steps"]], ["watermark_removal", "upscale"])
        # 每一步都补齐默认绑定
        for step in plan["arguments"]["steps"]:
            self.assertTrue(step["bindings"])

    def test_video_workflow_uses_the_video_tool(self) -> None:
        plan = embedded_loop.plan_for(
            {"modality": "video", "inputs": ["a.mp4"], "outputDir": "out",
             "steps": [{"capability": "upscale"}, {"capability": "upscale"}]}
        )
        self.assertEqual(plan["tool"], "media.video.workflow")

    def test_step_without_capability_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            embedded_loop.plan_for({"modality": "image", "inputs": ["a.png"], "outputDir": "out", "steps": [{}]})

    def test_single_step_keeps_the_single_capability_tool(self) -> None:
        plan = embedded_loop.plan_for(
            {"modality": "image", "inputs": ["a.png"], "outputDir": "out", "steps": [{"capability": "upscale"}]}
        )
        self.assertEqual(plan["tool"], "media.image.upscale")


class PrecheckTests(unittest.TestCase):
    """干净图上的去水印步骤应当被摘掉，而不是让整条链失败。"""

    def _detector(self, filled: bool):
        class FakeDetector:
            def __init__(self, *args, **kwargs):
                pass

            def detect(self, image, conf=0.25, expand=6):
                mask = np.ones(image.shape[:2], dtype=np.uint8) if filled else np.zeros(image.shape[:2], dtype=np.uint8)
                return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32), mask

        return lambda path, device: FakeDetector()

    def test_no_inputs_means_no_precheck(self) -> None:
        self.assertIsNone(embedded_loop.precheck([{"capability": "upscale"}], []))

    def test_empty_input_skips_precheck(self) -> None:
        self.assertIsNone(embedded_loop.precheck([{"capability": "watermark_removal"}], []))

    def test_clean_image_reports_no_watermark(self) -> None:
        import tempfile

        from PIL import Image as _Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            _Image.new("RGB", (32, 32), (10, 20, 30)).save(handle.name)
            check = embedded_loop.precheck(
                [{"capability": "watermark_removal"}], [handle.name], detector_factory=self._detector(False)
            )
        self.assertEqual(check["findings"][0]["code"], "no_watermark")

    def test_oversized_mask_is_flagged(self) -> None:
        import tempfile

        from PIL import Image as _Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            _Image.new("RGB", (32, 32), (10, 20, 30)).save(handle.name)
            check = embedded_loop.precheck(
                [{"capability": "watermark_removal"}], [handle.name], detector_factory=self._detector(True)
            )
        self.assertEqual(check["findings"][0]["code"], "mask_too_large")

    def test_missing_input_blocks_the_whole_plan(self) -> None:
        check = embedded_loop.precheck([{"capability": "upscale"}], ["/definitely/missing.png"])
        self.assertTrue(check["blocking"])
        self.assertEqual(check["findings"][0]["code"], "input_missing")

    def test_empty_input_blocks(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            pass
        check = embedded_loop.precheck([{"capability": "upscale"}], [handle.name])
        self.assertTrue(check["blocking"])
        self.assertEqual(check["findings"][0]["code"], "input_empty")

    def test_target_smaller_than_source_skips_upscale(self) -> None:
        import tempfile

        from PIL import Image as _Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            _Image.new("RGB", (1920, 1080), (10, 20, 30)).save(handle.name)
            check = embedded_loop.precheck(
                [{"capability": "upscale", "params": {"target": "720p"}}], [handle.name]
            )
        self.assertFalse(check["blocking"])
        self.assertEqual(check["findings"][0]["code"], "target_not_larger")
        self.assertEqual(check["findings"][0]["capability"], "upscale")

    def test_target_larger_than_source_passes(self) -> None:
        import tempfile

        from PIL import Image as _Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            _Image.new("RGB", (320, 240), (10, 20, 30)).save(handle.name)
            check = embedded_loop.precheck(
                [{"capability": "upscale", "params": {"target": "1080p"}}], [handle.name]
            )
        self.assertTrue(check["ok"])

    def test_detector_failure_does_not_block(self) -> None:
        import tempfile

        from PIL import Image as _Image

        def boom(*args, **kwargs):
            raise RuntimeError("torch missing")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            _Image.new("RGB", (32, 32), (10, 20, 30)).save(handle.name)
            check = embedded_loop.precheck(
                [{"capability": "watermark_removal"}], [handle.name], detector_factory=boom
            )
        # 预检不可用 → 不产生 block。"没预检成"和"预检通过"都不该拦住主流程
        self.assertFalse(check and check.get("blocking"), "预检不可用时不能拦住主流程")


class GateTests(unittest.TestCase):
    def test_explicit_approval_blocks_by_default(self) -> None:
        result = embedded_loop.run({"tool": "model.download", "arguments": {"modelId": "media:lama"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked"], "approval_required")

    def test_long_running_tool_is_handed_back_to_the_ledger(self) -> None:
        result = embedded_loop.run({"tool": "audio.separate", "arguments": {"model": "x", "inputs": ["a.wav"]}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked"], "long_running")

    def test_execution_exception_becomes_a_result(self) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("worker exploded")

        result = embedded_loop.run({"tool": "system.health", "arguments": {}}, dispatch=boom)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked"], "exception")


@unittest.skipUnless(PIL_AVAILABLE, "需要 numpy + Pillow")
class ConvergenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-loop-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = write_pattern(self.root / "source.png", (32, 24))

    def _goal(self) -> dict:
        return {"kind": "upscale", "inputs": [str(self.source)], "outputDir": str(self.root), "modality": "image"}

    def test_upscaled_output_passes(self) -> None:
        output = write_pattern(self.root / "out.png", (64, 48))
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: ok_outcome([str(output)]))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"], "完成")
        self.assertTrue(any("尺寸放大" in note for note in result["evaluations"][0]["notes"]))

    def test_identical_output_fails_the_verdict(self) -> None:
        same = write_pattern(self.root / "same.png", (32, 24))
        # 让产物与输入字节完全一致
        shutil.copyfile(self.source, same)
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: ok_outcome([str(same)]))
        self.assertFalse(result["ok"])
        self.assertTrue(any("完全相同" in reason for reason in result["reasons"]), result["reasons"])

    def test_missing_output_fails_the_verdict(self) -> None:
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: ok_outcome([str(self.root / "gone.png")]))
        self.assertFalse(result["ok"])
        self.assertTrue(result["reasons"])

    def test_no_outputs_at_all_fails(self) -> None:
        outcome = ok_outcome([])
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: outcome)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reasons"], ["没有产物"])

    def test_failed_execution_surfaces_the_recovery_hint(self) -> None:
        hint = {"code": "out_of_memory", "retryable": True, "actions": [{"action": "switch_device", "to": "cpu"}]}
        outcome = {
            "ok": False,
            "code": 1,
            "events": [],
            "result": {"type": "error", "payload": {"message": "CUDA out of memory", "recovery": hint}},
        }
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: outcome)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked"], "execution_failed")
        self.assertEqual(result["recovery"]["code"], "out_of_memory")

    def test_media_result_with_failed_entries_reports_the_reason(self) -> None:
        outcome = {
            "ok": False,
            "code": 0,
            "events": [],
            "result": {
                "type": "task.result",
                "payload": {
                    "outputs": [],
                    "completed": 0,
                    "failed": [{"path": "a.png", "error": "没有检测到水印", "recovery": {"code": "no_watermark", "retryable": False, "actions": []}}],
                },
            },
        }
        result = embedded_loop.run(self._goal(), dispatch=lambda *a, **k: outcome)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocked"], "execution_failed")
        self.assertIn("没有检测到水印", result["summary"])

    def test_recovery_attempts_are_reported(self) -> None:
        output = write_pattern(self.root / "out2.png", (64, 48))
        attempts = [{"code": "out_of_memory", "applied": ["reduce_param"]}]
        result = embedded_loop.run(
            self._goal(), dispatch=lambda *a, **k: ok_outcome([str(output)], attempts=attempts)
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["steps"][0]["attempts"], attempts)


if __name__ == "__main__":
    unittest.main()
