"""Verify a packaged bootstrap and optional tiny CPU inference without downloads."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from media_smoke_env import isolated_environment

ROOT = Path(__file__).resolve().parents[1]


def run(python: Path, args: list[str], data: Path, models: Path | None = None) -> tuple[subprocess.CompletedProcess, list]:
    env = isolated_environment(data, python, models)
    for key in list(env):
        if re.search(r"KEY|PASSWORD|TOKEN|SECRET", key, re.I) or key.startswith(("TAURI_", "VERSIONS_SIGNING_", "SIGNPATH_")):
            env.pop(key, None)
    process = subprocess.run([str(python), "-B", *args], cwd=data, env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=180)
    events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
    if process.returncode or any(event.get("type") == "error" for event in events):
        failures = [event for event in events if event.get("type") in {"error", "task.result"}]
        raise AssertionError(f"Packaged worker failed (exit {process.returncode}): {process.stderr[-2000:]} {failures or events[-2:]}")
    return process, events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve(strict=True)
    if not stage.is_relative_to((ROOT / "release").resolve()):
        raise ValueError("Use a packaged release stage")
    if bool(args.runtime_python) != bool(args.model_dir):
        raise ValueError("Inference requires both explicit interpreter and model root")
    worker = stage / "python/worker.py"
    python = stage / "python-runtime/python.exe"
    report = {"stage": str(stage), "checks": [], "guiLaunched": False}
    with tempfile.TemporaryDirectory(prefix="packaged-worker-", dir=ROOT / ".tmp") as temporary:
        data = Path(temporary)
        for command in ("health", "env_info", "list_models"):
            process, events = run(python, [str(worker), command], data)
            report["checks"].append({"command": command, "exitCode": process.returncode,
                                     "eventTypes": [event.get("type") for event in events]})
            if command == "env_info":
                info = next(event["payload"] for event in events if event["type"] == "env_info")
                assert info["pythonVersion"].startswith("3.12.") and info["runtimeMatchesManifest"]
                report["bootstrapPython"] = info["pythonVersion"]
        if args.runtime_python:
            runtime = args.runtime_python.resolve(strict=True)
            models = args.model_dir.resolve(strict=True)
            model = models / "media/RealESRGAN_x2plus.pth"
            before = (model.stat().st_size, model.stat().st_mtime_ns)
            code = """import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).parent))
def no_network(event,args):
    if event in {'socket.connect','socket.sendto'}: raise RuntimeError('Model smoke forbids network access')
sys.addaudithook(no_network)
import torch
torch.set_num_threads(4)
from PIL import Image
import worker
root=Path.cwd()
source=root/'synthetic.png'
image=Image.new('RGB',(32,32))
image.putdata([(x*8,y*8,(x+y)*4) for y in range(32) for x in range(32)])
image.save(source)
step={'capability':'upscale','bindings':{'upscaler':'media:real-esrgan-x2'},'params':{'scale':2,'target':'none','fit':'contain','tile':64}}
payload={'operation':'media_workflow','modality':'image','inputs':[str(source)],'outputDir':str(root/'outputs'),'modelDir':sys.argv[2],'device':'cpu','steps':[copy.deepcopy(step),copy.deepcopy(step)]}
initial=copy.deepcopy(payload)
single={**payload,'operation':'media_image','outputDir':str(root/'single-output'),'steps':copy.deepcopy(payload['steps'][:1])}
assert worker.run_command('media_tool',single)==0
single_outputs=list((root/'single-output').glob('*.png'))
assert len(single_outputs)==1 and Image.open(single_outputs[0]).size==(64,64)
status=worker.run_command('media_tool',payload)
assert payload==initial,'Execution changed the submitted plan'
print(json.dumps({'type':'smoke.snapshot','payload':{'unchanged':True}}))
raise SystemExit(status)
"""
            process, events = run(runtime, ["-c", code, str(worker), str(models)], data, models)
            terminal = next(event["payload"] for event in reversed(events) if event["type"] == "task.result")
            assert terminal["completed"] == 1 and not terminal["failed"]
            output = Path(terminal["outputs"][0]["path"])
            # Read dimensions through the same explicit runtime, without importing GUI libraries here.
            _, sizes = run(runtime, ["-c", "import json,sys; from PIL import Image; print(json.dumps({'type':'size','payload':list(Image.open(sys.argv[1]).size)}))", str(output)], data, models)
            assert sizes[-1]["payload"] == [128, 128]
            assert (model.stat().st_size, model.stat().st_mtime_ns) == before
            report["inference"] = {"python": str(runtime), "device": "cpu", "steps": 2,
                                    "inputSize": [32, 32], "outputSize": [128, 128],
                                    "singleStepOutputSize": [64, 64],
                                    "outputBytes": output.stat().st_size, "modelUnchanged": True,
                                    "network": "blocked by audit hook"}
        report["temporaryRoot"] = str(data)
    report.update(status="passed", workRootRemoved=not data.exists())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
