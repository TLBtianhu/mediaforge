"""BasicVSR++ 视频超分的序列、权重与 worker 契约。"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from media_ops import capabilities, image_ops, video_step, video_worker  # noqa: E402


class VideoSuperResolutionTests(unittest.TestCase):
    def test_missing_model_fails_before_loading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-vsr-missing-") as root:
            frames = image_ops.np.zeros((1, 4, 6, 3), dtype=image_ops.np.uint8)
            with self.assertRaisesRegex(FileNotFoundError, "BasicVSR\\+\\+ model not found"):
                image_ops.upscale_video_sequence(Path(root) / "missing.pth", frames)

    def test_sequence_uses_official_architecture_and_returns_four_x_frames(self) -> None:
        import torch

        created = []

        class FakeBasicVSRPlusPlus:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                created.append(self)

            def load_state_dict(self, state, strict):
                self.state = state
                self.strict = strict

            def eval(self):
                return self

            def to(self, device):
                self.device = device
                return self

            def forward(self, sequence):
                n, t, c, h, w = sequence.shape
                upscaled = torch.nn.functional.interpolate(
                    sequence.view(-1, c, h, w), scale_factor=4, mode="nearest"
                )
                return upscaled.view(n, t, c, h * 4, w * 4)

            def __call__(self, sequence):
                return self.forward(sequence)

        basicsr = types.ModuleType("basicsr")
        basicsr.__path__ = []
        archs = types.ModuleType("basicsr.archs")
        archs.__path__ = []
        arch = types.ModuleType("basicsr.archs.basicvsrpp_arch")
        arch.BasicVSRPlusPlus = FakeBasicVSRPlusPlus
        with tempfile.TemporaryDirectory(prefix="mf-vsr-model-") as root:
            model = Path(root) / "BasicVSR_PlusPlus.pth"
            model.write_bytes(b"test")
            frames = image_ops.np.arange(2 * 4 * 6 * 3, dtype=image_ops.np.uint8).reshape(2, 4, 6, 3)
            with mock.patch.object(image_ops, "_install_torchvision_shim"), \
                 mock.patch.object(torch, "load", return_value={"state_dict": {"weight": 1}, "meta": {}}) as load, \
                 mock.patch.dict(sys.modules, {"basicsr": basicsr, "basicsr.archs": archs, "basicsr.archs.basicvsrpp_arch": arch}):
                restored = image_ops.upscale_video_sequence(model, frames, device="cpu")
            self.assertEqual(restored.shape, (2, 16, 24, 3))
            self.assertEqual(restored.dtype, image_ops.np.uint8)
            self.assertEqual(created[0].kwargs["mid_channels"], 64)
            self.assertEqual(created[0].kwargs["num_blocks"], 7)
            self.assertTrue(created[0].kwargs["is_low_res_input"])
            self.assertIsNone(created[0].kwargs["spynet_path"])
            self.assertTrue(created[0].strict)
            self.assertEqual(created[0].state, {"weight": 1})
            self.assertEqual(created[0].device, "cpu")
            self.assertTrue(load.call_args.kwargs["weights_only"])

    def test_official_checkpoint_keys_are_projected_without_loosening_strict_loading(self) -> None:
        state = image_ops._normalize_basicvsrpp_state({"state_dict": {
            "generator.upsample1.upsample_conv.weight": "up",
            "generator.spynet.basic_module.0.basic_module.3.conv.weight": "flow",
            "step_counter": 3,
        }})
        self.assertEqual(state["upconv1.weight"], "up")
        self.assertEqual(state["spynet.basic_module.0.basic_module.6.weight"], "flow")
        self.assertNotIn("step_counter", state)
        self.assertNotIn("generator.upsample1.upsample_conv.weight", state)

    def test_video_worker_uses_the_catalog_model_and_writes_a_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-vsr-worker-") as root_name:
            root = Path(root_name)
            source = root / "clip.mp4"
            source.write_bytes(b"video")
            output = root / "out" / "clip_video_sr.mp4"
            events = []
            with mock.patch.object(video_worker.capabilities, "model_path", return_value=root / "model.pth") as model_path, \
                 mock.patch.object(video_worker.video_ops, "upscale_video", side_effect=lambda *_args, **_kwargs: {"path": str(output), "size": 1}) as upscale, \
                 mock.patch.object(video_worker, "emit", side_effect=lambda kind, payload, **kwargs: events.append((kind, payload))):
                code = video_worker.cmd_media_video({
                    "mode": "video_super_resolution", "inputs": [str(source)], "outputDir": str(root / "out"), "requestId": "vsr-test",
                })
            self.assertEqual(code, 0)
            model_path.assert_called_once_with("media:basicvsrpp")
            upscale.assert_called_once()
            self.assertEqual(events[-1][1]["completed"], 1)
            self.assertEqual(events[-1][1]["failed"], [])

    def test_sequence_capability_is_registered_without_becoming_a_frame_step(self) -> None:
        self.assertIn("video_super_resolution", video_step.SEQUENCE_CAPABILITIES)
        steps = capabilities.normalize_steps(capabilities.steps_from_mode("video_super_resolution"))
        self.assertEqual(steps[0]["bindings"], {"upscaler": "media:basicvsrpp"})
        self.assertEqual(capabilities.mode_for_steps(steps), "video_super_resolution")
        self.assertNotIn("video_super_resolution", video_step.registered())


if __name__ == "__main__":
    unittest.main()
