"""Validate publication wiring and Bash syntax without executing release steps."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(yaml, "release workflow checks require PyYAML")
class MainReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.document = yaml.load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.publish = self.document["jobs"]["github-release"]
        self.steps = self.publish["steps"]

    def step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def test_dispatch_does_not_silently_rebuild_an_old_version_or_unverified_platforms(self):
        inputs = self.document["on"]["workflow_dispatch"]["inputs"]
        self.assertNotIn("default", inputs["version"])
        self.assertEqual(inputs["version"]["required"], "true")
        self.assertEqual(inputs["target"]["default"], "windows")
        self.assertIn("contains(needs.*.result, 'success')", self.publish["if"])
        self.assertIn("!contains(needs.*.result, 'failure')", self.publish["if"])
        self.assertIn("!contains(needs.*.result, 'cancelled')", self.publish["if"])

    def test_signing_credentials_exist_only_on_signing_steps(self):
        self.assertFalse(any("SIGNING" in key for key in self.publish.get("env", {})))
        signing = [step["name"] for step in self.steps if any("SIGNING" in key for key in step.get("env", {}))]
        self.assertEqual(signing, ["Sign managed update assets", "Sign and verify stable version notification"])
        self.assertIn("capture_output=True", self.step("Sign managed update assets")["run"])
        self.assertNotIn("-k ", self.step("Sign managed update assets")["run"])

    def test_stable_notification_uses_fixed_client_paths_and_never_prerelease(self):
        for name in ["Read current stable version notification", "Sign and verify stable version notification"]:
            self.assertEqual(self.step(name)["if"], "env.RELEASE_PRERELEASE != 'true'")
        sign = self.step("Sign and verify stable version notification")
        self.assertIn('--version "$RELEASE_VERSION"', sign["run"])
        self.assertIn("--previous-dir", sign["run"])
        self.assertIn("VERSIONS_SIGNING_PRIVATE_KEY", sign["env"])
        notification = next(step for step in self.steps if step.get("with", {}).get("tag_name") == "versions")
        self.assertEqual(notification["if"], "env.RELEASE_PRERELEASE != 'true'")
        self.assertEqual(notification["with"]["make_latest"], "false")
        self.assertTrue(notification["with"]["files"].endswith("/mediaforge-version-notification/*"))
        rust = (ROOT / "src-tauri/src/version_check.rs").read_text(encoding="utf-8")
        self.assertIn("/releases/download/versions/versions.json", rust)
        self.assertIn("/resolve/master/versions/versions.json", rust)

    def test_mirrors_retain_the_existing_release_and_versions_directories(self):
        for name in ["Upload release files to HuggingFace dataset", "Upload release files to ModelScope dataset"]:
            script = self.step(name)["run"]
            self.assertIn("TLBtianhu/MediaForge-Dist", script)
            self.assertIn('"release-mirror/${RELEASE_VERSION}" release', script)
            self.assertIn('"$RUNNER_TEMP/mediaforge-version-notification" versions', script)
            self.assertIn('"$RELEASE_PRERELEASE" != "true"', script)
        metadata = self.step("Generate managed update metadata")
        self.assertEqual(set(metadata["env"]), {"RELEASE_WINDOWS_BUILD_RESULT", "RELEASE_MACOS_BUILD_RESULT", "RELEASE_LINUX_BUILD_RESULT"})

    @unittest.skipUnless(shutil.which("bash"), "Bash syntax checker unavailable")
    def test_every_bash_step_parses_without_running_it(self):
        for step in self.steps:
            if "run" in step and step.get("shell", "bash") == "bash":
                with self.subTest(step=step.get("name")):
                    # Binary stdin preserves LF on Windows; text=True translates
                    # it to CRLF and makes a valid Bash script fail spuriously.
                    result = subprocess.run([shutil.which("bash"), "-n"], input=step["run"].encode("utf-8"), capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
