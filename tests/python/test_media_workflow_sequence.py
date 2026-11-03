"""整段视频能力在工作流中的分派与多文件模型目录校验。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from media_ops import workflow_worker  # noqa: E402


class WorkflowSequenceTests(unittest.TestCase):
    def test_multifile_model_directory_is_not_misreported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-workflow-models-") as root_name:
            root = Path(root_name)
            with mock.patch.object(workflow_worker.capabilities, "model_path", return_value=root):
                workflow_worker._validate_models([{"bindings": {"primary": "media:propainter"}}])
            with mock.patch.object(workflow_worker.capabilities, "model_path", return_value=root / "missing"):
                with self.assertRaisesRegex(FileNotFoundError, "missing models"):
                    workflow_worker._validate_models([{"bindings": {"primary": "media:propainter"}}])

    def test_video_super_resolution_uses_sequence_entry_not_frame_planner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-workflow-vsr-") as root_name:
            root = Path(root_name)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            steps = [{"capability": "video_super_resolution", "bindings": {"upscaler": "media:basicvsrpp"}, "params": {}}]
            result = {"path": str(root / "out.mp4"), "size": 1}
            with mock.patch.object(workflow_worker.capabilities, "model_path", return_value=root / "model.pth"), \
                 mock.patch.object(workflow_worker.video_ops, "upscale_video", return_value=result) as upscale, \
                 mock.patch.object(workflow_worker.video_step, "plan", side_effect=AssertionError("frame planner must not run")):
                output = workflow_worker._run_video(source, root, steps, {}, None, 0, 1)
            upscale.assert_called_once()
            self.assertEqual(output["steps"], ["video_super_resolution"])
            self.assertEqual(output["mode"], "workflow")

    def test_video_inpainting_forwards_mask_and_subvideo_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-workflow-inpaint-") as root_name:
            root = Path(root_name)
            source = root / "input.mp4"
            source.write_bytes(b"video")
            mask = root / "masks"
            mask.mkdir()
            steps = [{
                "capability": "video_inpainting",
                "bindings": {"primary": "media:propainter"},
                "params": {"detectMode": "per_frame", "subvideoLength": 16},
            }]
            result = {"path": str(root / "clean.mp4"), "size": 1}
            with mock.patch.object(workflow_worker.capabilities, "model_path", return_value=root / "models"), \
                 mock.patch.object(workflow_worker.video_ops, "inpaint_video", return_value=result) as inpaint:
                output = workflow_worker._run_video(
                    source, root, steps, {"maskPath": str(mask)}, None, 0, 1
                )
            self.assertEqual(inpaint.call_args.kwargs["mask_path"], str(mask))
            self.assertEqual(inpaint.call_args.kwargs["detect_mode"], "per_frame")
            self.assertEqual(inpaint.call_args.kwargs["subvideo_length"], 16)
            self.assertEqual(output["steps"], ["video_inpainting"])

    def test_sequence_step_cannot_be_mixed_with_frame_steps(self) -> None:
        steps = [
            {"capability": "video_inpainting", "bindings": {"primary": "media:propainter"}, "params": {}},
            {"capability": "upscale", "bindings": {"upscaler": "media:real-esrgan-x2"}, "params": {}},
        ]
        with self.assertRaisesRegex(ValueError, "single-step workflow"):
            workflow_worker._run_video(Path("input.mp4"), Path("out"), steps, {}, None, 0, 1)


if __name__ == "__main__":
    unittest.main()
