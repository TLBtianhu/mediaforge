"""Release staging uses tiny isolated fixtures, never real runtimes or installers."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


class ReleasePackagingTests(unittest.TestCase):
    def test_windows_release_variants_share_the_bootstrap_stage_and_complete_updates(self) -> None:
        workflow = (ROOT / ".github/workflows/release-windows.yml").read_text(encoding="utf-8")
        stages = re.findall(r"(?ms)^      - name: Stage[^\n]*\n(.*?)(?=^      - |\Z)", workflow)
        self.assertEqual(len(stages), 4)
        for stage in stages:
            self.assertIn("./scripts/package-release.ps1", stage)
            self.assertIn("-SkipBuild -StageOnly", stage)
            self.assertNotIn("Copy-Item", stage)
            self.assertIn("isolated_environment(data, bootstrap)", stage)
            self.assertIn("TemporaryDirectory", stage)
            verification = stage.split("$verify = @'\n", 1)[1].split("\n          '@", 1)[0]
            compile(textwrap.dedent(verification), "staged-bootstrap-verification", "exec")
        self.assertEqual(workflow.count("prepare-python-runtime.ps1 -Variant default -Minimal"), 4)
        self.assertNotIn("-InitialBackend", workflow)
        self.assertNotIn("Pymss Studio", workflow)
        self.assertNotIn("pymss-project/pymss-studio", workflow)
        self.assertIn('$updateArchive "MediaForge.exe" "python" "bin" "integrations" "manifest"', workflow)

    def test_installer_uses_bounded_compression_and_candidate_icon_override(self) -> None:
        installer = (ROOT / "installer/mediaforge.iss").read_text(encoding="utf-8")
        dictionary = int(re.search(r"^LZMADictionarySize=(\d+)$", installer, re.M)[1])
        self.assertLessEqual(dictionary, 65536)  # Inno's unit is KiB.
        self.assertIn("LZMANumBlockThreads=2", installer)
        self.assertIn('GetEnv("MEDIAFORGE_INSTALLER_ICON")', installer)
        self.assertIn("SetupIconFile={#InstallerIcon}", installer)
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertNotIn("BuildRuntimeAsset", script)
        self.assertNotIn("$runtimeOut", script)

    def test_isolated_packaging_opt_outs_are_explicit(self) -> None:
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$SkipSign", script)
        self.assertIn("[switch]$SkipDownloadCopy", script)
        signing = script.split("if ($SkipSign) {", 1)[1].split('Write-Host "[5/6]', 1)[0]
        skipped, enabled = signing.split("} else {", 1)
        self.assertNotIn("GetEnvironmentVariable", skipped)
        self.assertIn("TAURI_SIGNING_PRIVATE_KEY_PATH", enabled)
        self.assertIn("Invoke-ReleaseArchiveSigning", enabled)
        self.assertNotIn("-p $", script)
        self.assertNotIn("Get-SigningPassword", script)

    def test_developer_only_dsh_scripts_are_removed_before_staging(self) -> None:
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertIn("function Remove-DevOnlyIntegrationScripts", script)
        self.assertIn("Remove-DevOnlyIntegrationScripts $Stage", script)
        self.assertIn("Remove-DevOnlyIntegrationScripts $Destination", script)
        self.assertIn("developer-only DSH scripts", script)

    def test_upgrade_note_reaches_the_staged_readme(self) -> None:
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertIn("Upgrading from an older build", script)
        self.assertIn("Older versions injected MCP rows", script)
        self.assertIn("inside the range between the BEGIN and END", script)
        self.assertIn("delete the two markers last", script)
        self.assertIn("Do not touch other blocks", script)

    def test_local_installer_copy_stays_inside_release_local(self) -> None:
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertIn('$localOut = "$out\\local"', script)
        self.assertIn('New-Item -ItemType Directory -Force -Path $localOut', script)
        self.assertIn('Copy-Item $setup (Join-Path $localOut', script)
        self.assertNotIn('$env:USERPROFILE\\Downloads', script)

    def test_first_install_http_server_uses_only_an_explicit_or_system_python(self) -> None:
        script = (ROOT / "scripts/smoke-first-install.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$HttpPython = ''", script)
        self.assertIn("if ($HttpPython) { (Resolve-Path -LiteralPath $HttpPython).Path } else { 'python' }", script)
        self.assertNotIn("$env:USERPROFILE", script)
        self.assertNotIn("runtime-envs\\cuda", script)


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows packaging helpers")
class ReleaseStagingFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="mediaforge-staging-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # Load only function definitions from the real script. Top-level building,
        # credential reading, asset compression and installer commands never run.
        self.runner = self.root / "run-functions.ps1"
        self.runner.write_text("""
