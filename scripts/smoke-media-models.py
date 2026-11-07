"""Run available media models through the real worker with explicit test assets.

Required: --python <test-python> --model-dir <test-models> --data-root <isolated-data>.
Inputs are synthetic unless --input is explicit; outputs are temporary unless
--output-dir is explicit. Existing user/dogfood profiles are never discovered.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from media_smoke_env import add_arguments, isolated_environment, model_paths, test_path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "python" / "worker.py"


def sample_image(outputs: Path, source: Path | None = None) -> Path:
    """Generate a dependency-free PNG, or copy only the explicitly chosen input."""
    outputs.mkdir(parents=True, exist_ok=True)
    if source is not None:
        target = outputs / f"sample{source.suffix}"
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
        return target
    width = height = 256
    pixels = b"".join(b"\0" + bytes(channel for x in range(width) for channel in (x, y, (x + y) % 256)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    target = outputs / "sample.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
    return target


def run(payload: dict, *, python: Path, models: Path, env: dict[str, str]) -> tuple[bool, str]:
    request = {**payload, "modelDir": str(models), "downloadMethod": "never"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8", dir=env["TMPDIR"]) as handle:
        json.dump(request, handle, ensure_ascii=False)
        path = Path(handle.name)
    try:
        proc = subprocess.run(
            [str(python), "-B", str(WORKER), "media_tool", "--payload", str(path)],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=900, env=env,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or f"worker exit {proc.returncode}")[-120:]
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        if not lines:
            return False, (proc.stderr or proc.stdout or "No worker output")[:120]
        event = json.loads(lines[-1])
        body = event.get("payload") or {}
        if event.get("type") == "error":
            return False, str(body.get("message"))[:120]
        outputs = body.get("outputs") or []
        failed = body.get("failed") or []
        if failed:
            return False, str(failed[0].get("error"))[:120]
        if not outputs:
            return False, "No output files"
        produced = Path(outputs[0]["path"])
        if not produced.is_file():
            return False, "Output file is missing"
        if produced.suffix.lower() == ".json":
            boxes = json.loads(produced.read_text(encoding="utf-8")).get("boxes") or []
            return True, f"{produced.name} ({len(boxes)} boxes)"
        if produced.stat().st_size < 512:
            return False, "Output file is too small"
        return True, f"{produced.name} ({produced.stat().st_size // 1024} KB)"
    finally:
        path.unlink(missing_ok=True)


def verify_models(python: Path, models: Path, outputs: Path, source: Path | None, env: dict[str, str]) -> int:
    catalog = json.loads((ROOT / "python/media_ops/media_catalog.json").read_text(encoding="utf-8"))
    available = [item for item in catalog["models"] if item.get("status") == "available"]
    sample = sample_image(outputs, source)
    results = []
    image_tasks = {"super_resolution", "denoise", "depth", "matting", "object_detection", "frame_interpolation"}
    declarations = None
    for item in available:
        model_id = f"media:{item['id']}"
        task = item.get("task")
        if task not in image_tasks:
            # The former watermark combo implicitly selected two models and no
            # longer satisfies the single-model task contract. Do not run it here.
            results.append((item["id"], "skip", f"Requires a separate adapter/workflow smoke: {task}"))
            continue
        if declarations is None:
            declarations = json.loads((ROOT / "python/media_ops/capabilities.json").read_text(encoding="utf-8"))["capabilities"]
        candidates = [(cap, next(iter(cap["roles"]))) for cap in declarations
                      if "image" in cap["modalities"] and model_id in cap["modelIds"] and len(cap["roles"]) == 1
                      and next(iter(cap["roles"].values())).get("task") == task]
        if len(candidates) != 1:
            raise ValueError(f"Expected one image capability/binding for {model_id}")
        capability, role = candidates[0]
        payload = {"operation": "media_image", "inputs": [str(sample)], "outputDir": str(outputs / item["id"]),
                   "steps": [{"capability": capability["id"], "bindings": {role: model_id}, "params": {}}]}
        if task == "frame_interpolation":
            payload["inputs"] = [str(sample), str(sample)]
        ok, detail = run(payload, python=python, models=models, env=env)
        results.append((item["id"], "pass" if ok else "FAIL", detail))
    width = max((len(row[0]) for row in results), default=1)
    for model_id, status, detail in results:
        print(f"  {model_id.ljust(width)}  {status:5}  {detail}")
    failures = sum(row[1] == "FAIL" for row in results)
    passed = sum(row[1] == "pass" for row in results)
    print(f"Total {len(results)}: passed {passed}, failed {failures}, skipped {len(results) - passed - failures}")
    return 1 if failures or not passed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        python, models, data = model_paths(args)
        source = test_path(args.input, "--input") if args.input else None
        if source is not None and not source.is_file():
            raise ValueError(f"sample image does not exist: {source}")
        output = test_path(args.output_dir, "--output-dir") if args.output_dir else None
        if output is not None and (output == models or models in output.parents):
            raise ValueError("--output-dir must be separate from the model directory")
    except ValueError as error:
        parser.error(str(error))
    with tempfile.TemporaryDirectory(prefix="mediaforge-model-smoke-") as temporary:
        outputs = output or Path(temporary) / "outputs"
        env = isolated_environment(data, python, models, outputs)
        return verify_models(python, models, outputs, source, env)


if __name__ == "__main__":
    raise SystemExit(main())
