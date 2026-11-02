"""音频接口投影必须可由 audio_catalog.json 无损重建。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from sync_model_registry import generate_registry, project_audio  # noqa: E402


def audio(name: str, **overrides) -> dict:
    return {
        "name": name,
        "model_type": "mel_band_roformer",
        "architecture": "mel_band_roformer",
        "primary_category": "vocal",
        "relpath": f"vocal/{name}",
        **overrides,
    }


class RegistryProjectionTests(unittest.TestCase):
    def test_checked_in_projection_is_fresh(self) -> None:
        registry = json.loads((ROOT / "python/media_ops/model_registry.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / "python/audio_catalog.json").read_text(encoding="utf-8"))["models"]
        self.assertEqual(generate_registry(registry, catalog), registry)

    def test_cleanup_legacy_and_unknown_rules_are_deterministic(self) -> None:
        cleanup = project_audio(audio("cleanup.th", primary_category="cleanup", model_type="legacy_demucs", architecture="demucs"))
        self.assertEqual(cleanup["family"], "demucs")
        self.assertEqual(cleanup["task"], "audio_restoration")
        self.assertEqual(cleanup["io"], {"in": "audio", "out": "audio", "arity": 1})
        self.assertEqual(cleanup["slots"], ["strength"])
        unknown = project_audio(audio("unknown.ckpt", architecture="unknown"))
        self.assertEqual(unknown["family"], "unknown")

    def test_new_catalog_entries_are_added_after_audio_without_reordering_media(self) -> None:
        registry = {
            "generatedFrom": [],
            "models": [
                {"id": "media:before", "name": "before"},
                {"id": "audio:a.ckpt", "name": "a.ckpt"},
                {"id": "media:after", "name": "after"},
            ],
        }
        generated = generate_registry(registry, [audio("a.ckpt"), audio("b.ckpt")])
        self.assertEqual(
            [item["id"] for item in generated["models"]],
            ["media:before", "audio:a.ckpt", "audio:b.ckpt", "media:after"],
        )
        self.assertEqual(generated["generatedFrom"], ["audio_catalog.json"])

    def test_registry_entry_missing_from_catalog_is_rejected(self) -> None:
        registry = {"models": [{"id": "audio:missing.ckpt", "name": "missing.ckpt"}]}
        with self.assertRaisesRegex(ValueError, "absent from audio_catalog"):
            generate_registry(registry, [])


if __name__ == "__main__":
    unittest.main()
