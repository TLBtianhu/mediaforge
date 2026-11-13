"""MIDI results must come from the operation report, never old directory contents."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.audio_tools_op_bridge import run_plugin_operation_as_tool


class MidiOutputOwnershipTests(unittest.TestCase):
    def run_midi(self, root, report):
        source = root / "voice.wav"
        source.write_bytes(b"fixture audio")
        runtime = Mock()
        runtime.run.return_value = {"report": report}
        with patch("core.audio_tools_op_bridge._resolved_inputs", side_effect=lambda value: value), patch("core.audio_tools_op_bridge.emit"):
            return run_plugin_operation_as_tool(runtime, "midi", "fixture.midi", {"inputPath": str(source), "outputDir": str(root)})

    def test_missing_output_report_never_claims_an_existing_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "voice-previous.mid"
            previous.write_bytes(b"old MIDI must remain untouched")
            with self.assertRaisesRegex(ValueError, "did not report"):
                self.run_midi(root, {"output_dir": str(root)})
            self.assertEqual(previous.read_bytes(), b"old MIDI must remain untouched")

    def test_only_an_explicit_existing_absolute_file_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "explicit.mid"
            output.write_bytes(b"reported MIDI")
            for key in ("output_midi", "result"):
                with self.subTest(key=key):
                    result = self.run_midi(root, {key: str(output), "note_count": 2})
                    self.assertEqual(result["outputPath"], str(output))
                    self.assertEqual(result["succeeded"], 1)
                    self.assertEqual(result["noteCount"], 2)
            for invalid in (str(root / "missing.mid"), str(root), "relative.mid", {"path": str(output)}):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "did not report"):
                    self.run_midi(root, {"output_midi": invalid})


if __name__ == "__main__":
    unittest.main()