param([string]$PackagingScript, [string]$FixtureScript, [string]$FixtureRoot, [string]$ReleaseProcessScript)
$ErrorActionPreference = 'Stop'
. $ReleaseProcessScript
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($PackagingScript, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([ScriptBlock]::Create($statement.Extent.Text))
    }
}
. $FixtureScript
""", encoding="utf-8-sig")

    def run_fixture(self, body: str, *, ok: bool = True) -> subprocess.CompletedProcess:
        fixture = self.root / "fixture.ps1"
        fixture.write_text(body, encoding="utf-8-sig")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self.runner),
             "-PackagingScript", str(ROOT / "scripts/package-release.ps1"),
             "-ReleaseProcessScript", str(ROOT / "scripts/release-signing.ps1"),
             "-FixtureScript", str(fixture), "-FixtureRoot", str(self.root)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def write_files(self, directory: str, files: dict[str, bytes]) -> Path:
        target = self.root / directory
        for relative, data in files.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return target

    def test_bootstrap_is_preserved_without_ever_copying_environments_or_caches(self) -> None:
        retained = {
            "python.exe": b"python fixture", "python311.dll": b"DLL fixture",
            "DLLs/_ssl.pyd": b"SSL fixture", "Lib/site-packages/pip/__init__.py": b"pip fixture",
            "Lib/sourceless.pyc": b"required sourceless module", ".bootstrap-id": b"fixture-id",
            "Tools/scripts/helper.py": b"helper fixture", "LICENSE.txt": b"license fixture",
        }
        excluded = {
            "runtime-envs/cuda/Lib/site-packages/torch/large.dll": b"must never be copied",
            "runtime-envs/.incoming-new-runtime/archive.zip": b"must never be copied",
            "Lib/site-packages/pip/__pycache__/__init__.pyc": b"cache",
            ".cache/pip/cache.bin": b"cache", ".pytest_cache/state": b"cache",
        }
        self.write_files("source", {**retained, **excluded})
        self.run_fixture("""
