"""Test-only catalog pins and an in-memory archive transport."""

import hashlib
import io
import tomllib
import zipfile
from pathlib import Path
from unittest import TestCase, mock

from core import plugin_marketplace_trust as trust
from core import worker_plugins as plugins


class ArchiveResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


class MarketplaceFixture:
    def __init__(self, test: TestCase):
        self.packages: dict[str, trust.Package] = {}
        self.archives: dict[str, bytes] = {}
        catalog = mock.patch.object(plugins, "catalog", return_value=self.packages)
        catalog.start()
        test.addCleanup(catalog.stop)
        download = mock.patch.object(trust, "_open_archive", side_effect=self.open_archive)
        self.download = download.start()
        test.addCleanup(download.stop)

    def open_archive(self, package: trust.Package):
        return ArchiveResponse(self.archives[package.archive_url], package.archive_url)

    def pin(self, source: Path, *, extra_members: dict[str, str] | None = None) -> trust.Package:
        tree = trust.source_tree_digest(source)
        revision = tree[:40]
        subpath = f"plugins/{source.name}"
        prefix = f"pymss-plugins-{revision}/{subpath}"
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, f"{prefix}/{path.relative_to(source).as_posix()}")
            for path, data in (extra_members or {}).items():
                archive.writestr(f"{prefix}/{path}", data)
        document = tomllib.loads(source.joinpath("pyproject.toml").read_text(encoding="utf-8"))
        data = output.getvalue()
        package = trust.Package(
            name=source.name, repository="pymss-project/pymss-plugins", commit=revision,
            archive_sha256=hashlib.sha256(data).hexdigest(), archive_size_bytes=len(data),
            subpath=subpath, version=document["project"]["version"], tree_sha256=tree,
            description="A test", tags=("audio",),
        )
        self.packages[package.name] = package
        self.archives[package.archive_url] = data
        return package
