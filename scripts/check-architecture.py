"""架构不变量检查（docs/architecture-blueprint.md §3）。

现在守十条：
  1. 引擎层（python/media_ops）不许 import app 层、音频层或 pymss；
  2. shared 层不许 import core / surfaces / media_ops；
  3. core 层不许 import surfaces；
  4. python/ 根目录只许白名单文件；
  5. worker 各层出现的点号事件名必须在 manifest/protocol.json 里声明；
  6. 任何 emit() 都不许再用 task_* / download_* 这类历史事件名；
  7. protocol.json 声明的 handler 必须真的存在于 worker 分发里；
  8. capabilities 引用的每个模型都在 catalog 里，且有 size / url / licenseSpdx；
  9. model_registry.json 的音频接口投影必须能由 audio_catalog.json 无损重建；
  10. 每个可下载媒体模型都必须声明有效 SHA-256；
  11. Rust 里硬编码的 task.* / asset.* 事件名必须在 protocol.json 里声明，且 events.terminal 是 canonical 的子集；
  12. protocol.json.execution 的 bootstrap 清单必须对应真实命令（worker 项在 domains 里，native 项不与 worker 重叠）。

退出码非 0 即视为不变量被破坏。放进 CI 与 check-command-surface.py 并列。
"""
from __future__ import annotations

import ast
import importlib.util
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

from sync_model_registry import generate_registry

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "python"
RUST_SRC = ROOT / "src-tauri" / "src"
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
    return set((manifest.get("events") or {}).get("canonical") or [])


#: python/ 根目录只允许两个进程入口 + 数据文件；其余必须进包。
ROOT_ALLOWED = {"__init__.py", "worker.py", "agent_bridge.py"}
ROOT_DATA_SUFFIXES = {".json"}


def check_root_allowlist() -> list[str]:
    problems: list[str] = []
    for path in sorted(PY.glob("*")):
        if not path.is_file():
            continue
        if path.suffix in ROOT_DATA_SUFFIXES:
            continue
        if path.suffix == ".py" and path.name in ROOT_ALLOWED:
            continue
        if path.suffix == ".py":
            problems.append(f"python/{path.name} 不应留在根目录：入口进 surfaces/，服务进 core/")
    return problems


def check_core_direction() -> list[str]:
    """core 可以依赖 engine/shared，但不能依赖 surfaces（那是上层）。"""
    problems: list[str] = []
    for path in python_files(PY / "core"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module == "surfaces":
                        problems.append(f"{path.relative_to(ROOT)}:{node.lineno} core 层 import 了 surfaces")
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                module = node.module.split(".")[0]
                if module == "surfaces":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno} core 层 import 了 {node.module}")
    return problems


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
    for path in python_files(PY):
        if "third_party" in path.relative_to(PY).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "emit":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                continue
            name = node.args[0].value
            if "." in name and name not in declared:
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} 发出不是规范名的事件 {name}（只能发 events.canonical 里的名字）"
                )
    return problems


RUST_EVENT_LITERAL = re.compile(r'"(task\.[a-z0-9_.]+|asset\.[a-z0-9_.]+)"')


def rust_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.rs") if "target" not in path.parts)


def check_rust_event_literals(manifest: dict) -> list[str]:
    """Rust 的运行时事件词表必须来自协议，不能自成一套。"""
    events = manifest.get("events") or {}
    declared = set(events.get("canonical") or [])
    terminal = set(events.get("terminal") or [])
    problems: list[str] = []
    if not terminal <= declared:
        problems.append(f"protocol: events.terminal 不在 canonical 里：{sorted(terminal - declared)}")
    for path in rust_files(RUST_SRC):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            code = line.split("//", 1)[0]
            # Session-log categories (e.g. editor.playback) are diagnostics, not
            # the engine's stream vocabulary; they share the dotted style only.
            if "session_log::append" in code:
                continue
            for match in RUST_EVENT_LITERAL.finditer(code):
                name = match.group(1)
                if name not in declared:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line_number} 硬编码事件 {name} 不在 events.canonical"
                    )
    return problems


