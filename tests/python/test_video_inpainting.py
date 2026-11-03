"""ProPainter 固定 mask 适配器的模型路径与 worker 契约。"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

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

    def test_mask_directory_is_mapped_to_each_frame(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-propainter-masks-") as root_name:
            root = Path(root_name)
            frames_dir = root / "frames"
            source_masks = root / "source-masks"
            output_masks = root / "output-masks"
            frames_dir.mkdir()
            source_masks.mkdir()
            for index in range(3):
                Image.fromarray(np.full((8, 8, 3), index * 10, dtype=np.uint8)).save(frames_dir / f"f_{index + 1:06d}.png")
                mask = np.zeros((8, 8), dtype=np.uint8)
                mask[index, index] = 1
                Image.fromarray(mask).save(source_masks / f"m_{index + 1:06d}.png")
            count = propainter_adapter.prepare_masks(
                sorted(frames_dir.glob("f_*.png")), output_masks, mask_path=source_masks
            )
            self.assertEqual(count, 3)
            for index in range(3):
                restored = np.asarray(Image.open(output_masks / f"m_{index + 1:06d}.png"))
                self.assertEqual(int(restored[index, index]), 1)

    def test_mask_directory_rejects_a_frame_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-propainter-mask-count-") as root_name:
            root = Path(root_name)
            frame = root / "f_000001.png"
            Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8)).save(frame)
            masks = root / "masks"
            masks.mkdir()
            with self.assertRaisesRegex(ValueError, "0 masks for 1 frames"):
                propainter_adapter.prepare_masks([frame], root / "out", mask_path=masks)

    def test_per_frame_detection_allows_empty_frames(self) -> None:
        class Detector:
            def detect(self, frame, **_kwargs):
                if float(frame.mean()) == 0:
                    return np.zeros((0, 4)), np.zeros((0,)), np.zeros((1,), dtype=np.uint8)
                return np.array([[1, 1, 3, 3]]), np.array([0.9]), np.array([255], dtype=np.uint8)

        with tempfile.TemporaryDirectory(prefix="mf-propainter-detect-") as root_name:
            root = Path(root_name)
            frames = []
            for index, value in enumerate((0, 100), start=1):
                path = root / f"f_{index:06d}.png"
                Image.fromarray(np.full((8, 8, 3), value, dtype=np.uint8)).save(path)
                frames.append(path)
            with mock.patch.dict(sys.modules, {"cv2": None}), \
                 mock.patch("media_ops.image_ops.boxes_to_mask", side_effect=lambda shape, _boxes, expand: np.ones(shape, dtype=np.uint8)):
                count = propainter_adapter.prepare_masks(
                    frames, root / "masks", detector=Detector(), detect_mode="per_frame"
                )
            self.assertEqual(count, 2)
            first = np.asarray(Image.open(root / "masks" / "m_000001.png"))
            second = np.asarray(Image.open(root / "masks" / "m_000002.png"))
            self.assertFalse(first.any())
            self.assertTrue(second.any())

    def test_streaming_adapter_receives_the_subvideo_window(self) -> None:
        captured = {}

        class Iterator:
            def __init__(self, *_args, **kwargs):
                captured.update(kwargs)
                self.frames = types.SimpleNamespace()
                self.masks = types.SimpleNamespace()

            def __iter__(self):
                yield np.zeros((1, 8, 8, 3), dtype=np.uint8)

        package = types.ModuleType("propainter")
        package.__path__ = []
        video = types.ModuleType("propainter.propainter_video")
        video.FilePathDirSequencer = lambda path: path
        video.RawFrameSequencer = lambda data: data
        video.RawMaskSequencer = lambda data: data
        video.ScaledProPainterIterator = Iterator
        with tempfile.TemporaryDirectory(prefix="mf-propainter-window-") as root_name:
            root = Path(root_name)
            frames = root / "frames"
            masks = root / "masks"
            frames.mkdir()
            masks.mkdir()
            Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8)).save(frames / "f_000001.png")
            Image.fromarray(np.ones((8, 8), dtype=np.uint8)).save(masks / "m_000001.png")
            models = {"raft": "raft", "flow": "flow", "propainter": "pp"}
            with mock.patch.dict(sys.modules, {"propainter": package, "propainter.propainter_video": video}), \
                 mock.patch.object(propainter_adapter, "load_raft", return_value=object()), \
                 mock.patch.object(propainter_adapter, "load_flow_completion", return_value=object()), \
                 mock.patch.object(propainter_adapter, "load_propainter", return_value=object()):
                count = propainter_adapter.inpaint_frames(
                    frames, masks, models, root / "out", use_cuda=False, subvideo_length=17
                )
            self.assertEqual(count, 1)
            self.assertEqual(captured["pp_window_size"], 17)
            self.assertTrue((root / "out" / "o_000001.png").is_file())

    def test_subvideo_window_is_validated_by_the_capability(self) -> None:
        with self.assertRaises(ValueError):
            capabilities.normalize_steps([{
                "capability": "video_inpainting",
                "bindings": {"primary": "media:propainter"},
                "params": {"subvideoLength": 4},
            }])

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
            self.assertEqual(inpaint.call_args.kwargs["detect_mode"], "fixed")
            self.assertEqual(inpaint.call_args.kwargs["subvideo_length"], 80)
            self.assertEqual(events[-1][1]["completed"], 1)
            self.assertEqual(events[-1][1]["failed"], [])

    def test_capability_is_a_sequence_adapter_not_a_frame_step(self) -> None:
        self.assertIn("video_inpainting", video_step.SEQUENCE_CAPABILITIES)
        steps = capabilities.normalize_steps(capabilities.steps_from_mode("video_inpainting"))
        self.assertEqual(steps[0]["bindings"], {"primary": "media:propainter"})
        self.assertEqual(steps[0]["params"]["subvideoLength"], 80)
        self.assertEqual(steps[0]["params"]["detectMode"], "fixed")
        self.assertEqual(capabilities.mode_for_steps(steps), "video_inpainting")
        self.assertNotIn("video_inpainting", video_step.registered())


if __name__ == "__main__":
    unittest.main()
