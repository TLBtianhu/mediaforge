"""Single-task parameters reach the actual image/video adapter boundary.

All files live in a temporary profile; model inference is replaced with small
adapters, while normalization, worker dispatch, image IO and result handling run.
"""
from __future__ import annotations

from contextlib import ExitStack
import os
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
from media_ops import capabilities, image_ops, image_worker, precheck, task_params, video_worker  # noqa: E402


class MediaTaskParamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="mf-task-params-")))
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "MEDIAFORGE_DATA_ROOT": str(self.root), "MEDIAFORGE_DATA_DIR": str(self.root),
            "MEDIAFORGE_MODEL_DIR": str(self.root / "models"),
        }))
        self.source = self.root / "input.png"
        Image.new("RGB", (8, 8), (60, 100, 140)).save(self.source)
        self.events, self.errors = [], []
        self.stack.enter_context(mock.patch.object(capabilities, "model_path", side_effect=lambda name: self.root / "models" / name.replace(":", "_")))
        self.stack.enter_context(mock.patch.object(image_worker.evaluation, "attach"))
        for worker in (image_worker, video_worker):
            self.stack.enter_context(mock.patch.object(worker, "emit", side_effect=lambda kind, payload, **kw: self.events.append((kind, payload))))
            self.stack.enter_context(mock.patch.object(worker, "emit_error", side_effect=self._error))

    def _error(self, code, message, *_args, **_kwargs):
        self.errors.append((code, message))
        return 1

    def _payload(self, capability, bindings, params=None, **overrides):
        return {
            "inputs": [str(self.source)], "outputDir": str(self.root / "out"), "device": "cpu",
            "steps": [{"capability": capability, "bindings": bindings, "params": params or {}}],
            **overrides,
        }

    def test_image_custom_size_tile_scale_and_binding_reach_upscaler(self) -> None:
        captured = {}

        class Upscaler:
            def __init__(_self, paths, **kwargs):
                captured.update(paths=paths, **kwargs)

            def upscale(_self, image, **kwargs):
                captured.update(kwargs)
                width, height = kwargs["target"]
                return np.zeros((height, width, 3), dtype=np.uint8), width / image.shape[1]

        payload = self._payload("upscale", {"upscaler": "media:real-esrgan-x4"}, {
            "target": "custom", "width": 192, "height": 128, "fit": "pad", "scale": 3, "tile": 128,
        })
        with mock.patch.object(image_ops, "RealESRGANUpscaler", Upscaler):
            self.assertEqual(image_worker.cmd_media_image(payload), 0)
        self.assertEqual(self.errors, [])
        self.assertEqual(captured["target"], (192, 128))
        self.assertEqual(captured["tile"], 128)
        self.assertEqual(captured["scale"], 3)
        self.assertEqual(captured["preferred_scale"], 4)
        self.assertEqual(captured["fit"], "pad")
        self.assertTrue(str(captured["paths"][4]).endswith("media_real-esrgan-x4"))
        with Image.open(self.events[-1][1]["outputs"][0]["path"]) as output:
            self.assertEqual(output.size, (192, 128))
        self.assertEqual(payload["steps"][0]["params"]["target"], "custom", "normalization must not mutate the caller")

    def test_image_detection_preserves_confidence_including_zero(self) -> None:
        for conf in (0, 0.9):
            with self.subTest(conf=conf), mock.patch.object(image_ops, "detect_objects", return_value=[]) as detect:
                code = image_worker.cmd_media_image(self._payload("object_detection", {"detector": "media:yolo11n"}, {"conf": conf}))
                self.assertEqual(code, 0)
                self.assertEqual(detect.call_args.kwargs["conf"], conf)
        self.assertEqual(self.errors, [])

    def test_image_watermark_zero_confidence_and_expansion_reach_detector(self) -> None:
        detector = mock.Mock()
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[0, 0] = 255
        detector.detect.return_value = (np.zeros((1, 4)), np.ones(1), mask)
        inpainter = mock.Mock()
        inpainter.inpaint.side_effect = lambda image, _mask: image
        with mock.patch.object(image_ops, "WatermarkDetector", return_value=detector), \
             mock.patch.object(image_ops, "LamaInpainter", return_value=inpainter):
            code = image_worker.cmd_media_image(self._payload("watermark_removal", capabilities.default_bindings("watermark_removal"), {"conf": 0, "expand": 0}))
        self.assertEqual(code, 0)
        self.assertEqual(self.events[-1][1]["failed"], [])
        self.assertEqual(detector.detect.call_args.kwargs, {"conf": 0, "expand": 0})

    def test_denoise_step_binding_selects_the_requested_model(self) -> None:
        model = "media:esrgan-1x-denoise-realplksr"
        with mock.patch.object(image_ops, "run_scale1_model", side_effect=lambda _model, image, **kw: image) as denoise:
            self.assertEqual(image_worker.cmd_media_image(self._payload("denoise", {"denoiser": model})), 0)
            self.assertTrue(str(denoise.call_args.args[0]).endswith(model.replace(":", "_")))

    def test_image_interpolation_preserves_timestep_zero_and_endpoints(self) -> None:
        for timestep in (0, 0.25, 1):
            payload = self._payload("frame_interpolation", {"interpolator": "media:rife-v46"}, {"timestep": timestep}, inputs=[str(self.source), str(self.source)])
            with self.subTest(timestep=timestep), mock.patch.object(image_ops, "interpolate_frames", side_effect=lambda _model, first, second, value: first) as interpolate:
                self.assertEqual(image_worker.cmd_media_image(payload), 0)
                self.assertEqual(interpolate.call_args.args[3], timestep)

    def test_video_custom_size_scale_fit_and_tile_reach_processing(self) -> None:
        payload = self._payload("upscale", {"upscaler": "media:real-esrgan-x4"}, {
            "target": "custom", "width": 1920, "height": 1080, "scale": 3, "fit": "cover", "tile": 256,
        }, crf=0)
        with mock.patch.object(image_ops, "ImageRestorer") as restorer, \
             mock.patch.object(video_worker.video_ops, "process_video", return_value={"path": "out.mp4"}) as process:
            self.assertEqual(video_worker.cmd_media_video(payload), 0)
        self.assertEqual(restorer.call_args.kwargs["tile"], 256)
        self.assertTrue(restorer.call_args.kwargs["model_paths"]["upscaler_x4"].endswith("media_real-esrgan-x4"))
        kwargs = process.call_args.kwargs
        self.assertEqual(kwargs["target"], (1920, 1080))
        self.assertEqual(kwargs["scale"], 3)
        self.assertEqual(kwargs["fit"], "cover")
        self.assertEqual(kwargs["preferred_scale"], 4)
        self.assertEqual(kwargs["crf"], 0)

    def test_video_watermark_uses_step_detection_settings_including_zero(self) -> None:
        payload = self._payload("watermark_removal", capabilities.default_bindings("watermark_removal"), {"conf": 0, "expand": 0, "detectMode": "per_frame", "tile": 128})
        with mock.patch.object(image_ops, "ImageRestorer"), mock.patch.object(video_worker.video_ops, "process_video", return_value={"path": "out.mp4"}) as process:
            self.assertEqual(video_worker.cmd_media_video(payload), 0)
        self.assertEqual(process.call_args.kwargs["conf"], 0)
        self.assertEqual(process.call_args.kwargs["expand"], 0)
        self.assertEqual(process.call_args.kwargs["detect_mode"], "per_frame")

    def test_video_interpolation_uses_multiplier_and_preserves_encoding_settings(self) -> None:
        payload = self._payload("frame_interpolation", {"interpolator": "media:rife-v46"}, {"multiplier": 4}, crf=0, preset="slow")
        with mock.patch.object(video_worker.video_ops, "interpolate_video", return_value={"path": "out.mp4"}) as interpolate:
            self.assertEqual(video_worker.cmd_media_video(payload), 0)
        self.assertEqual(interpolate.call_args.kwargs["factor"], 4)
        self.assertEqual(interpolate.call_args.kwargs["crf"], 0)
        self.assertEqual(interpolate.call_args.kwargs["preset"], "slow")

    def test_video_supported_defaults_are_normalized_before_execution(self) -> None:
        payload = self._payload("upscale", {"upscaler": "media:real-esrgan-x4"},
                                {"target": "custom", "width": 192, "height": 128, "tile": 128})
        with mock.patch.object(image_ops, "ImageRestorer") as restorer, mock.patch.object(video_worker.video_ops, "process_video", return_value={"path": "out.mp4"}) as process:
            self.assertEqual(video_worker.cmd_media_video(payload), 0)
        self.assertEqual(restorer.call_args.kwargs["tile"], 128)
        self.assertEqual(process.call_args.kwargs["target"], (192, 128))
        self.assertEqual(process.call_args.kwargs["scale"], 4)

    def test_inpainting_step_settings_reach_the_sequence_adapter(self) -> None:
        payload = self._payload("video_inpainting", {"primary": "media:propainter"}, {"conf": 0, "expand": 0, "detectMode": "per_frame", "subvideoLength": 24})
        with mock.patch.object(video_worker.video_ops, "inpaint_video", return_value={"path": "out.mp4"}) as inpaint:
            self.assertEqual(video_worker.cmd_media_video(payload), 0)
        self.assertEqual(inpaint.call_args.kwargs["conf"], 0)
        self.assertEqual(inpaint.call_args.kwargs["expand"], 0)
        self.assertEqual(inpaint.call_args.kwargs["detect_mode"], "per_frame")
        self.assertEqual(inpaint.call_args.kwargs["subvideo_length"], 24)

    def test_unsupported_params_invalid_sizes_and_multiple_steps_fail_before_execution(self) -> None:
        invalid = [
            self._payload("video_super_resolution", {"upscaler": "media:basicvsrpp"}, {"tile": 128}),
            self._payload("frame_interpolation", {"interpolator": "media:rife-v46"}, {"timestep": 0.25}),
            self._payload("upscale", {"upscaler": "media:real-esrgan-x2"}, {"target": "custom", "width": 0, "height": 128}),
            self._payload("upscale", {"upscaler": "media:real-esrgan-x2"}, {"bogus": 1}),
        ]
        with mock.patch.object(image_ops, "ImageRestorer") as restorer:
            for payload in invalid:
                with self.subTest(params=payload["steps"][0]["params"]):
                    self.assertEqual(video_worker.cmd_media_video(payload), 1)
            payload = self._payload("upscale", {"upscaler": "media:real-esrgan-x2"})
            payload["steps"] *= 2
            self.assertEqual(video_worker.cmd_media_video(payload), 1)
            self.assertEqual(self.errors[-1][0], "MEDIA_ONE_MODEL_PER_TASK")
        restorer.assert_not_called()

    def test_precheck_uses_the_same_zero_values_as_execution(self) -> None:
        detector = mock.Mock()
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[0, 0] = 255
        detector.detect.return_value = (np.zeros((1, 4)), np.ones(1), mask)
        steps = self._payload("watermark_removal", capabilities.default_bindings("watermark_removal"), {"conf": 0, "expand": 0})["steps"]
        result = precheck.precheck(steps, [str(self.source)], detector_factory=lambda *_args: detector)
        self.assertTrue(result["ok"])
        self.assertEqual(detector.detect.call_args.kwargs, {"conf": 0, "expand": 0})

    def test_explicit_unsupported_params_are_rejected_even_at_their_defaults(self) -> None:
        for capability, bindings, params, modality in (
            ("video_super_resolution", {"upscaler": "media:basicvsrpp"}, {"tile": 0}, "video"),
            ("frame_interpolation", {"interpolator": "media:rife-v46"}, {"timestep": 0.5}, "video"),
            ("frame_interpolation", {"interpolator": "media:rife-v46"}, {"multiplier": 2}, "image"),
        ):
            with self.subTest(capability=capability, params=params), self.assertRaisesRegex(ValueError, "not available"):
                task_params.normalize_steps(self._payload(capability, bindings, params), modality=modality)

    def test_omitted_unsupported_defaults_do_not_reject_current_empty_params(self) -> None:
        cases = (
            ("video_super_resolution", {"upscaler": "media:basicvsrpp"}, "video", {}),
            ("frame_interpolation", {"interpolator": "media:rife-v46"}, "video", {"multiplier": 2}),
            ("frame_interpolation", {"interpolator": "media:rife-v46"}, "image", {"timestep": 0.5}),
        )
        for capability, bindings, modality, expected in cases:
            with self.subTest(capability=capability, modality=modality):
                raw = self._payload(capability, bindings)
                self.assertEqual(task_params.normalize_steps(raw, modality=modality)[0]["params"], expected)
                self.assertEqual(raw["steps"][0]["params"], {})

    def test_missing_steps_and_legacy_aliases_are_rejected_before_adapter_or_output_creation(self) -> None:
        current = self._payload("upscale", {"upscaler": "media:real-esrgan-x2"})
        missing = {key: value for key, value in current.items() if key != "steps"}
        invalid = [missing, {**missing, "mode": "upscale", "modelId": "media:real-esrgan-x2"}]
        invalid.extend({**current, alias: value} for alias, value in
                       (("mode", "upscale"), ("modelId", "media:real-esrgan-x2"), ("denoiserModelId", "media:denoiser"), ("factor", 4)))
        with mock.patch.object(image_ops, "ImageRestorer") as restorer:
            for payload in invalid:
                with self.subTest(payload=payload):
                    self.assertEqual(image_worker.cmd_media_image(payload), 1)
        restorer.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_process_file_no_longer_generates_steps_from_a_mode(self) -> None:
        restorer = object.__new__(image_ops.ImageRestorer)
        with self.assertRaisesRegex(ValueError, "explicit steps"):
            restorer.process_file(self.source, self.root / "out", mode="upscale")
        self.assertFalse((self.root / "out").exists())

    def test_cli_smoke_emits_only_explicit_single_step_payloads_for_current_models(self) -> None:
        scripts = Path(__file__).resolve().parents[2] / "scripts"
        with mock.patch.object(sys, "path", [str(scripts), *sys.path]):
            spec = importlib.util.spec_from_file_location("explicit_media_smoke", scripts / "smoke-media-models.py")
            smoke = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(smoke)
        captured = []
        with mock.patch.object(smoke, "sample_image", return_value=self.source), \
                mock.patch.object(smoke, "run", side_effect=lambda payload, **kwargs: (captured.append(payload) or True, "fixture")):
            self.assertEqual(smoke.verify_models(Path(sys.executable), self.root / "models", self.root / "outputs", self.source, {}), 0)
        self.assertTrue(captured)
        for payload in captured:
            with self.subTest(payload=payload):
                self.assertFalse(set(payload) & {"mode", "modelId", "denoiserModelId", "factor"})
                self.assertEqual(len(payload["steps"]), 1)
                self.assertIn("params", payload["steps"][0])
                self.assertEqual(len(task_params.normalize_steps(payload, modality="image")), 1)


if __name__ == "__main__":
    unittest.main()
