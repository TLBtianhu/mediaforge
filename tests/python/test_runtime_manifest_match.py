"""解释器版本与打包声明的对账。

`python/runtime-manifest.json` 声明了**打包运行时**用的 Python（当前 3.12）。
但开发机的 venv 是从**宿主 Python** 建的——2026-10-29 实测这台机器只有 3.11，
于是本地跑的是 3.11、产品跑的是 3.12。

这种漂移不会报错，只会让"只在某个版本上出现"的问题被漏掉
（例如 `Path.is_junction` 是 3.12 才有的，在 3.11 上 junction 检测会静默失效）。
所以把它变成 `env_info` 里两个可读字段，而不是靠人记。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import worker_models  # noqa: E402


class PythonMatchesManifestTests(unittest.TestCase):
    def test_exact_major_minor_matches(self) -> None:
        self.assertTrue(worker_models._python_matches_manifest("3.12", (3, 12, 1)))

    def test_older_minor_is_reported_as_mismatch(self) -> None:
        self.assertFalse(worker_models._python_matches_manifest("3.12", (3, 11, 9)))

    def test_patch_version_is_ignored(self) -> None:
        self.assertTrue(worker_models._python_matches_manifest("3.13", (3, 13, 0)))

    def test_garbage_or_missing_expectation_is_not_a_match(self) -> None:
        self.assertFalse(worker_models._python_matches_manifest("", (3, 12)))
        self.assertFalse(worker_models._python_matches_manifest(None, (3, 12)))
        self.assertFalse(worker_models._python_matches_manifest("three.twelve", (3, 12)))
        self.assertFalse(worker_models._python_matches_manifest("3", (3, 0)))


class RuntimeManifestTests(unittest.TestCase):
    def test_repository_manifest_declares_a_python_version(self) -> None:
        manifest = worker_models._runtime_manifest()
        self.assertTrue(manifest, "仓库里应当有 python/runtime-manifest.json")
        declared = str(manifest.get("python") or "")
        self.assertRegex(declared, r"^\d+\.\d+$")

    def test_missing_manifest_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "runtime-manifest.json"
            with mock.patch.object(worker_models, "Path", mock.MagicMock(wraps=Path)):
                # 直接验证读取失败时的行为：把 __file__ 指向不存在的目录
                original = worker_models.__file__
                try:
                    worker_models.__file__ = str(Path(raw) / "worker_models.py")
                    self.assertEqual(worker_models._runtime_manifest(), {})
                finally:
                    worker_models.__file__ = original
            self.assertFalse(missing.exists())


class EnvInfoFieldsTests(unittest.TestCase):
    def test_env_info_reports_the_declared_version(self) -> None:
        captured: list[dict] = []
        with mock.patch.object(worker_models, "emit", lambda _t, payload: captured.append(payload)):
            worker_models.cmd_env_info()
        payload = captured[-1]
        self.assertIn("expectedPython", payload)
        self.assertIn("runtimeMatchesManifest", payload)
        self.assertIn("runtimeManifestVersion", payload)
        self.assertRegex(str(payload["expectedPython"]), r"^\d+\.\d+$")
        # 同一次调用里两个字段必须自洽
        self.assertEqual(
            payload["runtimeMatchesManifest"],
            worker_models._python_matches_manifest(payload["expectedPython"], sys.version_info),
        )


if __name__ == "__main__":
    unittest.main()