Copy-ReleaseTree -Source (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'stage') -ExcludeRootDirectories @('runtime-envs')
""")
        copied = {p.relative_to(self.root / "stage").as_posix(): p.read_bytes()
                  for p in (self.root / "stage").rglob("*") if p.is_file()}
        self.assertEqual(copied, retained)
        self.assertFalse((self.root / "stage/runtime-envs").exists())
        # Source assets are neither copied nor removed as a cleanup side effect.
        for path, data in excluded.items():
            self.assertEqual((self.root / "source" / path).read_bytes(), data)

    def test_real_packaging_entry_point_keeps_all_outputs_inside_the_candidate(self) -> None:
        version = "0.0.32"
        files = {
            "scripts/package-release.ps1": (ROOT / "scripts/package-release.ps1").read_bytes(),
            "scripts/release-signing.ps1": (ROOT / "scripts/release-signing.ps1").read_bytes() +
                b"\nfunction Get-ReleaseExecutableInfo { param($Path) [PSCustomObject]@{ProductName='MediaForge'; ProductVersion='0.0.32'} }\nfunction Get-ReleasePythonVersion { param($PythonPath) '3.12' }\n" +
                b"$script:FixtureReleaseProcess = ${function:Invoke-ReleaseProcess}\nfunction Invoke-ReleaseProcess { param($FilePath, $Arguments, $WorkingDirectory, $Environment = @{}, $TimeoutMilliseconds = 120000)\nif ($FilePath -like '*fake-iscc.cmd') { [IO.File]::WriteAllText((Join-Path $env:MEDIAFORGE_INSTALLER_OUTPUT ('MediaForge-' + $env:MEDIAFORGE_VERSION + '-' + $env:MEDIAFORGE_PACKAGE_SUFFIX + '-setup.exe')), 'fixture'); return [PSCustomObject]@{ExitCode=0;Stdout='';Stderr=''} }; & $script:FixtureReleaseProcess @PSBoundParameters }\n",
            "scripts/stage-plugin-sdk.ps1": (ROOT / "scripts/stage-plugin-sdk.ps1").read_bytes(),
            "installer/mediaforge.iss": (ROOT / "installer/mediaforge.iss").read_bytes(),
            "package.json": json.dumps({"version": version}).encode(),
            "src-tauri/tauri.conf.json": json.dumps({"version": version}).encode(),
            "src-tauri/Cargo.toml": f'[package]\nversion = "{version}"\n'.encode(),
            "src-tauri/target/release/pymss-studio.exe": b"never executed application",
            "src-tauri/icons/icon.ico": b"never compiled icon",
            "src-tauri/resources/plugin-sdk/pymss-sdk.js": b"SDK JS",
            "src-tauri/resources/plugin-sdk/pymss-sdk.css": b"SDK CSS",
            "python/worker.py": b"never executed worker", "python/core/runtime_audio_probe.py": b"probe",
            "python/shared/safe_fs.py": b"safe fs", "python/runtime-manifest.json": b'{"python":"3.12"}',
            "manifest/protocol.json": b"{}", "manifest/runtime.json": b"{}",
            "manifest/execution-quota.json": (ROOT / "manifest/execution-quota.json").read_bytes(),
            "manifest/library-schema.json": (ROOT / "manifest/library-schema.json").read_bytes(),
            "python-runtime/python.exe": b"never executed bootstrap",
            "release/bootstrap-selected/python.exe": b"explicit selected bootstrap",
            "python-runtime/runtime-envs/cuda/retained.dll": b"separate runtime asset",
            "release/bin/ffmpeg.exe": b"tool", "release/bin/ffprobe.exe": b"tool",
            "release/bin/aria2c.exe": b"tool", "release/bin/vcrt/msvcp140.dll": b"vcrt",
            "integrations/dsh-mediaforge/cordis.patch.yml": b"bridge",
            "release/stage-0.0.31/keep.bin": b"previous stage",
            "release/local/previous-setup.exe": b"previous local installer",
            "release/runtime/runtime-cuda-2026.11.4.zip": b"existing runtime asset",
            "release/candidates/0.0.32/MediaForge-0.0.32-windows-x64-update.zip.sig": b"stale signature",
        }
        project = self.write_files("project", files)
        # PE/Python identity probes and the compiler boundary are mocked. The
        # actual hidden subprocess helper is covered separately with real tiny
        # children. The real entry point stages
        # and compresses tiny source fixtures; it never executes application,
        # bootstrap, worker, media tools or an installer.
        compiler = self.root / "fake-iscc.cmd"
        compiler.write_text('@echo off\necho fixture > "%MEDIAFORGE_INSTALLER_OUTPUT%\\MediaForge-%MEDIAFORGE_VERSION%-%MEDIAFORGE_PACKAGE_SUFFIX%-setup.exe"\nexit /b 0\n')
        env = {name: os.environ[name] for name in os.environ
               if not any(part in name.upper() for part in ("KEY", "PASSWORD", "TOKEN", "SECRET"))}
        env["MEDIAFORGE_ISCC"] = str(compiler)
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
             str(project / "scripts/package-release.ps1"), "-Version", version, "-SkipBuild", "-SkipSign",
             "-SkipDownloadCopy", "-OutputDir", "release/candidates/0.0.32", "-BootstrapDir", "release/bootstrap-selected"],
            cwd=project, env=env, capture_output=True, text=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        candidate = project / "release/candidates/0.0.32"
        for name in ("portable.zip", "update.zip", "cuda-setup.exe"):
            suffix = "windows-x64-" + name
            self.assertTrue((candidate / f"MediaForge-{version}-{suffix}").is_file(), suffix)
        self.assertFalse((candidate / "runtime").exists())
        self.assertFalse((candidate / "local").exists())
        self.assertFalse((candidate / "update-stage-0.0.32").exists())
        self.assertFalse((candidate / "MediaForge-0.0.32-windows-x64-update.zip.sig").exists())
        stage = candidate / "stage-0.0.32"
        self.assertFalse((stage / "mediaforge.portable").exists())
        self.assertFalse((stage / "python-runtime/runtime-envs").exists())
        self.assertEqual((stage / "python-runtime/python.exe").read_bytes(), b"explicit selected bootstrap")
        self.assertEqual((project / "python-runtime/python.exe").read_bytes(), b"never executed bootstrap")
        with zipfile.ZipFile(candidate / "MediaForge-0.0.32-windows-x64-portable.zip") as archive:
            self.assertIn("mediaforge.portable", archive.namelist())
        with zipfile.ZipFile(candidate / "MediaForge-0.0.32-windows-x64-update.zip") as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            self.assertIn("manifest/protocol.json", names)
            self.assertIn("manifest/runtime.json", names)
            self.assertIn("manifest/execution-quota.json", names)
            self.assertIn("python/plugin-sdk/pymss-sdk.js", names)
            self.assertFalse(any(name.startswith("python-runtime/") for name in names))
        # A later invocation with the wrong bootstrap must preserve this exact
        # already-produced candidate before staging or signing can change it.
        retained = (stage / "README.txt").read_bytes()
        update_before = (candidate / f"MediaForge-{version}-windows-x64-update.zip").read_bytes()
        with (project / "scripts/release-signing.ps1").open("a", encoding="utf-8") as stream:
            stream.write("\nfunction Get-ReleasePythonVersion { param($PythonPath) '3.11' }\n")
        rejected = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                   str(project / "scripts/package-release.ps1"), "-Version", version, "-SkipBuild", "-StageOnly", "-SkipSign",
                                   "-OutputDir", "release/candidates/0.0.32", "-BootstrapDir", "release/bootstrap-selected"], cwd=project, env=env, capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Python 3.12 release contract", rejected.stderr)
        self.assertEqual((stage / "README.txt").read_bytes(), retained)
        self.assertEqual((candidate / f"MediaForge-{version}-windows-x64-update.zip").read_bytes(), update_before)
        for relative in ("release/stage-0.0.31/keep.bin", "release/local/previous-setup.exe",
                         "release/runtime/runtime-cuda-2026.11.4.zip", "python-runtime/runtime-envs/cuda/retained.dll"):
            self.assertEqual((project / relative).read_bytes(), files[relative])

    def test_excluded_environment_junction_is_not_followed(self) -> None:
        self.write_files("source", {"python.exe": b"bootstrap"})
        self.write_files("separate-environments", {"cuda/asset.bin": b"separate asset"})
        self.run_fixture("""
