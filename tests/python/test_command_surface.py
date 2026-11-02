"""命令面检查器必须使用 protocol.json，而不是已删除的 worker if 链。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_command_surface", ROOT / "scripts/check-command-surface.py")
assert SPEC and SPEC.loader
surface = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(surface)


class CommandSurfaceTests(unittest.TestCase):
    def test_protocol_supplies_all_worker_commands(self) -> None:
        implemented = surface.python_commands()
        self.assertIn("health", implemented)
        self.assertIn("model_hub", implemented)
        self.assertIn("media_tool", implemented)
        missing = sorted(name for name in surface.rust_commands() if name not in implemented)
        self.assertEqual(missing, [])

    def test_agent_contract_is_read_from_its_actual_surface_path(self) -> None:
        used = surface.agent_commands(surface.python_commands())
        self.assertIn("infer", used)
        self.assertIn("media_tool", used)
        self.assertIn("model_hub", used)

    def test_checker_forces_utf8_output_on_windows(self) -> None:
        source = (ROOT / "scripts/check-command-surface.py").read_text(encoding="utf-8")
        self.assertIn('stream.reconfigure(encoding="utf-8", errors="replace")', source)

    def test_strict_mode_is_a_ci_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/check-command-surface.py --strict", workflow)

    def test_strict_mode_accepts_the_current_command_surface(self) -> None:
        self.assertEqual(surface.main(["--strict"]), 0)


if __name__ == "__main__":
    unittest.main()
