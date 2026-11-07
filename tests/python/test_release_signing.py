"""Signing guards use fake PE metadata and a credential-redacting mock signer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PE and PowerShell release guards")
class ReleaseSigningTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mf-signing-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # Filter by name before fetching values; no real credential is read or
        # inherited by PowerShell or the mock signer.
        self.env = {key: os.environ[key] for key in os.environ
                    if not any(part in key.upper() for part in ("KEY", "PASSWORD", "TOKEN", "SECRET"))
                    and not key.upper().startswith(("MEDIAFORGE_", "PYMSS_", "TAURI_", "VERSIONS_SIGNING_", "SIGNPATH_"))
                    and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
                     "MEDIAFORGE_DATA_ROOT", "MEDIAFORGE_DATA_DIR", "MEDIAFORGE_MODEL_DIR", "PYMSS_MODEL_DIR",
                     "MEDIAFORGE_RUNTIME_ENVS_DIR", "WEBVIEW2_USER_DATA_FOLDER"):
            path = self.root / name.lower()
            path.mkdir()
            self.env[name] = str(path)
        self.env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", HF_HUB_OFFLINE="1")
        self.runner = self.root / "run.ps1"
        self.runner.write_text("""
param([string]$FixtureRoot, [string]$Fixture, [string]$Repository)
$ErrorActionPreference = 'Stop'
. (Join-Path $Repository 'scripts/release-signing.ps1')
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Repository 'scripts/package-release.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Packaging script has a syntax error' }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) { . ([ScriptBlock]::Create($statement.Extent.Text)) }
}
try { . $Fixture } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
""", encoding="utf-8-sig")

    def run_fixture(self, body, *, ok=True):
        fixture = self.root / "fixture.ps1"
        fixture.write_text(body, encoding="utf-8-sig")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(self.runner), "-FixtureRoot", str(self.root), "-Fixture", str(fixture),
                                 "-Repository", str(ROOT)], capture_output=True, text=True, env=self.env, timeout=25)
        # Never include raw process diagnostics in assertion failure output.
        self.assertEqual(result.returncode == 0, ok, f"fixture exit code {result.returncode}")
        return result

    def mock_signer(self, code):
        archive = self.root / "fixture update.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("MediaForge.exe", b"fake PE; version reader is mocked")
        key = self.root / "fixture.key"
        key.write_bytes(b"not a signing key")
        credential = 'fixture-only-&-password-"-$'
        config = {"password": credential, "keyPath": str(key), "exitCode": code}
        (self.root / "mock-config.json").write_text(json.dumps(config), encoding="utf-8")
        cli = self.root / "mock-cli.py"
        cli.write_text("""
import json, os, sys
from pathlib import Path
root = Path(__file__).parent
config = json.loads((root / 'mock-config.json').read_text())
record = {'args': sys.argv[1:],
          'keyMatches': os.environ.get('TAURI_SIGNING_PRIVATE_KEY_PATH') == config['keyPath'],
          'passwordMatches': os.environ.get('TAURI_SIGNING_PRIVATE_KEY_PASSWORD') == config['password'],
          'otherSecretPresent': any(name in os.environ for name in ('GITHUB_TOKEN', 'TAURI_SIGNING_PRIVATE_KEY', 'TAURI_PRIVATE_KEY'))}
(root / 'mock-record.json').write_text(json.dumps(record))
print(config['password'], flush=True)
print(config['password'], file=sys.stderr, flush=True)
if not config['exitCode']:
    Path(sys.argv[-1] + '.sig').write_text('mock signature')
raise SystemExit(config['exitCode'])
""", encoding="utf-8")
        # Only synthetic values are injected after the inherited environment was cleared.
        self.env.update(GITHUB_TOKEN="fixture-token", TAURI_PRIVATE_KEY="fixture-old-key", TAURI_SIGNING_PRIVATE_KEY="fixture-inline-key")
        body = """
