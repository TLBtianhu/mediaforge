"""架构不变量检查（docs/architecture-blueprint.md §3）。

现在守四条：
  1. 引擎层（python/media_ops）不许 import app 层、音频层或 pymss；
  2. media_ops 里出现的事件名必须在 manifest/protocol.json 里声明；
  3. protocol.json 声明的 handler 必须真的存在于 worker 分发里；
  4. capabilities 引用的每个模型都在 catalog 里，且有 size / url / licenseSpdx。

退出码非 0 即视为不变量被破坏。放进 CI 与 check-command-surface.py 并列。
"""
from __future__ import annotations

import ast
import sys

# GitHub 的 Windows runner 控制台默认是 cp1252；直接打印 ✓/中文会抛
# UnicodeEncodeError，把"检查失败"伪装成"脚本崩了"。先固定输出编码。
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "python"
ENGINE = PY / "media_ops"

# 引擎允许依赖的东西：标准库、第三方推理库、引擎自己的子模块。
FORBIDDEN_IMPORTS = {
    "pymss",
    "audio_tools",
    "worker_audio",
    "worker_tools",
    "worker_models",
    "worker_bootstrap",
    "agent_bridge",
    "agent_contract",
    "embedded_loop",
    "media_eval",
    "app_core",
    "core",
    "surfaces",
}

# 引擎允许依赖的共享原语（shared/ 不依赖任何层）
SHARED_IMPORTS = {"shared"}


def python_files(directory: Path) -> list[Path]:
    return [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]


def check_import_direction() -> list[str]:
    problems: list[str] = []
    for path in python_files(ENGINE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} 引擎层 import 了 {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                module = node.module.split(".")[0]
                if module in FORBIDDEN_IMPORTS:
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno} 引擎层 import 了 {node.module}")
    return problems


def load_manifest() -> dict:
    return json.loads((ROOT / "manifest" / "protocol.json").read_text(encoding="utf-8"))


def declared_events(manifest: dict) -> set[str]:
    names: set[str] = set()
    for domain in (manifest.get("domains") or {}).values():
        for legacy in (domain.get("events") or {}).values():
            names.update(legacy)
    return names


def check_shared_direction() -> list[str]:
    """shared/ 是跨层原语：只能依赖标准库与自身，不能反向依赖任何层。"""
    problems: list[str] = []
    for path in python_files(PY / "shared"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in {"media_ops", "app_core", "core", "surfaces"}:
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} shared 层 import 了 {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                module = node.module.split(".")[0]
                if module in {"media_ops", "app_core", "core", "surfaces"}:
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno} shared 层 import 了 {node.module}")
    return problems


def check_event_coverage(manifest: dict) -> list[str]:
    declared = declared_events(manifest)
    problems: list[str] = []
    pattern = re.compile(r"""emit\(\s*["']([a-z_]+)["']""")
    for path in python_files(ENGINE):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in declared:
                line = text[: match.start()].count("\n") + 1
                problems.append(
                    f"{path.relative_to(ROOT)}:{line} 发出未声明的事件 {name}（先写进 protocol.json）"
                )
    return problems


def check_handlers_exist(manifest: dict) -> list[str]:
    worker = (PY / "worker.py").read_text(encoding="utf-8")
    problems: list[str] = []
    for domain_id, domain in (manifest.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            handler = str(entry.get("handler") or "")
            if not handler:
                problems.append(f"protocol: {domain_id}.{name} 没有 handler")
            elif f'"{handler}"' not in worker:
                problems.append(f"protocol: {domain_id}.{name} 的 handler {handler} 在 worker.py 里找不到")
    return problems


def check_catalog_links() -> list[str]:
    capabilities = json.loads((ROOT / "python" / "media_ops" / "capabilities.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "python" / "media_ops" / "media_catalog.json").read_text(encoding="utf-8"))
    models = {item["id"]: item for item in catalog["models"]}
    problems: list[str] = []
    for capability in capabilities.get("capabilities") or []:
        for model_id in capability.get("modelIds") or []:
            short = model_id.split(":", 1)[-1]
            item = models.get(short)
            if item is None:
                problems.append(f"{capability['id']} 引用了 catalog 里不存在的模型 {model_id}")
                continue
            if item.get("status") == "available":
                for field in ("size", "url", "licenseSpdx"):
                    if not item.get(field):
                        problems.append(f"{short} 已可用但缺少 {field}（资产信息必须可核验）")
            elif not item.get("blockedReason"):
                problems.append(f"{short} 未可用但没有 blockedReason（要么写明卡点，要么删掉）")
    return problems


def main() -> int:
    manifest = load_manifest()
    results = {
        "引擎依赖方向": check_import_direction(),
        "shared 依赖方向": check_shared_direction(),
        "事件覆盖": check_event_coverage(manifest),
        "handler 存在性": check_handlers_exist(manifest),
        "catalog 关联": check_catalog_links(),
    }
    failed = False
    for label, problems in results.items():
        if problems:
            failed = True
            print(f"  [FAIL] {label}（{len(problems)}）")
            for problem in problems[:10]:
                print(f"      {problem}")
            if len(problems) > 10:
                print(f"      …… 还有 {len(problems) - 10} 条")
        else:
            print(f"  [ok] {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
