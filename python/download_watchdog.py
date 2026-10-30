"""下载看门狗：卡住比失败更糟。

真实事故（2026-10-30）：`legacy_models/demucs.th.part` 停在 0 字节十分钟，
既不报错也没进度——用户看到的是"永远在下载"。

这里只做一件事：进度多久没动就算卡死，抛出可恢复的错误，让上层重试/换源/提示。
它不负责重试策略，也不碰网络，因此可以单测（不用真的等 10 分钟）。
"""
from __future__ import annotations

import os
import threading
import time

DEFAULT_STALL_SECONDS = 60.0


class DownloadStalled(RuntimeError):
    """下载在 `timeout` 秒内没有任何进展。"""

    def __init__(self, label: str, timeout: float, bytes_done: int = 0) -> None:
        self.label = label
        self.timeout = timeout
        self.bytes_done = bytes_done
        super().__init__(
            f"下载 {label} 已 {timeout:.0f} 秒没有进展（已收到 {bytes_done} 字节）；"
            "可重试，或在设置里切换下载源"
        )


def stall_seconds() -> float:
    """环境变量 `MEDIAFORGE_DOWNLOAD_STALL_SECONDS` 可调，默认 60 秒。"""
    raw = os.environ.get("MEDIAFORGE_DOWNLOAD_STALL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_STALL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STALL_SECONDS
    return value if value > 0 else DEFAULT_STALL_SECONDS


class ProgressWatchdog:
    """在后台线程里盯着"最后一次进度"的时间。

    用法：

        watchdog = ProgressWatchdog("HTDemucs4.th", timeout=60)
        with watchdog:
            while chunk := read():
                watchdog.beat(len(chunk))

    退出 `with` 时若已判定卡死，抛出 `DownloadStalled`；正常结束则静默返回。
    """

    def __init__(self, label: str, timeout: float | None = None,
                 clock=time.monotonic, poll_interval: float | None = None) -> None:
        self.label = label
        self.timeout = stall_seconds() if timeout is None else max(0.001, float(timeout))
        self.bytes_done = 0
        self._clock = clock
        self._poll = max(0.005, min(1.0, self.timeout / 4)) if poll_interval is None else poll_interval
        self._last_beat = clock()
        self._stalled = threading.Event()
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self, size: int = 0) -> None:
        self.bytes_done += max(0, int(size))
        self._last_beat = self._clock()
        if self._stalled.is_set():
            # 卡死已判定：调用方应当尽快退出读取循环
            raise DownloadStalled(self.label, self.timeout, self.bytes_done)

    def stalled(self) -> bool:
        return self._stalled.is_set()

    def _run(self) -> None:
        while not self._finished.wait(self._poll):
            if self._clock() - self._last_beat >= self.timeout:
                self._stalled.set()
                return

    def __enter__(self) -> "ProgressWatchdog":
        self._thread = threading.Thread(target=self._run, name=f"download-watchdog-{self.label[:20]}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._finished.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._stalled.is_set() and exc is None:
            raise DownloadStalled(self.label, self.timeout, self.bytes_done)
        return False