New-Item -ItemType Junction -Path (Join-Path $FixtureRoot 'source/runtime-envs') -Target (Join-Path $FixtureRoot 'separate-environments') | Out-Null
Copy-ReleaseTree -Source (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'stage') -ExcludeRootDirectories @('runtime-envs')
""")
        self.assertFalse((self.root / "stage/runtime-envs").exists())
        self.assertEqual((self.root / "separate-environments/cuda/asset.bin").read_bytes(), b"separate asset")

    def test_real_staging_loop_filters_runtime_at_the_copy_boundary(self) -> None:
        self.write_files("source", {
            "python/shared/safe_fs.py": b"worker fixture",
            "integrations/dsh-mediaforge/cordis.patch.yml": b"bridge fixture",
            "manifest/protocol.json": b"{}", "manifest/runtime.json": b"{}",
            "manifest/execution-quota.json": (ROOT / "manifest/execution-quota.json").read_bytes(),
            "manifest/library-schema.json": (ROOT / "manifest/library-schema.json").read_bytes(),
            "python-runtime/python.exe": b"bootstrap",
            "python-runtime/runtime-envs/cuda/large.dll": b"excluded runtime",
            "python-runtime/Lib/__pycache__/cached.pyc": b"excluded cache",
        })
        self.run_fixture("""
