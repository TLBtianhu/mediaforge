"""Acquire and verify only marketplace packages pinned by the application."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
from typing import BinaryIO
import unicodedata
import urllib.request
import zipfile

from shared.asset_references import ensure_plain_path


_CATALOG_PATH = Path(__file__).resolve().parents[1] / "plugin-marketplace.json"
_REPOSITORY = "pymss-project/pymss-plugins"
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CHUNK_BYTES = 1024 * 1024
_RECEIPT = ".pymss-install.json"
_DEPENDENCIES = ".studio-deps"
_RESERVED = {_RECEIPT, _DEPENDENCIES, ".git", "__pycache__"}
_DEVICE_NAMES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in "123456789¹²³"
}


@dataclass(frozen=True)
class Package:
    name: str
    repository: str
    commit: str
    archive_sha256: str
    archive_size_bytes: int
    subpath: str
    version: str
    tree_sha256: str
    description: str
    tags: tuple[str, ...]

    @property
    def archive_url(self) -> str:
        return f"https://codeload.github.com/{self.repository}/zip/{self.commit}"

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.repository}"


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate marketplace catalog key: {name}")
        result[name] = value
    return result


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def catalog() -> dict[str, Package]:
    """Load the bundled catalog. Caller payload and process environment supply no pins."""
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
    if (not isinstance(data, dict)
            or set(data) != {"schemaVersion", "repository", "commit", "archive", "plugins"}
            or type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1
            or data["repository"] != _REPOSITORY or not _matches(_COMMIT, data["commit"])
            or not isinstance(data["plugins"], list)):
        raise ValueError("Unsupported or invalid bundled plugin marketplace catalog")
    archive = data["archive"]
    if (not isinstance(archive, dict) or set(archive) != {"sha256", "sizeBytes"}
            or not _matches(_SHA256, archive["sha256"])
            or type(archive["sizeBytes"]) is not int or archive["sizeBytes"] <= 0):
        raise ValueError("Invalid bundled marketplace archive pin")
    packages = {}
    for row in data["plugins"]:
        if (not isinstance(row, dict)
                or set(row) != {"name", "subpath", "version", "treeSha256", "description", "tags"}
                or not _matches(_NAME, row["name"]) or row["name"] in packages
                or row["subpath"] != f"plugins/{row['name']}"
                or not isinstance(row["version"], str) or not row["version"].strip()
                or not _matches(_SHA256, row["treeSha256"])
                or not isinstance(row["description"], str)
                or not isinstance(row["tags"], list)
                or not all(isinstance(tag, str) for tag in row["tags"])):
            raise ValueError("Invalid or duplicate bundled marketplace plugin entry")
        packages[row["name"]] = Package(
            name=row["name"], repository=data["repository"], commit=data["commit"],
            archive_sha256=archive["sha256"], archive_size_bytes=archive["sizeBytes"],
            subpath=row["subpath"], version=row["version"], tree_sha256=row["treeSha256"],
            description=row["description"], tags=tuple(row["tags"]),
        )
    return packages


def _portable_component(name: str) -> None:
    if (not name or name in {".", ".."} or name.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
            or name.partition(".")[0].rstrip(" ").casefold() in _DEVICE_NAMES):
        raise ValueError(f"Unsafe marketplace package path component: {name!r}")


def _package_component(name: str) -> None:
    _portable_component(name)
    lower = name.casefold()
    if lower in _RESERVED or lower.endswith((".pyc", ".pyo")):
        raise ValueError(f"Reserved marketplace package path: {name!r}")


def _portable_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _plain_metadata(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
            or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1):
        raise ValueError(f"Marketplace package contains a link or special entry: {path}")
    return metadata


def _file_stamp(metadata: os.stat_result) -> tuple[int, ...]:
    # Windows 3.12 reports different legacy st_ctime values for lstat and
    # fstat. Birth time identifies the opened file consistently there.
    identity_time = metadata.st_birthtime_ns if os.name == "nt" else metadata.st_ctime_ns
    # Path-based Windows stat infers executable bits from the extension;
    # descriptor-based stat does not have the filename.
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode), metadata.st_nlink,
            metadata.st_size, metadata.st_mtime_ns, identity_time)


def _file_digest(path: Path, metadata: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        expected = _file_stamp(metadata)
        if _file_stamp(os.fstat(descriptor)) != expected:
            raise ValueError(f"Marketplace package file changed during verification: {path}")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if _file_stamp(os.fstat(descriptor)) != expected or _file_stamp(_plain_metadata(path)) != expected:
            raise ValueError(f"Marketplace package file changed during verification: {path}")
    return digest


def source_tree_digest(directory: Path, *, installed: bool = False) -> str:
    """SHA-256 over canonical JSON mapping relative paths to SHA-256 file digests."""
    files: dict[str, str] = {}
    visited: dict[Path, tuple[int, ...]] = {}
    pending = [directory]
    while pending:
        parent = pending.pop()
        ensure_plain_path(parent)
        metadata = _plain_metadata(parent)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Marketplace package directory changed during verification: {parent}")
        visited[parent] = _file_stamp(metadata)
        names = set()
        for path in parent.iterdir():
            key = _portable_key(path.name)
            if key in names:
                raise ValueError(f"Duplicate portable marketplace path: {path}")
            names.add(key)
            metadata = _plain_metadata(path)
            is_dir = stat.S_ISDIR(metadata.st_mode)
            if installed and parent == directory and path.name in {_RECEIPT, _DEPENDENCIES}:
                if is_dir != (path.name == _DEPENDENCIES):
                    raise ValueError(f"Invalid marketplace installation metadata: {path}")
                continue
            _package_component(path.name)
            if is_dir:
                pending.append(path)
            else:
                files[path.relative_to(directory).as_posix()] = _file_digest(path, metadata)
    for parent, stamp in visited.items():
        if _file_stamp(_plain_metadata(parent)) != stamp:
            raise ValueError(f"Marketplace package directory changed during verification: {parent}")
    encoded = json.dumps(files, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dependency_tree_digest(directory: Path) -> str:
    return source_tree_digest(directory)


def verify_source(package: Package, directory: Path, *, installed: bool = False) -> None:
    if source_tree_digest(directory, installed=installed) != package.tree_sha256:
        raise ValueError(f"Marketplace package tree SHA-256 mismatch: {package.name}")
    with (directory / "pyproject.toml").open("rb") as manifest:
        project = tomllib.load(manifest).get("project")
    if not isinstance(project, dict) or project.get("version") != package.version:
        raise ValueError(f"Marketplace package version does not match the catalog: {package.name}")


class _NoArchiveRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Marketplace archive redirects are not permitted")


def _open_archive(package: Package):
    request = urllib.request.Request(package.archive_url, headers={
        "Accept": "application/zip", "User-Agent": "MediaForge",
    })
    return urllib.request.build_opener(_NoArchiveRedirect()).open(request, timeout=30)


def _download_archive(package: Package, target: BinaryIO) -> None:
    digest = hashlib.sha256()
    length = 0
    with _open_archive(package) as response:
        if response.geturl() != package.archive_url:
            raise ValueError("Unauthorized marketplace archive origin")
        while chunk := response.read(_CHUNK_BYTES):
            length += len(chunk)
            if length > package.archive_size_bytes:
                raise ValueError(f"Marketplace archive size mismatch: {package.name}")
            digest.update(chunk)
            target.write(chunk)
    if length != package.archive_size_bytes:
        raise ValueError(f"Marketplace archive size mismatch: {package.name}")
    if digest.hexdigest() != package.archive_sha256:
        raise ValueError(f"Marketplace archive SHA-256 mismatch: {package.name}")
    target.seek(0)


def _zip_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    raw_name = info.orig_filename
    is_dir = info.is_dir()
    parts = tuple(raw_name.removesuffix("/").split("/"))
    for part in parts:
        _portable_component(part)
    kind = stat.S_IFMT(info.external_attr >> 16)
    expected_kind = stat.S_IFDIR if is_dir else stat.S_IFREG
    if (raw_name != info.filename or kind not in {0, expected_kind}
            or info.external_attr & 0x400 or info.flag_bits & 1 or is_dir and info.file_size != 0):
        raise ValueError(f"Marketplace ZIP contains a link or invalid entry: {raw_name!r}")
    return parts


def _zip_members(archive: zipfile.ZipFile, package: Package) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    archive_root = f"{package.repository.rsplit('/', 1)[-1]}-{package.commit}"
    prefix = (archive_root, *package.subpath.split("/"))
    aliases: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    explicit = set()
    members = []
    for info in archive.infolist():
        parts = _zip_parts(info)
        if parts[0] != archive_root:
            raise ValueError(f"Marketplace ZIP entry is outside its pinned archive root: {info.orig_filename!r}")
        if parts in explicit:
            raise ValueError(f"Duplicate marketplace ZIP entry: {info.orig_filename!r}")
        explicit.add(parts)
        key = tuple(map(_portable_key, parts))
        for depth in range(1, len(parts) + 1):
            kind = depth < len(parts) or info.is_dir()
            node = (parts[:depth], kind)
            if aliases.setdefault(key[:depth], node) != node:
                raise ValueError(f"Colliding marketplace ZIP paths: {info.orig_filename!r}")
        if parts[:len(prefix)] == prefix and len(parts) > len(prefix):
            relative = parts[len(prefix):]
            for part in relative:
                _package_component(part)
            members.append((info, relative))
    if not any(not info.is_dir() for info, _ in members):
        raise ValueError(f"Marketplace archive does not contain its pinned plugin: {package.name}")
    return members


def _extract_plugin(source: BinaryIO, package: Package, directory: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        members = _zip_members(archive, package)
        directory.mkdir()
        for info, relative in members:
            path = directory.joinpath(*relative)
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as data, path.open("xb") as target:
                while chunk := data.read(_CHUNK_BYTES):
                    target.write(chunk)


def stage_package(package: Package, destination: Path) -> None:
    """Expose only the selected package after validating its archive and tree."""
    if (package.repository != _REPOSITORY or not _matches(_COMMIT, package.commit)
            or not _matches(_NAME, package.name) or package.subpath != f"plugins/{package.name}"):
        raise ValueError("Unauthorized marketplace package origin")
    ensure_plain_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="marketplace-") as temporary:
        staging = Path(temporary) / "verified"
        with tempfile.TemporaryFile(dir=temporary) as archive:
            _download_archive(package, archive)
            _extract_plugin(archive, package, staging)
        verify_source(package, staging)
        staging.rename(destination)
