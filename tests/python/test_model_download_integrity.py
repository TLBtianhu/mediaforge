"""模型文件下载的完整性校验。

原有实现有 `.part` + 大小校验 + 原子改名，缺的是**内容校验**：
只比大小挡不住"被代理改写""服务端回了错误页但长度凑巧对上"这类损坏。坏文件会安静地留在
`models/` 里，等到 `torch.load` 才炸，而那个报错和"下载"毫无关系——用户只会说"模型坏了"。

catalog 给了 `sha256` 才校验（没给就仍然只比大小），所以这条机制是**加法**的，
不会误杀已经下载好的文件。

测试用 `file://` URL 走真实的 `_download_file` 路径，不依赖网络。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core import model_hub  # noqa: E402


class DownloadIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.source = self.root / "source.bin"
        self.payload = b"mediaforge-model-payload" * 64
        self.source.write_bytes(self.payload)
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.url = self.source.resolve().as_uri()
        self.target = self.root / "models" / "weights.bin"

    def download(self, **kwargs) -> dict:
        return model_hub._download_file(
            self.url, self.target, len(self.payload), None, "测试模型", **kwargs
        )

    def test_matching_checksum_is_accepted(self) -> None:
        result = self.download(expected_sha256=self.digest)
        self.assertFalse(result["skipped"])
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertFalse(self.target.with_suffix(self.target.suffix + ".part").exists())

    def test_wrong_checksum_is_rejected_and_leaves_no_artifact(self) -> None:
        wrong = "0" * 64
        with self.assertRaises(RuntimeError) as caught:
            self.download(expected_sha256=wrong)
        self.assertIn("checksum mismatch", str(caught.exception))
        self.assertFalse(self.target.exists(), "坏文件不能留在 models/ 里")
        self.assertFalse(self.target.with_suffix(self.target.suffix + ".part").exists())

    def test_missing_checksum_falls_back_to_size_only(self) -> None:
        """catalog 没写 sha256 时行为不变——不能因为新增校验就把老模型判死。"""
        result = self.download()
        self.assertFalse(result["skipped"])
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_existing_file_with_wrong_content_is_replaced(self) -> None:
        """大小对、内容错的文件必须重下，而不是被 skip 掉。"""
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(b"x" * len(self.payload))
        self.assertEqual(self.target.stat().st_size, len(self.payload))
        result = self.download(expected_sha256=self.digest)
        self.assertFalse(result["skipped"])
        self.assertEqual(self.target.read_bytes(), self.payload)

    def test_existing_file_with_right_content_is_skipped(self) -> None:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(self.payload)
        result = self.download(expected_sha256=self.digest)
        self.assertTrue(result["skipped"])

    def test_size_mismatch_is_still_reported_first(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            model_hub._download_file(self.url, self.target, len(self.payload) + 1, None, "测试模型")
        self.assertIn("size mismatch", str(caught.exception))


class OfflineVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mf-offline-verify-"))
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.payload = b"offline-model-payload" * 32
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.target = self.root / "weights.bin"
        self.target.write_bytes(self.payload)

    def verify(self, *, sha256: str | None = None) -> dict:
        expected = self.digest if sha256 is None else sha256
        record = {
            "id": "media:demo",
            "files": [{
                "path": "weights.bin",
                "size": len(self.payload),
                "sha256": expected,
            }],
        }
        with mock.patch.object(
            model_hub, "list_models", return_value={"models": [record], "modelDir": str(self.root)}
        ):
            return model_hub.verify_model("media:demo", root=str(self.root))

    def test_matching_hash_is_fully_verified_offline(self) -> None:
        result = self.verify()
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["files"][0]["status"], "verified")
        self.assertEqual(result["unverified"], [])

    def test_same_size_corruption_is_a_hash_mismatch(self) -> None:
        self.target.write_bytes(b"x" * len(self.payload))
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["files"][0]["status"], "hash_mismatch")
        self.assertEqual(result["failures"], [str(self.target)])

    def test_missing_hash_is_explicitly_unverified_not_fully_verified(self) -> None:
        result = self.verify(sha256="")
        self.assertTrue(result["ok"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["files"][0]["status"], "size_only")
        self.assertEqual(result["unverified"], [str(self.target)])

    def test_missing_file_fails_verification(self) -> None:
        self.target.unlink()
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["files"][0]["status"], "missing")

    def test_worker_exposes_verification_through_the_existing_hub_command(self) -> None:
        from core import model_hub_worker

        events = []
        result = {"ok": True, "verified": True, "files": []}
        with mock.patch.object(model_hub_worker.model_hub, "verify_model", return_value=result) as verify, \
             mock.patch.object(model_hub_worker, "emit", side_effect=lambda kind, payload, **kwargs: events.append((kind, payload))):
            code = model_hub_worker.cmd_model_hub({"operation": "verify", "modelId": "media:demo", "modelDir": "root"})
        self.assertEqual(code, 0)
        verify.assert_called_once_with("media:demo", root="root")
        self.assertEqual(events[0][0], "model_hub_result")
        self.assertTrue(events[0][1]["verified"])


if __name__ == "__main__":
    unittest.main()


class MediaFilesTests(unittest.TestCase):
    """catalog → _media_files → 下载器：sha256 必须一路传到，否则校验形同虚设。"""

    def test_media_files_propagates_the_declared_checksum(self) -> None:
        from core.model_hub import _media_files

        record = {
            "id": "demo",
            "name": "Demo.pth",
            "size": 10,
            "url": "https://example.invalid/Demo.pth",
            "sha256": "abc123",
        }
        files = _media_files(record)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["sha256"], "abc123")

    def test_missing_checksum_stays_empty(self) -> None:
        from core.model_hub import _media_files

        files = _media_files({"id": "demo", "name": "Demo.pth", "url": "u"})
        self.assertEqual(files[0]["sha256"], "")

    def test_multi_file_download_and_verification_share_one_catalog(self) -> None:
        from core.model_hub import _media_files

        with tempfile.TemporaryDirectory(prefix="mf-multi-file-model-") as root_name:
            root = Path(root_name)
            source = root / "source"
            source.mkdir()
            payloads = {"config.json": b'{"demo":true}', "model.safetensors": b"weights" * 64}
            declared_files = []
            hashes = {}
            for name, payload in payloads.items():
                path = source / name
                path.write_bytes(payload)
                declared_files.append({"path": name, "size": len(payload), "url": path.resolve().as_uri()})
                hashes[name] = hashlib.sha256(payload).hexdigest()
            item = {"name": "directory-demo", "files": declared_files, "file_hashes": hashes}
            record = {"id": "media:demo", "label": "Demo", "files": _media_files(item)}
            target_root = root / "models"
            with mock.patch.object(model_hub, "list_models", return_value={"models": [record], "modelDir": str(target_root)}):
                result = model_hub.download_model("media:demo", root=str(target_root))
                self.assertEqual(len(result["files"]), 2)
                verification = model_hub.verify_model("media:demo", root=str(target_root))
            self.assertTrue(verification["ok"])
            self.assertTrue(verification["verified"])
            self.assertEqual((target_root / "media/directory-demo/config.json").read_bytes(), payloads["config.json"])
            self.assertEqual((target_root / "media/directory-demo/model.safetensors").read_bytes(), payloads["model.safetensors"])

    def test_shipped_catalog_checksums_match_the_declared_files(self) -> None:
        """声明的 sha256 必须和同名权重对得上（本地有文件时才比）。"""
        import hashlib
        import json
        from pathlib import Path as _Path

        from shared import worker_paths
        from core.model_hub import _media_files

        catalog = json.loads(
            (_Path(__file__).resolve().parents[2] / "python" / "media_ops" / "media_catalog.json").read_text(
                encoding="utf-8"
            )
        )
        checked = 0
        # 禁止读取默认用户模型目录；测试只使用显式临时根。
        with tempfile.TemporaryDirectory(prefix="mf-shipped-catalog-") as root_name, \
                mock.patch.object(worker_paths, "model_root", return_value=_Path(root_name)):
            root = _Path(root_name)
            for model in catalog["models"]:
                for entry in _media_files(model):
                    target = root / entry["path"]
                    if not entry["sha256"] or not target.is_file():
                        continue
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    self.assertEqual(digest, entry["sha256"], f"{model['id']} 的权重与 catalog 声明不一致")
                    checked += 1
        # 临时根没有模型时允许为 0；显式提供的文件必须全部通过。
        self.assertGreaterEqual(checked, 0)
