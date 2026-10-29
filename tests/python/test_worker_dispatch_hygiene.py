"""worker 分派层的卫生检查：局部导入不许遮蔽模块级名字。

2026-10-29 真实调用踩到的坑：`media_precheck` 分支里写了
`from worker_protocol import emit`，于是 `emit` 在整个 `run_command` 里变成**局部变量**，
其它分支调用 emit 时抛 `UnboundLocalError: cannot access local variable 'emit'`。
单元测试全部直接调用 `cmd_*`，所以一路绿灯——只有从分派入口进来才炸。

这组测试盯两件事：
* `run_command` 里不再出现遮蔽模块级名字的局部导入
* 从分派入口真的能跑通（用一个必定 emit 的命令验证）
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

WORKER = PYTHON_DIR / "worker.py"


def module_level_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


class ShadowingTests(unittest.TestCase):
    def test_no_function_level_import_shadows_a_module_import(self) -> None:
        tree = ast.parse(WORKER.read_text(encoding="utf-8"))
        module_names = module_level_imports(tree)
        problems: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    names = [alias.asname or alias.name for alias in child.names]
                elif isinstance(child, ast.Import):
                    names = [(alias.asname or alias.name).split(".")[0] for alias in child.names]
                else:
                    continue
                shadowed = sorted(set(names) & module_names)
                if shadowed:
                    problems.append(f"{node.name}: {', '.join(shadowed)}")
                # 嵌套函数会重复遍历，去重即可
        self.assertEqual(sorted(set(problems)), [], f"局部导入遮蔽了模块级名字：{sorted(set(problems))}")


class DispatchSmokeTests(unittest.TestCase):
    """从分派入口（而不是 cmd_* 直调）跑一遍，才能发现作用域问题。"""

    def _run(self, command: str, payload: dict | None = None) -> list[dict]:
        import worker
        from worker_protocol import set_event_sink

        events: list[dict] = []
        set_event_sink(lambda kind, data, request_id, task_id: events.append({"type": kind, "payload": data}))
        try:
            worker.run_command(command, payload or {})
        finally:
            set_event_sink(None)
        return events

    def test_media_capabilities_emits_through_dispatch(self) -> None:
        events = self._run("media_capabilities", {"modelDir": None})
        self.assertTrue(events, "media_capabilities 必须产生事件")
        self.assertEqual(events[-1]["type"], "media_capabilities_result")

    def test_failure_stats_emits_through_dispatch(self) -> None:
        events = self._run("failure_stats")
        self.assertEqual(events[-1]["type"], "failure_stats")

    def test_media_precheck_emits_through_dispatch(self) -> None:
        events = self._run("media_precheck", {"steps": [], "inputs": []})
        self.assertEqual(events[-1]["type"], "media_precheck_result")


if __name__ == "__main__":
    unittest.main()


def function_locals(node: ast.AST) -> set[str]:
    """函数内"有定义"的名字：参数、赋值、导入、推导式/循环/with/except 目标。"""
    names: set[str] = set()
    args = getattr(node, "args", None)
    if isinstance(args, ast.arguments):
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            names.add(arg.arg)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.add(child.id)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
            names.add(child.name)
    return names


def find_unbound(source: str, label: str, watched: tuple[str, ...]) -> list[str]:
    """找出"在函数里被读取、但该作用域与模块都没有定义"的名字。"""
    tree = ast.parse(source)
    global_names = module_level_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for inner in ast.walk(target):
                    if isinstance(inner, ast.Name):
                        global_names.add(inner.id)
        elif isinstance(node, ast.AnnAssign):
            for inner in ast.walk(node.target):
                if isinstance(inner, ast.Name):
                    global_names.add(inner.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            global_names.add(node.name)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        declared_global = {
            name
            for inner in ast.walk(node)
            if isinstance(inner, ast.Global)
            for name in inner.names
        }
        locals_ = function_locals(node) | declared_global
        for child in ast.walk(node):
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            if child.id not in watched:
                continue
            if child.id in locals_ or child.id in global_names:
                continue
            problems.append(f"{label}:{node.name} 使用了未定义的名字 {child.id}")
    return problems


class UnboundNameTests(unittest.TestCase):
    """函数里引用了自己作用域没有的名字——`payload` 就是这么炸过一次。

    2026-10-29：`workflow_worker._run_image` 里写了 `payload.get("device")`，
    但该函数没有 payload 参数，真实调用才抛 NameError。单测都是直接调别的函数，全绿。
    """

    WATCHED = ("payload",)

    @staticmethod
    def _problems_in(source: str, label: str) -> list[str]:
        return find_unbound(source, label, UnboundNameTests.WATCHED)

    def test_the_checker_itself_catches_a_missing_payload(self) -> None:
        """守卫要能抓到真 bug，否则它只是装饰。"""
        bad = """
def _run_image(source):
    restorer = build(device=payload.get("device"))
    return restorer
"""
        self.assertEqual(len(self._problems_in(bad, "bad.py")), 1)

    def test_the_checker_accepts_a_bound_payload(self) -> None:
        good = """
def _run_image(source, payload):
    return build(device=payload.get("device"))
"""
        self.assertEqual(self._problems_in(good, "good.py"), [])

    def test_watched_names_are_bound_wherever_they_are_used(self) -> None:
        modules = [
            *sorted((PYTHON_DIR / "media_ops").glob("*.py")),
            *sorted(PYTHON_DIR.glob("worker*.py")),
            PYTHON_DIR / "agent_contract.py",
            PYTHON_DIR / "agent_bridge.py",
            PYTHON_DIR / "embedded_loop.py",
            PYTHON_DIR / "retry_policy.py",
        ]
        problems: list[str] = []
        for module in modules:
            if not module.is_file():
                continue
            problems.extend(
                find_unbound(
                    module.read_text(encoding="utf-8"),
                    module.name,
                    UnboundNameTests.WATCHED,
                )
            )
        self.assertEqual(sorted(set(problems)), [], "这些函数引用了作用域里没有的名字：")
