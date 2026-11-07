"""Serial workflow support must match its actual adapters before any inference.

Tiny files and replacement model adapters only; no runtime/model downloads or
user profiles. Normalization, dispatch, image I/O and planner code remain real.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from media_ops import capabilities, image_ops, video_step, workflow_worker  # noqa: E402


def step(capability, params=None, bindings=None):
    return {"capability": capability, "bindings": bindings or capabilities.default_bindings(capability), "params": params or {}}


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="mf-workflow-contract-")))
        self.models = self.root / "models"
        self.models.mkdir()
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "MEDIAFORGE_DATA_ROOT": str(self.root), "MEDIAFORGE_DATA_DIR": str(self.root),
            "MEDIAFORGE_MODEL_DIR": str(self.models), "PYMSS_MODEL_DIR": str(self.models),
            "MEDIAFORGE_RUNTIME_ENVS_DIR": str(self.root / "runtime-envs"),
            "WEBVIEW2_USER_DATA_FOLDER": str(self.root / "webview"), "MEDIAFORGE_AUTO_EVALUATE": "0",
        }))
        self.paths = {}
        for model_id, record in capabilities._catalog().items():
            path = self.models / model_id.replace(":", "_")
            if record.get("files"):
                path.mkdir()
                for entry in record["files"]:
                    target = path / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"fixture weight")
            else:
                path.write_bytes(b"fixture weight")
            self.paths[model_id] = path
        self.stack.enter_context(mock.patch.object(capabilities, "model_path", side_effect=lambda model_id: self.paths[model_id]))
        self.image = self.root / "input.png"
        Image.new("RGB", (8, 8), (40, 80, 100)).save(self.image)
        self.video = self.root / "input.mp4"
        self.video.write_bytes(b"video fixture; adapter mocked")
        self.events, self.errors = [], []
        self.stack.enter_context(mock.patch.object(workflow_worker, "emit", side_effect=lambda kind, payload, **kw: self.events.append((kind, payload))))
        self.stack.enter_context(mock.patch.object(workflow_worker, "emit_error", side_effect=self._error))

    def _error(self, code, message, *_args, **_kw):
        self.errors.append((code, message))
        return 1

    def payload(self, steps, modality="image", **overrides):
        return {"inputs": [str(self.image if modality == "image" else self.video)],
                "outputDir": str(self.root / "out"), "modality": modality,
                "steps": steps, **({"device": "cpu"} if modality == "image" else {}), **overrides}

    def test_exported_support_matches_real_serial_executors_not_single_task_availability(self):
        listed = capabilities.list_capabilities()
        supported = {modality: {item["id"] for item in listed["capabilities"] if item["workflow"][modality]["supported"]}
                     for modality in ("image", "video")}
        self.assertEqual(supported["image"], {"upscale", "watermark_removal", "denoise"})
        self.assertEqual(supported["video"], {"upscale", "watermark_removal", "video_inpainting", "video_super_resolution"})
        for item in listed["capabilities"]:
            self.assertEqual(item["workflow"], workflow_worker.workflow_support(item["id"]))
        self.assertEqual(workflow_worker.workflow_support("video_super_resolution")["video"]["maxSteps"], 1)
        self.assertNotIn("tile", workflow_worker.workflow_support("video_super_resolution")["video"]["supportedParams"])
        self.assertNotIn("detectMode", workflow_worker.workflow_support("watermark_removal")["image"]["supportedParams"])

    def test_support_contract_does_not_import_execution_engines_or_optional_dependencies(self):
        # -S emulates a base interpreter without site-packages, even when the
        # parent suite is running in the development CPU environment.
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from media_ops.workflow_contract import workflow_support
assert workflow_support('upscale')['image']['supported']
assert workflow_support('video_inpainting')['video']['maxSteps'] == 1
assert not workflow_support('frame_interpolation')['image']['supported']
for name in ('numpy', 'PIL', 'torch', 'media_ops.image_ops', 'media_ops.video_ops', 'media_ops.workflow_worker'):
    assert name not in sys.modules, name
print('dependency-light workflow contract: ok')
"""
        result = subprocess.run([sys.executable, "-B", "-S", "-c", script,
                                 str(Path(__file__).resolve().parents[2] / "python")],
                                capture_output=True, text=True, timeout=15, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dependency-light workflow contract: ok", result.stdout)

    def test_unsupported_later_step_fails_before_loading_first_model_or_creating_outputs(self):
        payload = self.payload([step("upscale"), step("face_restoration")])
        with mock.patch.object(image_ops, "ImageRestorer") as restorer, \
             mock.patch.object(workflow_worker, "_validate_models", side_effect=AssertionError("static validation must run first")):
            self.assertEqual(workflow_worker.cmd_media_workflow(payload), 1)
        restorer.assert_not_called()
        self.assertIn("face_restoration", self.errors[-1][1])
        self.assertFalse((self.root / "out").exists())

    def test_rife_two_images_to_one_is_rejected_explicitly_instead_of_looped_per_file(self):
        support = workflow_worker.workflow_support("frame_interpolation")
        self.assertFalse(support["image"]["supported"])
        self.assertEqual(support["image"]["inputArity"], {"min": 2, "max": 2})
        payload = self.payload([step("frame_interpolation")], inputs=[str(self.image), str(self.image)])
        with mock.patch.object(image_ops, "ImageRestorer") as restorer:
            self.assertEqual(workflow_worker.cmd_media_workflow(payload), 1)
        self.assertIn("two frames", self.errors[-1][1])
        self.assertIn("single-task", self.errors[-1][1])
        restorer.assert_not_called()

    def test_modality_sequence_composition_and_unsupported_parameter_checks_are_upfront(self):
        invalid = [
            self.payload([step("denoise")], "video"),
            self.payload([step("video_super_resolution")]),
            self.payload([step("frame_interpolation")], "video"),
            self.payload([step("upscale"), step("video_super_resolution")], "video"),
            self.payload([step("watermark_removal", {"detectMode": "per_frame"})]),
            self.payload([step("watermark_removal", {"tile": 128})]),
            self.payload([step("video_super_resolution", {"tile": 128})], "video"),
            self.payload([step("upscale", {"target": "custom", "width": 0, "height": 128})]),
            self.payload([step("upscale", {"target": "custom", "width": 128, "height": 128, "fit": "nonsense"})]),
            self.payload([step("upscale", {"tile": 32})]),
        ]
        with mock.patch.object(image_ops, "ImageRestorer") as restorer, mock.patch.object(workflow_worker.video_ops, "upscale_video") as sequence:
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(workflow_worker.cmd_media_workflow(payload), 1)
        restorer.assert_not_called()
        sequence.assert_not_called()

    def test_wrong_role_and_extra_bindings_cannot_be_silently_accepted(self):
        invalid = [
            step("watermark_removal", bindings={"detector": "media:lama", "inpainter": "media:watermark-yolo11x"}),
            step("upscale", bindings={"upscaler": "media:real-esrgan-x2", "unused": "media:real-esrgan-x4"}),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "role|bindings"):
                workflow_worker.validate_workflow(self.payload([value]))

    def test_all_input_files_and_output_directory_are_checked_before_execution(self):
        with mock.patch.object(image_ops, "ImageRestorer") as restorer:
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload([step("upscale")], inputs=[str(self.image), str(self.root / "missing.png")])), 1)
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload([step("upscale")], outputDir="")), 1)
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload([step("upscale")], inputs=str(self.image))), 1)
        restorer.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_image_dimensions_and_shared_tile_reach_the_real_image_pipeline(self):
        captured = []

        class Upscaler:
            def __init__(_self, paths, **kwargs):
                captured.append({"paths": paths, **kwargs})
            def upscale(_self, image, **kwargs):
                captured[-1].update(kwargs)
                width, height = kwargs["target"]
                return np.zeros((height, width, 3), dtype=np.uint8), width / image.shape[1]

        raw = [step("upscale", {"target": "custom", "width": 192, "height": 128, "fit": "pad", "tile": 128, "scale": 3}, {"upscaler": "media:real-esrgan-x4"})]
        with mock.patch.object(image_ops, "RealESRGANUpscaler", Upscaler):
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload(raw, tile=512)), 0)
        self.assertEqual(captured[0]["target"], (192, 128))
        self.assertEqual(captured[0]["tile"], 128)
        self.assertEqual(captured[0]["preferred_scale"], 4)
        self.assertEqual(captured[0]["scale"], 3)
        self.assertEqual(captured[0]["fit"], "pad")
        self.assertEqual(captured[0]["paths"][4], str(self.paths["media:real-esrgan-x4"]))
        self.assertEqual(raw[0]["params"]["target"], "custom")
        with Image.open(self.events[-1][1]["outputs"][0]["path"]) as output:
            self.assertEqual(output.size, (192, 128))

    def test_image_watermark_preserves_explicit_zero_values(self):
        detector = mock.Mock()
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[0, 0] = 255
        detector.detect.return_value = (np.zeros((1, 4)), np.ones(1), mask)
        with mock.patch.object(image_ops.ImageRestorer, "detector_for", return_value=detector), \
             mock.patch.object(image_ops.ImageRestorer, "inpainter_for") as inpainter:
            inpainter.return_value.inpaint.side_effect = lambda image, _mask: image
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload([step("watermark_removal", {"conf": 0, "expand": 0})])), 0)
        self.assertEqual(detector.detect.call_args.kwargs, {"conf": 0.0, "expand": 0})

    def test_video_model_slot_and_tile_conflicts_fail_before_any_model_work(self):
        cases = [
            [step("upscale", bindings={"upscaler": "media:real-esrgan-x2"}), step("upscale", bindings={"upscaler": "media:swinir-m-x2"})],
            [step("upscale", {"tile": 128}), step("upscale", {"tile": 512})],
        ]
        with mock.patch.object(image_ops, "ImageRestorer") as restorer:
            for steps in cases:
                self.assertEqual(workflow_worker.cmd_media_workflow(self.payload(steps, "video")), 1)
        restorer.assert_not_called()

    def test_video_planner_zero_values_dimensions_and_tile_reach_the_frame_executor(self):
        raw = [step("watermark_removal", {"conf": 0, "expand": 0, "detectMode": "per_frame"}),
               step("upscale", {"target": "custom", "width": 192, "height": 128, "fit": "pad", "tile": 128}, {"upscaler": "media:real-esrgan-x4"})]
        result = {"path": str(self.root / "out.mp4"), "size": 1, "info": {}}
        with mock.patch.object(workflow_worker.video_ops, "process_video_steps", return_value=result) as process:
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload(raw, "video", device="cpu", crf=0)), 0)
        options = process.call_args.kwargs
        self.assertEqual(options["steps"][0].conf, 0)
        self.assertEqual(options["steps"][0].expand, 0)
        self.assertEqual(options["steps"][0].detect_mode, "per_frame")
        self.assertEqual(options["steps"][1].target, (192, 128))
        self.assertEqual(options["steps"][1].preferred_scale, 4)
        self.assertEqual(options["restorer"].tile, 128)
        self.assertEqual(options["crf"], 0)

    def test_sequence_mask_and_auxiliary_detector_missing_fail_before_video_work(self):
        self.paths[capabilities.default_bindings("watermark_removal")["detector"]].unlink()
        payload = self.payload([step("video_inpainting")], "video")
        with mock.patch.object(workflow_worker.video_ops, "inpaint_video") as inpaint:
            self.assertEqual(workflow_worker.cmd_media_workflow(payload), 1)
            self.assertIn("watermark", self.errors[-1][1])
            self.assertEqual(workflow_worker.cmd_media_workflow({**payload, "maskPath": str(self.root / "missing-mask.png")}), 1)
            self.assertIn("mask not found", self.errors[-1][1])
        inpaint.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_sequence_explicit_mask_avoids_hidden_detector_and_preserves_zero_parameters(self):
        self.paths[capabilities.default_bindings("watermark_removal")["detector"]].unlink()
        mask = self.root / "mask.png"
        Image.new("L", (8, 8), 255).save(mask)
        payload = self.payload([step("video_inpainting", {"conf": 0, "expand": 0, "subvideoLength": 16, "detectMode": "per_frame"})], "video", maskPath=str(mask), crf=0)
        with mock.patch.object(workflow_worker.video_ops, "inpaint_video", return_value={"path": "out.mp4", "size": 1}) as inpaint:
            self.assertEqual(workflow_worker.cmd_media_workflow(payload), 0)
        options = inpaint.call_args.kwargs
        self.assertEqual(options["mask_path"], str(mask))
        self.assertIsNone(options["detector_path"])
        self.assertEqual(options["conf"], 0)
        self.assertEqual(options["expand"], 0)
        self.assertEqual(options["subvideo_length"], 16)
        self.assertEqual(options["crf"], 0)

    def test_multifile_model_is_checked_completely_before_inference(self):
        (self.paths["media:propainter"] / "raft-things.pth").unlink()
        with mock.patch.object(workflow_worker.video_ops, "inpaint_video") as inpaint:
            self.assertEqual(workflow_worker.cmd_media_workflow(self.payload([step("video_inpainting")], "video")), 1)
        self.assertIn("raft-things.pth", self.errors[-1][1])
        inpaint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
