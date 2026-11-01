from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from types import ModuleType, SimpleNamespace
from unittest import mock
from urllib.error import URLError

if __package__:
    from . import _bootstrap as _worker_test_bootstrap
else:
    import _bootstrap as _worker_test_bootstrap

from core.worker_download import (
    _make_pymss_progress_adapter,
    _pymss_reports_progress,
    _watch_download_progress,
    catalog_file_index,
    download_studio_model,
    files_for_studio_model,
    prepare_pymss_download,
)
from shared.worker_proxy import effective_proxy_url, parse_proxy_config


class EffectiveProxyUrlTest(unittest.TestCase):
    """The studio proxy setting resolves to the pymss ``proxy=`` argument."""

    def test_custom_http_proxy_passes_through(self):
        proxy = parse_proxy_config({"mode": "custom", "url": "http://127.0.0.1:8080", "bypass": ""})
        self.assertEqual(effective_proxy_url(proxy), "http://127.0.0.1:8080")

    def test_custom_socks_proxy_passes_through(self):
        # pymss 2.1.1+ handles SOCKS itself (PySocks via pymss[proxy]); the worker no
        # longer decides downloader routing for it.
        proxy = parse_proxy_config({"mode": "custom", "url": "socks5://127.0.0.1:1080", "bypass": ""})
        self.assertEqual(effective_proxy_url(proxy), "socks5://127.0.0.1:1080")

    def test_none_mode_forces_direct_connection(self):
        # "" tells pymss to skip even the environment lookup; None would not.
        proxy = parse_proxy_config({"mode": "none", "url": "", "bypass": ""})
        self.assertEqual(effective_proxy_url(proxy), "")

    def test_system_mode_mirrors_urllib_discovery(self):
        proxy = parse_proxy_config({"mode": "system", "url": "", "bypass": ""})
        with mock.patch("shared.worker_proxy.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7890"}):
            self.assertEqual(effective_proxy_url(proxy), "http://127.0.0.1:7890")

    def test_system_mode_without_discovered_proxy_is_direct(self):
        proxy = parse_proxy_config({"mode": "system", "url": "", "bypass": ""})
        with mock.patch("shared.worker_proxy.urllib.request.getproxies", return_value={}):
            self.assertEqual(effective_proxy_url(proxy), "")

    def test_system_mode_rejects_schemes_pymss_cannot_use(self):
        proxy = parse_proxy_config({"mode": "system", "url": "", "bypass": ""})
        with mock.patch("shared.worker_proxy.urllib.request.getproxies", return_value={"http": "ftp://127.0.0.1:21"}):
            self.assertEqual(effective_proxy_url(proxy), "")


class PymssCapabilityTest(unittest.TestCase):
    """Which progress source is usable depends on the pymss actually installed."""

    def test_detects_a_pymss_that_accepts_the_callback(self):
        def new_style(model_name, model_dir=None, progress_callback=None):
            pass

        self.assertTrue(_pymss_reports_progress(new_style))

    def test_detects_a_pymss_that_predates_the_callback(self):
        def old_style(model_name, model_dir=None):
            pass

        self.assertFalse(_pymss_reports_progress(old_style))

    def test_treats_an_unintrospectable_callable_as_unsupported(self):
        # Some builtins have no retrievable signature; guessing yes would raise TypeError at the
        # call site and fail the download outright.
        self.assertFalse(_pymss_reports_progress(print))

    def test_old_pymss_with_aria2_falls_back_before_stdout_is_corrupted(self):
        def old_style(model_name, model_dir=None):
            pass

        module = SimpleNamespace(ARIA2C_PATH="C:/bin/aria2c.exe")
        with mock.patch("core.worker_download.emit"):
            prepare_pymss_download(module, "task", old_style)

        self.assertIsNone(module.ARIA2C_PATH)

    def test_new_pymss_keeps_aria2_when_proxy_allows_it(self):
        def new_style(model_name, model_dir=None, progress_callback=None):
            pass

        module = SimpleNamespace(ARIA2C_PATH="C:/bin/aria2c.exe")
        with mock.patch("core.worker_download.emit"):
            prepare_pymss_download(module, "task", new_style)

        self.assertEqual(module.ARIA2C_PATH, "C:/bin/aria2c.exe")

    def test_user_can_force_urllib(self):
        def new_style(model_name, model_dir=None, progress_callback=None):
            pass

        module = SimpleNamespace(ARIA2C_PATH="C:/bin/aria2c.exe")
        with mock.patch("core.worker_download.emit"):
            prepare_pymss_download(module, "task", new_style, "urllib")

        self.assertIsNone(module.ARIA2C_PATH)


