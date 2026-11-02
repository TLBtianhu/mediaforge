"""媒体 catalog 哈希覆盖与写回规则。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sync_media_catalog_hashes", ROOT / "scripts/sync-media-catalog-hashes.py")
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)

VALID = "a" * 64
OTHER = "b" * 64


class MediaCatalogHashTests(unittest.TestCase):
    def catalog(self) -> dict:
        return {"models": [
            {"id": "hashed", "implemented": True, "url": "https://example.invalid/a", "sha256": VALID},
            {"id": "missing", "implemented": True, "url": "https://example.invalid/b"},
            {"id": "planned", "implemented": False, "url": "https://example.invalid/c"},
        ]}

    def test_coverage_requires_every_downloadable_entry(self) -> None:
        problems = sync.hash_problems(self.catalog())
        self.assertEqual(problems, ["missing: missing or invalid sha256"])

    def test_apply_only_fills_missing_valid_hashes(self) -> None:
        catalog = self.catalog()
        updated = sync.apply_hashes(catalog, {"hashed": OTHER, "missing": OTHER})
        self.assertEqual(updated, 1)
        self.assertEqual(catalog["models"][0]["sha256"], VALID)
        self.assertEqual(catalog["models"][1]["sha256"], OTHER)
        self.assertEqual(sync.hash_problems(catalog), [])

    def test_multi_file_models_require_and_apply_each_file_hash(self) -> None:
        catalog = {"models": [{
            "id": "directory", "implemented": True, "url": "https://example.invalid/repo",
            "files": [
                {"path": "config.json", "size": 10, "url": "https://example.invalid/config"},
                {"path": "model.safetensors", "size": 20, "url": "https://example.invalid/model"},
            ],
        }]}
        self.assertEqual(len(sync.hash_problems(catalog)), 2)
        item = catalog["models"][0]
        updated = sync.apply_hashes(catalog, {"directory": {"config.json": VALID, "model.safetensors": OTHER}})
        self.assertEqual(updated, 1)
        self.assertEqual(item["file_hashes"], {"config.json": VALID, "model.safetensors": OTHER})
        self.assertEqual(item["size"], 30)
        self.assertEqual(sync.hash_problems(catalog), [])
        self.assertTrue(sync.hashes_complete(item, {}))
        self.assertEqual(sync.coverage(catalog), (1, 1))

    def test_cleanup_cannot_escape_the_temporary_work_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-hash-cleanup-") as root:
            outside = Path(root).parent / "outside.bin"
            outside.write_bytes(b"do not delete")
            self.addCleanup(outside.unlink, missing_ok=True)
            with self.assertRaisesRegex(RuntimeError, "outside the temporary work root"):
                sync.cleanup_files(Path(root), {"files": [{"path": str(outside)}]})
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
