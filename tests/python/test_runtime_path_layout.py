from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shared import worker_paths


class RuntimePathLayoutTest(unittest.TestCase):
    def test_installed_layout_uses_sibling_data_models_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-layout-") as root_name:
            root = Path(root_name)
            with mock.patch.object(worker_paths, "_app_root", return_value=root / "app"):
                data, models, runtime = worker_paths.default_layout()
            self.assertEqual(data, root / "data")
            self.assertEqual(models, root / "models")
            self.assertEqual(runtime, root / "runtime-envs")

    def test_portable_layout_uses_the_same_sibling_structure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-portable-layout-") as root_name:
            root = Path(root_name)
            (root / "mediaforge.portable").write_text("portable", encoding="ascii")
            with mock.patch.object(worker_paths, "_app_root", return_value=root):
                data, models, runtime = worker_paths.default_layout()
            self.assertEqual(data, root / "data")
            self.assertEqual(models, root / "models")
            self.assertEqual(runtime, root / "runtime-envs")

    def test_data_root_sibling_and_custom_root_child_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-data-root-") as root_name:
            root = Path(root_name)
            self.assertEqual(worker_paths.runtime_envs_root(data_root=root / "data"), root / "runtime-envs")
            self.assertEqual(worker_paths.model_root(data_root=root / "custom"), root / "custom" / "models")

    def test_default_paths_do_not_use_legacy_mediaforge_root(self) -> None:
        data, models, runtime = worker_paths.default_layout()
        for path in (data, models, runtime):
            self.assertNotIn(".mediaforge", str(path))

    def test_bundled_runtime_and_debug_override_use_shared_aliases(self) -> None:
        with mock.patch.dict(os.environ, {"MEDIAFORGE_BUNDLED_RUNTIME_ENVS_DIR": "C:/runtime", "MEDIAFORGE_ALLOW_DEBUG_RUNTIME_OVERRIDE": "1"}, clear=True):
            self.assertEqual(worker_paths.bundled_runtime_envs_dir(), Path("C:/runtime"))
            self.assertTrue(worker_paths.allow_debug_runtime_override())
        with mock.patch.dict(os.environ, {"PYMSS_STUDIO_BUNDLED_RUNTIME_ENVS_DIR": "C:/legacy", "PYMSS_STUDIO_ALLOW_DEBUG_RUNTIME_OVERRIDE": "1"}, clear=True):
            self.assertEqual(worker_paths.bundled_runtime_envs_dir(), Path("C:/legacy"))
            self.assertTrue(worker_paths.allow_debug_runtime_override())

    def test_bridge_uses_the_shared_runtime_resolver(self) -> None:
        source = Path(__file__).parents[2].joinpath("python/agent_bridge.py").read_text(encoding="utf-8")
        self.assertIn("worker_paths.runtime_envs_root(data_root=data_root)", source)
        self.assertNotIn('data_root / "runtime-envs"', source)


if __name__ == "__main__":
    unittest.main()
