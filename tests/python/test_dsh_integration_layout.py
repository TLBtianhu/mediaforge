from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest


class DshIntegrationLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.root = cls.repo / "integrations" / "dsh-mediaforge"
        cls.patch = (cls.root / "cordis.patch.yml").read_text(encoding="utf-8")
        cls.register = (cls.root / "register.ps1").read_text(encoding="utf-8")

    def test_bundle_uses_a_relative_cordis_entry(self) -> None:
        self.assertIn("name: './mcp-plugin.mjs'", self.patch)
        self.assertNotIn("command:", self.patch)
        self.assertNotIn("%LOCALAPPDATA%", self.patch)

    def stage_bundle(self, root: Path, *, portable=False, development=False, real=False) -> tuple[Path, Path]:
        bundle = root / "integrations" / "dsh-mediaforge"
        bundle.mkdir(parents=True)
        for name in ("package.json", "cordis.patch.yml", "mcp-plugin.mjs"):
            shutil.copy2(self.root / name, bundle)
        app = root if portable or development else root / "app"
        if portable:
            (root / "mediaforge.portable").touch()
        if development:
            (root / "src-tauri").mkdir()
            (root / "src-tauri" / "Cargo.toml").touch()
        if real:
            ignore_cache = shutil.ignore_patterns("__pycache__", "*.pyc", "runtime-envs")
            for name in ("python", "python-runtime", "manifest"):
                shutil.copytree(self.repo / name, app / name, ignore=ignore_cache)
        else:
            for relative in ("python/agent_bridge.py", "python-runtime/python.exe"):
                target = app / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
        return bundle, app

    def make_host_fixture(self, root: Path) -> Path:
        package = root / "dsh-home" / "installation" / "node_modules" / "@deepseek-ai" / "dsh-mcp-client"
        package.mkdir(parents=True)
        (package / "package.json").write_text('{"type":"module","exports":"./index.mjs"}', encoding="utf-8")
        (package / "index.mjs").write_text(
            "export const Config = config => config;\nexport const fixtureClient = true;\n", encoding="utf-8",
        )
        profile = root / "dsh-home" / "profiles" / "isolated"
        (profile / "node_modules" / "@local").mkdir(parents=True)
        (profile / "cordis.patch.yml").write_bytes(b"# Foreign host layer\r\n- id: web_search\r\n  disabled: false\r\n")
        return profile

    def load_linked_bundle(self, profile: Path, bundle: Path, ambient: dict[str, str]) -> dict:
        link = profile / "node_modules" / "@local" / "mediaforge-mcp"
        if os.name == "nt":
            from _winapi import CreateJunction
            CreateJunction(str(bundle), str(link))
        else:
            link.symlink_to(bundle, target_is_directory=True)
        try:
            return self.mount_bundle(profile, link, ambient)
        finally:
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()

    def mount_bundle(self, profile: Path, link: Path, ambient: dict[str, str]) -> dict:
        manifest = json.loads((link / "package.json").read_text(encoding="utf-8"))
        patch = link / manifest["dsh"]["bundle"]["patch"]
        # DSH resolves inserted relative module names beside the bundle patch.
        relative = re.search(r"(?m)^\s+name: '(\./[^']+)'$", patch.read_text(encoding="utf-8"))
        self.assertIsNotNone(relative)
        module = patch.parent / relative.group(1)
        host_anchor = profile.parents[1] / "installation" / "package.json"
        result = subprocess.run(
            ["node", "--input-type=module", "-e", """
import { realpathSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
const adapterURL = pathToFileURL(realpathSync(process.argv[1])).href;
const hostURL = pathToFileURL(process.argv[2]).href;
const peers = JSON.parse(process.argv[3]);
// DSH routes declared peers of linked roots to its installation's modules.
registerHooks({
  resolve(specifier, context, next) {
    if (context.parentURL === adapterURL && Object.hasOwn(peers, specifier)) {
      return next(specifier, { ...context, parentURL: hostURL });
    }
    return next(specifier, context);
  }
});
const adapter = await import(adapterURL);
await adapter.apply({
  plugin(client, config) {
    if (!client.fixtureClient) throw new Error('MCP client did not resolve to the host');
    return { async await() { console.log(JSON.stringify(config)); } };
  }
});
""", str(module), str(host_anchor), json.dumps(manifest.get("peerDependencies", {}))],
            cwd=profile, env={**os.environ, **ambient}, capture_output=True, text=True,
            encoding="utf-8", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node runtime for linked DSH bundle fixtures")
    def test_linked_instances_keep_their_own_roots_and_preserve_host_yaml(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-dsh-bundle-") as raw:
            root = Path(raw)
            profile = self.make_host_fixture(root)
            host_yaml = profile / "cordis.patch.yml"
            before = hashlib.sha256(host_yaml.read_bytes()).digest()
            default = root / "Local App Data" / "Programs" / "MediaForge"
            custom = root / "自定义 & 100% ! app"
            portable = root / "portable" / "app"
            ambient = {
                "LOCALAPPDATA": str(root / "Local App Data"),
                "MEDIAFORGE_HOME": str(default / "app"),
                "MEDIAFORGE_DATA_ROOT": str(root / "foreign-data"),
                "MEDIAFORGE_MODEL_DIR": str(root / "foreign-models"),
                "MEDIAFORGE_RUNTIME_ENVS_DIR": str(root / "foreign-runtime"),
            }
            for instance in (default, custom, portable):
                with self.subTest(instance=instance):
                    bundle, app = self.stage_bundle(instance, portable=instance == portable)
                    connection = self.load_linked_bundle(profile, bundle, ambient)
                    self.assertEqual(connection["command"], str(app / "python-runtime" / "python.exe"))
                    self.assertEqual(connection["cwd"], str(app))
                    self.assertEqual(connection["args"], [
                        "-I", "-B", str(app / "python" / "agent_bridge.py"), "--stdio",
                        "--data-root", str(instance / "data"),
                    ])
                    self.assertEqual(connection["serverName"], "mediaforge")
                    self.assertEqual(connection["transport"], "stdio")
                    for key, relative in (("MEDIAFORGE_DATA_ROOT", "data"), ("MEDIAFORGE_MODEL_DIR", "models"),
                                          ("PYMSS_MODEL_DIR", "models"), ("MEDIAFORGE_RUNTIME_ENVS_DIR", "runtime-envs")):
                        self.assertEqual(connection["env"][key], str(instance / relative))
                    self.assertEqual(hashlib.sha256(host_yaml.read_bytes()).digest(), before)

    @unittest.skipUnless(shutil.which("node"), "Node runtime for linked DSH bundle fixtures")
    def test_resolved_launch_starts_a_real_mediaforge_stdio_bridge(self) -> None:
        if not (self.repo / "python-runtime" / "python.exe").is_file():
            self.skipTest("local Windows bootstrap fixture is unavailable")
        with tempfile.TemporaryDirectory(prefix="mf-dsh-real-bridge-") as raw:
            root = Path(raw)
            profile = self.make_host_fixture(root)
            bundle, app = self.stage_bundle(root / "安装 & test !", real=True)
            connection = self.load_linked_bundle(profile, bundle, {})
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"},
                }},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ]
            result = subprocess.run(
                [connection["command"], *connection["args"]],
                cwd=connection["cwd"], env={**os.environ, **connection["env"]},
                input="".join(json.dumps(request) + "\n" for request in requests),
                capture_output=True, text=True, encoding="utf-8", timeout=40,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            initialize, tools = [json.loads(line)["result"] for line in result.stdout.splitlines()]
            self.assertEqual(initialize["serverInfo"]["name"], "mediaforge")
            self.assertIn("mediaforge_status", {tool["name"] for tool in tools["tools"]})
            self.assertTrue((app.parent / "data").is_dir())
            self.assertTrue((app.parent / "models").is_dir())

    @unittest.skipUnless(shutil.which("node"), "Node runtime for linked DSH bundle fixtures")
    def test_development_bundle_targets_the_development_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mf-dsh-dev-bundle-") as raw:
            root = Path(raw)
            profile = self.make_host_fixture(root)
            bundle, app = self.stage_bundle(root / "source", development=True)
            connection = self.load_linked_bundle(profile, bundle, {})
            self.assertEqual(connection["cwd"], str(app))
            self.assertEqual(connection["env"]["MEDIAFORGE_DATA_ROOT"], str(app / ".dev-data"))
            self.assertEqual(connection["env"]["MEDIAFORGE_MODEL_DIR"], str(app / ".dev-data" / "models"))
            self.assertEqual(connection["env"]["MEDIAFORGE_RUNTIME_ENVS_DIR"], str(app / ".dev-data" / "runtime-envs"))

    def test_migration_tool_no_longer_writes_a_launcher_or_registry_pointer(self) -> None:
        self.assertNotIn("mediaforge-mcp.cmd", self.register)
        self.assertNotIn("HKCU:", self.register)

    def test_bundle_file_manifest_entries_exist(self) -> None:
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        for entry in package["files"]:
            if "*" in entry:
                continue
            self.assertTrue((self.root / entry).is_file(), f"bundle manifest lists a missing file: {entry}")

    def test_readme_documents_manual_upgrade_cleanup(self) -> None:
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("-File register.ps1", readme, "users no longer have the helper")
        self.assertIn("mediaforge MCP BEGIN", readme)
        self.assertIn("手动删除", readme)
        self.assertIn("只删 MediaForge 自己的行", readme)
        self.assertIn("其它行一律保留", readme)
        self.assertIn("不要动其它区块", readme)
        self.assertIn("最后", readme)
        self.assertNotIn("删除从", readme, "deleting the whole block would remove the user's rows")

    def test_published_bundle_excludes_powershell_helpers(self) -> None:
        import json
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("register.ps1", package["files"])
        self.assertNotIn("setup.ps1", package["files"])
        self.assertNotIn("verify.ps1", package["files"])
        self.assertIn("mcp-plugin.mjs", package["files"])
        self.assertNotIn("mediaforge-mcp.cmd", package["files"])

    def test_icon_stays_below_the_dsh_metadata_limit(self) -> None:
        icon = self.root / "icon.svg"
        self.assertLess(icon.stat().st_size, 256 * 1024)
        self.assertNotIn("data:image", icon.read_text(encoding="utf-8"))





@unittest.skipUnless(os.name == "nt", "Windows PowerShell migration tool")
class DshRegisterMigrationTests(unittest.TestCase):
    """register.ps1 不再是安装器：默认只报告，-RemoveLegacyBlock 才写文件。"""

    LEGACY = (
        "- id: keep-me\n"
        "  name: keep\n"
        "# === mediaforge MCP BEGIN ===\n"
        "- insert:\n"
        "    - id: mediaforge-mcp\n"
        "      name: '@deepseek-ai/dsh-mcp-client'\n"
        "- id: mediaforge-mcp\n"
        "  disabled: true\n"
        "- id: web\n"
        "  name: \"@deepseek-ai/dsh-web\"\n"
        "  config:\n"
        "    searchProvider: deepseek-search-api\n"
        "# === mediaforge MCP END ===\n"
        "- id: after-block\n"
        "  name: after\n"
    )

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dsh-register-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, True))
        self.patch = self.write_patch("desktop")
        self.script = Path(__file__).parents[2] / "integrations" / "dsh-mediaforge" / "register.ps1"

    def write_patch(self, profile: str) -> Path:
        directory = self.root / "profiles" / profile
        directory.mkdir(parents=True)
        path = directory / "cordis.patch.yml"
        path.write_text(self.LEGACY, encoding="utf-8")
        return path

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
             "-DshHome", str(self.root), *args],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_default_run_never_writes_the_profile_patch(self) -> None:
        before = hashlib.sha256(self.patch.read_bytes()).digest()
        for _ in range(3):
            result = self.run_script()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(hashlib.sha256(self.patch.read_bytes()).digest(), before)

    def test_remove_requires_an_explicit_profile(self) -> None:
        before = self.patch.read_bytes()
        result = self.run_script("-RemoveLegacyBlock")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Profile", result.stderr)
        self.assertEqual(self.patch.read_bytes(), before, "a refused run must not touch the file")

    def test_remove_only_touches_the_requested_profile(self) -> None:
        web = self.write_patch("web")
        web_before = web.read_bytes()
        result = self.run_script("-Profile", "desktop", "-RemoveLegacyBlock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("mediaforge MCP BEGIN", self.patch.read_text(encoding="utf-8"))
        self.assertEqual(web.read_bytes(), web_before, "another profile must stay untouched")

    def test_remove_legacy_block_keeps_foreign_rows(self) -> None:
        result = self.run_script("-Profile", "desktop", "-RemoveLegacyBlock")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.patch.read_text(encoding="utf-8")
        self.assertIn("- id: keep-me", text)
        self.assertIn("- id: web", text)
        self.assertIn("- id: after-block", text)
        self.assertNotIn("mediaforge MCP BEGIN", text)
        self.assertNotIn("mediaforge-mcp", text)


if __name__ == "__main__":
    unittest.main()
