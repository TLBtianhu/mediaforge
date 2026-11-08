"""Current SQLite identity and the library/recycling lease; only owned temp fixtures."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from . import _bootstrap as _worker_test_bootstrap
from core import library
from shared.asset_references import AssetReferencesBusy, asset_reference_lock, ensure_plain_path
from shared import library_schema
from shared.worker_subprocess import hidden_subprocess_kwargs


@contextmanager
def fixture_database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class LibraryReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mediaforge-library-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"

    def asset(self, name="source.wav"):
        path = self.root / name
        path.write_bytes(b"small media fixture; no actual decoding")
        return path

    def import_asset(self, path, root=None):
        with mock.patch.object(library.ffmpeg, "probe", return_value={"size": path.stat().st_size, "duration": 1}), \
             mock.patch.object(library.ffmpeg, "kind_of", return_value="audio"):
            return library.import_paths([path], data_root=root or self.data)["assets"][0]

    def test_fresh_library_matches_the_shared_contract_and_zero_content_lease(self):
        outcome = library.stats(data_root=self.data)
        self.assertEqual(outcome["total"], {"count": 0, "bytes": 0})
        database = self.data / "library.db"
        with fixture_database(database) as connection:
            library_schema.validate_connection(connection)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0x4D464C42)
            actual = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
            self.assertEqual(actual, {entry["name"] for entry in library_schema.CONTRACT["objects"]})
        self.assertEqual((self.data / ".asset-references.lock").read_bytes(), b"")
        self.assertFalse(any(self.data.glob("library.db-*")))

    def test_current_crud_preserves_sources_and_keeps_sqlite_the_only_index(self):
        source = self.asset()
        item = self.import_asset(source)
        self.assertEqual(item["path"], str(source.resolve()))
        self.assertEqual(library.update_tags(item["id"], "fixture", data_root=self.data)["asset"]["tags"], "fixture")
        self.assertEqual(library.list_assets("fixture", data_root=self.data)["assets"][0]["id"], item["id"])
        self.assertEqual(library.stats(data_root=self.data)["total"]["count"], 1)
        self.assertEqual(library.remove_assets([item["id"]], data_root=self.data), {"removed": 1})
        self.assertTrue(source.is_file())
        self.assertEqual(library.stats(data_root=self.data)["total"]["count"], 0)
        self.assertEqual({path.name for path in self.data.iterdir()}, {"library.db", ".asset-references.lock"})

    def test_existing_bad_schema_precedes_import_scan_and_releases_the_lease(self):
        self.data.mkdir()
        (self.data / "library.db").write_bytes(b"")
        with mock.patch.object(library, "_iter_files") as scan, mock.patch.object(library.ffmpeg, "probe") as probe:
            with self.assertRaises(library_schema.LibrarySchemaError):
                library.import_paths([self.root], data_root=self.data)
        scan.assert_not_called()
        probe.assert_not_called()
        with asset_reference_lock(self.data, timeout=0):
            pass
        self.assertEqual((self.data / "library.db").read_bytes(), b"")

    def test_failed_scan_closes_connection_and_releases_lease_before_next_operation(self):
        with mock.patch.object(library, "_iter_files", side_effect=ValueError("fixture scan failed")):
            with self.assertRaisesRegex(ValueError, "fixture scan failed"):
                library.import_paths([self.root], data_root=self.data)
        with asset_reference_lock(self.data, timeout=0):
            pass
        self.assertEqual(library.stats(data_root=self.data)["total"]["count"], 0)

    def test_more_than_ui_limit_remain_in_the_original_sqlite_reference_source(self):
        library.stats(data_root=self.data)
        with asset_reference_lock(self.data):
            connection = library_schema.connect_current(self.data / "library.db")
            try:
                connection.executemany("INSERT INTO assets(path,name,kind,created_at,updated_at) VALUES (?,?,?,'fixture','fixture')",
                                       [(str(self.root / f"source-{index}.wav"), f"source-{index}", "audio") for index in range(2205)])
                connection.commit()
                self.assertEqual(connection.execute("SELECT COUNT(path) FROM assets").fetchone()[0], 2205)
            finally:
                connection.close()
        self.assertEqual(len(library.list_assets(limit=9999, data_root=self.data)["assets"]), 2000)
        self.assertEqual(library.stats(data_root=self.data)["total"]["count"], 2205)

    def test_existing_empty_and_legacy_databases_are_rejected_without_byte_changes(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                root = self.root / str(legacy)
                root.mkdir()
                path = root / "library.db"
                if legacy:
                    with fixture_database(path) as connection:
                        for entry in library_schema.CONTRACT["objects"]:
                            if entry["create"]:
                                connection.execute(entry["sql"])
                else:
                    path.write_bytes(b"")
                original = path.read_bytes()
                for call in (lambda: library.list_assets(data_root=root), lambda: library.stats(data_root=root),
                             lambda: library.remove_assets([1], data_root=root),
                             lambda: library.update_tags(1, "test", data_root=root),
                             lambda: library.import_paths([self.root / "absent.wav"], data_root=root)):
                    with self.assertRaises(library_schema.LibrarySchemaError):
                        call()
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(any(root.glob("library.db-*")))

    def test_future_foreign_changed_and_extra_schema_objects_are_not_repaired(self):
        changes = ["PRAGMA user_version=2", "PRAGMA application_id=0", "DROP INDEX idx_assets_kind",
                   "CREATE INDEX extra_idx ON assets(name)", "CREATE TABLE extra(value)",
                   "CREATE VIEW extra_view AS SELECT path FROM assets",
                   "CREATE TRIGGER extra_trigger AFTER INSERT ON assets BEGIN SELECT 1; END",
                   "ALTER TABLE assets ADD COLUMN extra TEXT"]
        for index, sql in enumerate(changes):
            with self.subTest(sql=sql):
                root = self.root / str(index)
                library.stats(data_root=root)
                path = root / "library.db"
                with fixture_database(path) as connection:
                    connection.execute(sql)
                before = path.read_bytes()
                with self.assertRaises(library_schema.LibrarySchemaError):
                    library.stats(data_root=root)
                self.assertEqual(path.read_bytes(), before)

    def test_same_named_trigger_is_not_hidden_by_a_later_index_row(self):
        source = self.asset()
        item = self.import_asset(source)
        path = self.data / "library.db"
        with fixture_database(path) as connection:
            connection.execute("DROP INDEX idx_assets_kind")
            connection.execute("CREATE TRIGGER idx_assets_kind AFTER DELETE ON assets BEGIN DELETE FROM assets; END")
            connection.execute("CREATE INDEX idx_assets_kind ON assets(kind)")
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name='idx_assets_kind'")]
            self.assertEqual(names, ["idx_assets_kind", "idx_assets_kind"])
        before = path.read_bytes()
        for call in (lambda: library.stats(data_root=self.data), lambda: library.list_assets(data_root=self.data),
                     lambda: library.update_tags(item["id"], "changed", data_root=self.data),
                     lambda: library.remove_assets([item["id"]], data_root=self.data)):
            with self.assertRaisesRegex(library_schema.LibrarySchemaError, "objects"):
                call()
            self.assertEqual(path.read_bytes(), before)
        self.assertTrue(source.is_file())

    def test_every_entry_rejects_invalid_rows_before_any_update_or_scan(self):
        cases = [("path", ""), ("path", "relative.wav"), ("path", b"blob.wav"),
                 ("path", "\ufeff" + str(self.root / "bom.wav")), ("path", str(self.root / "nul\0.wav")),
                 ("thumbnail", None), ("thumbnail", b"blob.jpg"), ("thumbnail", "relative.jpg"),
                 ("thumbnail", str(self.root / "nul\0.jpg")), ("kind", "folder"), ("kind", b"audio"),
                 ("name", ""), ("name", b"blob name"), ("format", b"blob format"), ("tags", b"blob tags"),
                 ("size", -1), ("size", 1.5), ("width", -1), ("height", 2.5), ("duration", -.1),
                 ("fps", float("inf")), ("created_at", ""), ("updated_at", b"blob timestamp"), ("id", 0),
                 ("path", "INVALID_UTF8_SQL")]
        source = self.asset()
        for index, (column, value) in enumerate(cases):
            with self.subTest(column=column, value=value):
                root = self.root / f"bad-row-{index}"
                item = self.import_asset(source, root)
                database = root / "library.db"
                with fixture_database(database) as connection:
                    if value == "INVALID_UTF8_SQL":
                        connection.execute("UPDATE assets SET path=CAST(x'80' AS TEXT)")
                    else:
                        connection.execute(f"UPDATE assets SET {column}=?", (value,))
                before = database.read_bytes()
                for call in (lambda: library.list_assets(data_root=root), lambda: library.stats(data_root=root),
                             lambda: library.update_tags(item["id"], "changed", data_root=root),
                             lambda: library.remove_assets([item["id"]], data_root=root),
                             lambda: library.import_paths([source], data_root=root)):
                    with mock.patch.object(library, "_iter_files") as scan, self.assertRaises(library_schema.LibrarySchemaError):
                        call()
                    scan.assert_not_called()
                    self.assertEqual(database.read_bytes(), before)
                    self.assertFalse(any(root.glob("library.db-*")))
                with asset_reference_lock(root, timeout=0):
                    pass

    def test_full_row_validation_does_not_stop_at_the_visible_two_thousand_assets(self):
        library.stats(data_root=self.data)
        database = self.data / "library.db"
        with fixture_database(database) as connection:
            connection.executemany("INSERT INTO assets(path,name,kind,created_at,updated_at) VALUES (?,?,?,'fixture','fixture')",
                                   [(str(self.root / f"row-{index}.wav"), f"row-{index}", "audio") for index in range(2005)])
            connection.execute("UPDATE assets SET thumbnail=NULL WHERE id=2005")
        before = database.read_bytes()
        with self.assertRaises(library_schema.LibrarySchemaError):
            library.list_assets(limit=1, data_root=self.data)
        self.assertEqual(database.read_bytes(), before)

    def test_nullable_metadata_stays_valid_without_weakening_path_requirements(self):
        item = self.import_asset(self.asset())
        with fixture_database(self.data / "library.db") as connection:
            connection.execute("UPDATE assets SET format=NULL,tags=NULL,size=NULL,duration=NULL,width=NULL,height=NULL,fps=NULL")
        saved = library.list_assets(data_root=self.data)["assets"][0]
        self.assertEqual(saved["id"], item["id"])
        self.assertEqual(saved["size"], 0)
        self.assertEqual(saved["tags"], "")

    def test_quick_check_requires_exactly_one_ok_and_never_repairs(self):
        library.stats(data_root=self.data)
        database = self.data / "library.db"
        before = database.read_bytes()
        with fixture_database(database) as actual:
            class Probe:
                def execute(inner, sql):
                    return [("fixture integrity failure",)] if sql == "PRAGMA quick_check" else actual.execute(sql)
            with self.assertRaisesRegex(library_schema.LibrarySchemaError, "integrity"):
                library_schema.validate_connection(Probe())
        self.assertEqual(database.read_bytes(), before)

    def test_corrupt_database_and_sidecars_are_preserved_without_automatic_recovery(self):
        self.data.mkdir()
        path = self.data / "library.db"
        path.write_bytes(b"invalid SQLite fixture")
        before = path.read_bytes()
        with self.assertRaises(library_schema.LibrarySchemaError):
            library.stats(data_root=self.data)
        self.assertEqual(path.read_bytes(), before)
        for suffix in ("-wal", "-shm", "-journal"):
            for main_exists in (False, True):
                with self.subTest(suffix=suffix, main_exists=main_exists):
                    root = self.root / (suffix + str(main_exists))
                    if main_exists:
                        library.stats(data_root=root)
                    else:
                        root.mkdir()
                    database = root / "library.db"
                    original = database.read_bytes() if main_exists else None
                    sidecar = root / ("library.db" + suffix)
                    sidecar.write_bytes(b"preserve sidecar fixture")
                    with self.assertRaises(library_schema.LibrarySchemaError):
                        library.stats(data_root=root)
                    self.assertEqual(sidecar.read_bytes(), b"preserve sidecar fixture")
                    self.assertEqual(database.read_bytes() if database.exists() else None, original)

    def test_declared_rollback_mode_rejects_a_wal_database_even_without_live_sidecars(self):
        library.stats(data_root=self.data)
        path = self.data / "library.db"
        with fixture_database(path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        before = path.read_bytes()
        entries = {item.name for item in self.data.iterdir()}
        self.assertFalse(any(path.name.startswith("library.db-") for path in self.data.iterdir()))
        with self.assertRaises(library_schema.LibrarySchemaError):
            library.stats(data_root=self.data)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({item.name for item in self.data.iterdir()}, entries)

    def test_explicit_roots_and_data_root_alias_do_not_mutate_the_environment(self):
        first, second = self.root / "one", self.root / "two"
        source_a, source_b = self.asset("a.wav"), self.asset("b.wav")
        keys = ("MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "PYMSS_STUDIO_DATA_ROOT")
        before = {key: os.environ.get(key) for key in keys}
        self.import_asset(source_a, first)
        self.import_asset(source_b, second)
        self.assertEqual(library.list_assets(data_root=first)["assets"][0]["path"], str(source_a))
        self.assertEqual(library.list_assets(data_root=second)["assets"][0]["path"], str(source_b))
        self.assertEqual({key: os.environ.get(key) for key in keys}, before)
        with mock.patch.dict(os.environ, {"MEDIAFORGE_DATA_ROOT": str(first), "MEDIAFORGE_DATA_DIR": str(second)}):
            self.assertEqual(library.data_dir(), first)
            self.assertEqual(library.stats()["total"]["count"], 1)
        self.assertIsNone(library._OPERATION_ROOT.get())

    def test_concurrent_explicit_roots_have_independent_contexts(self):
        roots = [self.root / "thread-one", self.root / "thread-two"]
        ready = threading.Barrier(2)
        errors = []
        original = library._connect

        def connect():
            ready.wait(timeout=3)
            return original()

        def action(root):
            try:
                self.assertEqual(library.stats(data_root=root)["database"], str(root / "library.db"))
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(library, "_connect", side_effect=connect):
            threads = [threading.Thread(target=action, args=(root,)) for root in roots]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        if errors:
            raise errors[0]
        self.assertTrue(all((root / "library.db").is_file() for root in roots))

    def test_import_lease_covers_scan_probe_thumbnail_commit_and_close(self):
        source = self.asset()
        active = False
        phases = []
        connect = library._connect

        @contextmanager
        def lease(root):
            nonlocal active
            with asset_reference_lock(root):
                active = True
                try:
                    yield
                finally:
                    self.assertIn("closed", phases)
                    active = False

        def scan(paths):
            self.assertTrue(active)
            phases.append("scan")
            return iter([source])

        def probe(path):
            self.assertTrue(active)
            phases.append("probe")
            return {"size": 5}

        class Connection:
            def __init__(inner):
                inner.actual = connect()
            def __getattr__(inner, name):
                return getattr(inner.actual, name)
            def commit(inner):
                self.assertTrue(active)
                phases.append("commit")
                return inner.actual.commit()
            def close(inner):
                self.assertTrue(active)
                phases.append("closed")
                inner.actual.close()

        def thumbnail(*args):
            self.assertTrue(active)
            phases.append("thumbnail")
            return ""

        with mock.patch.object(library, "asset_reference_lock", side_effect=lease), \
             mock.patch.object(library, "_iter_files", side_effect=scan), \
             mock.patch.object(library, "_connect", side_effect=Connection), \
             mock.patch.object(library.ffmpeg, "probe", side_effect=probe), \
             mock.patch.object(library.ffmpeg, "kind_of", return_value="audio"), \
             mock.patch.object(library, "_thumbnail", side_effect=thumbnail):
            self.assertEqual(library.import_paths([source], data_root=self.data)["total"], 1)
        self.assertEqual(phases, ["scan", "probe", "thumbnail", "commit", "closed"])
        with asset_reference_lock(self.data, timeout=0):
            pass

    def test_remove_only_unlinks_the_exact_owned_thumbnail(self):
        source = self.asset()
        item = self.import_asset(source)
        external = self.asset("unrelated.jpg")
        with fixture_database(self.data / "library.db") as connection:
            connection.execute("UPDATE assets SET thumbnail=? WHERE id=?", (str(external), item["id"]))
        library.remove_assets([item["id"]], data_root=self.data)
        self.assertTrue(external.is_file())
        self.assertTrue(source.is_file())
        item = self.import_asset(source)
        digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:20]
        owned = self.data / "thumbnails" / f"{digest}.jpg"
        owned.parent.mkdir(exist_ok=True)
        owned.write_bytes(b"thumbnail")
        with fixture_database(self.data / "library.db") as connection:
            connection.execute("UPDATE assets SET thumbnail=? WHERE id=?", (str(owned), item["id"]))
        library.remove_assets([item["id"]], data_root=self.data)
        self.assertFalse(owned.exists())
        self.assertTrue(source.exists())

    def test_shared_schema_contract_parser_rejects_unknown_and_duplicate_keys(self):
        contract = copy.deepcopy(library_schema.CONTRACT)
        path = self.root / "schema.json"
        for value in ({**contract, "extra": True}, {**contract, "schemaVersion": 0}, {**contract, "lockFile": "other.lock"}):
            path.write_text(json.dumps(value), encoding="utf-8")
            with mock.patch.object(library_schema, "CONTRACT_PATH", path), self.assertRaises(library_schema.LibrarySchemaError):
                library_schema._load_contract()
        path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
        with mock.patch.object(library_schema, "CONTRACT_PATH", path), self.assertRaises(library_schema.LibrarySchemaError):
            library_schema._load_contract()

    def test_links_are_rejected_before_opening_or_mutating_any_library_file(self):
        metadata = mock.Mock(st_mode=0o100600, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "reparse"):
                ensure_plain_path(self.data / "library.db")

    def test_real_hidden_process_lease_blocks_every_entry_before_work_and_is_reusable(self):
        project_python = Path(__file__).resolve().parents[2] / "python"
        script = """import sys
