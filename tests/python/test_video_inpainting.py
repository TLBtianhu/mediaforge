"""ProPainter 固定 mask 适配器的模型路径与 worker 契约。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from media_ops import capabilities, propainter_adapter, video_ops, video_step, video_worker  # noqa: E402


class VideoInpaintingTests(unittest.TestCase):
    def test_missing_any_official_weight_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-propainter-") as root_name:
            root = Path(root_name)
            with self.assertRaisesRegex(FileNotFoundError, "ProPainter.pth"):
                video_ops._propainter_model_paths(root)
            for name in ("ProPainter.pth", "recurrent_flow_completion.pth", "raft-things.pth"):
                (root / name).write_bytes(b"test")
            paths = video_ops._propainter_model_paths(root)
            self.assertEqual(set(paths), {"propainter", "flow", "raft"})

    def test_inpaint_video_checks_models_before_importing_the_adapter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-propainter-worker-") as root_name:
            root = Path(root_name)
            source = root / "clip.mp4"
            source.write_bytes(b"video")
            mask = root / "mask.png"
            mask.write_bytes(b"mask")
            with self.assertRaisesRegex(FileNotFoundError, "ProPainter model file not found"):
                video_ops.inpaint_video(root / "models", source, root / "out", mask_path=mask)

    def test_official_raft_keys_are_mapped_to_the_streaming_model(self) -> None:
        state = propainter_adapter.normalize_raft_state({
            "module.fnet.conv1.weight": "init",
            "module.cnet.layer2.0.norm1.weight": "norm",
            "module.cnet.layer2.0.norm3.weight": "downsample_norm",
            "module.update_block.gru.convz2.weight": "gru",
            "module.update_block.mask.0.weight": "mask",
        })
        self.assertEqual(state["fnet.features.init_block.conv.weight"], "init")
        self.assertEqual(state["cnet.features.stage2.unit1.body.conv1.bn.weight"], "norm")
        self.assertEqual(state["cnet.features.stage2.unit1.identity_conv.bn.weight"], "downsample_norm")
        self.assertEqual(state["update_block.gru.vertical_gru.conv_z.conv.weight"], "gru")
        self.assertEqual(state["update_block.mask.conv1.conv.weight"], "mask")

    def test_official_flow_keys_are_mapped_to_the_streaming_model(self) -> None:
        state = propainter_adapter.normalize_flow_state({
            "encoder1.0.conv1.0.weight": "encoder",
            "feat_prop_module.deform_align.backward_.conv_offset.0.weight": "offset",
            "feat_prop_module.backbone.forward_.2.weight": "backbone",
            "edgeDetector.projection.0.weight": "edge",
        })
        self.assertEqual(state["hg.down_seq.down2.block1.conv1.conv.weight"], "encoder")
        self.assertEqual(state["hg.skip_seq.skip4.feat_prop_module.deform_align.backward_.conv_offset.conv1.conv.weight"], "offset")
        self.assertEqual(state["hg.skip_seq.skip4.feat_prop_module.backbone.forward_.conv2.conv.weight"], "backbone")
        self.assertEqual(state["edgeDetector.proj.conv.weight"], "edge")

    def test_official_propainter_keys_are_mapped_to_the_streaming_model(self) -> None:
        state = propainter_adapter.normalize_propainter_state({
            "encoder.layers.2.weight": "encoder",
            "decoder.0.conv.weight": "decoder",
            "feat_prop_module.deform_align.backward_1.conv_offset.0.weight": "offset",
            "feat_prop_module.fuse.0.weight": "fuse",
        })
        self.assertEqual(state["encoder.layers.1.conv.weight"], "encoder")
        self.assertEqual(state["decoder.unit1.conv1.conv.weight"], "decoder")
        self.assertEqual(state["feat_prop_module.deform_align.backward_1.conv_offset.conv1.conv.weight"], "offset")
        self.assertEqual(state["feat_prop_module.fuse.conv1.conv.weight"], "fuse")

    def test_worker_uses_explicit_mask_and_the_catalog_model(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-propainter-route-") as root_name:
            root = Path(root_name)
            source = root / "clip.mp4"
            source.write_bytes(b"video")
            mask = root / "mask.png"
            mask.write_bytes(b"mask")
            output = root / "out" / "clip_video_clean.mp4"
            events = []
            with mock.patch.object(video_worker.capabilities, "model_path", return_value=root / "models") as model_path, \
                 mock.patch.object(video_worker.video_ops, "inpaint_video", return_value={"path": str(output), "size": 1}) as inpaint, \
                 mock.patch.object(video_worker, "emit", side_effect=lambda kind, payload, **kwargs: events.append((kind, payload))):
                code = video_worker.cmd_media_video({
                    "mode": "video_inpainting", "inputs": [str(source)], "outputDir": str(root / "out"),
                    "maskPath": str(mask), "requestId": "inpaint-test",
                })
            self.assertEqual(code, 0)
            model_path.assert_called_once_with("media:propainter")
            self.assertEqual(inpaint.call_args.kwargs["mask_path"], str(mask))
            self.assertIsNone(inpaint.call_args.kwargs["detector_path"])
            self.assertEqual(events[-1][1]["completed"], 1)
            self.assertEqual(events[-1][1]["failed"], [])

    def test_capability_is_a_sequence_adapter_not_a_frame_step(self) -> None:
        self.assertIn("video_inpainting", video_step.SEQUENCE_CAPABILITIES)
        steps = capabilities.normalize_steps(capabilities.steps_from_mode("video_inpainting"))
        self.assertEqual(steps[0]["bindings"], {"primary": "media:propainter"})
        self.assertEqual(capabilities.mode_for_steps(steps), "video_inpainting")
        self.assertNotIn("video_inpainting", video_step.registered())


if __name__ == "__main__":
    unittest.main()
