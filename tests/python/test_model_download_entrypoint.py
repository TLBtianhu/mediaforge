"""模型库下载入口与前后台生命周期必须保持同一契约。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ModelDownloadEntrypointTests(unittest.TestCase):
    def test_model_hub_view_passes_a_flat_worker_payload(self) -> None:
        source = (ROOT / "src/views/ModelHubView.vue").read_text(encoding="utf-8")
        block = source.split("async function download(item: ModelRecord)", 1)[1].split("onMounted", 1)[0]
        self.assertIn("await invoke('start_model_hub_download', {", block)
        self.assertNotIn("payload:", block)
        for field in ("requestId: requestId.value", "modelId: item.id", "modelDir: modelDir.value", "source: 'modelscope'"):
            self.assertIn(field, block)

    def test_busy_cards_and_delete_waiting_use_the_shared_lifecycle(self) -> None:
        view = (ROOT / "src/views/ModelsView.vue").read_text(encoding="utf-8")
        busy = view.split("function isModelBusy", 1)[1].split("const modelMenuOptions", 1)[0]
        self.assertIn("isActiveDownloadStatus(task.status)", busy)
        store = (ROOT / "src/stores/model.ts").read_text(encoding="utf-8")
        waiting = store.split("async function waitForDeleteTask", 1)[1].split("async function deleteToolModel", 1)[0]
        self.assertIn("isTerminalDeleteTaskStatus(task)", waiting)
        self.assertIn("task.status === 'cancelled'", waiting)


if __name__ == "__main__":
    unittest.main()
