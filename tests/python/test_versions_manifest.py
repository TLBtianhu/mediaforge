"""versions.json 的形状与"运行时描述符来自唯一真源"的契约。"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_script():
    spec = importlib.util.spec_from_file_location("make_versions", ROOT / "scripts" / "make-versions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_versions"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VersionsManifestTests(unittest.TestCase):
    def test_document_carries_the_two_live_axes(self) -> None:
        doc = load_script().build()
        self.assertEqual(doc["schemaVersion"], 1)
        self.assertIn("version", doc["app"])
        self.assertIn("assetId", doc["runtime"])
        # catalog 轴已删除：catalog 随应用发布，没有独立更新通道
        self.assertNotIn("catalog", doc)

    def test_runtime_descriptor_matches_the_manifest(self) -> None:
        doc = load_script().build()
        manifest = json.loads((ROOT / "manifest" / "runtime.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["runtimes"] if item["assetId"] == doc["runtime"]["assetId"])
        self.assertEqual(doc["runtime"]["sha256"], entry["sha256"])
        self.assertEqual(doc["runtime"]["size"], entry["size"])
        self.assertEqual(doc["runtime"]["urls"], entry["urls"])

    def test_runtime_descriptor_is_complete_enough_to_download(self) -> None:
        runtime = load_script().build()["runtime"]
        self.assertEqual(len(runtime["sha256"]), 64)
        self.assertGreater(runtime["size"], 0)
        self.assertTrue(all(url.startswith("https://") for url in runtime["urls"]))
    def test_tauri_signature_envelope_becomes_minisign_text(self) -> None:
        signature = (
            "untrusted comment: signature from tauri secret key\n"
            "c2lnbmF0dXJl\n"
            "trusted comment: timestamp:1\tfile:versions.json\n"
            "Z2xvYmFsLXNpZ25hdHVyZQ==\n"
        )
        envelope = base64.b64encode(signature.encode("utf-8")).decode("ascii")
        self.assertEqual(load_script().decode_tauri_signature(envelope), signature)

    def test_invalid_signature_envelope_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            load_script().decode_tauri_signature("not-base64")

    def test_explicit_release_version_overrides_the_checkout_without_changing_runtime(self) -> None:
        module = load_script()
        document = module.build("9.8.7")
        self.assertEqual(document["app"]["version"], "9.8.7")
        self.assertEqual(document["runtime"], module.load_runtime())
        with self.assertRaises(ValueError):
            module.build("../invalid")


@unittest.skipUnless(importlib.util.find_spec("nacl"), "release signer verification requires PyNaCl")
class VersionSignatureTests(unittest.TestCase):
    def setUp(self):
        from nacl.signing import SigningKey

        self.module = load_script()
        self.key = SigningKey.generate()  # Disposable fixture key; never printed or persisted.
        self.key_id = b"fixture1"
        self.public = base64.b64encode(b"untrusted comment: fixture public key\n" + base64.b64encode(b"Ed" + self.key_id + bytes(self.key.verify_key)) + b"\n").decode("ascii")
        self.document = b'{"schemaVersion":1,"app":{"version":"1.2.3"}}\n'

    def signature(self, document=None):
        document = self.document if document is None else document
        signed = self.key.sign(hashlib.blake2b(document, digest_size=64).digest()).signature
        trusted = b"timestamp:1\tfile:versions.json"
        global_signature = self.key.sign(signed + trusted).signature
        return "\n".join(["untrusted comment: fixture", base64.b64encode(b"ED" + self.key_id + signed).decode("ascii"), "trusted comment: " + trusted.decode("ascii"), base64.b64encode(global_signature).decode("ascii")]) + "\n"

    def test_verifies_document_and_trusted_comment_and_rejects_other_keys(self):
        signature = self.signature()
        self.module.verify_signature(self.document, signature, self.public)
        for document, signed, key in [
            (self.document + b" ", signature, self.public),
            (self.document, signature.replace("timestamp:1", "timestamp:2"), self.public),
            (self.document, signature, base64.b64encode(b"untrusted comment: key\n" + base64.b64encode(b"Ed" + b"other-id" + bytes(self.key.verify_key)) + b"\n").decode("ascii")),
        ]:
            with self.subTest(document=document, wrong_key=key != self.public), self.assertRaises(Exception):
                self.module.verify_signature(document, signed, key)

    def public_fixture(self):
        # Reuse only the existing public release bytes, not any signing material.
        import re

        source = (ROOT / "src-tauri/src/version_check.rs").read_text(encoding="utf-8")
        fixture = source[source.index("fn signed_fixture()") : source.index("struct TestServer")]
        encoded = re.search(r'let encoded = concat!\(\s*"([^"\n]+)"', fixture).group(1)
        signature_block = fixture[fixture.index("signature: concat!(") : fixture.index(").into()")]
        signature = "".join(json.loads('"' + value + '"') for value in re.findall(r'"((?:[^"\\]|\\.)*)"', signature_block))
        return base64.b64decode(encoded), signature

    def test_public_release_fixture_matches_the_app_trust_root(self):
        document, signature = self.public_fixture()
        self.module.verify_signature(document, signature)
        with self.assertRaises(Exception):
            self.module.verify_signature(document, self.signature())

    def test_existing_stable_notification_is_verified_and_never_downgraded(self):
        document, signature = self.public_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.module.verify_previous(root, "0.0.32")
            (root / "versions.json").write_bytes(document)
            (root / "versions.json.sig").write_text(signature, encoding="utf-8")
            self.module.verify_previous(root, "0.0.32")
            self.module.verify_previous(root, "0.0.31+rebuild")
            with self.assertRaisesRegex(ValueError, "newer stable"):
                self.module.verify_previous(root, "0.0.30")
            (root / "versions.json").write_bytes(document + b" ")
            with self.assertRaises(Exception):
                self.module.verify_previous(root, "0.0.32")

    def test_signer_uses_only_environment_credentials_and_suppresses_cli_output(self):
        with tempfile.TemporaryDirectory(prefix="mediaforge-version-signer-") as temporary:
            root = Path(temporary)
            target = root / "versions.json"
            target.write_bytes(self.document)
            cli = root / "tauri.js"
            cli.touch()
            fixture_secret, fixture_password = "fixture-only-private-key", "fixture-only-password"

            def run(command, **kwargs):
                self.assertEqual(command, ["fixture-node", str(cli), "signer", "sign", str(target)])
                self.assertEqual(kwargs["env"]["TAURI_SIGNING_PRIVATE_KEY"], fixture_secret)
                self.assertEqual(kwargs["env"]["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"], fixture_password)
                self.assertNotIn("OTHER_API_TOKEN", kwargs["env"])
                self.assertEqual(kwargs["stdin"], self.module.subprocess.DEVNULL)
                self.assertTrue(kwargs["capture_output"])
                return self.module.subprocess.CompletedProcess(command, 1, stdout=fixture_secret, stderr=fixture_password)

            output = io.StringIO()
            with mock.patch.dict(os.environ, {"VERSIONS_SIGNING_PRIVATE_KEY": fixture_secret, "VERSIONS_SIGNING_PRIVATE_KEY_PASSWORD": fixture_password, "OTHER_API_TOKEN": "fixture-only-token"}, clear=True), mock.patch.object(self.module.shutil, "which", return_value="fixture-node"), mock.patch.object(self.module.subprocess, "run", side_effect=run), redirect_stdout(output), redirect_stderr(output):
                with self.assertRaisesRegex(SystemExit, "退出码 1"):
                    self.module.sign(target, cli)
            self.assertEqual(output.getvalue(), "")
            self.assertFalse(target.with_suffix(".json.sig").exists())

    def test_missing_dedicated_key_never_falls_back_to_the_updater_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            cli = Path(temporary) / "tauri.js"
            cli.touch()
            with mock.patch.dict(os.environ, {"TAURI_SIGNING_PRIVATE_KEY": "fixture-only-updater-key"}, clear=True), mock.patch.object(self.module.shutil, "which", return_value="fixture-node"), mock.patch.object(self.module.subprocess, "run") as run:
                with self.assertRaisesRegex(SystemExit, "VERSIONS_SIGNING_PRIVATE_KEY"):
                    self.module.sign(Path(temporary) / "versions.json", cli)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
