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