class StudioModelFileResolutionTest(unittest.TestCase):
    def test_uses_studio_catalog_entry_paths(self):
        entry = SimpleNamespace(
            name="debug_model.ckpt",
            source="catalog",
            relpath="debug/debug_model.ckpt",
            config_relpath="debug/debug_model.yaml",
            auxiliary_relpaths=("debug/debug_model.json",),
        )
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))

        with mock.patch("core.worker_download.get_any_model_entry", return_value=entry):
            resolved_entry, files = files_for_studio_model("debug_model.ckpt", str(root))

        self.assertIs(resolved_entry, entry)
        self.assertEqual(files, [
            ("debug/debug_model.ckpt", root / "debug" / "debug_model.ckpt"),
            ("debug/debug_model.yaml", root / "debug" / "debug_model.yaml"),
            ("debug/debug_model.json", root / "debug" / "debug_model.json"),
        ])


class Aria2FallbackTest(unittest.TestCase):
    def test_retries_with_urllib_when_aria2_fails(self):
        calls: list[str | None] = []
        openssl_env: list[tuple[str | None, str | None]] = []

        def download_file(_url, _dest, _expected_size, _expected_sha256, **_kwargs):
            calls.append(module.ARIA2C_PATH)
            openssl_env.append((os.environ.get("OPENSSL_CONF"), os.environ.get("OPENSSL_MODULES")))
            if module.ARIA2C_PATH:
                raise RuntimeError("aria2c failed with exit code 1: OSSL_PROVIDER_load 'legacy' failed")

        module = SimpleNamespace(
            ARIA2C_PATH="/app/bin/aria2c",
            remote_url=lambda relpath, source="modelscope", endpoint=None: f"https://example.test/{relpath}",
            _download_file=download_file,
            _already_valid=lambda *_args: False,
            _expected_size_and_hash=lambda *_args: (1, "sha256"),
            fetch_modelscope_file_index=lambda timeout=30, proxy=None: {},
        )
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))

        with mock.patch("core.worker_download.emit"), \
             mock.patch.dict(os.environ, {
                 "PYMSS_STUDIO_OPENSSL_CONF": "/app/bin/openssl/openssl.cnf",
                 "PYMSS_STUDIO_OPENSSL_MODULES": "/app/bin/openssl/ossl-modules",
             }, clear=True):
            result = download_studio_model(module, "model", [("model.th", root / "model.th")], task_id="task")

        self.assertEqual(calls, ["/app/bin/aria2c", None])
        self.assertEqual(openssl_env, [("/app/bin/openssl/openssl.cnf", "/app/bin/openssl/ossl-modules"), (None, None)])
        self.assertIsNone(module.ARIA2C_PATH)
        self.assertEqual(result["downloaded"], [str(root / "model.th")])


class DownloadCallbackSafetyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def module(self, download):
        return SimpleNamespace(
            ARIA2C_PATH="fixture-aria2c",
            remote_url=lambda relpath, **_kwargs: f"https://example.test/{relpath}",
            _download_file=download,
            _already_valid=lambda *_args: False,
            _expected_size_and_hash=lambda *_args: (6, None),
            fetch_modelscope_file_index=lambda **_kwargs: {},
        )

    def test_boundary_and_later_callback_errors_do_not_stop_or_retry_downloads(self):
        attempts, updates = [], []

        def download(_url, dest, _size, _hash, *, timeout, progress_callback):
            attempts.append((dest.name, module.ARIA2C_PATH))
            for done in (0, 3, 6):
                progress_callback(done, 6, "")
            dest.write_bytes(b"abcdef")

        def progress(*args):
            updates.append(args)
            raise OSError("aria2c progress output closed")

        module = self.module(download)
        files = [(name, self.root / name) for name in ("first.bin", "second.bin")]
        result = download_studio_model(module, "model", files, progress_callback=progress)
        self.assertEqual(attempts, [("first.bin", "fixture-aria2c"), ("second.bin", "fixture-aria2c")])
        self.assertEqual(len(updates), 8, "both file boundaries and every transfer callback were attempted")
        self.assertEqual(module.ARIA2C_PATH, "fixture-aria2c")
        self.assertEqual(result["downloaded"], [str(dest) for _, dest in files])
        self.assertTrue(all(dest.read_bytes() == b"abcdef" for _, dest in files))

    def test_keyboard_interrupt_from_boundary_or_later_callback_still_propagates(self):
        for cancel_on in (1, 3):
            with self.subTest(cancel_on=cancel_on):
                attempts, updates = [], []

                def download(_url, _dest, _size, _hash, *, timeout, progress_callback):
                    attempts.append(module.ARIA2C_PATH)
                    for done in (0, 3, 6):
                        progress_callback(done, 6, "")

                def progress(*args):
                    updates.append(args)
                    if len(updates) == cancel_on:
                        raise KeyboardInterrupt("cancel download")

                module = self.module(download)
                with self.assertRaises(KeyboardInterrupt):
                    download_studio_model(module, "model", [("model.bin", self.root / "model.bin")],
                                          progress_callback=progress)
                self.assertEqual(len(attempts), 0 if cancel_on == 1 else 1)
                self.assertEqual(module.ARIA2C_PATH, "fixture-aria2c")

    def test_file_download_errors_are_not_treated_as_callback_errors(self):
        def download(_url, _dest, _size, _hash, *, timeout, progress_callback):
            progress_callback(0, 6, "")
            raise OSError("HTTP file transfer failed")

        module = self.module(download)
        with self.assertRaisesRegex(OSError, "HTTP file transfer failed"):
            download_studio_model(module, "model", [("model.bin", self.root / "model.bin")],
                                  progress_callback=lambda *_args: None)


