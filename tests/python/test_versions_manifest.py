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

# Public 0.0.31 stable metadata, including the signed CRLF line endings.
PREVIOUS_STABLE_DOCUMENT = base64.b64decode(
    "ew0KICAic2NoZW1hVmVyc2lvbiI6IDEsDQogICJhcHAiOiB7DQogICAgInZlcnNpb24iOiAiMC4wLjMxIg0KICB9LA0KICAicn"
    "VudGltZSI6IHsNCiAgICAiYXNzZXRJZCI6ICJydW50aW1lLWN1ZGEtMjAyNi4xMS40IiwNCiAgICAiYmFja2VuZCI6ICJjdWRh"
    "IiwNCiAgICAic2hhMjU2IjogImNjYmViNjdkMDExMGRjMWI2YWJjMTkwNmQwNmU3NjM2Yzk5NDVlMzMzNjcyODc0YjQzNzM4Zj"
    "UzYjI5ZDAyMWYiLA0KICAgICJzaXplIjogMzYxODIwMDcxNiwNCiAgICAidXJscyI6IFsNCiAgICAgICJodHRwczovL2hmLW1p"
    "cnJvci5jb20vZGF0YXNldHMvVExCdGlhbmh1L01lZGlhRm9yZ2UtRGlzdC9yZXNvbHZlL21haW4vcnVudGltZS9ydW50aW1l"
    "LWN1ZGEtMjAyNi4xMS40LnppcCIsDQogICAgICAiaHR0cHM6Ly93d3cubW9kZWxzY29wZS5jbi9kYXRhc2V0cy9UTEJ0aWFu"
    "aHUvTWVkaWFGb3JnZS1EaXN0L3Jlc29sdmUvbWFzdGVyL3J1bnRpbWUvcnVudGltZS1jdWRhLTIwMjYuMTEuNC56aXAiDQog"
    "ICAgXQ0KICB9DQp9DQo="
)
PREVIOUS_STABLE_SIGNATURE = (
    "untrusted comment: signature from tauri secret key\n"
    "RUQ2YSexQlGRlycV4Cmm6MlN6cQS6IgTNrYMZ5agoTSZbplomXMmLL+/9faFq7/IxLwD4TGfoEaeSddfdiVfZVZc+d50BZNHrww=\n"
    "trusted comment: timestamp:1793891135\tfile:versions.json\n"
    "W6GrCBXPPGf5FkPMUNz1j7MziJYQyufLt+Fyu0AeRPwU3MDo75f39qxLNC1u02OBKsprrtyjJuPPf4L12wwvDw==\n"
)


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
        self.assertEqual(doc["schemaVersion"], 3)
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
        self.assertEqual(doc["runtime"]["python"], "3.12")
        self.assertEqual(doc["runtime"]["platform"], entry["platform"])
        self.assertEqual(doc["runtime"]["arch"], entry["arch"])

    def test_runtime_version_and_schema_must_match_before_publishing(self) -> None:
        module = load_script()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest").mkdir()
            (root / "python").mkdir()
            (root / "python/runtime-manifest.json").write_text('{"python":"3.12"}', encoding="utf-8")
            valid = {"assetId": "runtime-test", "backend": "cuda", "python": "3.12",
                     "platform": "windows", "arch": "x86_64"}
            invalid = ((2, valid), (3, {**valid, "python": "3.11"}),
                       (3, {**valid, "platform": None}), (3, {**valid, "arch": "arm64"}))
            for schema, entry in invalid:
                (root / "manifest/runtime.json").write_text(json.dumps({
                    "schemaVersion": schema, "fallbackAssetId": "runtime-test", "runtimes": [entry]}), encoding="utf-8")
                with self.subTest(schema=schema, entry=entry), mock.patch.object(module, "ROOT", root):
                    with self.assertRaises(ValueError):
                        module.load_runtime()

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

    def test_retired_key_is_rejected_by_the_current_signature_verifier(self):
        with self.assertRaisesRegex(ValueError, "trusted versions key"):
            self.module.verify_signature(PREVIOUS_STABLE_DOCUMENT, PREVIOUS_STABLE_SIGNATURE)

    def test_existing_stable_notification_is_verified_and_never_downgraded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.module.verify_previous(root, "0.0.32")
            document_path = root / "versions.json"
            signature_path = root / "versions.json.sig"
            document_path.write_bytes(PREVIOUS_STABLE_DOCUMENT)
            with self.assertRaises(FileNotFoundError):
                self.module.verify_previous(root, "0.0.32")
            signature_path.write_text(PREVIOUS_STABLE_SIGNATURE, encoding="utf-8")
            self.module.verify_previous(root, "0.0.32")
            self.module.verify_previous(root, "0.0.31+rebuild")
            with self.assertRaisesRegex(ValueError, "newer stable"):
                self.module.verify_previous(root, "0.0.30")
            signature_path.write_text(PREVIOUS_STABLE_SIGNATURE.replace("timestamp:1793891135", "timestamp:1"), encoding="utf-8")
            from nacl.exceptions import BadSignatureError
            with self.assertRaises(BadSignatureError):
                self.module.verify_previous(root, "0.0.32")
            signature_path.write_text(PREVIOUS_STABLE_SIGNATURE, encoding="utf-8")
            document_path.write_bytes(PREVIOUS_STABLE_DOCUMENT + b" ")
            with self.assertRaisesRegex(ValueError, "trusted versions key"):
                self.module.verify_previous(root, "0.0.32")

    def test_older_key_cannot_sign_another_stable_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "versions.json").write_bytes(self.document)
            (root / "versions.json.sig").write_text(self.signature(), encoding="utf-8")
            with mock.patch.object(self.module, "PREVIOUS_STABLE_PUBKEY", self.public):
                with self.assertRaisesRegex(ValueError, "trusted versions key"):
                    self.module.verify_previous(root, "1.2.4")

    def test_newer_stable_notification_uses_the_current_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trust_source = root / "src-tauri" / "src" / "version_check.rs"
            trust_source.parent.mkdir(parents=True)
            trust_source.write_text(f'const VERSIONS_PUBKEY: &str = "{self.public}";\n', encoding="utf-8")
            (root / "versions.json").write_bytes(self.document)
            (root / "versions.json.sig").write_text(self.signature(), encoding="utf-8")
            with mock.patch.object(self.module, "ROOT", root):
                self.module.verify_previous(root, "1.2.4")
                with self.assertRaisesRegex(ValueError, "newer stable"):
                    self.module.verify_previous(root, "1.2.2")

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
