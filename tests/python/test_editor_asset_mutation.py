from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import shutil
import hashlib
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

from tests.python._bootstrap import *  # noqa: F403
from core import worker_audio
from shared.asset_references import AssetReferencesBusy, asset_reference_lock


class WaveformAssetMutationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="editor-asset-mutation-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name) / "data"
        self.root.mkdir()
        self.audio = Path(self.work.name) / "external.wav"
        self.audio.write_bytes(b"fixture source")
        self.events = []
        self.errors = []
        self.entered = 0
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(worker_audio, "emit", side_effect=lambda kind, payload, **kwargs: self.events.append((kind, payload))).start()
        mock.patch.object(worker_audio, "emit_error", side_effect=lambda *args, **kwargs: self.errors.append(args) or 1).start()
        mock.patch.dict(sys.modules, {"numpy": types.ModuleType("numpy")}).start()
        mock.patch.object(worker_audio, "_waveform_peaks_soundfile", side_effect=self.generate).start()

    def project(self, project_id="edit_p", raw=None):
        directory = self.root / "editor-projects" / project_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "project.json").write_text(raw if raw is not None else json.dumps({
            "id": project_id, "version": 4, "sources": [], "tracks": [],
        }), encoding="utf-8")
        return directory

    def generate(self, path, resolution):
        self.entered += 1
        with self.assertRaises(AssetReferencesBusy):
            with asset_reference_lock(self.root, timeout=0):
                self.fail("waveform generation lost its lease")
        return [0.25], [[0.25]], {"sampleRate": 44100, "channels": 1, "duration": 1}

    def run_peaks(self, path=None, cache=None):
        return worker_audio.cmd_waveform_peaks({"path": str(path or self.audio), "cacheDir": str(cache), "dataRoot": str(self.root)})

    def test_deleted_cache_project_is_not_recreated(self):
        cache = self.root / "editor-projects" / "deleted" / "peaks"
        self.assertEqual(self.run_peaks(cache=cache), 1)
        self.assertEqual(self.entered, 0)
        self.assertFalse(cache.parent.exists())

    def test_missing_source_project_cannot_publish_into_a_surviving_project(self):
        cache = self.project() / "peaks"
        source = self.root / "editor-projects" / "deleted" / "recordings" / "take.wav"
        self.assertEqual(self.run_peaks(source, cache), 1)
        self.assertEqual(self.entered, 0)
        self.assertFalse(cache.exists())

    def test_current_project_generation_holds_lease_until_cache_publication(self):
        cache = self.project() / "peaks"
        self.assertEqual(self.run_peaks(cache=cache), 0)
        self.assertEqual(self.entered, 1)
        produced = Path(self.events[-1][1]["peaksPath"])
        self.assertTrue(produced.is_file())
        self.assertEqual(json.loads(produced.read_text())["peaks"], [0.25])
        with asset_reference_lock(self.root, timeout=0):
            pass

    def test_invalid_project_version_identity_and_duplicate_keys_preserve_original(self):
        for raw in ['{"id":"edit_p","version":3,"sources":[],"tracks":[]}',
                    '{"id":"other","version":4,"sources":[],"tracks":[]}',
                    '{"id":"other","id":"edit_p","version":4,"sources":[],"tracks":[]}']:
            with self.subTest(raw=raw):
                directory = self.project(raw=raw)
                self.assertEqual(self.run_peaks(cache=directory / "peaks"), 1)
                self.assertEqual((directory / "project.json").read_text(), raw)
                self.assertFalse((directory / "peaks").exists())
        self.assertEqual(self.entered, 0)

    def test_explicit_root_wins_over_environment_and_external_cache_remains_supported(self):
        cache = Path(self.work.name) / "external-cache"
        with mock.patch.dict("os.environ", {"MEDIAFORGE_DATA_ROOT": str(Path(self.work.name) / "wrong-root")}):
            self.assertEqual(self.run_peaks(cache=cache), 0)
        self.assertTrue(cache.is_dir())
        self.assertFalse((Path(self.work.name) / "wrong-root").exists())

    def test_backend_exception_releases_asset_lease_without_publishing_cache(self):
        cache = self.project() / "peaks"
        def fail(*args):
            raise RuntimeError("probe failed")
        with mock.patch.object(worker_audio, "_waveform_peaks_soundfile", side_effect=fail), mock.patch.object(worker_audio, "_load_audio_channels", side_effect=fail):
            self.assertEqual(self.run_peaks(cache=cache), 1)
        self.assertEqual(list(cache.glob("*.json")), [])
        with asset_reference_lock(self.root, timeout=0):
            pass

    def test_waiting_generation_rechecks_project_after_a_deletion_holding_the_lease(self):
        directory = self.project()
        entered = threading.Event()
        @contextmanager
        def observed(root, **kwargs):
            entered.set()
            with asset_reference_lock(root, **kwargs):
                yield
        with ThreadPoolExecutor(max_workers=1) as executor:
            with asset_reference_lock(self.root):
                with mock.patch("shared.asset_references.asset_reference_lock", observed):
                    pending = executor.submit(self.run_peaks, cache=directory / "peaks")
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(pending.done())
                    # This is the exact fixture project created by this test.
                    shutil.rmtree(directory)
            self.assertEqual(pending.result(timeout=3), 1)
        self.assertFalse(directory.exists())
        self.assertEqual(self.entered, 0)

    def test_derived_cache_link_is_rejected_before_reading_or_replacing_the_sentinel(self):
        cache = self.project() / "peaks"
        cache.mkdir()
        key = hashlib.sha1(str(self.audio.resolve()).encode("utf-8")).hexdigest()[:16]
        target = cache / f"{self.audio.stem}_{key}_1400_v2.json"
        sentinel = b'{"sentinel":"untouched"}'
        target.write_bytes(sentinel)
        original = Path.lstat
        def metadata(path, *args, **kwargs):
            if path == target:
                return types.SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
            return original(path, *args, **kwargs)
        # Model a reparse cache entry at the filesystem boundary without requiring
        # elevated Windows symlink privileges. A missed guard would read and emit it.
        with mock.patch.object(Path, "lstat", autospec=True, side_effect=metadata):
            self.assertEqual(self.run_peaks(cache=cache), 1)
        self.assertEqual(target.read_bytes(), sentinel)
        self.assertEqual(self.events, [])
        self.assertEqual(self.entered, 0)


if __name__ == "__main__":
    unittest.main()
