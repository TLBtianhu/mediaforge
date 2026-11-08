"""Real PowerShell entry points, fake compiler/installer/worker children, no Windows installation."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("installer_smoke_tests", ROOT / "scripts/installer_smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
try:
    import yaml
except ImportError:
    yaml = None

FAKE_PROGRAM = r'''
import json,os,re,shutil,sys
from pathlib import Path
state=Path(os.environ['INSTALLER_SMOKE_FIXTURE_STATE'])
trace=Path(os.environ['INSTALLER_SMOKE_FIXTURE_TRACE'])
mode=os.environ.get('INSTALLER_SMOKE_FIXTURE_MODE','')
exe=Path(sys.argv[0])
def record(kind):
    with trace.open('a',encoding='utf-8') as out:out.write(json.dumps({'kind':kind,'argv':sys.argv})+'\n')
if exe.name=='fake compiler.exe':
    record('compile')
    if mode=='compile-failure':print('fake compiler failed',file=sys.stderr);raise SystemExit(23)
    source=Path(sys.argv[-1]).read_text(encoding='utf-8-sig')
    assert '[Icons]\n\n' in source and '[Run]\n\n' in source
    assert 'CloseApplications=no' in source
    assert 'AppId={{34A687CC-EE1D-401D-8FE1-98E4061C32CB}' not in source
    config={'appId':re.search(r'^AppId=\{(\{[A-F0-9-]+\})',source,re.M).group(1),
            'appName':re.search(r'^#define MyAppName "([^"]+)"',source,re.M).group(1),
            'key':re.search(r'Root: HKCU; Subkey: "([^"]+)"',source).group(1),
            'stage':os.environ['MEDIAFORGE_PORTABLE_DIR'],'version':os.environ['MEDIAFORGE_VERSION']}
    output=Path(os.environ['MEDIAFORGE_INSTALLER_OUTPUT'])/'candidate-smoke.exe'
    output.write_text(Path(__file__).read_text(),encoding='utf-8')
    output.with_suffix('.fixture.json').write_text(json.dumps(config))
elif exe.name=='candidate-smoke.exe':
    record('install')
    config=json.loads(exe.with_suffix('.fixture.json').read_text())
    install=Path(next(arg[5:] for arg in sys.argv[1:] if arg.startswith('/DIR=')))
    assert install.name=='install' and install.parent.name.startswith('MediaForge-candidate-')
    shutil.copytree(config['stage'],install/'app')
    if (install/'app/integrations').exists():shutil.move(str(install/'app/integrations'),str(install/'integrations'))
    if mode=='flat-layout':(install/'app/MediaForge.exe').rename(install/'MediaForge.exe')
    uninstaller=install/'unins000.exe'
    uninstaller.write_text(Path(__file__).read_text(),encoding='utf-8')
    uninstaller.with_suffix('.fixture.json').write_text(json.dumps(config))
    registry=json.loads(state.read_text())
    registry[config['key']]={'InstallDir':str(install/'app')}
    registry['Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'+config['appId']+'_is1']={
        'DisplayName':config['appName']+' '+config['version'],'DisplayVersion':config['version'],'InstallLocation':str(install)}
    if mode=='production-drift':registry['Software\\MediaForge']={'InstallDir':'changed by fake installer'}
    state.write_text(json.dumps(registry))
elif exe.name=='unins000.exe':
    record('uninstall')
    config=json.loads(exe.with_suffix('.fixture.json').read_text());install=exe.parent
    shutil.rmtree(install/'app')
    if (install/'integrations').exists():shutil.rmtree(install/'integrations')
    (install/'MediaForge.exe').unlink(missing_ok=True)
    registry=json.loads(state.read_text())
    registry.pop(config['key'],None)
    registry.pop('Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'+config['appId']+'_is1',None)
    state.write_text(json.dumps(registry));exe.unlink()
elif exe.name=='python.exe':
    record('health')
    assert sys.argv[-1]=='health' and Path(sys.argv[-2]).name=='worker.py'
    selected={key:value for key,value in os.environ.items() if key.startswith(('MEDIAFORGE_','PYMSS_','TAURI_','HF_')) or key in ['WEBVIEW2_USER_DATA_FOLDER','HOME','APPDATA']}
    Path(os.environ['INSTALLER_SMOKE_FIXTURE_ENV']).write_text(json.dumps(selected))
    assert not any('SECRET' in key or 'TOKEN' in key or 'KEY' in key for key in os.environ)
    if mode=='health-failure':print('fake health failed',file=sys.stderr);raise SystemExit(7)
    if mode=='health-invalid':print(json.dumps({'type':'health','payload':{'ok':False}}));raise SystemExit(0)
    print(json.dumps({'type':'health','payload':{'ok':True}}))
else:
    raise AssertionError('an application or production installer was executed: '+str(exe))
'''

FIXTURE_DRIVER = r'''
import json,os,runpy,sys
from pathlib import Path
scope=runpy.run_path(REAL_HELPER)
scope['execute'].__globals__['ROOT']=Path(PROJECT)
candidate=scope['CANDIDATE']
candidate.ROOT=Path(PROJECT)
candidate.registry_values=lambda key:json.loads(Path(os.environ['INSTALLER_SMOKE_FIXTURE_STATE']).read_text()).get(key)
candidate.running_test_processes=lambda work:[]
original=candidate.run_hidden
def run_hidden(command,**kwargs):
    program=Path(command[0])
    assert program.name in {'fake compiler.exe','candidate-smoke.exe','python.exe','unins000.exe'},program
    return original([sys.executable,'-I','-B',*command],**kwargs)
candidate.run_hidden=run_hidden
raise SystemExit(scope['main'](sys.argv[1:]))
'''


class InstallerSmokeHelpTests(unittest.TestCase):
    def test_help_does_not_probe_stages_registry_or_start_any_process(self):
        with mock.patch.object(SMOKE, "execute") as execute, mock.patch.object(SMOKE.CANDIDATE, "run_hidden") as run:
            with self.assertRaises(SystemExit) as stopped:
                SMOKE.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        execute.assert_not_called()
        run.assert_not_called()


@unittest.skipUnless(yaml, "workflow assertions need PyYAML")
class InstallerSmokeWorkflowTests(unittest.TestCase):
    def test_all_four_installer_variants_share_the_isolated_smoke_action(self):
        workflow = yaml.load((ROOT / ".github/workflows/release-windows.yml").read_text(), Loader=yaml.BaseLoader)
        callers = [job for job in workflow["jobs"].values() if any(step.get("uses") == "./.github/actions/build-windows-installer" for step in job.get("steps", []))]
        self.assertEqual(len(callers), 4)
        self.assertNotIn("./scripts/smoke-installer.ps1 -Installer", (ROOT / ".github/workflows/release-windows.yml").read_text())
        action = yaml.load((ROOT / ".github/actions/build-windows-installer/action.yml").read_text(), Loader=yaml.BaseLoader)
        steps = action["runs"]["steps"]
        smoke = next(step for step in steps if step.get("name") == "Verify isolated headless installer identity")
        for argument in ("-Stage", "-Version", "-Python", "-Compiler", "-Installer", "-Report"):
            self.assertIn(argument, smoke["run"])
        upload = next(step for step in steps if step.get("name") == "Upload Windows installer artifact")
        self.assertEqual(upload["with"]["path"], "release/${{ inputs.output-file }}")
        self.assertLess(steps.index(smoke), steps.index(upload))
        evidence = next(step for step in steps if step.get("name") == "Upload isolated installer smoke evidence")
        self.assertEqual(evidence["if"], "always()")
        self.assertTrue(evidence["with"]["path"].endswith(".json"))

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "PowerShell syntax inspection")
    def test_composite_scripts_parse_and_each_native_start_is_hidden(self):
        action = yaml.load((ROOT / ".github/actions/build-windows-installer/action.yml").read_text(), Loader=yaml.BaseLoader)
        with tempfile.TemporaryDirectory(prefix="mf-installer-action-parser-") as raw:
            directory = Path(raw)
            scripts = directory / "scripts.json"
            scripts.write_text(json.dumps([step["run"] for step in action["runs"]["steps"] if "run" in step]), encoding="utf-8")
            inspector = directory / "inspect.ps1"
            inspector.write_text(r'''
param($Scripts)
$ErrorActionPreference='Stop'
foreach($source in (Get-Content -LiteralPath $Scripts -Raw|ConvertFrom-Json)) {
    $source=[regex]::Replace($source,'\$\{\{.*?\}\}','fixture')
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw ($errors|ForEach-Object Message|Out-String)}
    $starts=$ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Start-Process'},$true)
    foreach($command in $starts) {
        $values=@($command.CommandElements|ForEach-Object {$_.Extent.Text})
        $position=[Array]::IndexOf($values,'-WindowStyle')
        if($position -lt 0 -or $values[$position+1] -ne 'Hidden'){throw 'A native child is not hidden'}
    }
}
''', encoding="utf-8-sig")
            result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(inspector), "-Scripts", str(scripts)],
                                    capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell installer fixtures")
class InstallerSmokeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mf-installer-smoke-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project with spaces"
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/smoke-installer.ps1", scripts / "smoke-installer.ps1")
        driver = FIXTURE_DRIVER.replace("REAL_HELPER", repr(str(ROOT / "scripts/installer_smoke.py"))).replace("PROJECT", repr(str(self.project)))
        (scripts / "installer_smoke.py").write_text(driver, encoding="utf-8")
        (self.project / "installer").mkdir()
        shutil.copyfile(ROOT / "installer/mediaforge.iss", self.project / "installer/mediaforge.iss")
        self.stage = self.project / "stage with spaces"
        for name in SMOKE.REQUIRED:
            path = self.stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"stage fixture")
        (self.stage / "python/runtime-manifest.json").write_text('{"python":"3.12"}')
        (self.stage / "python-runtime/python.exe").write_text(FAKE_PROGRAM, encoding="utf-8")
        integration = self.stage / "integrations/dsh-mediaforge/mediaforge-mcp.cmd"
        integration.parent.mkdir(parents=True)
        integration.write_bytes(b"integration fixture")
        for index in range(6):
            path = self.stage / f"bin/vcrt/fixture{index}.dll"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"vc fixture")
        self.compiler = self.root / "fake compiler.exe"
        self.compiler.write_text(FAKE_PROGRAM, encoding="utf-8")
        self.production_installer = self.root / "production setup.exe"
        self.production_installer.write_bytes(b"production artifact must never execute")
        self.report = self.project / ".tmp/report.json"
        self.state, self.trace, self.environment = (self.root / name for name in ("registry.json", "trace.jsonl", "worker-env.json"))
        self.production = {r"Software\MediaForge": {"InstallDir": "untouched production pointer"},
                           SMOKE.CANDIDATE.UNINSTALL_PREFIX + SMOKE.CANDIDATE.PRODUCTION_APP_ID + "_is1": {"DisplayName": "untouched production uninstall"}}
        self.state.write_text(json.dumps(self.production))
        self.env = dict(os.environ, INSTALLER_SMOKE_FIXTURE_STATE=str(self.state), INSTALLER_SMOKE_FIXTURE_TRACE=str(self.trace),
                        INSTALLER_SMOKE_FIXTURE_ENV=str(self.environment), MEDIAFORGE_DATA_ROOT="C:/DO-NOT-TOUCH/data",
                        PYMSS_MODEL_DIR="C:/DO-NOT-TOUCH/models", WEBVIEW2_USER_DATA_FOLDER="C:/DO-NOT-TOUCH/webview",
                        TAURI_SIGNING_PRIVATE_KEY="fixture-secret", GITHUB_TOKEN="fixture-secret", OPENAI_API_KEY="fixture-secret")
        self.addCleanup(self.cleanup_fake_work)

    def cleanup_fake_work(self):
        if self.report.is_file():
            report = json.loads(self.report.read_text())
            work = Path(report["workRoot"])
            if work.exists():
                # All fixture children are finite Python scripts and have exited before this cleanup.
                SMOKE.CANDIDATE.remove_owned_work(report)

    def run_smoke(self, mode=""):
        env = dict(self.env, INSTALLER_SMOKE_FIXTURE_MODE=mode)
        audit = self.root / "environment-after.json"
        env["INSTALLER_SMOKE_ENV_AUDIT"] = str(audit)
        script = self.root / "invoke.ps1"
        script.write_text(r'''
param($Smoke,$Stage,$Python,$Compiler,$Installer,$Report)
$names=@('MEDIAFORGE_DATA_ROOT','PYMSS_MODEL_DIR','WEBVIEW2_USER_DATA_FOLDER','TAURI_SIGNING_PRIVATE_KEY','GITHUB_TOKEN','OPENAI_API_KEY')
$before=@{}; foreach($name in $names){$before[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
& $Smoke -Stage $Stage -Version '0.0.33' -Python $Python -Compiler $Compiler -Installer $Installer -Report $Report
$result=$LASTEXITCODE
$after=@{}; foreach($name in $names){$after[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
@{before=$before;after=$after}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $env:INSTALLER_SMOKE_ENV_AUDIT -Encoding UTF8
exit $result
''', encoding="utf-8-sig")
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                                 "-Smoke", str(self.project / "scripts/smoke-installer.ps1"), "-Stage", str(self.stage),
                                 "-Python", sys.executable, "-Compiler", str(self.compiler), "-Installer", str(self.production_installer),
                                 "-Report", str(self.report)], env=env, capture_output=True, text=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if audit.exists():
            observed = json.loads(audit.read_text(encoding="utf-8-sig"))
            self.assertEqual(observed["before"], observed["after"])
        else:
            self.fail("PowerShell did not return to its caller for environment auditing: " + result.stdout + result.stderr)
        return result

    def test_real_powershell_entry_runs_only_fake_children_and_preserves_host_environment(self):
        result = self.run_smoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "cleaned")
        self.assertTrue(report["workRootRemoved"])
        self.assertFalse(Path(report["workRoot"]).exists())
        self.assertFalse(report["applicationLaunched"])
        self.assertFalse(report["productionInstallerExecuted"])
        self.assertTrue(report["productionRegistryUnchanged"])
        self.assertEqual(report["assetsBeforeUninstall"], report["uninstallPreservedAssets"])
        self.assertEqual(json.loads(self.state.read_text()), self.production)
        trace = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual([item["kind"] for item in trace], ["compile", "install", "health", "uninstall"])
        self.assertNotIn(str(self.production_installer), str(trace))
        environment = json.loads(self.environment.read_text())
        self.assertTrue(Path(environment["MEDIAFORGE_DATA_ROOT"]).is_relative_to(Path(report["workRoot"])))
        self.assertEqual(Path(environment["MEDIAFORGE_DATA_ROOT"]), Path(report["installDir"]) / "data")
        self.assertNotIn("TAURI_SIGNING_PRIVATE_KEY", environment)

    def test_worker_failure_still_uninstalls_only_test_identity_and_keeps_diagnostics(self):
        result = self.run_smoke("health-failure")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-cleaned")
        self.assertTrue(report["workRootRemoved"])
        self.assertIn("fake health failed", report["logTails"]["worker-health.log"])
        self.assertEqual(json.loads(self.state.read_text()), self.production)

    def test_zero_exit_without_successful_health_payload_fails_and_cleans(self):
        result = self.run_smoke("health-invalid")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-cleaned")
        self.assertIn("worker health did not succeed", report["error"])
        self.assertEqual(json.loads(self.state.read_text()), self.production)

    def test_flat_layout_is_rejected_and_the_verified_fake_identity_is_cleaned(self):
        result = self.run_smoke("flat-layout")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-cleaned")
        self.assertIn("installed app/ payload", report["error"])
        self.assertEqual(json.loads(self.state.read_text()), self.production)

    def test_production_registry_change_is_reported_without_restoration_or_automatic_cleanup(self):
        result = self.run_smoke("production-drift")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-retained")
        self.assertFalse(report["productionRegistryUnchanged"])
        self.assertTrue(Path(report["workRoot"]).exists())
        trace = [json.loads(line)["kind"] for line in self.trace.read_text().splitlines()]
        self.assertNotIn("uninstall", trace)

    def test_invalid_stage_and_external_report_are_rejected_before_any_compiler_process(self):
        (self.stage / "models").mkdir()
        result = self.run_smoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.report.exists())
        (self.stage / "models").rmdir()
        with mock.patch.object(SMOKE, "ROOT", self.project), mock.patch.object(SMOKE, "production_registry_snapshot") as registry, mock.patch.object(SMOKE.CANDIDATE, "run_hidden") as run:
            args = type("Args", (), dict(stage=self.stage, version="0.0.33", iscc=self.compiler, installer=None,
                                         report=self.root / "outside.json"))()
            with self.assertRaisesRegex(ValueError, "report must be"):
                SMOKE.execute(args)
        registry.assert_not_called()
        run.assert_not_called()

    def test_missing_quota_manifest_is_rejected_before_any_compiler_process(self):
        (self.stage / "manifest/execution-quota.json").unlink()
        with self.assertRaisesRegex(ValueError, "manifest/execution-quota.json"):
            SMOKE.validate_stage(self.stage)
        result = self.run_smoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.report.exists())
        self.assertEqual(json.loads(self.state.read_text()), self.production)

    def test_missing_library_schema_is_rejected_before_any_compiler_process(self):
        (self.stage / "manifest/library-schema.json").unlink()
        with self.assertRaisesRegex(ValueError, "manifest/library-schema.json"):
            SMOKE.validate_stage(self.stage)
        result = self.run_smoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.exists())
        self.assertFalse(self.report.exists())

    def test_compiler_timeout_retains_owned_work_without_uninstall_or_deletion(self):
        args = type("Args", (), dict(stage=self.stage, version="0.0.33", iscc=self.compiler, installer=None, report=self.report))()
        with mock.patch.object(SMOKE, "ROOT", self.project), mock.patch.object(SMOKE.CANDIDATE, "registry_values", return_value=None), \
             mock.patch.object(SMOKE.CANDIDATE, "run_hidden", side_effect=subprocess.TimeoutExpired("fake compiler", 1)), \
             mock.patch.object(SMOKE.CANDIDATE, "remove_owned_work") as remove:
            with self.assertRaisesRegex(RuntimeError, "installer smoke failed"):
                SMOKE.execute(args)
        remove.assert_not_called()
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-retained")
        self.assertTrue(Path(report["workRoot"]).is_dir())

    def test_nonzero_compiler_exit_also_retains_work_instead_of_guessing_descendants_ended(self):
        result = self.run_smoke("compile-failure")
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "failed-retained")
        self.assertTrue(Path(report["workRoot"]).is_dir())
        self.assertIn("fake compiler failed", report["logTails"]["compile.log"])
        self.assertEqual(json.loads(self.state.read_text()), self.production)
        self.assertEqual([json.loads(line)["kind"] for line in self.trace.read_text().splitlines()], ["compile"])


if __name__ == "__main__":
    unittest.main()
