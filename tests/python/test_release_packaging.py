"""隔离打包验证必须能显式关闭签名凭据读取与 Downloads 复制。"""
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
        self.assertIn("if ($SkipDownloadCopy)", script)


if __name__ == "__main__":
    unittest.main()
