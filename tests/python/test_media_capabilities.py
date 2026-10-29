"""能力注册表里"模型自报属性"的契约。

2026-10-29：三处 `4 if "x4" in model_id else 2` 的字符串嗅探被换成读 catalog 的
`params.scale.default`。这里的测试钉住两件事：
  1. 倍率来自声明，不来自名字（改名不该改变行为）；
  2. 不许再把这行嗅探写回去（结构性防回归）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from media_ops import capabilities  # noqa: E402


class ModelScaleTests(unittest.TestCase):
    def test_declared_scale_is_read_from_catalog(self) -> None:
        self.assertEqual(capabilities.model_scale("media:real-esrgan-x2"), 2)
        self.assertEqual(capabilities.model_scale("media:real-esrgan-x4"), 4)

    def test_unknown_model_falls_back(self) -> None:
        self.assertEqual(capabilities.model_scale("media:does-not-exist"), 2)
        self.assertEqual(capabilities.model_scale("media:does-not-exist", fallback=1), 1)

    def test_scale_comes_from_the_declaration_not_from_the_name(self) -> None:
        """把模型改名叫 anything-2x，倍率仍必须是 catalog 里写的 4。"""
        with tempfile.TemporaryDirectory() as raw:
            catalog = Path(raw) / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "id": "renamed-super-res",
                                "name": "whatever-2x.pth",
                                "params": {"scale": {"default": 4}},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(capabilities, "CATALOG_PATH", catalog):
                self.assertEqual(capabilities.model_scale("media:renamed-super-res"), 4)

    def test_missing_scale_spec_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            catalog = Path(raw) / "catalog.json"
            catalog.write_text(json.dumps({"models": [{"id": "bare", "name": "bare.pth"}]}), encoding="utf-8")
            with mock.patch.object(capabilities, "CATALOG_PATH", catalog):
                self.assertEqual(capabilities.model_scale("media:bare"), 2)


class ModelDirResolutionTests(unittest.TestCase):
    """模型根必须落在"用户数据目录"，绝不能落在程序目录。

    2026-10-29 之前 `capabilities.model_dir()` 兜底到 `Path(__file__).parents[2]/"models"`，
    也就是仓库根；打包后那是安装目录（Program Files、只读、且不含 models/）。
    平时不发作，因为主流程都从 payload 传 modelDir——**但只要某条路径忘了传，就是发布后才炸**。
    """

    ENV_KEYS = (
        "MEDIAFORGE_MODEL_DIR",
        "PYMSS_MODEL_DIR",
        "MEDIAFORGE_DATA_DIR",
        "MEDIAFORGE_DATA_ROOT",
    )

    def _env_without_overrides(self) -> dict:
        return {key: value for key, value in os.environ.items() if key not in self.ENV_KEYS}

    def test_never_falls_back_into_the_repository(self) -> None:
        with mock.patch.dict(os.environ, self._env_without_overrides(), clear=True):
            root = capabilities.model_dir()
        repo_root = Path(capabilities.__file__).resolve().parents[2]
        self.assertFalse(
            str(root).lower().startswith(str(repo_root).lower()),
            f"模型根不能兜底到程序目录：{root}",
        )
        self.assertEqual(root.name, "models")

    def test_follows_the_data_root_env(self) -> None:
        with mock.patch.dict(os.environ, {"MEDIAFORGE_DATA_ROOT": r"C:\tmp\mf-data"}):
            os.environ.pop("MEDIAFORGE_MODEL_DIR", None)
            os.environ.pop("PYMSS_MODEL_DIR", None)
            self.assertEqual(capabilities.model_dir(), Path(r"C:\tmp\mf-data") / "models")

    def test_explicit_model_dir_wins_over_data_root(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MEDIAFORGE_MODEL_DIR": r"C:\tmp\explicit", "MEDIAFORGE_DATA_ROOT": r"C:\tmp\mf-data"},
        ):
            self.assertEqual(capabilities.model_dir(), Path(r"C:\tmp\explicit"))


class NoNameSniffingTests(unittest.TestCase):
    def test_media_ops_has_no_scale_sniffing_left(self) -> None:
        media_dir = Path(capabilities.__file__).resolve().parent
        offenders = [
            path.name
            for path in media_dir.glob("*.py")
            if '"x4" in' in path.read_text(encoding="utf-8") or "'x4' in" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], f"这些文件里又出现了按名字嗅探倍率：{offenders}")


if __name__ == "__main__":
    unittest.main()


class StepParamValidationTests(unittest.TestCase):
    """步骤参数必须是声明过的，且类型/范围正确——不能静默透传。"""

    def _step(self, capability_id: str, bindings: dict, params: dict) -> dict:
        return {"capability": capability_id, "bindings": bindings, "params": params}

    def test_unknown_parameter_is_rejected_with_the_allowed_list(self) -> None:
        with self.assertRaises(ValueError) as caught:
            capabilities.normalize_steps(
                [self._step("upscale", {"upscaler": "media:real-esrgan-x2"}, {"bogus": 1})]
            )
        message = str(caught.exception)
        self.assertIn("does not accept parameters: bogus", message)
        self.assertIn("allowed:", message)

    def test_wrong_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            capabilities.normalize_steps(
                [self._step("upscale", {"upscaler": "media:real-esrgan-x2"}, {"scale": "x2"})]
            )
        self.assertIn("scale should be integer", str(caught.exception))

    def test_slot_range_is_enforced(self) -> None:
        with self.assertRaises(ValueError) as caught:
            capabilities.normalize_steps(
                [
                    self._step(
                        "watermark_removal",
                        {"inpainter": "media:lama", "detector": "media:watermark-yolo11x"},
                        {"conf": 2.5},
                    )
                ]
            )
        self.assertIn("conf must be <= 1.0", str(caught.exception))

    def test_valid_parameters_merge_with_defaults(self) -> None:
        normalized = capabilities.normalize_steps(
            [self._step("watermark_removal",
                        {"inpainter": "media:lama", "detector": "media:watermark-yolo11x"},
                        {"conf": 0.3})]
        )
        params = normalized[0]["params"]
        self.assertEqual(params["conf"], 0.3)
        self.assertIn("expand", params)  # 默认值被保留
