from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import _bootstrap  # noqa: F401
from core import worker_infer
from core import worker_workflows


class WorkflowBatchLifecycleTests(unittest.TestCase):
    def test_advanced_batches_keep_independent_graph_execution(self):
        payload = {"taskId": "advanced", "workflow": {"nodes": []}, "tasks": [
            {"taskId": "first"}, {"taskId": "second"},
        ]}
        with patch.object(worker_workflows, "_run_pymss", return_value={}) as run, \
             patch.object(worker_workflows, "_workflow_graph") as graph, \
             patch.object(worker_workflows, "emit"):
            self.assertEqual(worker_workflows.cmd_infer_workflow(payload), 0)
        graph.assert_not_called()
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all("separator_cache" not in call.kwargs for call in run.call_args_list))

    def test_unavailable_batch_runtime_reports_every_file(self):
        payload = {"taskId": "batch", "workflow": {"steps": []}, "tasks": [
            {"taskId": "first"}, {"taskId": "second"},
        ]}
        with patch.object(worker_workflows, "_workflow_graph", side_effect=RuntimeError("Update the runtime core")), \
             patch.object(worker_workflows, "emit_error", return_value=1) as errors:
            self.assertEqual(worker_workflows.cmd_infer_workflow(payload), 1)
        self.assertEqual([call.kwargs["task_id"] for call in errors.call_args_list], ["first", "second"])


class NativeWorkflowBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy as np
            import pymss.graph as graph
            from pymss.audio_io import load_audio, save_audio
        except (ImportError, ModuleNotFoundError) as exc:
            raise unittest.SkipTest(f"Native pymss graph runtime is unavailable: {exc}") from exc
        cls.np, cls.graph = np, graph
        cls.load_audio, cls.save_audio = staticmethod(load_audio), staticmethod(save_audio)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.inputs = [self.root / name for name in ("first.wav", "second.wav")]
        for path, amplitude in zip(self.inputs, (0.6, 0.2)):
            self.save_audio(str(path), self.np.full((128, 2), amplitude, dtype=self.np.float32),
                            44100, "wav", {"wav_bit_depth": "FLOAT"})
        self.created, self.processed, self.closed = [], [], []
        self.abort_first = None
        self.live, self.peak_live = 0, 0
        owner = self

        class Separator:
            def __init__(self, **kwargs):
                self.model_name = kwargs["model_name"]
                self.model_type = "mel_band_roformer"
                self.config = SimpleNamespace(audio={"sample_rate": 44100})
                self.logger = kwargs["logger"]
                self.progress_callback = None
                self.closed = False
                owner.created.append(self.model_name)
                owner.live += 1
                owner.peak_live = max(owner.peak_live, owner.live)

            def separate(self, audio, **_kwargs):
                owner.assertFalse(self.closed)
                owner.processed.append((self.model_name, float(audio.mean())))
                self.logger.info("Batch separator processing")
                self.progress_callback(1, 2, "Batch separator progress")
                if owner.abort_first is not None:
                    error, owner.abort_first = owner.abort_first, None
                    raise error
                return {"Vocals": audio * 0.5}

            def close(self):
                owner.assertFalse(self.closed)
                self.closed = True
                owner.closed.append(self.model_name)
                owner.live -= 1

        self.enterContext(patch.object(worker_workflows, "_workflow_separator_factory", side_effect=Separator))
        self.events = self.enterContext(patch.object(worker_workflows, "emit"))
        self.logs = self.enterContext(patch.object(worker_infer, "emit"))
        self.errors = self.enterContext(patch.object(worker_workflows, "emit_error", return_value=1))

    def payload(self, models=("model-a", "model-a")):
        steps = [{"id": f"step{index}", "model": model, "input": "input" if index == 0 else f"step{index - 1}.Vocals",
                  "stems": ["Vocals"], "save": {"Vocals": "Default"} if index == len(models) - 1 else {}}
                 for index, model in enumerate(models)]
        return {
            "taskId": "simple-batch", "workflow": {"version": 1, "steps": steps}, "workbench": True,
            "output": str(self.root / "scratch"), "outputLayout": "flat", "device": "cpu",
            "tasks": [{"taskId": f"simple-batch-{index}", "input": str(path), "inputs": {"input": str(path)},
                       "workbenchOutputs": {f"save:step{len(models) - 1}:Vocals": str(self.root / f"out-{index}")}}
                      for index, path in enumerate(self.inputs)],
        }

    def done(self):
        return {call.kwargs["task_id"]: call.args[1] for call in self.events.call_args_list if call.args[0] == "task_done"}

    def test_same_model_is_loaded_once_across_nodes_and_files(self):
        self.assertEqual(worker_workflows.cmd_infer_workflow(self.payload()), 0)
        self.assertEqual(self.created, ["model-a"])
        self.assertEqual(self.closed, ["model-a"])
        self.assertEqual(len(self.processed), 4)
        done = self.done()
        self.assertEqual(set(done), {"simple-batch-0", "simple-batch-1"})
        for index, amplitude in enumerate((0.15, 0.05)):
            outputs = done[f"simple-batch-{index}"]["outputs"]
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0]["nodeId"], "save:step1:Vocals")
            self.assertEqual(Path(outputs[0]["path"]).parent, (self.root / f"out-{index}").resolve())
            audio, _rate = self.load_audio(outputs[0]["path"], sr=None, mono=False)
            self.np.testing.assert_allclose(audio, amplitude, atol=1e-6)
        progress = [call for call in self.events.call_args_list
                    if call.args[0] == "task_progress" and "Batch separator progress" in call.args[1]["message"]]
        self.assertEqual([(call.kwargs["task_id"], call.args[1]["nodeId"]) for call in progress], [
            ("simple-batch-0", "step:step0"), ("simple-batch-0", "step:step1"),
            ("simple-batch-1", "step:step0"), ("simple-batch-1", "step:step1"),
        ])
        log_calls = [call for call in self.logs.call_args_list if call.args[1]["message"] == "Batch separator processing"]
        self.assertEqual([(call.kwargs["task_id"], call.args[1]["nodeId"]) for call in log_calls],
                         [(call.kwargs["task_id"], call.args[1]["nodeId"]) for call in progress])
        self.assertFalse(list((self.root / "scratch").glob(".pymss-workflow-*")))

    def test_different_models_keep_the_native_single_model_memory_bound(self):
        self.assertEqual(worker_workflows.cmd_infer_workflow(self.payload(("model-a", "model-b"))), 0)
        self.assertEqual(self.created, ["model-a", "model-b", "model-a", "model-b"])
        self.assertEqual(self.closed, self.created)
        self.assertEqual((self.live, self.peak_live), (0, 1))

    def test_failed_file_does_not_abort_the_rest_of_the_batch(self):
        self.abort_first = RuntimeError("Input could not be processed")
        self.assertEqual(worker_workflows.cmd_infer_workflow(self.payload()), 1)
        self.assertEqual(self.created, ["model-a"])
        self.assertEqual(self.closed, ["model-a"])
        self.assertEqual(set(self.done()), {"simple-batch-1"})
        self.assertEqual([call.kwargs["task_id"] for call in self.errors.call_args_list], ["simple-batch-0"])

    def test_interruption_closes_the_cache_and_does_not_start_the_next_file(self):
        self.abort_first = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            worker_workflows.cmd_infer_workflow(self.payload())
        self.assertEqual(self.closed, ["model-a"])
        self.assertEqual(len(self.processed), 1)
        self.assertEqual(self.done(), {})
        self.assertEqual(self.live, 0)


if __name__ == "__main__":
    unittest.main()