class ProgressAdapterTest(unittest.TestCase):
    """pymss reports one file at a time; the UI shows one bar for the whole model."""

    def _collect(self, **kwargs):
        seen: list[dict] = []
        adapter = _make_pymss_progress_adapter(emit_progress=lambda **kw: seen.append(kw), **kwargs)
        return adapter, seen

    def test_accumulates_across_files_when_the_byte_count_restarts(self):
        adapter, seen = self._collect(skipped_bytes=0, skipped_files=0, total_bytes=300)
        # 0..100 is the first file; the drop back to 0 is the only signal that a second began.
        for done in (0, 50, 100, 0, 60, 200):
            adapter(done, 0, "")

        self.assertEqual([kw["downloaded_bytes"] for kw in seen], [0, 50, 100, 100, 160, 300])
        self.assertEqual([kw["completed_files"] for kw in seen], [0, 0, 0, 1, 1, 1])

    def test_files_that_were_already_valid_seed_the_total(self):
        # pymss never calls back for a file it skips, so those bytes have to be counted up front.
        adapter, seen = self._collect(skipped_bytes=500, skipped_files=2, total_bytes=600)
        adapter(50, 0, "")

        self.assertEqual(seen[0]["downloaded_bytes"], 550)
        self.assertEqual(seen[0]["completed_files"], 2)


class DownloadCacheIntegrityTest(unittest.TestCase):
    def setUp(self):
        from pymss import model_download

        self.module = model_download
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.dest = self.root / "model.bin"
        self.files = [("model.bin", self.dest)]
        self.payload = b"complete-model-payload"
        self.download_calls = []
        self.fetch = self.enterContext(mock.patch.object(
            self.module, "fetch_modelscope_file_index", side_effect=URLError("index unavailable"),
        ))

        def download(_url, dest, _size, _digest, *, timeout, progress_callback=None, proxy=None):
            self.download_calls.append(dest)
            dest.write_bytes(self.payload)
            if progress_callback is not None:
                progress_callback(len(self.payload), len(self.payload), "Downloaded model.bin")

        self.enterContext(mock.patch.object(self.module, "_download_file", download))
        self.enterContext(mock.patch.object(self.module, "ARIA2C_PATH", None))
        self.enterContext(mock.patch.object(self.module, "remote_url", return_value="https://example.test/model.bin"))
        self.events = self.enterContext(mock.patch("core.worker_download.emit"))
        self.failures = self.enterContext(mock.patch("core.worker_download.emit_error", return_value=1))
        self.enterContext(mock.patch("core.worker_download.effective_proxy_url", return_value=""))
        self.enterContext(mock.patch("core.worker_download.model_root", return_value=self.root))
        self.model_info = self.enterContext(mock.patch("core.worker_download.model_to_dict", return_value={"name": "model"}))
        self.enterContext(mock.patch("core.worker_download.effective_source_for", return_value="modelscope"))

    def test_direct_download_rejects_unverified_cache_after_index_failure(self):
        self.dest.write_bytes(b"truncated")

        with self.assertRaisesRegex(RuntimeError, "cached"):
            download_studio_model(self.module, "model", self.files, proxy="")

        self.fetch.assert_called_once_with(timeout=30, proxy="")
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.dest.read_bytes(), b"truncated")

    def test_explicit_unverified_modes_keep_existing_cache_semantics(self):
        self.dest.write_bytes(self.payload)
        self.fetch.side_effect = AssertionError("metadata must not be fetched")

        for options in [{"verify": False}, {"endpoint": "https://custom.test"}]:
            with self.subTest(options=options):
                result = download_studio_model(self.module, "model", self.files, **options)
                self.assertEqual(result, {"downloaded": [], "skipped": [str(self.dest)]})

        self.fetch.assert_not_called()
        self.assertEqual(self.download_calls, [])
        self.assertEqual(self.dest.read_bytes(), self.payload)

    def test_available_metadata_still_reuses_a_verified_cache(self):
        self.dest.write_bytes(self.payload)
        self.fetch.side_effect = None
        self.fetch.return_value = {"model.bin": {
            "Size": len(self.payload), "Sha256": hashlib.sha256(self.payload).hexdigest(),
        }}

        result = download_studio_model(self.module, "model", self.files, proxy="")

        self.assertEqual(result, {"downloaded": [], "skipped": [str(self.dest)]})
        self.assertEqual(self.download_calls, [])
        self.fetch.assert_called_once_with(timeout=30, proxy="")


