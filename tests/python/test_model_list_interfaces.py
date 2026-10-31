"""模型列表可以顺带带槽位接口（默认不带，避免列表载荷变大）。

用事件 sink 在进程内捕获 `models` 事件，不依赖真实模型库和子进程。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import worker_models  # noqa: E402
from app_core import interface as model_interface  # noqa: E402
from shared.worker_protocol import set_event_sink  # noqa: E402

FAKE_INTERFACE = {
    "modelId": "audio:Demo.ckpt",
    "task": "separation",
    "slots": {"chunk_size": {"bind": "chunk_size", "min": 64, "max": 8192, "step": 128, "default": 512}},
}


def capture(payload: dict) -> dict:
    events: list[dict] = []
    set_event_sink(lambda event_type, data, request_id, task_id: events.append({"type": event_type, "payload": data}))
    try:
        with mock.patch.object(worker_models, "load_model_catalog", return_value={}), \
             mock.patch.object(worker_models, "list_catalog_models", return_value=["demo-entry"]), \
             mock.patch.object(worker_models, "list_registered_user_models", return_value=[]), \
             mock.patch.object(worker_models, "model_to_dict", return_value={"name": "Demo.ckpt", "category": "vocal"}), \
             mock.patch.object(model_interface, "interface_for", return_value=FAKE_INTERFACE) as lookup:
            worker_models.cmd_list_models(payload)
    finally:
        set_event_sink(None)
    result = next(event for event in events if event["type"] == "models")
    return {"payload": result["payload"], "lookup": lookup}


class InterfaceInListTests(unittest.TestCase):
    def test_interfaces_are_opt_in(self) -> None:
        captured = capture({"supportedOnly": False, "includeLocalState": False})
        model = captured["payload"]["models"][0]
        self.assertNotIn("slots", model)
        self.assertNotIn("slotTask", model)
        self.assertFalse(captured["lookup"].called, "没要求接口时不应该去查")

    def test_interfaces_are_attached_when_requested(self) -> None:
        captured = capture({"supportedOnly": False, "includeLocalState": False, "includeInterfaces": True})
        model = captured["payload"]["models"][0]
        self.assertIn("chunk_size", model["slots"])
        self.assertEqual(model["slotTask"], "separation")

    def test_lookup_failures_do_not_break_the_list(self) -> None:
        events: list[dict] = []
        set_event_sink(lambda event_type, data, request_id, task_id: events.append({"type": event_type, "payload": data}))
        try:
            with mock.patch.object(worker_models, "load_model_catalog", return_value={}), \
                 mock.patch.object(worker_models, "list_catalog_models", return_value=["demo-entry"]), \
                 mock.patch.object(worker_models, "list_registered_user_models", return_value=[]), \
                 mock.patch.object(worker_models, "model_to_dict", return_value={"name": "Demo.ckpt"}), \
                 mock.patch.object(model_interface, "interface_for", side_effect=RuntimeError("boom")):
                worker_models.cmd_list_models({"includeInterfaces": True, "supportedOnly": False})
        finally:
            set_event_sink(None)
        payload = next(event["payload"] for event in events if event["type"] == "models")
        self.assertEqual(payload["count"], 1)
        self.assertNotIn("slots", payload["models"][0])


if __name__ == "__main__":
    unittest.main()
