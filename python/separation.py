"""分离后端接口：业务层不该认识 pymss 的方法名。

`worker_infer` 的两条推理路径目前只用了三个动作：

    prepare()                      -> 把模型装起来（下载、加载、设备选择都算这一步）
    separate(来源, 第几个输入)      -> 成功的文件名列表
    outputs()                      -> 产物 [{path, ...}]
    （收尾）close()

`PymssBackend` 是现在唯一的实现，它把 pymss 形状的方法名
（`process_folder` / `studio_outputs` / `del_cache` / `close`）**收在这个文件里**。
将来换后端（自研 runner、demucs、云端 API）只需要再写一个实现，业务流程一行不改。

刻意用**注入的工厂**而不是在本模块 import pymss：

* 这个模块因此不拉 torch / pymss，测试里可以塞假对象；
* 真要换掉 pymss 时，删掉 `PymssBackend` 这一个类即可，不会牵连别处的 import。

另一个理由更实际：`StudioMSSeparator` 是**子类化** pymss 的 `MSSeparator` 来改造其行为的，
这种咬合每次升级上游都可能被内部实现变化咬到。把它收在一个文件里，
至少让"上游改了什么"和"我们依赖了什么"能对上账。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class SeparationBackend(Protocol):
    """推理后端的最小契约。

    `@runtime_checkable` 让测试能 `isinstance(obj, SeparationBackend)`，
    从而把"新后端是否满足契约"变成一条断言，而不是靠人记。
    """

    def prepare(self) -> None:
        """把后端装起来（必要时下载/加载模型）。可重复调用，应幂等。"""
        ...

    def separate(self, source: str, input_index: int = 1) -> list[str]:
        """分离一个输入，返回产出的文件名列表（不是绝对路径，保持上游语义）。"""
        ...

    def outputs(self) -> list[dict[str, Any]]:
        """最近一次分离的产物明细。"""
        ...

    def close(self) -> None:
        """释放资源；可重复调用。"""
        ...


class PymssBackend:
    """把一个 pymss 形状的 separator 适配成 `SeparationBackend`。

    只要求**工厂**返回的对象具备 `process_folder` / `studio_outputs`，
    以及 `close()` 或 `del_cache()` 之一——也就是 `MSSeparator` 及其子类。
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._separator: Any = None
        self._closed = False

    def _handle(self) -> Any:
        if self._separator is None:
            if self._closed:
                raise RuntimeError("separation backend is already closed")
            self._separator = self._factory()
        return self._separator

    def prepare(self) -> None:
        self._handle()

    def separate(self, source: str, input_index: int = 1) -> list[str]:
        produced = self._handle().process_folder(source, int(input_index))
        return [str(name) for name in (produced or [])]

    def outputs(self) -> list[dict[str, Any]]:
        return [dict(item) for item in (self._handle().studio_outputs() or [])]

    def close(self) -> None:
        separator = self._separator
        self._separator = None
        self._closed = True
        if separator is None:
            return
        close = getattr(separator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
            return
        # 老版本 pymss 只有 del_cache()，没有 close()
        try:
            separator.del_cache()
        except Exception:
            pass