$root = Join-Path $FixtureRoot 'source'
$stage = Join-Path $FixtureRoot 'stage'
Copy-ReleaseResources -Root $root -Stage $stage
Assert-ReleaseContract $stage
""")
        self.assertEqual((self.root / "stage/python-runtime/python.exe").read_bytes(), b"bootstrap")
        self.assertFalse((self.root / "stage/python-runtime/runtime-envs").exists())
        self.assertFalse((self.root / "stage/python-runtime/Lib/__pycache__").exists())
        self.assertTrue((self.root / "stage/python/shared/safe_fs.py").is_file())

    def test_candidate_paths_are_separate_and_versions_cannot_escape(self) -> None:
        result = self.run_fixture("""
$paths = Get-ReleasePaths -Root $FixtureRoot -Version '0.0.32' -OutputDir 'release/candidates/0.0.32' -StageName '' -PackageSuffix 'windows-x64-cuda'
$paths | ConvertTo-Json -Compress
""")
        paths = json.loads(result.stdout.strip())
        self.assertEqual(Path(paths["Output"]), self.root / "release/candidates/0.0.32")
        self.assertEqual(Path(paths["Stage"]), self.root / "release/candidates/0.0.32/stage-0.0.32")
        for value in ("../0.0.31", "0.0.32/../../other", "0.0.32\\..\\other", "0.0.32\n"):
            result = self.run_fixture(
                f"Get-ReleasePaths -Root $FixtureRoot -Version '{value}' -OutputDir '' -StageName '' -PackageSuffix 'windows-x64-cuda'", ok=False)
            self.assertIn("Invalid release version", result.stderr)

    def test_cleanup_requires_the_exact_stage_child_and_preserves_other_versions(self) -> None:
        self.write_files("release", {"stage-0.0.31/keep.bin": b"previous release", "stage-0.0.32/stale.bin": b"stale"})
        self.run_fixture("""
$out = Join-Path $FixtureRoot 'release'
Remove-ReleaseOwnedPath -Path (Join-Path $out 'stage-0.0.32') -OutputRoot $out -ExpectedName 'stage-0.0.32'
""")
        self.assertFalse((self.root / "release/stage-0.0.32").exists())
        self.assertEqual((self.root / "release/stage-0.0.31/keep.bin").read_bytes(), b"previous release")
        result = self.run_fixture("""
$out = Join-Path $FixtureRoot 'release'
Remove-ReleaseOwnedPath -Path (Join-Path $out 'stage-0.0.31') -OutputRoot $out -ExpectedName 'stage-0.0.32'
""", ok=False)
        self.assertIn("exact output path", result.stderr)
        self.assertTrue((self.root / "release/stage-0.0.31/keep.bin").is_file())

    def test_output_and_cleanup_refuse_reparse_points_without_touching_targets(self) -> None:
        self.write_files("separate", {"keep.bin": b"external asset"})
        result = self.run_fixture("""
New-Item -ItemType Directory -Path (Join-Path $FixtureRoot 'release') | Out-Null
New-Item -ItemType Junction -Path (Join-Path $FixtureRoot 'release/candidates') -Target (Join-Path $FixtureRoot 'separate') | Out-Null
Get-ReleasePaths -Root $FixtureRoot -Version '0.0.32' -OutputDir 'release/candidates/0.0.32' -StageName '' -PackageSuffix 'windows-x64-cuda'
""", ok=False)
        self.assertIn("refuses reparse point", result.stderr)
        result = self.run_fixture("""
