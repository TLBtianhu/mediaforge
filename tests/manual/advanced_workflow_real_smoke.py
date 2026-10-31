"""Run one real CUDA advanced workflow through the Studio worker boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def workflow(model_name: str) -> dict:
    return {
        "last_node_id": 5,
        "last_link_id": 4,
        "version": 0.4,
        "nodes": [
            {
                "id": 1,
                "type": "input_audio",
                "inputs": [],
                "outputs": [{"name": "audio", "type": "AUDIO", "links": [1]}],
                "widgets_values": [],
            },
            {
                "id": 2,
                "type": "pymss_mss_params",
                "inputs": [],
                "outputs": [{"name": "mss_params", "type": "PYMSS_MSS_PARAMS", "links": [2]}],
                "widgets_values": [1, "Default", "Default", False, False, False],
            },
            {
                "id": 3,
                "type": "mss_separate",
                "inputs": [
                    {"name": "audio", "type": "AUDIO", "link": 1},
                    {"name": "params", "type": "PYMSS_MSS_PARAMS", "link": 2},
                ],
                "outputs": [
                    {"name": "Vocals (Audio)", "type": "AUDIO", "links": [3]},
                    {"name": "Vocals (String)", "type": "STRING", "links": None},
                    {"name": "Instrumental (Audio)", "type": "AUDIO", "links": [4]},
                    {"name": "Instrumental (String)", "type": "STRING", "links": None},
                ],
                "widgets_values": [model_name, "cuda", False, "modelscope", "0", False],
            },
            {
                "id": 4,
                "type": "pymss_save_audio",
                "inputs": [{"name": "audio", "type": "AUDIO", "link": 3}],
                "outputs": [],
                "widgets_values": ["wav", "44100", "FLOAT", "PCM_24", "320k"],
            },
            {
                "id": 5,
                "type": "pymss_save_audio",
                "inputs": [{"name": "audio", "type": "AUDIO", "link": 4}],
                "outputs": [],
                "widgets_values": ["wav", "44100", "FLOAT", "PCM_24", "320k"],
            },
        ],
        "links": [
            [1, 1, 0, 3, 0, "AUDIO"],
            [2, 2, 0, 3, 1, "PYMSS_MSS_PARAMS"],
            [3, 3, 0, 4, 0, "AUDIO"],
            [4, 3, 2, 5, 0, "AUDIO"],
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model", default="melband_roformer_instvox_duality_v2.ckpt")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root / "python"))
    from core.worker_workflows import _run_pymss

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "taskId": "advanced-workflow-real-smoke",
        "workflowName": "Advanced workflow real smoke",
        "workflow": workflow(args.model),
        "input": str(input_path),
        "output": str(output_dir),
        "outputLayout": "flat",
        "outputFormat": "wav",
        "modelDir": str(Path(args.model_dir).resolve()),
        "device": "cuda",
        "downloadMethod": "never",
        "source": "modelscope",
        "audioParams": {"wav_bit_depth": "FLOAT"},
    }
    result = _run_pymss(
        payload,
        payload["taskId"],
        input_path=str(input_path),
        inputs=None,
        output_dir=str(output_dir),
        output_layout="flat",
    )
    outputs = result.get("outputs") or []
    stems = {str(item.get("stem") or "").lower() for item in outputs}
    expected = {"vocals", "instrumental"}
    missing = expected - stems
    missing_files = [item.get("path") for item in outputs if not Path(str(item.get("path") or "")).is_file()]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if missing or missing_files:
        print(f"missing_stems={sorted(missing)} missing_files={missing_files}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
