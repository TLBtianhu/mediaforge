"""模型库下载的富进度：真实字节/速度/逐文件，对齐前端下载卡片。

粗进度 {progress, completedFiles:0, totalFiles:1} 是曾经的保真度回退；
这里钉住补回后的契约：真实字节聚合 + 富 asset.progress 字段。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from core import model_hub, model_hub_worker


class DetailedProgressAggregationTest(unittest.TestCase):
    def test_aggregates_real_bytes_not_file_count(self):
        """大权重 900B + 小 config 100B：下完大权重时真实进度是 900 字节，
        不是按文件数算的 50%。主权重占绝大部分字节，按文件数会严重失真。"""
        record = {
            "id": "audio:x", "label": "x",
            "files": [
                {"path": "big", "size": 900, "url": "http://x/big"},
                {"path": "small", "size": 100, "url": "http://x/small"},
            ],
        }
        events = []

        def fake_download_file(_url, _target, _size, progress, _label, _sha=""):
            progress(1.0, "done")
            return {"path": "x", "skipped": False, "size": _size}

        with mock.patch.object(model_hub, "list_models", return_value={"models": [record], "modelDir": "/tmp/m"}), \
             mock.patch.object(model_hub, "_download_file", side_effect=fake_download_file):
            model_hub.download_model("audio:x", detailed_progress=lambda *args: events.append(args))

        # 第一个文件（900B）下完：真实聚合 = 900，不是 50%。
        self.assertEqual(events[0][0], 900)
        self.assertEqual(events[0][1], 1000)  # total_bytes
        self.assertEqual(events[0][2], 0)     # completed_files（当前还在第 1 个之后=index 0? 见下）
        # 全部下完：真实聚合 = 1000。
        self.assertEqual(events[-1][0], 1000)


class ModelHubWorkerRichProgressTest(unittest.TestCase):
    def test_download_emits_rich_asset_progress(self):
        events = []

        def fake_download(model_id, root, *, source, detailed_progress):
            detailed_progress(500, 1000, 0, 1, "half")
            detailed_progress(1000, 1000, 1, 1, "done")
            return {
                "model": {"id": model_id},
                "files": [{"path": "x", "size": 1000, "skipped": False}],
                "modelDir": root,
            }

        with mock.patch.object(model_hub_worker.model_hub, "download_model", side_effect=fake_download), \
             mock.patch.object(model_hub_worker, "emit", side_effect=lambda t, p, **kw: events.append((t, p))):
            model_hub_worker.cmd_model_download({"modelId": "audio:x", "requestId": "r1"})

        progress = [p for t, p in events if t == "asset.progress"]
        self.assertTrue(progress, "必须发 asset.progress")
        first = progress[0]
        self.assertEqual(first["aggregateDownloadedBytes"], 500)
        self.assertEqual(first["aggregateTotalBytes"], 1000)
        self.assertEqual(first["totalFiles"], 1)
        self.assertEqual(first["progress"], 50.0)
        self.assertIn("speedBytesPerSecond", first)
        self.assertEqual(events[-1][0], "asset.done")


if __name__ == "__main__":
    unittest.main()
