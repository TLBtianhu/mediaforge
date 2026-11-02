"""人脸修复适配器的模型参数、颜色边界与 worker 输出契约。"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from media_ops import image_ops  # noqa: E402
from media_ops import image_worker  # noqa: E402


class FaceRestorationTests(unittest.TestCase):
    def test_missing_model_fails_before_loading_gfpgan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-face-missing-") as root:
            with self.assertRaisesRegex(FileNotFoundError, "GFPGAN model not found"):
                image_ops.restore_faces(Path(root) / "missing.pth", image_ops.np.zeros((2, 2, 3), dtype=image_ops.np.uint8))

    def test_gfpgan_uses_clean_architecture_and_preserves_rgb(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-face-model-") as root:
            model = Path(root) / "GFPGANv1.4.pth"
            model.write_bytes(b"test")
            created = []
            fake_utils = types.SimpleNamespace()

            class FakeFaceHelper:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs
                    fake_utils.last = self

            fake_utils.FaceRestoreHelper = FakeFaceHelper

            class FakeGFPGANer:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs
                    self.helper = fake_utils.FaceRestoreHelper(model_rootpath="gfpgan/weights")
                    created.append(self)

                def enhance(self, image, **kwargs):
                    self.input = image.copy()
                    self.enhance_kwargs = kwargs
                    return None, [], image

            fake_cv2 = types.SimpleNamespace(
                COLOR_RGB2BGR=1,
                COLOR_BGR2RGB=2,
                cvtColor=lambda image, _code: image[:, :, ::-1].copy(),
            )
            source = image_ops.np.zeros((4, 6, 3), dtype=image_ops.np.uint8)
            source[:, :, 0] = 200
            source[:, :, 1] = 100
            with mock.patch.object(image_ops, "_install_torchvision_shim"), \
                 mock.patch.dict(sys.modules, {"gfpgan": types.SimpleNamespace(GFPGANer=FakeGFPGANer, utils=fake_utils), "cv2": fake_cv2}):
                restored = image_ops.restore_faces(model, source, device="cpu")
            self.assertEqual(created[0].kwargs["arch"], "clean")
            self.assertEqual(created[0].kwargs["channel_multiplier"], 2)
            self.assertEqual(created[0].kwargs["upscale"], 1)
            self.assertEqual(created[0].kwargs["device"], "cpu")
            self.assertIsNone(created[0].kwargs["bg_upsampler"])
            self.assertEqual(created[0].enhance_kwargs["weight"], 0.5)
            self.assertTrue(created[0].enhance_kwargs["paste_back"])
            self.assertEqual(Path(created[0].helper.kwargs["model_rootpath"]), model.parent / "gfpgan" / "weights")
            self.assertIs(fake_utils.FaceRestoreHelper, FakeFaceHelper)
            self.assertEqual(created[0].input[:, :, 0].tolist(), source[:, :, 2].tolist())
            self.assertTrue((restored == source).all())

    def test_image_worker_writes_a_face_output_through_the_adapter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-face-worker-") as root_name:
            root = Path(root_name)
            source = root / "portrait.png"
            source.write_bytes(b"test-input")
            output_dir = root / "out"
            rgb = image_ops.np.full((3, 5, 3), 127, dtype=image_ops.np.uint8)
            events = []
            with mock.patch.object(image_worker.capabilities, "model_path", return_value=root / "model.pth"), \
                 mock.patch.object(image_worker.image_ops, "read_image", return_value=rgb), \
                 mock.patch.object(image_worker.image_ops, "restore_faces", side_effect=lambda _model, image: image.copy()) as restore, \
                 mock.patch.object(image_worker.image_ops, "write_image", side_effect=lambda path, _image: Path(path).write_bytes(b"restored")), \
                 mock.patch.object(image_worker, "emit", side_effect=lambda kind, payload, **kwargs: events.append((kind, payload))):
                code = image_worker.cmd_media_image({
                    "mode": "face", "inputs": [str(source)], "outputDir": str(output_dir), "requestId": "face-test",
                })
            self.assertEqual(code, 0)
            restore.assert_called_once()
            result = events[-1][1]
            self.assertEqual(result["completed"], 1)
            self.assertEqual(result["failed"], [])
            self.assertTrue(Path(result["outputs"][0]["path"]).is_file())
            self.assertTrue(result["outputs"][0]["path"].endswith("portrait_face.png"))


if __name__ == "__main__":
    unittest.main()
