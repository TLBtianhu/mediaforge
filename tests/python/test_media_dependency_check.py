"""Dependency declarations must be proven from source, never installed packages."""
from __future__ import annotations

import contextlib
import importlib.util
from importlib import metadata
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-media-deps.py"


class MediaDependencyCheckTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("media_dependency_check", SCRIPT)
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        temporary = tempfile.TemporaryDirectory(prefix="mediaforge-static-deps-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.python = self.root / "python"
        self.python.mkdir()
        self.requirements = self.root / "requirements.txt"
        self.requirements.write_text("", encoding="utf-8")
        self.runtime = self.root / "runtime-manifest.json"
        self.optional = self.python / "optional-components.py"
        patches = mock.patch.multiple(self.checker, PY_DIR=self.python, REQ_FILES=[self.requirements],
                                      RUNTIME_MANIFEST=self.runtime, OPTIONAL_PACKAGES=self.optional)
        patches.start()
        self.addCleanup(patches.stop)

    def source(self, text: str, declarations: str = ""):
        (self.python / "worker.py").write_text(text, encoding="utf-8")
        self.requirements.write_text(declarations, encoding="utf-8")

    def test_requirement_names_parse_bounds_extras_markers_and_canonical_separators(self):
        cases = {
            'Requests[socks]>=2.32,<3; python_version >= "3.11"': "requests",
            "PyMSS_Core==0.1.14": "pymss-core",
            "some.PKG_name[foo-bar,baz] ~= 1.2": "some-pkg-name",
            "plain-package": "plain-package",
            "named @ https://example.invalid/named.whl": "named",
            "https://example.invalid/Torch_Vision-1.2.3-py3-none-any.whl": "torch-vision",
        }
        for requirement, name in cases.items():
            with self.subTest(requirement=requirement):
                self.assertEqual(self.checker.requirement_name(requirement), name)

    def test_declared_but_uninstalled_dependencies_pass_without_any_metadata_lookup(self):
        self.source("import not_installed\n", "not-installed>=1,<2\n")
        with mock.patch.object(metadata, "packages_distributions", side_effect=AssertionError("must not inspect installed distributions")) as distributions, \
             mock.patch.object(importlib.util, "find_spec", side_effect=AssertionError("must not probe importability")) as available:
            rows = self.checker.check()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][3])
        distributions.assert_not_called()
        available.assert_not_called()

    def test_an_installed_but_undeclared_module_still_fails(self):
        self.source("import installed_only\n")
        with mock.patch.object(metadata, "packages_distributions", return_value={"installed_only": ["installed-only"]}), \
             mock.patch.object(importlib.util, "find_spec", return_value=object()):
            rows = self.checker.check()
        self.assertEqual(rows, [("installed_only", "installed-only", "missing declaration", False)])

    def test_explicit_module_distribution_aliases_do_not_require_installed_metadata(self):
        self.source("import PIL, cv2, yaml, pymss_core, socks\n",
                    "pillow>=10\nopencv-python-headless==5.0\nPyYAML>=6\npymss-core==0.1\nPySocks>=1.7\n")
        rows = self.checker.check()
        self.assertEqual({row[0] for row in rows}, {"PIL", "cv2", "yaml", "pymss_core", "socks"})
        self.assertTrue(all(row[3] for row in rows))

    def test_runtime_manifest_common_bootstrap_and_backend_are_declared_sources(self):
        self.source("import requests, torch, mlx\n")
        self.runtime.write_text(json.dumps({"bootstrap": {"requests": "requests[socks]>=2.32"}, "common": {},
                                          "backends": {"cpu": {"torch": {"requirement": "torch==2.7.1"}}, "mlx": {"extras": ["mlx"]}}}), encoding="utf-8")
        rows = self.checker.check()
        self.assertTrue(all(row[3] for row in rows))
        self.assertTrue(all("runtime-manifest.json:" in row[2] for row in rows))

    def test_pip_has_a_specific_bootstrap_source_but_unknown_optional_modules_are_not_waived(self):
        self.source("import pip\nimport funasr\nimport unknown_feature\n")
        rows = {row[0]: row for row in self.checker.check()}
        self.assertTrue(rows["pip"][3])
        self.assertIn("ensurepip", rows["pip"][2])
        self.assertFalse(rows["funasr"][3])
        self.assertFalse(rows["unknown_feature"][3])

    def test_optional_feature_declarations_are_read_without_importing_the_feature(self):
        self.source("from funasr import AutoModel\n")
        self.optional.write_text('OPTIONAL_PACKAGES: dict = {"asr": {"requirement": "funasr==1.3.26"}}', encoding="utf-8")
        rows = self.checker.check()
        self.assertEqual(rows[0][1], "funasr")
        self.assertTrue(rows[0][3])
        self.assertIn("OPTIONAL_PACKAGES", rows[0][2])

    def test_importerror_fallback_only_guards_try_body_not_else_or_finally(self):
        self.source("try:\n import guarded\nexcept (ImportError, ModuleNotFoundError):\n pass\nelse:\n import else_required\nfinally:\n import finally_required\n")
        rows = {row[0]: row for row in self.checker.check()}
        self.assertTrue(rows["guarded"][3])
        self.assertFalse(rows["else_required"][3])
        self.assertFalse(rows["finally_required"][3])

    def test_a_required_occurrence_is_not_hidden_by_a_second_optional_import(self):
        self.source("import mixed\ntry:\n import mixed\nexcept ImportError:\n pass\n")
        rows = self.checker.check()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0][3])

    def test_deferred_function_imports_and_reraised_importerrors_are_not_optional(self):
        self.source("try:\n def later():\n  import deferred_required\nexcept ImportError:\n pass\ntry:\n import reraised_required\nexcept ImportError:\n raise\n")
        rows = self.checker.check()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not row[3] for row in rows))

    def test_requirement_includes_are_followed_but_constraints_are_not_install_declarations(self):
        self.source("import included, constrained\n", "-r included.txt\n-c constraints.txt\n--index-url https://example.invalid/simple\n")
        (self.root / "included.txt").write_text("included[feature]>=1\n", encoding="utf-8")
        (self.root / "constraints.txt").write_text("constrained==1\n", encoding="utf-8")
        rows = {row[0]: row for row in self.checker.check()}
        self.assertTrue(rows["included"][3])
        self.assertFalse(rows["constrained"][3])

    def test_invalid_python_cannot_silently_hide_missing_imports(self):
        self.source("import missing\nthis is not valid python :=\n")
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(self.checker.main(), 1)
        self.assertIn("Static dependency check failed", error.getvalue())

    def test_the_real_repository_check_runs_in_an_isolated_stdlib_interpreter(self):
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(SCRIPT)], capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("0 missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