from pathlib import Path
assert sys.version_info[:2]==(3,12)
sys.path.insert(0,sys.argv[1])
from shared.asset_references import asset_reference_lock
with asset_reference_lock(Path(sys.argv[2])):
 print('held',flush=True)
 sys.stdin.buffer.read(1)
"""
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", script, str(project_python), str(self.data)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, **hidden_subprocess_kwargs())
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertEqual(ready.get(timeout=5).strip(), "held")
            reader.join(timeout=1)
            def short_lease(root):
                return asset_reference_lock(root, timeout=.01)
            with mock.patch.object(library, "asset_reference_lock", side_effect=short_lease), \
                 mock.patch.object(library, "_iter_files") as scan:
                for call in (lambda: library.stats(data_root=self.data), lambda: library.list_assets(data_root=self.data),
                             lambda: library.import_paths([], data_root=self.data), lambda: library.remove_assets([], data_root=self.data),
                             lambda: library.update_tags(1, "tags", data_root=self.data)):
                    with self.assertRaises(AssetReferencesBusy):
                        call()
                scan.assert_not_called()
            self.assertFalse((self.data / "library.db").exists())
            child.communicate("release\n", timeout=5)
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                try:
                    child.communicate("release\n", timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()  # Only the finite lease fixture process created above.
                    child.communicate(timeout=5)
            else:
                child.communicate(timeout=5)
            reader.join(timeout=1)
        self.assertEqual(library.stats(data_root=self.data)["total"]["count"], 0)
        self.assertEqual((self.data / ".asset-references.lock").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