function Get-ReleaseExecutableInfo { param($Path) [PSCustomObject]@{ProductName='MediaForge'; ProductVersion='0.0.32'} }
$archive = Join-Path $FixtureRoot 'fixture update.zip'
Invoke-ReleaseArchiveSigning -Archive $archive -ExpectedVersion '0.0.32' -Root $Repository `
    -KeyPath (Join-Path $FixtureRoot 'fixture.key') -Password ((Get-Content (Join-Path $FixtureRoot 'mock-config.json') -Raw | ConvertFrom-Json).password) `
    -Node '%PYTHON%' -Cli (Join-Path $FixtureRoot 'mock-cli.py')
if ($env:GITHUB_TOKEN -cne 'fixture-token' -or $env:TAURI_SIGNING_PRIVATE_KEY -cne 'fixture-inline-key') { throw 'Parent environment was changed' }
""".replace("%PYTHON%", sys.executable.replace("'", "''"))
        return archive, credential, body

    def test_mock_signer_receives_only_child_environment_credentials_and_no_secret_arguments(self):
        archive, credential, body = self.mock_signer(0)
        result = self.run_fixture(body)
        self.assertNotIn(credential, result.stdout + result.stderr)
        record = json.loads((self.root / "mock-record.json").read_text())
        self.assertEqual(record["args"], ["signer", "sign", str(archive)])
        self.assertTrue(record["keyMatches"] and record["passwordMatches"])
        self.assertFalse(record["otherSecretPresent"])
        self.assertEqual(Path(str(archive) + ".signature.b64").read_text(), "mock signature")

    def test_signer_failure_discards_secret_diagnostics_and_does_not_reuse_old_signature(self):
        archive, credential, body = self.mock_signer(17)
        Path(str(archive) + ".sig").write_text("stale signature")
        Path(str(archive) + ".signature.b64").write_text("stale signature")
        result = self.run_fixture(body, ok=False)
        self.assertEqual(result.stderr.strip(), "Release signer failed (exit 17)")
        self.assertNotIn(credential, result.stdout + result.stderr)
        self.assertFalse(Path(str(archive) + ".sig").exists())
        self.assertFalse(Path(str(archive) + ".signature.b64").exists())

    def test_stale_source_is_rejected_before_any_existing_stage_changes(self):
        for name, version in (("MediaForge", "0.0.31"), ("Pymss Studio", "0.0.32")):
            result = self.run_fixture("""
function Get-ReleaseExecutableInfo { param($Path) [PSCustomObject]@{ProductName='%NAME%'; ProductVersion='%VERSION%'} }
$root = $FixtureRoot; $Version = '0.0.32'; $out = Join-Path $FixtureRoot 'release'; $stageName = 'stage-0.0.32'; $stage = Join-Path $out $stageName
[IO.Directory]::CreateDirectory($stage) | Out-Null
[IO.File]::WriteAllText((Join-Path $stage 'keep.txt'), 'existing candidate evidence')
$text = Get-Content (Join-Path $Repository 'scripts/package-release.ps1') -Raw
$staging = ($text -split 'Write-Host "\[2/6\] staging"', 2)[1]
$staging = ($staging -split 'Copy-ReleaseResources -Root', 2)[0]
. ([ScriptBlock]::Create($staging))
""".replace("%NAME%", name).replace("%VERSION%", version), ok=False)
            self.assertIn("identity/version mismatch", result.stderr)
            self.assertEqual((self.root / "release/stage-0.0.32/keep.txt").read_text(), "existing candidate evidence")

    def test_stage_and_archive_version_mismatch_reject_before_signer_or_signature_changes(self):
        archive, _, body = self.mock_signer(0)
        Path(str(archive) + ".sig").write_text("retained signature")
        body = body.replace("ProductVersion='0.0.32'", "ProductVersion='0.0.31'")
        result = self.run_fixture(body, ok=False)
        self.assertIn("identity/version mismatch", result.stderr)
        self.assertFalse((self.root / "mock-record.json").exists())
        self.assertEqual(Path(str(archive) + ".sig").read_text(), "retained signature")
        self.run_fixture("""
