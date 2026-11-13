"""Run every advanced-workflow palette node against the installed pymss runtime.

This is a manual integration smoke test. Run it with the Python interpreter from a
prepared Pymss Studio runtime so codec, scipy and graph capabilities are available.
Separation nodes use a deterministic fake separator; one real-model workflow should
be run separately after this fast contract test passes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from pymss import save_audio
from pymss.graph import get_node_type
from pymss.graph.core import (
    AudioArtifact,
    DAGNode,
    NodeContext,
    ParamsArtifact,
    SeparatorCache,
    StringArtifact,
)
import pymss.graph.nodes as graph_nodes


PALETTE_NODE_TYPES = {
    "AudioAdjustVolume",
    "AudioConcat",
    "AudioEqualizer3Band",
    "AudioMerge",
    "CaseConverter",
    "EmptyAudio",
    "JoinAudioChannels",
    "JsonExtractString",
    "LoadAudio",
    "PreviewAudio",
    "RegexExtract",
    "RegexReplace",
    "SaveAudio",
    "SaveAudioAdvanced",
    "SaveAudioMP3",
    "SaveAudioOpus",
    "SplitAudioChannels",
    "StringConcatenate",
    "StringConstant",
    "StringFormat",
    "StringReplace",
    "StringSubstring",
    "StringTrim",
    "TrimAudioDuration",
    "custom_mss_separate",
    "custom_mss_separate_list",
    "input_audio",
    "mss_separate",
    "mss_separate_list",
    "pymss_audio_ensemble",
    "pymss_audio_invert_phase",
    "pymss_audio_normalize",
    "pymss_load_audio",
    "pymss_load_audio_batch",
    "pymss_mss_params",
    "pymss_save_audio",
    "pymss_vr_params",
    "vr_separate",
    "vr_separate_list",
}

KNOWN_CORE_CONTRACT_FAILURES = {
    "AudioConcat.direction",
    "AudioMerge.merge_method",
    "CaseConverter.mode",
    "RegexExtract.mode",
    "SaveAudioOpus.bitrate",
    "StringTrim.mode",
    "mss_separate.download_missing",
    "mss_separate.source",
}


class FakeSeparator:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            audio={"sample_rate": 8000},
            training=SimpleNamespace(target_instrument=None),
            instruments=["Vocals", "Instrumental"],
        )
        self.progress_callback = None

    def __enter__(self) -> "FakeSeparator":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def separate(self, mix: np.ndarray, pbar: bool = False, stems=None):
        del pbar, stems
        audio = np.asarray(mix, dtype=np.float32)
        return {
            "Vocals": audio * np.float32(0.6),
            "Instrumental": audio * np.float32(0.4),
        }


class SmokeRunner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.output_dir = root / "outputs"
        self.output_dir.mkdir()
        self.executed: set[str] = set()
        self.failures: dict[str, str] = {}
        self.separator_calls: list[dict[str, object]] = []
        self.counter = 0
        self.cache = SeparatorCache(factory=self._fake_separator)
        self.ctx = NodeContext(
            output_dir=self.output_dir,
            logger=None,
            debug=False,
            progress_callback=None,
            separator_cache=self.cache,
            source="modelscope",
            device="cpu",
            audio_params={
                "wav_bit_depth": "PCM_16",
                "flac_bit_depth": "PCM_16",
                "mp3_bit_rate": "128k",
            },
        )

    def _fake_separator(self, **kwargs: object) -> FakeSeparator:
        self.separator_calls.append(dict(kwargs))
        return FakeSeparator()

    def execute(
        self,
        node_type: str,
        *,
        widgets: list[object] | None = None,
        inputs: dict[str, object] | None = None,
        outputs: list[dict[str, object]] | None = None,
    ):
        self.counter += 1
        node_id = f"{self.counter}:{node_type}"
        info = get_node_type(node_type)
        node = DAGNode(
            id=node_id,
            type=node_type,
            inputs=[],
            data={"widgets_values": list(widgets or []), "outputs": list(outputs or [])},
            title=node_type,
        )
        node.signature = info.signature(node)
        self.ctx.nodes_by_id = {node_id: node}
        self.ctx.current_node_id = node_id
        result = info.execute(self.ctx, dict(inputs or {}))
        self.executed.add(node_type)
        return result

    def attempt(self, node_type: str, callback):
        try:
            return callback()
        except Exception as exc:  # diagnostic report must continue through every node
            self.failures[node_type] = f"{type(exc).__name__}: {exc}"
            return None

    def close(self) -> None:
        self.cache.close()


def ui_widget_map(project_root: Path) -> dict[str, list[str]]:
    script = """
      import('./src/litegraph/nodeSpecs.ts').then(({ NODE_SPECS, BUILTIN_SPECS }) => {
        const specs = { ...NODE_SPECS, ...BUILTIN_SPECS }
        console.log(JSON.stringify(Object.fromEntries(
          Object.values(specs).map(spec => [spec.type, spec.widgets.map(widget => widget.name)]),
        )))
      })
    """
    output = subprocess.check_output(
        ["node", "-e", script],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(output)


def stereo_tone(sample_rate: int = 8000, duration: float = 0.25) -> np.ndarray:
    time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
    left = np.sin(2 * np.pi * 220 * time).astype(np.float32) * np.float32(0.2)
    right = np.cos(2 * np.pi * 330 * time).astype(np.float32) * np.float32(0.1)
    return np.stack([left, right])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-contracts",
        action="store_true",
        help="fail while any known editor/runtime contract mismatch remains",
    )
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    widgets_by_type = ui_widget_map(project_root)
    palette_types = set(widgets_by_type)
    if palette_types != PALETTE_NODE_TYPES:
        missing = sorted(PALETTE_NODE_TYPES - palette_types)
        added = sorted(palette_types - PALETTE_NODE_TYPES)
        print(f"Palette inventory drifted: missing={missing}, added={added}")
        return 1

    with tempfile.TemporaryDirectory(prefix="pymss-all-nodes-") as temporary:
        root = Path(temporary)
        input_dir = root / "inputs"
        input_dir.mkdir()
        waveform = stereo_tone()
        first_input = input_dir / "tone-a.wav"
        second_input = input_dir / "tone-b.wav"
        save_audio(str(first_input), waveform.T, 8000, "wav", {"wav_bit_depth": "PCM_16"})
        save_audio(str(second_input), (waveform * 0.5).T, 8000, "wav", {"wav_bit_depth": "PCM_16"})

        audio = AudioArtifact(waveform, 8000, source_path=str(first_input), stem_name="Audio")
        audio_b = AudioArtifact(waveform * 0.5, 8000, source_path=str(second_input), stem_name="AudioB")
        mono_left = AudioArtifact(waveform[0:1], 8000, source_path=str(first_input))
        mono_right = AudioArtifact(waveform[1:2], 8000, source_path=str(first_input))
        hot_audio = AudioArtifact(waveform * 8, 8000, source_path=str(first_input))

        runner = SmokeRunner(root)
        runner.ctx.input_path = str(first_input)
        runner.ctx.inputs = {"runtime_audio": str(first_input)}

        input_audio = runner.attempt("input_audio", lambda: runner.execute("input_audio"))
        runner.attempt(
            "pymss_load_audio",
            lambda: runner.execute("pymss_load_audio", widgets=[str(first_input), ""]),
        )
        runner.attempt(
            "LoadAudio",
            lambda: runner.execute("LoadAudio", widgets=[str(first_input)]),
        )
        runner.attempt(
            "pymss_load_audio_batch",
            lambda: runner.execute(
                "pymss_load_audio_batch",
                widgets=[str(input_dir), False, True, ""],
            ),
        )

        mss_params = runner.attempt(
            "pymss_mss_params",
            lambda: runner.execute(
                "pymss_mss_params",
                widgets=[1, "Default", "Default", False, False, False],
            ),
        )
        vr_params = runner.attempt(
            "pymss_vr_params",
            lambda: runner.execute(
                "pymss_vr_params",
                widgets=[1, 512, 5, False, False, False, 0.2, False],
            ),
        )
        mss_artifact = mss_params.outputs[0] if mss_params else ParamsArtifact({}, "mss")
        vr_artifact = vr_params.outputs[0] if vr_params else ParamsArtifact({}, "vr")
        stem_outputs = [
            {"name": "Vocals (Audio)", "type": "AUDIO"},
            {"name": "Vocals (String)", "type": "STRING"},
            {"name": "Instrumental (Audio)", "type": "AUDIO"},
            {"name": "Instrumental (String)", "type": "STRING"},
        ]
        for node_type, params, widgets in [
            ("mss_separate", mss_artifact, ["fake.ckpt", "cpu", False, "modelscope", "0", False]),
            ("mss_separate_list", mss_artifact, ["fake.ckpt", "cpu", False, "modelscope", "0", False]),
            ("vr_separate", vr_artifact, ["fake-vr.pth", "cpu", False, "modelscope", "0", False]),
            ("vr_separate_list", vr_artifact, ["fake-vr.pth", "cpu", False, "modelscope", "0", False]),
        ]:
            runner.attempt(
                node_type,
                lambda node_type=node_type, params=params, widgets=widgets: runner.execute(
                    node_type,
                    widgets=widgets,
                    inputs={"audio": audio, "params": params},
                    outputs=[] if node_type.endswith("_list") else stem_outputs,
                ),
            )

        with mock.patch.object(
            graph_nodes,
            "_resolve_user_model",
            return_value={"model_path": str(root / "fake.ckpt"), "config_path": str(root / "fake.yaml")},
        ):
            for node_type in ["custom_mss_separate", "custom_mss_separate_list"]:
                runner.attempt(
                    node_type,
                    lambda node_type=node_type: runner.execute(
                        node_type,
                        widgets=["fake-custom", "mel_band_roformer", "cpu", "0", False],
                        inputs={"audio": audio, "params": mss_artifact},
                        outputs=[] if node_type.endswith("_list") else stem_outputs,
                    ),
                )

        runner.attempt(
            "pymss_audio_ensemble",
            lambda: runner.execute(
                "pymss_audio_ensemble",
                widgets=["2", "avg_wave", "1", "1"],
                inputs={"audio_1": audio, "audio_2": audio_b},
            ),
        )
        runner.attempt(
            "pymss_audio_invert_phase",
            lambda: runner.execute("pymss_audio_invert_phase", inputs={"a": audio}),
        )
        runner.attempt(
            "pymss_audio_normalize",
            lambda: runner.execute("pymss_audio_normalize", inputs={"audio": hot_audio}),
        )
        runner.attempt("PreviewAudio", lambda: runner.execute("PreviewAudio", inputs={"audio": audio}))

        runner.attempt(
            "TrimAudioDuration",
            lambda: runner.execute("TrimAudioDuration", widgets=[0.01, 0.05], inputs={"audio": audio}),
        )
        split = runner.attempt(
            "SplitAudioChannels",
            lambda: runner.execute("SplitAudioChannels", inputs={"audio": audio}),
        )
        runner.attempt(
            "JoinAudioChannels",
            lambda: runner.execute(
                "JoinAudioChannels",
                inputs={
                    "audio_left": split.outputs[0] if split else mono_left,
                    "audio_right": split.outputs[1] if split else mono_right,
                },
            ),
        )
        runner.attempt(
            "AudioConcat",
            lambda: runner.execute("AudioConcat", widgets=["after"], inputs={"audio1": audio, "audio2": audio_b}),
        )
        runner.attempt(
            "AudioMerge",
            lambda: runner.execute("AudioMerge", widgets=["subtract"], inputs={"audio1": audio, "audio2": audio_b}),
        )
        runner.attempt(
            "AudioAdjustVolume",
            lambda: runner.execute("AudioAdjustVolume", widgets=[-6], inputs={"audio": audio}),
        )
        runner.attempt("EmptyAudio", lambda: runner.execute("EmptyAudio", widgets=[0.05, 8000, 1]))
        runner.attempt(
            "AudioEqualizer3Band",
            lambda: runner.execute(
                "AudioEqualizer3Band",
                widgets=[1.5, 100, -1.0, 1000, 0.707, 1.0, 3000],
                inputs={"audio": audio},
            ),
        )

        constant = runner.attempt(
            "StringConstant",
            lambda: runner.execute("StringConstant", widgets=["  alpha-123  "]),
        )
        base_string = constant.outputs[0] if constant else StringArtifact("  alpha-123  ")
        runner.attempt(
            "StringConcatenate",
            lambda: runner.execute(
                "StringConcatenate",
                widgets=["", "", ""],
                inputs={"string_a": StringArtifact("alpha"), "string_b": StringArtifact("beta"), "delimiter": StringArtifact("-")},
            ),
        )
        runner.attempt(
            "StringSubstring",
            lambda: runner.execute("StringSubstring", widgets=["", 2, 7], inputs={"string": base_string}),
        )
        runner.attempt(
            "StringReplace",
            lambda: runner.execute("StringReplace", widgets=["", "alpha", "omega"], inputs={"string": base_string}),
        )
        runner.attempt(
            "StringTrim",
            lambda: runner.execute("StringTrim", widgets=["", "Both"], inputs={"string": base_string}),
        )
        runner.attempt(
            "CaseConverter",
            lambda: runner.execute("CaseConverter", widgets=["", "Title Case"], inputs={"string": base_string}),
        )
        runner.attempt(
            "StringFormat",
            lambda: runner.execute("StringFormat", widgets=["value={a}"], inputs={"value": StringArtifact("42")}),
        )
        runner.attempt(
            "RegexReplace",
            lambda: runner.execute("RegexReplace", widgets=["", r"\d+", "N"], inputs={"string": base_string}),
        )
        runner.attempt(
            "RegexExtract",
            lambda: runner.execute("RegexExtract", widgets=["", r"(\d+)", "First Group", 1], inputs={"string": base_string}),
        )
        runner.attempt(
            "JsonExtractString",
            lambda: runner.execute(
                "JsonExtractString",
                widgets=["", "answer"],
                inputs={"json_string": StringArtifact('{"answer": 42}')},
            ),
        )

        save_inputs = {"audio": audio, "filename": StringArtifact("pymss-save")}
        runner.attempt(
            "pymss_save_audio",
            lambda: runner.execute(
                "pymss_save_audio",
                widgets=["wav", "8000", "PCM_16", "PCM_16", "128k"],
                inputs=save_inputs,
            ),
        )
        runner.attempt(
            "SaveAudio",
            lambda: runner.execute(
                "SaveAudio",
                widgets=["native/flac"],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/flac")},
            ),
        )
        runner.attempt(
            "SaveAudioMP3",
            lambda: runner.execute(
                "SaveAudioMP3",
                widgets=["native/mp3", "V0"],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/mp3")},
            ),
        )
        runner.attempt(
            "SaveAudioOpus",
            lambda: runner.execute(
                "SaveAudioOpus",
                widgets=["native/opus", "128k"],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/opus")},
            ),
        )
        runner.attempt(
            "SaveAudioAdvanced",
            lambda: runner.execute(
                "SaveAudioAdvanced",
                widgets=["native/advanced", "wav", ""],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/advanced")},
            ),
        )

        missing_execution = sorted(PALETTE_NODE_TYPES - runner.executed - set(runner.failures))
        unexpected_execution = sorted(runner.executed - PALETTE_NODE_TYPES)
        missing_controls = {
            node_type: missing
            for node_type, required in {
                "TrimAudioDuration": ["start_index", "duration"],
                "AudioAdjustVolume": ["volume"],
                "EmptyAudio": ["duration", "sample_rate", "channels"],
                "AudioEqualizer3Band": [
                    "low_gain_dB", "low_freq", "mid_gain_dB", "mid_freq",
                    "mid_q", "high_gain_dB", "high_freq",
                ],
                "StringSubstring": ["start", "end"],
                "RegexExtract": ["group_index"],
            }.items()
            if (missing := [name for name in required if name not in widgets_by_type[node_type]])
        }
        contract_failures: dict[str, str] = {}

        concat_front = runner.execute(
            "AudioConcat", widgets=["before"], inputs={"audio1": audio, "audio2": audio_b},
        ).outputs[0].audio
        concat_back = runner.execute(
            "AudioConcat", widgets=["after"], inputs={"audio1": audio, "audio2": audio_b},
        ).outputs[0].audio
        if np.array_equal(concat_front, concat_back):
            contract_failures["AudioConcat.direction"] = "before/after widgets produce identical output"

        merge_add = runner.execute(
            "AudioMerge", widgets=["add"], inputs={"audio1": audio, "audio2": audio_b},
        ).outputs[0].audio
        merge_subtract = runner.execute(
            "AudioMerge", widgets=["subtract"], inputs={"audio1": audio, "audio2": audio_b},
        ).outputs[0].audio
        if np.array_equal(merge_add, merge_subtract):
            contract_failures["AudioMerge.merge_method"] = "add/subtract widgets produce identical output"

        trim_left = runner.execute(
            "StringTrim", widgets=["", "Left"], inputs={"string": StringArtifact("  value  ")},
        ).outputs[0].value
        if trim_left != "value  ":
            contract_failures["StringTrim.mode"] = f"left produced {trim_left!r}"

        lower = runner.execute(
            "CaseConverter", widgets=["", "lowercase"], inputs={"string": StringArtifact("MiXeD")},
        ).outputs[0].value
        if lower != "mixed":
            contract_failures["CaseConverter.mode"] = f"lower produced {lower!r}"

        all_matches = runner.execute(
            "RegexExtract",
            widgets=["", "", "All Matches", 1],
            inputs={"string": StringArtifact("a1 b2"), "regex_pattern": StringArtifact(r"\d")},
        ).outputs[0].value
        if all_matches != "1\n2":
            contract_failures["RegexExtract.mode"] = f"all produced {all_matches!r}"

        runner.ctx.audio_params["mp3_bit_rate"] = "320k"
        with mock.patch("pymss.audio_io.save_audio") as mocked_save:
            runner.execute(
                "SaveAudioMP3",
                widgets=["native/probe-mp3", "128k"],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/probe-mp3")},
            )
        saved_params = mocked_save.call_args.args[4]
        if saved_params.get("mp3_bit_rate") != "128k":
            contract_failures["SaveAudioMP3.quality"] = (
                f"128k widget used {saved_params.get('mp3_bit_rate')!r}"
            )

        with mock.patch("pymss.audio_io.save_audio") as mocked_save:
            runner.execute(
                "SaveAudioOpus",
                widgets=["native/probe-opus", "96k"],
                inputs={"audio": audio, "filename_prefix": StringArtifact("native/probe-opus")},
            )
        opus_params = mocked_save.call_args.args[4]
        if opus_params.get("opus_bit_rate") != "96k":
            contract_failures["SaveAudioOpus.bitrate"] = (
                f"96k widget used {opus_params.get('opus_bit_rate')!r}"
            )

        runner.ctx.download = True
        runner.separator_calls.clear()
        runner.cache.close()
        runner.execute(
            "mss_separate",
            widgets=["fake-options.ckpt", "cpu", False, "huggingface", "0", False],
            inputs={"audio": audio, "params": mss_artifact},
            outputs=stem_outputs,
        )
        separator_options = runner.separator_calls[-1]
        if separator_options.get("download") is not False:
            contract_failures["mss_separate.download_missing"] = (
                f"false widget produced download={separator_options.get('download')!r}"
            )
        if separator_options.get("source") != "huggingface":
            contract_failures["mss_separate.source"] = (
                f"huggingface widget produced source={separator_options.get('source')!r}"
            )
        runner.ctx.download = False

        print(f"Executed successfully: {len(runner.executed)}/{len(PALETTE_NODE_TYPES)}")
        for node_type in sorted(runner.executed):
            print(f"  PASS {node_type}")
        for node_type, failure in sorted(runner.failures.items()):
            print(f"  FAIL {node_type}: {failure}")
        if missing_controls:
            print("Missing editor controls:")
            for node_type, fields in sorted(missing_controls.items()):
                print(f"  {node_type}: {', '.join(fields)}")
        if contract_failures:
            print("Editor/runtime contract failures:")
            for key, failure in sorted(contract_failures.items()):
                print(f"  {key}: {failure}")
        unexpected_contract_failures = sorted(
            set(contract_failures) - KNOWN_CORE_CONTRACT_FAILURES
        )
        missing_known_failures = sorted(
            KNOWN_CORE_CONTRACT_FAILURES - set(contract_failures)
        )
        if missing_known_failures:
            print("Known core contracts now passing:")
            for key in missing_known_failures:
                print(f"  {key}")
        if unexpected_contract_failures:
            print("Unexpected contract failures:")
            for key in unexpected_contract_failures:
                print(f"  {key}")
        if missing_execution:
            print(f"Not executed: {', '.join(missing_execution)}")
        if unexpected_execution:
            print(f"Unexpected node types: {', '.join(unexpected_execution)}")

        runner.close()
        return 1 if (
            runner.failures
            or missing_controls
            or unexpected_contract_failures
            or (args.strict_contracts and contract_failures)
            or missing_execution
            or unexpected_execution
        ) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
