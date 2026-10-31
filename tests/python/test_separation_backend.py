"""分离后端接口（`separation.py`）：让业务层不认识 pymss 的方法名。

背景：音频分离的**能力**在权重上，但"把权重跑起来"这件事原来由 pymss 的 `MSSeparator`
承担，而 `worker_infer` 直接调了它的 `process_folder` / `studio_outputs` / `del_cache`。
换句话说，业务流程的形状是 pymss 的形状。

现在业务层只认 `SeparationBackend` 的四个动作（prepare / separate / outputs / close），
pymss 的方法名收在 `PymssBackend` 一个类里。这组测试钉三件事：

① 适配器行为正确（含 `close()` 缺失时回退 `del_cache()`）；
② 假后端满足协议 —— 也就是"能换"是真的，不是口号；
③ **缝没有被缝合回去**：`separation.py` 不在模块级 import pymss/torch，
   `worker_infer.py` 不再出现 `process_folder` / `studio_outputs` 这两个方法名。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from core.separation import PymssBackend, SeparationBackend  # noqa: E402


class FakeSeparator:
    """形状与 `MSSeparator` 一致的最小替身。"""

    def __init__(self, files: list[str] | None = None, *, with_close: bool = True) -> None:
        self.files = files if files is not None else ["vocals.wav", "other.wav"]
        self.calls: list[tuple[str, object]] = []
        self.closed = False
        self.cache_dropped = False
        if with_close:
            self.close = self._close  # type: ignore[assignment]

    def process_folder(self, source, input_index=1):
        self.calls.append(("process_folder", (source, input_index)))
        return list(self.files)

    def studio_outputs(self):
        self.calls.append(("studio_outputs", None))
        return [{"path": name, "stem": name.split(".")[0]} for name in self.files]

    def _close(self):
        self.closed = True
        self.calls.append(("close", None))

    def del_cache(self):
        self.cache_dropped = True
        self.calls.append(("del_cache", None))


class FakeBackend:
    """另一个满足协议的后端：证明业务层不必认识 pymss。"""

    def __init__(self) -> None:
        self.prepared = 0
        self.closed = False

    def prepare(self) -> None:
        self.prepared += 1

    def separate(self, source: str, input_index: int = 1) -> list[str]:
        return [f"{Path(source).stem}_stem1.wav"]

    def outputs(self) -> list[dict]:
        return [{"path": "stem1.wav"}]

    def close(self) -> None:
        self.closed = True


class PymssBackendTests(unittest.TestCase):
    def test_factory_is_lazy_and_runs_once(self) -> None:
        created: list[FakeSeparator] = []

        def factory() -> FakeSeparator:
            separator = FakeSeparator()
            created.append(separator)
            return separator

        backend = PymssBackend(factory)
        self.assertEqual(created, [], "构造不应在 PymssBackend() 时发生")
        backend.prepare()
        backend.prepare()
        self.assertEqual(len(created), 1, "prepare 应幂等，只建一次")

    def test_separate_forwards_the_input_index_as_int(self) -> None:
        separator = FakeSeparator()
        backend = PymssBackend(lambda: separator)
        files = backend.separate("song.flac", "3")
        self.assertEqual(files, ["vocals.wav", "other.wav"])
        self.assertEqual(separator.calls[0], ("process_folder", ("song.flac", 3)))

    def test_outputs_are_returned_as_copies(self) -> None:
        separator = FakeSeparator()
        backend = PymssBackend(lambda: separator)
        backend.separate("song.flac")
        outputs = backend.outputs()
        outputs[0]["path"] = "tampered.wav"
        self.assertEqual(separator.studio_outputs()[0]["path"], "vocals.wav")

    def test_close_prefers_close_and_falls_back_to_del_cache(self) -> None:
        with_close = FakeSeparator()
        PymssBackend(lambda: with_close).close()  # 未 prepare 也能安全收尾
        self.assertFalse(with_close.closed, "没建出来就不该收尾")
        backend = PymssBackend(lambda: with_close)
        backend.prepare()
        backend.close()
        self.assertTrue(with_close.closed)

        legacy = FakeSeparator(with_close=False)
        legacy_backend = PymssBackend(lambda: legacy)
        legacy_backend.prepare()
        legacy_backend.close()
        self.assertTrue(legacy.cache_dropped, "没有 close() 的老版本应回退到 del_cache()")

    def test_close_is_idempotent_and_blocks_reuse(self) -> None:
        separator = FakeSeparator()
        backend = PymssBackend(lambda: separator)
        backend.prepare()
        backend.close()
        backend.close()
        with self.assertRaises(RuntimeError):
            backend.separate("song.flac")


class SubstitutabilityTests(unittest.TestCase):
    def test_both_backends_satisfy_the_protocol(self) -> None:
        self.assertIsInstance(PymssBackend(lambda: FakeSeparator()), SeparationBackend)
        self.assertIsInstance(FakeBackend(), SeparationBackend)

    def test_a_fake_backend_can_drive_a_business_shaped_flow(self) -> None:
        """模拟 worker_infer 的调用顺序：prepare → separate → outputs → close。"""
        backend = FakeBackend()
        backend.prepare()
        files = backend.separate("song.flac", 1)
        outputs = backend.outputs()
        backend.close()
        self.assertEqual(files, ["song_stem1.wav"])
        self.assertEqual(outputs[0]["path"], "stem1.wav")
        self.assertTrue(backend.closed)


class SeamTests(unittest.TestCase):
    def test_separation_module_does_not_import_pymss_or_torch(self) -> None:
        """`separation.py` 必须与 pymss/torch 解耦，靠注入工厂。"""
        tree = ast.parse((PYTHON_DIR / "core" / "separation.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("pymss", imported)
        self.assertNotIn("torch", imported)

    def test_command_handlers_no_longer_name_pymss_separator_methods(self) -> None:
        """缝的回归测试：两条业务流程只认 `backend.separate/outputs`。

        注意范围：`StudioMSSeparator` 子类仍然 `override process_folder`（那是改造上游行为，
        不是业务调用），所以这里只检查**命令处理器**这两个函数体的源码。
        """
        tree = ast.parse((PYTHON_DIR / "core" / "worker_infer.py").read_text(encoding="utf-8"))
        handlers = {
            node.name: ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in {"cmd_infer", "cmd_infer_batch"}
        }
        self.assertEqual(set(handlers), {"cmd_infer", "cmd_infer_batch"})
        for name, code in handlers.items():
            self.assertNotIn("process_folder", code, f"{name} 仍在直接调用 pymss 的方法")
            self.assertNotIn("studio_outputs", code, f"{name} 仍在直接调用 pymss 的方法")
        self.assertIn("backend.separate(", handlers["cmd_infer_batch"])
        self.assertIn("backend.separate(", handlers["cmd_infer"])

        separation = (PYTHON_DIR / "core" / "separation.py").read_text(encoding="utf-8")
        self.assertIn("process_folder", separation)
        self.assertIn("studio_outputs", separation)


if __name__ == "__main__":
    unittest.main()
