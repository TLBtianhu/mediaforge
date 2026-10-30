"""逐个跑一遍 catalog 里 available 的模型，确认"标着可用就能跑出东西"。

走真实入口（worker.py audio_tools），不是直接调函数——这样连路由、模式、
payload 形状都一起验证。任何一项失败就以非零退出，便于放进 CI。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path(os.environ.get("MEDIAFORGE_RUNTIME_PYTHON") or (Path(os.environ["USERPROFILE"]) / ".mediaforge" / "runtime-envs" / "cuda" / "Scripts" / "python.exe"))
WORKER = ROOT / "python" / "worker.py"
MODELS = Path(os.environ["USERPROFILE"]) / ".mediaforge" / "models" / "media"
OUTPUTS = Path(os.environ["USERPROFILE"]) / ".mediaforge" / "outputs" / "smoke"


def sample_image() -> Path:
    """用一张真实素材的小裁剪，避免每次跑整张 4K。"""
    sys.path.insert(0, str(ROOT / "python"))
    from media_ops import image_ops

    source = None
    for candidate in (Path(os.environ["USERPROFILE"]) / ".mediaforge" / "outputs").glob("*.png"):
        source = candidate
        break
    if source is None:
        raise SystemExit("没找到可用的样例图片")
    image = image_ops.read_image(source)[:256, :256]
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    target = OUTPUTS / "sample.png"
    image_ops.write_image(target, image)
    return target


def run(payload: dict) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        path = handle.name
    try:
        proc = subprocess.run(
            [str(PYTHON), str(WORKER), "audio_tools", "--payload", path],
            cwd=str(ROOT), capture_output=True, text=True, timeout=900,
        )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        if not lines:
            return False, (proc.stderr or proc.stdout or "无输出")[:120]
        event = json.loads(lines[-1])
        body = event.get("payload") or {}
        if event.get("type") == "error":
            return False, str(body.get("message"))[:120]
        outputs = body.get("outputs") or []
        failed = body.get("failed") or []
        if failed:
            return False, str(failed[0].get("error"))[:120]
        if not outputs:
            return False, "没有输出文件"
        produced = Path(outputs[0]["path"])
        if not produced.exists():
            return False, "输出文件缺失"
        # 检测类输出是 JSON：一张没有目标的图也会合法地得到空框列表，不能按体积判失败。
        if produced.suffix.lower() == ".json":
            try:
                body = json.loads(produced.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                return False, f"JSON 无法解析: {exc}"
            return True, f"{produced.name} ({len(body.get('boxes') or [])} 个框)"
        if produced.stat().st_size < 512:
            return False, "输出文件过小"
        return True, f"{produced.name} ({produced.stat().st_size // 1024} KB)"
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> int:
    catalog = json.loads((ROOT / "python" / "media_ops" / "media_catalog.json").read_text(encoding="utf-8"))
    available = [m for m in catalog["models"] if m.get("status") == "available"]
    sample = sample_image()
    OUTPUTS.mkdir(parents=True, exist_ok=True)

    results = []
    for item in available:
        model_id = f"media:{item['id']}"
        task = item.get("task")
        target_dir = OUTPUTS / item["id"]
        payload = {"operation": "media_image", "inputs": [str(sample)], "outputDir": str(target_dir), "modelId": model_id}
        if task == "super_resolution":
            payload["mode"] = "upscale"
            payload["steps"] = [{"capability": "upscale", "bindings": {"upscaler": model_id}, "params": {"scale": 4}}]
            payload.pop("modelId")
        elif task == "denoise":
            payload["mode"] = "denoise"
        elif task == "depth":
            payload["mode"] = "depth"
        elif task == "matting":
            payload["mode"] = "matting"
        elif task == "object_detection":
            payload["mode"] = "detect"
        elif task == "frame_interpolation":
            payload["mode"] = "interpolate"
            payload["inputs"] = [str(sample), str(sample)]
        elif task == "inpainting":
            results.append((item["id"], "skip", "需要遮罩，单独验证"))
            continue
        elif task == "watermark_detection":
            results.append((item["id"], "skip", "属于组合流程，单独验证"))
            continue
        else:
            results.append((item["id"], "skip", f"未知任务 {task}"))
            continue
        ok, detail = run(payload)
        results.append((item["id"], "pass" if ok else "FAIL", detail))

    width = max(len(r[0]) for r in results)
    failures = 0
    for model_id, status, detail in results:
        if status == "FAIL":
            failures += 1
        print(f"  {model_id.ljust(width)}  {status:5}  {detail}")
    print(f"\n  合计 {len(results)}：通过 {sum(1 for r in results if r[1] == 'pass')} / "
          f"失败 {failures} / 跳过 {sum(1 for r in results if r[1] == 'skip')}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())