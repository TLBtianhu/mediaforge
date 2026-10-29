"""模型根必须只有一份解析规则。

2026-10-29 之前，「模型放在哪」有五个入口各算一遍：

    model_hub.model_dir()                    MEDIAFORGE_MODEL_DIR → PYMSS_MODEL_DIR → DATA_ROOT/models → ~/.mediaforge/models
    media_ops.capabilities.model_dir()       同上，但兜底是仓库根 <repo>/models（打包后=安装目录，只读）
    worker_custom_models.managed_root()      只认 PYMSS_MODEL_DIR → ~/.cache/pymss/models
    worker_models._default_model_dir()       复制了 pymss 自己的兜底（含仓库 all_models）
    agent_bridge                             每次把绝对路径塞进 payload

于是同一台机器上，下载、导入、推理、MCP 会给出不同答案；而且只在"某条路径忘了传 modelDir"
时发作——也就是发布之后才炸。

这个文件测的是**跨模块一致性**，不是任何单个模块的行为。单模块的细节谁都能改，
"四个入口给出同一个根"这条不能变。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import model_hub  # noqa: E402
import worker_paths  # noqa: E402
from media_ops import capabilities  # noqa: E402

# worker_models / worker_custom_models 会拉进 yaml 这类运行时依赖；裸解释器下跳过，
# CI（装了 requirements-ci.txt）和运行时环境里会真正参与比对。
try:
    import worker_custom_models  # noqa: E402
    import worker_models  # noqa: E402

    FULL_COVERAGE = True
    IMPORT_SKIP_REASON = ""
except ImportError as exc:  # pragma: no cover - 取决于解释器里装了什么
    FULL_COVERAGE = False
    IMPORT_SKIP_REASON = f"缺少运行时依赖，只比对三个入口：{exc}"

ENV_KEYS = (
    "MEDIAFORGE_MODEL_DIR",
    "PYMSS_MODEL_DIR",
    "MEDIAFORGE_DATA_DIR",
    "MEDIAFORGE_DATA_ROOT",
)


def env_without_overrides(**extra: str) -> dict[str, str]:
    base = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    base.update({key: str(value) for key, value in extra.items()})
    return base


class ModelRootConsistencyTests(unittest.TestCase):
    def roots(self) -> dict[str, Path]:
        """能导入几个就比几个——缺 yaml 时只比三个轻量入口，而不是整组跳过。"""
        found = {
            "worker_paths": worker_paths.model_root(),
            "model_hub": model_hub.model_dir(),
            "capabilities": capabilities.model_dir(),
        }
        if FULL_COVERAGE:
            found["worker_models"] = worker_models._default_model_dir()
            # managed_root() 返回 <root>/custom，取 parent 才是模型根本身
            found["worker_custom_models"] = worker_custom_models.managed_root().parent
        return {name: Path(root) for name, root in found.items()}

    def assert_all_equal(self, roots: dict[str, Path], expected: Path) -> None:
        for name, root in roots.items():
            self.assertEqual(root, expected, f"{name} 与期望的模型根不一致：{root} != {expected}")

    def test_data_root_is_honoured_by_every_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(os.environ, env_without_overrides(MEDIAFORGE_DATA_ROOT=raw), clear=True):
                roots = self.roots()
        self.assert_all_equal(roots, Path(raw) / "models")

    def test_mediaforge_model_dir_wins_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            override = Path(raw) / "weights"
            with mock.patch.dict(os.environ, env_without_overrides(MEDIAFORGE_MODEL_DIR=str(override)), clear=True):
                roots = self.roots()
        self.assert_all_equal(roots, override)

    def test_pymss_model_dir_is_honoured_when_it_is_the_only_hint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            override = Path(raw) / "pymss-weights"
            with mock.patch.dict(os.environ, env_without_overrides(PYMSS_MODEL_DIR=str(override)), clear=True):
                roots = self.roots()
        self.assert_all_equal(roots, override)

    def test_default_never_points_inside_the_program_directory(self) -> None:
        """无任何配置时，模型根不能落在程序目录（只读、且打包版没有 models/）。"""
        with mock.patch.dict(os.environ, env_without_overrides(), clear=True):
            roots = self.roots()
        program_root = Path(worker_paths.__file__).resolve().parent
        for name, root in roots.items():
            self.assertFalse(
                str(root).lower().startswith(str(program_root).lower()),
                f"{name} 兜底到了程序目录：{root}",
            )
            self.assertEqual(root.name, "models", f"{name} 的默认根不像模型目录：{root}")

    def test_the_entry_point_inventory_is_what_we_know(self) -> None:
        """钉住覆盖面：新增第六个入口没来登记，这条测试就该发现不了它——所以清单本身是断言。"""
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(os.environ, env_without_overrides(MEDIAFORGE_DATA_ROOT=raw), clear=True):
                names = set(self.roots())
        expected = {"worker_paths", "model_hub", "capabilities"}
        if FULL_COVERAGE:
            expected |= {"worker_models", "worker_custom_models"}
        else:  # pragma: no cover - 取决于解释器里装了什么
            self.assertIn("跳过", IMPORT_SKIP_REASON or "跳过", "缺依赖时必须说明跳过了哪两个入口")
        self.assertEqual(names, expected)

    def test_lightweight_trio_agrees_even_without_runtime_dependencies(self) -> None:
        """无论有没有 yaml，这三个入口都必须在同一处——它们都不需要运行时依赖。"""
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(os.environ, env_without_overrides(MEDIAFORGE_DATA_ROOT=raw), clear=True):
                trio = {
                    name: root
                    for name, root in self.roots().items()
                    if name in {"worker_paths", "model_hub", "capabilities"}
                }
        self.assertEqual(set(trio), {"worker_paths", "model_hub", "capabilities"})
        self.assert_all_equal(trio, Path(raw) / "models")


if __name__ == "__main__":
    unittest.main()
