"""Local media asset library (SQLite + thumbnails).

The library is deliberately independent from the model catalog: it indexes
source media so the UI can work like a media shelf instead of sending users
back to Explorer for every task.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import ffmpeg

SUPPORTED_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".mts", ".wmv", ".flv",
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
}
MAX_THUMBNAIL_SIDE = 480


def data_dir() -> Path:
    override = os.environ.get("MEDIAFORGE_DATA_DIR")
    if override:
        root = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        root = Path(appdata) / "MediaForge" if appdata else Path.home() / ".mediaforge"
    root.mkdir(parents=True, exist_ok=True)
    return root


def database_path() -> Path:
    return data_dir() / "library.db"


def thumbnail_dir() -> Path:
    path = data_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path(), timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            format TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            duration REAL DEFAULT 0,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            fps REAL DEFAULT 0,
            thumbnail TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_assets_updated ON assets(updated_at)")
    connection.commit()
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_asset(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "path": row["path"],
        "name": row["name"],
        "kind": row["kind"],
        "format": row["format"] or "",
        "size": int(row["size"] or 0),
        "duration": float(row["duration"] or 0),
        "width": int(row["width"] or 0),
        "height": int(row["height"] or 0),
        "fps": float(row["fps"] or 0),
        "thumbnail": row["thumbnail"] or "",
        "tags": row["tags"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _iter_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    yield child
        elif path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path


def _thumbnail(path: Path, meta: dict, kind: str) -> str:
    if kind == "audio":
        return ""
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8", errors="replace")).hexdigest()[:20]
    target = thumbnail_dir() / f"{digest}.jpg"
    if target.is_file():
        return str(target)
    command = [ffmpeg.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if kind == "video" and float(meta.get("duration") or 0) > 0.4:
        command += ["-ss", "0.2"]
    command += [
        "-i", str(path),
        "-vf", f"scale='min({MAX_THUMBNAIL_SIDE},iw)':-2",
        "-frames:v", "1", "-q:v", "3", str(target),
    ]
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except Exception:  # noqa: BLE001
        return ""
    return str(target) if target.is_file() else ""


def import_paths(paths: Iterable[str | Path]) -> dict:
    files = list(_iter_files(paths))
    imported: list[dict] = []
    errors: list[dict] = []
    connection = _connect()
    try:
        for path in files:
            try:
                meta = ffmpeg.probe(path)
                kind = ffmpeg.kind_of(meta)
                video = meta.get("video") or {}
                audio = meta.get("audio") or {}
                now = _now()
                thumbnail = _thumbnail(path, meta, kind)
                connection.execute(
                    """
                    INSERT INTO assets
                        (path, name, kind, format, size, duration, width, height, fps, thumbnail, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        name = excluded.name,
                        kind = excluded.kind,
                        format = excluded.format,
                        size = excluded.size,
                        duration = excluded.duration,
                        width = excluded.width,
                        height = excluded.height,
                        fps = excluded.fps,
                        thumbnail = excluded.thumbnail,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(path.resolve()), path.name, kind, meta.get("format") or "",
                        int(meta.get("size") or 0), float(meta.get("duration") or 0),
                        int(video.get("width") or 0), int(video.get("height") or 0),
                        float(video.get("fps") or 0), thumbnail, now, now,
                    ),
                )
                connection.commit()
                row = connection.execute("SELECT * FROM assets WHERE path = ?", (str(path.resolve()),)).fetchone()
                if row:
                    imported.append(_row_to_asset(row))
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})
        return {"assets": imported, "errors": errors, "total": len(files)}
    finally:
        connection.close()


def list_assets(query: str = "", kind: str = "all", limit: int = 500) -> dict:
    connection = _connect()
    try:
        clauses: list[str] = []
        params: list[object] = []
        text = str(query or "").strip()
        if text:
            clauses.append("(name LIKE ? OR path LIKE ? OR tags LIKE ?)")
            like = f"%{text}%"
            params.extend([like, like, like])
        if kind in {"audio", "image", "video"}:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit or 500), 2000)))
        rows = connection.execute(
            f"SELECT * FROM assets {where} ORDER BY updated_at DESC LIMIT ?", params
        ).fetchall()
        return {"assets": [_row_to_asset(row) for row in rows]}
    finally:
        connection.close()


def remove_assets(ids: Iterable[int]) -> dict:
    values = [int(value) for value in ids]
    if not values:
        return {"removed": 0}
    placeholders = ",".join("?" for _ in values)
    connection = _connect()
    try:
        rows = connection.execute(
            f"SELECT thumbnail FROM assets WHERE id IN ({placeholders})", values
        ).fetchall()
        connection.execute(f"DELETE FROM assets WHERE id IN ({placeholders})", values)
        connection.commit()
        for row in rows:
            thumbnail = str(row["thumbnail"] or "")
            if thumbnail:
                try:
                    Path(thumbnail).unlink(missing_ok=True)
                except OSError:
                    pass
        return {"removed": len(rows)}
    finally:
        connection.close()


def update_tags(asset_id: int, tags: str) -> dict:
    connection = _connect()
    try:
        connection.execute(
            "UPDATE assets SET tags = ?, updated_at = ? WHERE id = ?",
            (str(tags or ""), _now(), int(asset_id)),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (int(asset_id),)).fetchone()
        return {"asset": _row_to_asset(row) if row else None}
    finally:
        connection.close()


def stats() -> dict:
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT kind, COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes FROM assets GROUP BY kind"
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes FROM assets"
        ).fetchone()
        return {
            "kinds": {row["kind"]: {"count": int(row["count"]), "bytes": int(row["bytes"])} for row in rows},
            "total": {"count": int(total["count"]), "bytes": int(total["bytes"])},
            "database": str(database_path()),
        }
    finally:
        connection.close()