function Get-ReleaseExecutableInfo { param($Path) [PSCustomObject]@{ProductName='MediaForge'; ProductVersion='0.0.31'} }
Assert-ReleaseExecutable -Path (Join-Path $FixtureRoot 'stage/MediaForge.exe') -ExpectedVersion '0.0.32'
""", ok=False)

    def test_existing_mismatched_stage_is_rejected_before_deletion(self):
        result = self.run_fixture("""
function Get-ReleaseExecutableInfo {
    param($Path)
    $version = if ($Path -like '*src-tauri*') { '0.0.32' } else { '0.0.31' }
    [PSCustomObject]@{ProductName='MediaForge'; ProductVersion=$version}
}
$root = $FixtureRoot; $Version = '0.0.32'; $out = Join-Path $FixtureRoot 'release'; $stageName = 'stage-0.0.32'; $stage = Join-Path $out $stageName
[IO.Directory]::CreateDirectory($stage) | Out-Null
[IO.File]::WriteAllText((Join-Path $stage 'MediaForge.exe'), 'existing candidate evidence')
$text = Get-Content (Join-Path $Repository 'scripts/package-release.ps1') -Raw
$staging = ($text -split 'Write-Host "\[2/6\] staging"', 2)[1]
$staging = ($staging -split 'Copy-ReleaseResources -Root', 2)[0]
. ([ScriptBlock]::Create($staging))
""", ok=False)
        self.assertIn("identity/version mismatch", result.stderr)
        self.assertEqual((self.root / "release/stage-0.0.32/MediaForge.exe").read_text(), "existing candidate evidence")

    def test_duplicate_windows_case_alias_cannot_bypass_archive_version_check(self):
        archive, _, body = self.mock_signer(0)
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("mediaforge.exe", b"a conflicting executable")
        result = self.run_fixture(body, ok=False)
        self.assertIn("exactly one root MediaForge.exe", result.stderr)
        self.assertFalse((self.root / "mock-record.json").exists())

    def test_skip_sign_branch_never_resolves_credentials_or_invokes_signer(self):
        self.run_fixture("""
$SkipSign = $true
function Invoke-ReleaseArchiveSigning { throw 'SkipSign invoked a signer' }
$text = Get-Content (Join-Path $Repository 'scripts/package-release.ps1') -Raw
$signing = 'if ($SkipSign) {' + ($text -split 'if \(\$SkipSign\) \{', 2)[1]
$signing = ($signing -split 'Write-Host "\[5/6\]', 2)[0]
. ([ScriptBlock]::Create($signing))
if (Get-Variable keyPath -ErrorAction SilentlyContinue) { throw 'SkipSign resolved a credential' }
""")

    def test_existing_real_source_and_candidate_pe_have_expected_identity_without_running_them(self):
        source = ROOT / "src-tauri/target/release/pymss-studio.exe"
        stage = ROOT / "release/candidates/0.0.32/stage-0.0.32/MediaForge.exe"
        if not source.is_file() or not stage.is_file():
            self.skipTest("Existing 0.0.32 candidate PE is unavailable")
        self.run_fixture("""
Assert-ReleaseExecutable -Path (Join-Path $Repository 'src-tauri/target/release/pymss-studio.exe') -ExpectedVersion '0.0.32'
Assert-ReleaseExecutable -Path (Join-Path $Repository 'release/candidates/0.0.32/stage-0.0.32/MediaForge.exe') -ExpectedVersion '0.0.32'
""")

    def test_standalone_signer_has_no_legacy_key_registry_or_password_argument(self):
        source = (ROOT / "scripts/sign-update.ps1").read_text(encoding="utf-8-sig")
        for forbidden in ("HKCU:", "Get-ItemProperty", "Read-Host", ".mediaforge-keys", "-p $", "$env:USERPROFILE"):
            self.assertNotIn(forbidden, source)
        self.assertIn("Invoke-ReleaseArchiveSigning", source)
        self.assertIn("TAURI_SIGNING_PRIVATE_KEY_PATH", source)


if __name__ == "__main__":
    unittest.main()
