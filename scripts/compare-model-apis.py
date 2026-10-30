"""模型系统统一的对账脚本。

统一前有两套模型 API：

  A  model_hub          334 条（音频 326 + 媒体 8），给模型库页面用
  B  list_models        326 条（音频），给音频/图像页的模型选择器用

统一方向是 A 当唯一真相，但 B 独有的字段（supported / modelPath / slots …）
是选择器和推理必需的。所以验收标准不是"能跑"，而是：

  对每个音频模型，B 有而 A 没有的**必需字段**数量必须为 0

用法：

    python scripts/compare-model-apis.py            # 打印差异并给出结论
    python scripts/compare-model-apis.py --strict   # 有缺失字段时退出码 1

实现上直接调 worker（不经过 UI），所以可以在 CI 或本地随时跑。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "python" / "worker.py"

# 选择器与推理真正用到的字段。B 必须有、A 统一后也必须给出来。
REQUIRED = (
    "supported",
    "downloaded",
    "modelPath",
    "configPath",
    "sizeBytes",
    "slots",
    "slotTask",
    "modelType",
)


def call_worker(command: str, payload: dict, python: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(payload, handle)
        path = handle.name
    try:
        result = subprocess.run(
            [python, str(WORKER), command, "--payload", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        Path(path).unlink(missing_ok=True)
    line = next((l for l in result.stdout.splitlines() if l.startswith("{")), "")
    if not line:
        raise SystemExit(f"{command} 没有输出；stderr: {result.stderr[:300]}")
    data = json.loads(line)
    if data.get("type") == "error":
        raise SystemExit(f"{command} 报错: {data['payload'].get('message')}")
    return data["payload"]


def key(model: dict) -> str:
    return str(model.get("name") or model.get("label") or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="用哪个解释器跑 worker")
    parser.add_argument("--strict", action="store_true", help="有缺失字段时返回非 0")
    parser.add_argument("--limit", type=int, default=3, help="打印多少个例子的明细")
    args = parser.parse_args(argv)

    a = call_worker("model_hub", {"modality": "all"}, args.python)
    b = call_worker(
        "list_models",
        {"category": None, "supportedOnly": False, "includeLocalState": True, "includeCustom": True},
        args.python,
    )

    a_models = {key(m): m for m in (a.get("models") or []) if key(m)}
    b_models = {key(m): m for m in (b.get("models") or []) if key(m)}
    print(f"A(model_hub)      : {len(a_models)} 条")
    print(f"B(list_models)    : {len(b_models)} 条")

    missing_in_a = sorted(set(b_models) - set(a_models))
    print(f"只存在于 B 的模型  : {len(missing_in_a)}")
    if missing_in_a[:5]:
        print("  " + ", ".join(missing_in_a[:5]))

    incomplete: list[tuple[str, list[str]]] = []
    for name in sorted(set(a_models) & set(b_models)):
        # 只在"B 有值、A 没有"时才算缺口：有的模型本来就没有 config 文件，
        # 两边都空是正常状态，不该记成未统一。
        absent = [
            field for field in REQUIRED
            if b_models[name].get(field) not in (None, "", [])
            and a_models[name].get(field) in (None, "", [])
        ]
        if absent:
            incomplete.append((name, absent))

    print(f"缺必需字段的条目  : {len(incomplete)} / {len(a_models) & 0 if False else len(set(a_models) & set(b_models))}")
    for name, absent in incomplete[: args.limit]:
        print(f"  {name}: 缺 {', '.join(absent)}")

    ok = not incomplete and not missing_in_a
    print()
    print("结论:", "统一完成，A 已覆盖 B 的必需字段" if ok else "还没统一：先补字段再切换前端")
    if args.strict and not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
