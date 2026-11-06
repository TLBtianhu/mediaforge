from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

from core import worker_bootstrap as worker


class RuntimeAssetBindingTest(unittest.TestCase):
    def test_update_identity_includes_asset_id(self) -> None:
        state = {"backend": "cuda", "pythonPath": "C:/runtime/python.exe", "assetId": "asset-a"}
        other = {**state, "assetId": "asset-b"}
        self.assertNotEqual(worker._core_update_identity(state, {}, {}), worker._core_update_identity(other, {}, {}))

    def test_plan_is_bound_to_the_active_asset(self) -> None:
        plan = {"id": "a" * 32, "createdAt": time.time(), "assetId": "asset-a"}
        with mock.patch.object(worker, "_read_json_object", return_value=plan), mock.patch.object(worker, "_resolve_runtime_state_from", return_value={"assetId": "asset-b"}):
            with self.assertRaisesRegex(ValueError, "different runtime asset"):
                worker._load_core_update_plan("a" * 32)

    def test_rollback_does_not_cross_assets(self) -> None:
        state = {"backend": "cuda", "pythonPath": "C:/runtime/python.exe", "assetId": "asset-a"}
        previous = {"pythonPath": state["pythonPath"], "assetId": "asset-b", "pymssGraphAvailable": True}
        with mock.patch.object(worker, "_runtime_health", return_value="ready"):
            self.assertFalse(worker._core_rollback_available({**state, "previousRuntime": previous}, {}))


if __name__ == "__main__":
    unittest.main()