New-Item -ItemType Directory -Path (Join-Path $FixtureRoot 'release/stage-0.0.32') | Out-Null
New-Item -ItemType Junction -Path (Join-Path $FixtureRoot 'release/stage-0.0.32/linked') -Target (Join-Path $FixtureRoot 'separate') | Out-Null
Remove-ReleaseOwnedPath -Path (Join-Path $FixtureRoot 'release/stage-0.0.32') -OutputRoot (Join-Path $FixtureRoot 'release') -ExpectedName 'stage-0.0.32'
""", ok=False)
        self.assertIn("cleanup refuses reparse point", result.stderr)
        self.assertEqual((self.root / "separate/keep.bin").read_bytes(), b"external asset")

    def test_output_cannot_leave_release_or_use_an_unrelated_stage(self) -> None:
        for arguments in ("-OutputDir '../outside' -StageName ''", "-OutputDir 'release' -StageName 'stage-0.0.31'"):
            result = self.run_fixture("Get-ReleasePaths -Root $FixtureRoot -Version '0.0.32' -PackageSuffix 'windows-x64-cuda' " + arguments, ok=False)
            self.assertRegex(result.stderr, "OutputDir must be inside|Invalid stage directory")

    def test_full_stage_contract_requires_bootstrap_sources_sdk_tools_and_no_user_assets(self) -> None:
        names = ["MediaForge.exe", "manifest/protocol.json", "manifest/runtime.json", "manifest/execution-quota.json", "manifest/library-schema.json", "python/worker.py",
                 "python/core/runtime_audio_probe.py", "python/shared/safe_fs.py", "python/runtime-manifest.json",
                 "python/plugin-sdk/pymss-sdk.js", "python/plugin-sdk/pymss-sdk.css", "python-runtime/python.exe",
                 "bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/aria2c.exe", "bin/vcrt/msvcp140.dll",
                 "integrations/dsh-mediaforge/cordis.patch.yml"]
        self.write_files("stage", {name: b"fixture" for name in names})
        self.run_fixture("Assert-ReleaseStage (Join-Path $FixtureRoot 'stage')")
        self.write_files("stage", {"models/unwanted-weight.bin": b"must not ship"})
        result = self.run_fixture("Assert-ReleaseStage (Join-Path $FixtureRoot 'stage')", ok=False)
        self.assertIn("mutable user assets: models", result.stderr)

    def test_destination_junction_cannot_redirect_copy_to_external_assets(self) -> None:
        self.write_files("source", {"python.exe": b"bootstrap"})
        self.write_files("separate", {"keep.bin": b"external"})
        result = self.run_fixture("""
New-Item -ItemType Junction -Path (Join-Path $FixtureRoot 'stage') -Target (Join-Path $FixtureRoot 'separate') | Out-Null
Copy-ReleaseTree -Source (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'stage')
""", ok=False)
        self.assertIn("refuses reparse point", result.stderr)
        self.assertFalse((self.root / "separate/python.exe").exists())
        self.assertEqual((self.root / "separate/keep.bin").read_bytes(), b"external")

    def test_unexpected_included_junction_is_rejected(self) -> None:
        self.write_files("source", {"python.exe": b"bootstrap"})
        self.write_files("separate-dependency", {"asset.bin": b"keep separate"})
        result = self.run_fixture("""