class DownloadMetadataTest(unittest.TestCase):
    def test_verify_false_ignores_a_supplied_index_and_does_not_fetch_metadata(self):
        fetch = mock.Mock(side_effect=AssertionError("metadata must not be fetched"))
        expected = mock.Mock(return_value=(None, None))
        download = mock.Mock()
        module = SimpleNamespace(
            ARIA2C_PATH=None,
            remote_url=lambda relpath, **_kwargs: f"https://example.test/{relpath}",
            _download_file=download,
            _already_valid=lambda *_args: False,
            _expected_size_and_hash=expected,
            fetch_modelscope_file_index=fetch,
        )
        dest = Path("fixture-model.bin")
        result = download_studio_model(module, "model", [("model.bin", dest)],
                                       verify=False, file_index={"model.bin": {"Size": 64}})
        fetch.assert_not_called()
        expected.assert_called_once_with("model.bin", None)
        self.assertEqual(download.call_args.args[2:4], (None, None))
        self.assertEqual(result["downloaded"], [str(dest)])


class PartialFileWatcherTest(unittest.TestCase):
    """The fallback for a pymss that cannot report progress itself."""

    def _watch_once(self, *, count_partial_bytes: bool) -> dict:
        tmp = Path(tempfile.mkdtemp())
        dest = tmp / "model.pth"
        # aria2 preallocates, so a barely-started download already sits at its full size.
        (tmp / "model.pth.part").write_bytes(b"x" * 120)

        seen: list[dict] = []
        stop = threading.Event()

        def emit_progress(**kwargs):
            seen.append(kwargs)
            stop.set()  # one pass is all the assertions need

        _watch_download_progress(
            files=[("vr/model.pth", dest)],
            expected_sizes={str(dest): 120},
            already_done=set(),
            stop=stop,
            emit_progress=emit_progress,
            count_partial_bytes=count_partial_bytes,
        )
        return seen[0]

    def test_counts_partial_bytes_when_the_downloader_grows_the_file(self):
        result = self._watch_once(count_partial_bytes=True)

        self.assertEqual(result["downloaded_bytes"], 120)

    def test_ignores_partial_bytes_that_would_report_a_finished_download(self):
        result = self._watch_once(count_partial_bytes=False)

        self.assertEqual(result["downloaded_bytes"], 0)
        self.assertEqual(result["completed_files"], 0)




class CatalogFileIndexTest(unittest.TestCase):
    """catalog 的 file_hashes 直接当下载校验索引用，全程离线。"""

    def _entry(self):
        return SimpleNamespace(
            relpath="a/model.ckpt", config_relpath="a/model.yaml",
            auxiliary_relpaths=("a/state.pt",), size_bytes=1000,
            file_hashes={"a/model.ckpt": "h1", "a/model.yaml": "h2", "a/state.pt": "h3"},
        )

    def test_index_covers_every_file_with_primary_size(self):
        index = catalog_file_index(self._entry())
        self.assertEqual(index["a/model.ckpt"], {"Size": 1000, "Sha256": "h1"})
        self.assertEqual(index["a/model.yaml"], {"Size": None, "Sha256": "h2"})
        self.assertEqual(index["a/state.pt"], {"Size": None, "Sha256": "h3"})

    def test_catalog_index_disables_the_online_index_lookup(self):
        fetch = mock.Mock(side_effect=AssertionError("must not fetch"))
        download = mock.Mock()
        module = SimpleNamespace(
            ARIA2C_PATH=None,
            remote_url=lambda relpath, **_kw: f"https://example.test/{relpath}",
            _download_file=download,
            _already_valid=lambda *_args: False,
            _expected_size_and_hash=lambda relpath, index: (index[relpath]["Size"], index[relpath]["Sha256"]),
            fetch_modelscope_file_index=fetch,
        )
        dest = Path("fixture-catalog.bin")
        index = catalog_file_index(self._entry())
        download_studio_model(module, "model", [("a/model.ckpt", dest)], file_index=index)
        fetch.assert_not_called()
        self.assertEqual(download.call_args.args[2:4], (1000, "h1"))
