"""The current SQLite library contract; existing unknown databases are never repaired."""
from __future__ import annotations

import json
from collections import Counter
import math
from pathlib import Path
import sqlite3
import stat

from shared.asset_references import LOCK_NAME, ensure_plain_path

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "manifest/library-schema.json"


class LibrarySchemaError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LibrarySchemaError(f"Duplicate library schema key: {key}")
        result[key] = value
    return result


def _load_contract():
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"), object_pairs_hook=_object)
    fields = {"schemaVersion", "applicationId", "databaseFile", "lockFile", "journalMode", "objects"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["applicationId"]) is not int or value["applicationId"] != 0x4D464C42
            or value["databaseFile"] != "library.db" or value["lockFile"] != LOCK_NAME
            or value["journalMode"] != "delete" or not isinstance(value["objects"], list)
            or not value["objects"]):
        raise LibrarySchemaError("Invalid library schema contract")
    names = set()
    for entry in value["objects"]:
        if (not isinstance(entry, dict) or set(entry) != {"type", "name", "table", "sql", "create"}
                or not isinstance(entry["type"], str) or entry["type"] not in {"table", "index"}
                or not isinstance(entry["name"], str) or not entry["name"] or entry["name"] in names
                or not isinstance(entry["table"], str) or not entry["table"]
                or not (entry["sql"] is None or isinstance(entry["sql"], str) and entry["sql"])
                or type(entry["create"]) is not bool or entry["create"] and entry["sql"] is None):
            raise LibrarySchemaError("Invalid library schema object contract")
        names.add(entry["name"])
    return value


CONTRACT = _load_contract()


def _sql(value):
    return " ".join(value.split()) if isinstance(value, str) else value


def validate_connection(connection: sqlite3.Connection) -> None:
    try:
        _validate_connection(connection)
    except (sqlite3.Error, UnicodeError, TypeError, OverflowError) as error:
        raise LibrarySchemaError(f"Invalid library contents; original database preserved: {error}") from error


def _validate_connection(connection: sqlite3.Connection) -> None:
    if (connection.execute("PRAGMA user_version").fetchone()[0] != CONTRACT["schemaVersion"]
            or connection.execute("PRAGMA application_id").fetchone()[0] != CONTRACT["applicationId"]
            or str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != CONTRACT["journalMode"]):
        raise LibrarySchemaError("Unsupported library identity/version/journal mode; original database preserved")
    # Triggers and indexes occupy different namespaces and may share a name.
    # Preserve every complete row, rather than hiding one behind a name map.
    actual = Counter((row[0], row[1], row[2], _sql(row[3])) for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"))
    expected = Counter((entry["type"], entry["name"], entry["table"], _sql(entry["sql"])) for entry in CONTRACT["objects"])
    if actual != expected:
        raise LibrarySchemaError("Unsupported library objects; original database preserved")
    if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise LibrarySchemaError("Library integrity check failed; original database preserved")
    for row in connection.execute("SELECT id,path,name,kind,format,size,duration,width,height,fps,thumbnail,tags,created_at,updated_at FROM assets"):
        if type(row[0]) is not int or row[0] <= 0:
            raise LibrarySchemaError("Invalid library asset identity")
        for index in (2, 3, 12, 13):
            if not isinstance(row[index], str) or not row[index]:
                raise LibrarySchemaError("Invalid required library asset text")
        if row[3] not in {"audio", "image", "video"}:
            raise LibrarySchemaError("Invalid library media kind")
        for index in (4, 11):
            if row[index] is not None and not isinstance(row[index], str):
                raise LibrarySchemaError("Invalid optional library asset text")
        for index in (5, 7, 8):
            if row[index] is not None and not (type(row[index]) is int and row[index] >= 0):
                raise LibrarySchemaError("Invalid library asset integer")
        for index in (6, 9):
            if row[index] is not None and not (type(row[index]) in {int, float} and math.isfinite(row[index]) and row[index] >= 0):
                raise LibrarySchemaError("Invalid library asset number")
        for index in (1, 10):
            value = row[index]
            if not isinstance(value, str):
                raise LibrarySchemaError("Invalid library asset/thumbnail path type")
            if index == 10 and value == "":
                continue
            if not value or "\0" in value or value.startswith("\ufeff") or not Path(value).is_absolute():
                raise LibrarySchemaError("Unresolved or invalid library asset/thumbnail path")
            value.encode("utf-8", errors="strict")


def _check_paths(path: Path) -> bool:
    ensure_plain_path(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        ensure_plain_path(sidecar)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        raise LibrarySchemaError(f"Unresolved library sidecar; original database preserved: {sidecar.name}")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise LibrarySchemaError("Library database must be a regular file")
    with path.open("rb") as source:
        header = source.read(20)
    # Read-only SQLite can still create WAL shared-memory sidecars. Reject its
    # persistent WAL header before opening SQLite, including after a clean close.
    if len(header) != 20 or header[:16] != b"SQLite format 3\0" or header[18:20] != b"\x01\x01":
        raise LibrarySchemaError("Unsupported library header/journal mode; original database preserved")
    return True


def connect_current(path: Path) -> sqlite3.Connection:
    """Caller holds the asset lease until this returned connection is closed."""
    path = Path(path).absolute()
    existing = _check_paths(path)
    if existing:
        # Validate in read-only mode before a writable connection can recover or
        # mutate an old/foreign/empty database. No CREATE IF NOT EXISTS fallback.
        readonly = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            readonly.execute("PRAGMA trusted_schema=OFF")
            validate_connection(readonly)
        finally:
            readonly.close()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        if not existing:
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != CONTRACT["journalMode"]:
                raise LibrarySchemaError("Cannot create a rollback-journal library")
            connection.execute("BEGIN IMMEDIATE")
            for entry in CONTRACT["objects"]:
                if entry["create"]:
                    connection.execute(entry["sql"])
            connection.execute(f"PRAGMA application_id={CONTRACT['applicationId']}")
            connection.execute(f"PRAGMA user_version={CONTRACT['schemaVersion']}")
            connection.commit()
        validate_connection(connection)
        return connection
    except BaseException:
        connection.close()
        raise
