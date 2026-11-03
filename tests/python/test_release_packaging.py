"""隔离打包验证必须能显式关闭签名凭据读取与本机副本复制。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReleasePackagingTests(unittest.TestCase):
    def test_isolated_packaging_opt_outs_are_explicit(self) -> None:
        script = (ROOT / "scripts/package-release.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$SkipSign", script)
        self.assertIn("[switch]$SkipDownloadCopy", script)
        self.assertIn("if ($SkipSign) { '' } else { Get-SigningPassword }", script)
        self.assertIn("-not $SkipSign -and $signingPassword", script)

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


if __name__ == "__main__":
    unittest.main()