def audio_tool_handlers() -> set[str]:
    """Operation names the audio-tools registry actually dispatches (static parse)."""
    path = ROOT / "python" / "audio_tools" / "registry.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except OSError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_HANDLERS" for target in targets):
            continue
        return {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return set()


def check_execution_manifest(manifest: dict) -> list[str]:
    """执行清单也只有一个真源：bootstrap 选择不能靠 Rust 再写一份命令名单。"""
    commands = set()
    for domain in (manifest.get("domains") or {}).values():
        for entry in (domain.get("commands") or {}).values():
            name = entry.get("command")
            if isinstance(name, str) and name:
                commands.add(name)
    execution = manifest.get("execution") or {}
    bootstrap = execution.get("bootstrap") or {}
    worker = list(bootstrap.get("worker") or [])
    native = list(bootstrap.get("native") or [])
    problems: list[str] = []
    for name in worker:
        if name not in commands:
            problems.append(f"protocol: execution.bootstrap.worker 的 {name} 不是任何命令的 command")
    for label, items in (("worker", worker), ("native", native)):
        if len(set(items)) != len(items):
            problems.append(f"protocol: execution.bootstrap.{label} 有重复项")
    overlap = set(worker) & set(native)
    if overlap:
        problems.append(f"protocol: execution.bootstrap 的 worker/native 重叠：{sorted(overlap)}")
    if not worker:
        problems.append("protocol: execution.bootstrap.worker 为空，bootstrap 选择会退回猜测")
    known_bootstrap = commands | set(native)
    for label in ("withoutRuntime", "updatesRuntime"):
        names = list(bootstrap.get(label) or [])
        if len(set(names)) != len(names):
            problems.append(f"protocol: execution.bootstrap.{label} 有重复项")
        for name in names:
            if name not in known_bootstrap:
                problems.append(
                    f"protocol: execution.bootstrap.{label} 的 {name} 既不是命令也不是 native 项"
                )
    result_jobs = execution.get("resultJobs") or {}
    result_commands = list(result_jobs.get("commands") or [])
    result_operations = list(result_jobs.get("operations") or [])
    for name in result_commands:
        if name not in commands:
            problems.append(f"protocol: execution.resultJobs.commands 的 {name} 不是任何命令的 command")
        elif name in result_operations:
            problems.append(f"protocol: execution.resultJobs 的 {name} 同时被声明为命令和操作")
    for label, items in (("commands", result_commands), ("operations", result_operations)):
        if len(set(items)) != len(items):
            problems.append(f"protocol: execution.resultJobs.{label} 有重复项")
    output_scope = execution.get("outputScope") or {}
    for label in ("commands", "audioToolCommands"):
        for name in list(output_scope.get(label) or []):
            if name not in commands:
                problems.append(f"protocol: execution.outputScope.{label} 的 {name} 不是任何命令的 command")
    handlers = audio_tool_handlers()
    for name in list(output_scope.get("operations") or []):
        if name not in handlers:
            problems.append(f"protocol: execution.outputScope.operations 的 {name} 不在 audio_tools 注册表里")
    for name in list(execution.get("backgroundResult") or []):
        if name not in commands:
            problems.append(f"protocol: execution.backgroundResult 的 {name} 不是任何命令的 command")
    if result_operations:
        routing_path = ROOT / "python" / "core" / "worker_tools.py"
        routing = routing_path.read_text(encoding="utf-8") if routing_path.is_file() else ""
        for name in result_operations:
            if name in commands:
                problems.append(f"protocol: execution.resultJobs.operations 的 {name} 与 worker 命令同名")
            elif f'"{name}"' not in routing:
                problems.append(f"protocol: execution.resultJobs.operations 的 {name} 没有 media 路由")
    return problems


def check_handlers_exist(manifest: dict) -> list[str]:
    """handler 是 module:function：静态确认模块文件与函数定义都存在（不 import，避免副作用）。"""
    problems: list[str] = []
    for domain_id, domain in (manifest.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            handler = str(entry.get("handler") or "")
            if ":" not in handler:
                problems.append(f"protocol: {domain_id}.{name} 的 handler 不是 module:function")
                continue
            module_name, function_name = handler.split(":", 1)
            path = PY.joinpath(*module_name.split(".")).with_suffix(".py")
            if not path.is_file():
                problems.append(f"protocol: {domain_id}.{name} 的模块不存在：{module_name}")
                continue
            text = path.read_text(encoding="utf-8")
            if not re.search(rf"^def {re.escape(function_name)}\(", text, re.MULTILINE):
                problems.append(f"protocol: {domain_id}.{name} 的函数不存在：{handler}")
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
def check_registry_projection() -> list[str]:
    registry = json.loads((ROOT / "python" / "media_ops" / "model_registry.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "python" / "audio_catalog.json").read_text(encoding="utf-8"))["models"]
    try:
        generated = generate_registry(registry, catalog)
    except ValueError as error:
        return [str(error)]
    if generated != registry:
        return ["model_registry.json 音频投影已漂移：运行 python scripts/sync_model_registry.py 重建"]
    return []
def check_media_hashes() -> list[str]:
    path = ROOT / "scripts" / "sync-media-catalog-hashes.py"
    spec = importlib.util.spec_from_file_location("sync_media_catalog_hashes", path)
    if spec is None or spec.loader is None:
        return [f"无法加载媒体哈希检查器：{path}"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    catalog = json.loads((ROOT / "python" / "media_ops" / "media_catalog.json").read_text(encoding="utf-8"))
    return module.hash_problems(catalog)


LEGACY_EVENT_PREFIXES = ("task_", "download_")


def check_event_names() -> list[str]:
    """流式事件必须是 <域>.<动词>：task_* / download_* 是历史写法，出现即违规。

    只扫 emit() 的第一个字符串参数，命令结果型事件（models、env_info 等）不受影响。
    """
    problems: list[str] = []
    pattern = re.compile(r"""emit\(\s*["']([A-Za-z_][A-Za-z0-9_.]*)["']""")
    for path in python_files(PY):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            name = match.group(1)
            if name.startswith(LEGACY_EVENT_PREFIXES):
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT)}:{line} 事件名 {name} 是历史写法，应写成 task.* / asset.*")
    return problems


def main() -> int:
    manifest = load_manifest()
    results = {
        "引擎依赖方向": check_import_direction(),
        "shared 依赖方向": check_shared_direction(),
        "core 依赖方向": check_core_direction(),
        "根目录白名单": check_root_allowlist(),
        "事件覆盖": check_event_coverage(manifest),
        "事件命名": check_event_names(),
        "Rust 事件名": check_rust_event_literals(manifest),
        "执行清单": check_execution_manifest(manifest),
        "handler 存在性": check_handlers_exist(manifest),
        "catalog 关联": check_catalog_links(),
        "接口投影": check_registry_projection(),
        "媒体哈希": check_media_hashes(),
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
