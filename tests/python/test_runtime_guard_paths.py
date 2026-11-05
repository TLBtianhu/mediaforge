from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from media_ops import runtime_guard


class RuntimeGuardPathTest(unittest.TestCase):
    def test_data_root_uses_the_shared_resolver_and_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-runtime-guard-") as root_name:
            with mock.patch.dict(os.environ, {"MEDIAFORGE_DATA_ROOT": root_name}, clear=True):
                self.assertEqual(runtime_guard.data_root(), Path(root_name))

    def test_data_root_does_not_fall_back_to_legacy_mediaforge(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertNotIn(".mediaforge", str(runtime_guard.data_root()))


if __name__ == "__main__":
    unittest.main()
