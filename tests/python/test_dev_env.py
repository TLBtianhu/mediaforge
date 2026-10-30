"""令牌只该存在于本机开发目录，绝不能进仓库。

真实情况提醒：`.dev-data/ms-token.txt` 曾经存在过（已按约定删除），
`dev.env` 现在是唯一入口；两个文件都要被忽略。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import dev_env  # noqa: E402


class DevEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mf-dev-env-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dev.env"

    def test_reads_simple_pairs_and_ignores_comments(self) -> None:
        self.path.write_text(
            "# 注释\n\nMODELSCOPE_API_TOKEN=abc123\nOTHER=value\n",
            encoding="utf-8",
        )
        values = dev_env.read_dev_env(self.path)
        self.assertEqual(values["MODELSCOPE_API_TOKEN"], "abc123")
        self.assertEqual(values["OTHER"], "value")

    def test_strips_quotes_and_spaces(self) -> None:
        self.path.write_text('MODELSCOPE_API_TOKEN="  spaced  "\n', encoding="utf-8")
        self.assertEqual(dev_env.read_dev_env(self.path)["MODELSCOPE_API_TOKEN"], "spaced")

    def test_env_var_wins_over_the_file(self) -> None:
        self.path.write_text("MODELSCOPE_API_TOKEN=from-file\n", encoding="utf-8")
        original = dict(os.environ)
        try:
            os.environ["MODELSCOPE_API_TOKEN"] = "from-env"
            self.assertEqual(dev_env.modelscope_token({"MODELSCOPE_API_TOKEN": "from-env"}), "from-env")
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_placeholder_is_rejected_with_actionable_message(self) -> None:
        import unittest.mock as mock

        with mock.patch.object(dev_env, "DEV_ENV", self.path):
            self.path.write_text("MODELSCOPE_API_TOKEN=PASTE_TOKEN_HERE\n", encoding="utf-8")
            with mock.patch.object(dev_env, "LEGACY_TOKEN_FILE", self.path.parent / "missing.txt"):
                with self.assertRaises(dev_env.MissingToken) as ctx:
                    dev_env.modelscope_token({})
        self.assertIn("dev.env", str(ctx.exception))

    def test_dev_data_is_gitignored(self) -> None:
        """`.dev-data` 必须被忽略，否则令牌会随提交泄露。"""
        import subprocess

        result = subprocess.run(
            ["git", "check-ignore", "-q", str(REPO_ROOT / ".dev-data" / "dev.env")],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        if result.returncode not in (0, 1):
            self.skipTest("git 不可用")
        self.assertEqual(result.returncode, 0, ".dev-data/dev.env 没有被 .gitignore 覆盖")


if __name__ == "__main__":
    unittest.main()
