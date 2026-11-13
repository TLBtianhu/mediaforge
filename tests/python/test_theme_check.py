from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("check_theme_tokens", ROOT / "scripts" / "check-theme-tokens.py")
assert spec.loader is not None
theme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(theme)


def make_tree(files: dict[str, str]) -> Path:
    """Create a temporary project root with the given relative files."""
    root = Path(tempfile.mkdtemp(prefix="theme-audit-"))
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


BASE = {
    "src/utils/theme.ts": "style.setProperty('--primary', tokens.primary)\n",
    "src/styles/global.scss": "--primary: #123456;\n",
}


class LiteralColorTests(unittest.TestCase):
    def test_unregistered_literal_color_fails(self) -> None:
        root = make_tree({**BASE, "src/views/Foo.vue": "<style>.a { color: #abcdef; }</style>"})
        with mock.patch.dict(theme.DEBT, {}, clear=True):
            problems, counts = theme.check_literal_colors(theme.source_files(root), root)
        self.assertEqual(counts["src/views/Foo.vue"], 1)
        self.assertTrue(any("src/views/Foo.vue" in problem for problem in problems))

    def test_allowed_file_is_ignored_and_its_reason_stays_documented(self) -> None:
        root = make_tree({**BASE, "src/utils/theme.ts": "style.setProperty('--primary', '#123456')\n"})
        with mock.patch.dict(
            theme.ALLOWED,
            {"src/utils/theme.ts": "真源", "src/styles/global.scss": "默认值真源"},
            clear=True,
        ), mock.patch.dict(theme.DEBT, {}, clear=True):
            problems, _counts = theme.check_literal_colors(theme.source_files(root), root)
        self.assertEqual(problems, [])

    def test_debt_budget_rejects_growth_and_stale_entries(self) -> None:
        root = make_tree({
            **BASE,
            "src/views/Grown.vue": "<style>.a { color: #111; } .b { color: #222; }</style>",
            "src/views/Cleaned.vue": "<style>.a { color: var(--primary); }</style>",
        })
        with mock.patch.dict(theme.ALLOWED, {}, clear=True), mock.patch.dict(
            theme.DEBT,
            {"src/views/Grown.vue": (1, "预算"), "src/views/Cleaned.vue": (2, "已清零")},
            clear=True,
        ):
            problems, _counts = theme.check_literal_colors(theme.source_files(root), root)
        self.assertTrue(any("超过预算" in problem for problem in problems))
        self.assertTrue(any("已过期" in problem for problem in problems))

    def test_lowering_a_count_requires_tightening_the_budget(self) -> None:
        root = make_tree({**BASE, "src/views/Shrunk.vue": "<style>.a { color: #111; }</style>"})
        with mock.patch.dict(theme.ALLOWED, {}, clear=True), mock.patch.dict(
            theme.DEBT, {"src/views/Shrunk.vue": (4, "旧预算")}, clear=True
        ):
            problems, _counts = theme.check_literal_colors(theme.source_files(root), root)
        self.assertTrue(any("收紧" in problem for problem in problems))


class TokenReferenceTests(unittest.TestCase):
    def test_undefined_token_is_reported(self) -> None:
        root = make_tree({**BASE, "src/views/Foo.vue": "<style>.a { color: var(--missing-token); }</style>"})
        problems, undefined = theme.check_token_references(theme.source_files(root), root)
        self.assertEqual(undefined, {"src/views/Foo.vue": ["--missing-token"]})
        self.assertTrue(problems)

    def test_defined_and_external_tokens_are_allowed(self) -> None:
        root = make_tree({
            **BASE,
            "src/views/Foo.vue": (
                "<style>.a { color: var(--primary); border-color: var(--n-border-color); }</style>"
                "<script>const style = { '--local-token': '#111' }</script>"
            ),
        })
        problems, undefined = theme.check_token_references(theme.source_files(root), root)
        self.assertEqual(undefined, {})
        self.assertEqual(problems, [])


class TokenParityTests(unittest.TestCase):
    def test_runtime_token_without_default_fails(self) -> None:
        root = make_tree({
            "src/utils/theme.ts": "style.setProperty('--primary', tokens.primary)\nstyle.setProperty('--ghost', tokens.ghost)\n",
            "src/styles/global.scss": "--primary: #123456;\n",
        })
        problems = theme.check_token_parity(theme.source_files(root), root)
        self.assertTrue(any("--ghost" in problem and "没有默认值" in problem for problem in problems))

    def test_default_without_runtime_assignment_fails(self) -> None:
        root = make_tree({
            "src/utils/theme.ts": "style.setProperty('--primary', tokens.primary)\n",
            "src/styles/global.scss": "--primary: #123456;\n--danger: #b4233a;\n",
        })
        problems = theme.check_token_parity(theme.source_files(root), root)
        self.assertTrue(any("--danger" in problem and "从不按主题重新赋值" in problem for problem in problems))

    def test_layout_tokens_are_not_part_of_the_color_contract(self) -> None:
        root = make_tree({
            "src/utils/theme.ts": "style.setProperty('--primary', tokens.primary)\n",
            "src/styles/global.scss": "--primary: #123456;\n--space-md: 12px;\n--radius-lg: 14px;\n",
        })
        self.assertEqual(theme.check_token_parity(theme.source_files(root), root), [])


if __name__ == "__main__":
    unittest.main()