New-Item -ItemType Junction -Path (Join-Path $FixtureRoot 'source/linked-dependency') -Target (Join-Path $FixtureRoot 'separate-dependency') | Out-Null
Copy-ReleaseTree -Source (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'stage')
""", ok=False)
        self.assertIn("refuses linked source", result.stderr)
        self.assertFalse((self.root / "stage/linked-dependency/asset.bin").exists())

    def test_update_zip_keeps_complete_contract_and_loads_it_outside_the_checkout(self) -> None:
        files = {
            "MediaForge.exe": b"application fixture", "python/worker.py": b"worker fixture",
            "python/shared/protocol_contract.py": (ROOT / "python/shared/protocol_contract.py").read_bytes(),
            "bin/ffmpeg.exe": b"tool fixture", "integrations/bridge/config.json": b"{}",
            "manifest/protocol.json": (ROOT / "manifest/protocol.json").read_bytes(),
            "manifest/runtime.json": (ROOT / "manifest/runtime.json").read_bytes(),
            "manifest/execution-quota.json": (ROOT / "manifest/execution-quota.json").read_bytes(),
            "manifest/library-schema.json": (ROOT / "manifest/library-schema.json").read_bytes(),
            "manifest/future-contract.json": b'{"future":true}',
            "python-runtime/runtime-envs/cuda/asset.bin": b"exclude the runtime",
            "data/settings.json": b"exclude user data", "models/weights.bin": b"exclude models",
            "mediaforge.portable": b"exclude portable marker",
        }
        self.write_files("source", files)
        self.run_fixture("""
Copy-ReleaseUpdate -Stage (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'update')
Compress-Folder -Source (Join-Path $FixtureRoot 'update') -Destination (Join-Path $FixtureRoot 'fixture-update.zip') -Label 'tiny fixture'
""")
        with zipfile.ZipFile(self.root / "fixture-update.zip") as archive:
            members = {name.replace("\\", "/"): archive.read(name)
                       for name in archive.namelist() if not name.endswith(("/", "\\"))}
        expected = {name: data for name, data in files.items()
                    if name.split("/", 1)[0] in {"MediaForge.exe", "python", "bin", "integrations", "manifest"}}
        self.assertEqual(members, expected)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
             "from shared.protocol_contract import load_protocol; "
             "print(load_protocol()['schemaVersion'])", str(self.root / "update/python")],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(result.stdout.strip()), json.loads(files["manifest/protocol.json"])["schemaVersion"])

    def test_update_refuses_a_stage_without_its_worker_protocol(self) -> None:
        self.write_files("source", {"manifest/runtime.json": b"{}"})
        result = self.run_fixture("""
Copy-ReleaseUpdate -Stage (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'update')
""", ok=False)
        self.assertIn("protocol.json", result.stderr)
        self.assertFalse((self.root / "update").exists())

    def test_missing_execution_quota_rejects_stage_and_update_before_copying(self) -> None:
        files = {"manifest/protocol.json": b"{}", "manifest/runtime.json": b"{}", "keep.bin": b"unchanged"}
        source = self.write_files("source", files)
        for statement in (
            "Assert-ReleaseStage (Join-Path $FixtureRoot 'source')",
            "Copy-ReleaseUpdate -Stage (Join-Path $FixtureRoot 'source') -Destination (Join-Path $FixtureRoot 'update')",
        ):
            with self.subTest(statement=statement):
                result = self.run_fixture(statement, ok=False)
                self.assertIn("execution-quota.json", result.stderr)
                self.assertFalse((self.root / "update").exists())
                self.assertEqual({path.relative_to(source).as_posix(): path.read_bytes()
                                  for path in source.rglob("*") if path.is_file()}, files)

    def test_missing_library_schema_rejects_update_before_copying(self) -> None:
        files = {"manifest/protocol.json": b"{}", "manifest/runtime.json": b"{}",
                 "manifest/execution-quota.json": b"{}", "keep.bin": b"unchanged"}
        source = self.write_files("source-library-schema", files)
        result = self.run_fixture("Copy-ReleaseUpdate -Stage (Join-Path $FixtureRoot 'source-library-schema') -Destination (Join-Path $FixtureRoot 'update-library-schema')", ok=False)
        self.assertIn("library-schema.json", result.stderr)
        self.assertFalse((self.root / "update-library-schema").exists())
        self.assertEqual({path.relative_to(source).as_posix(): path.read_bytes()
                          for path in source.rglob("*") if path.is_file()}, files)


if __name__ == "__main__":
    unittest.main()
