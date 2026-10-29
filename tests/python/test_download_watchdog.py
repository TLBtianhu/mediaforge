"""看门狗要能在"下载停住"时抛错，而不是让人等到天荒地老。

真实事故：`legacy_models/demucs.th.part` 0 字节挂十分钟。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from download_watchdog import (  # noqa: E402
    DEFAULT_STALL_SECONDS,
    DownloadStalled,
    ProgressWatchdog,
    stall_seconds,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class WatchdogTests(unittest.TestCase):
    def test_a_watchdog_without_beats_reports_a_stall_when_it_exits(self) -> None:
        clock = FakeClock()
        watchdog = ProgressWatchdog("file.bin", timeout=5, clock=clock, poll_interval=999)
        with self.assertRaises(DownloadStalled):
            with watchdog:
                clock.advance(6)
                watchdog._last_beat = 0.0  # 模拟"最后一次进度在 6 秒前"
                # 直接触发一次判定，等价于后台线程醒来
                watchdog._stalled.set() if clock.now - watchdog._last_beat >= watchdog.timeout else None
        self.assertTrue(watchdog.stalled())

    def test_beats_keep_it_alive(self) -> None:
        clock = FakeClock()
        watchdog = ProgressWatchdog("file.bin", timeout=5, clock=clock, poll_interval=999)
        with watchdog:
            for _ in range(3):
                clock.advance(3)
                watchdog.beat(1024)
            self.assertFalse(watchdog.stalled())
        self.assertFalse(watchdog.stalled())
        self.assertEqual(watchdog.bytes_done, 3072)

    def test_beat_after_a_stall_raises_so_the_reader_loop_can_bail(self) -> None:
        watchdog = ProgressWatchdog("file.bin", timeout=1)
        watchdog._stalled.set()
        with self.assertRaises(DownloadStalled):
            watchdog.beat(1)

    def test_message_says_what_to_do(self) -> None:
        error = DownloadStalled("HTDemucs4.th", 60, 1024)
        self.assertIn("HTDemucs4.th", str(error))
        self.assertIn("重试", str(error))
        self.assertEqual(error.bytes_done, 1024)

    def test_the_timeout_can_be_configured_and_bad_values_fall_back(self) -> None:
        import os

        original = os.environ.get("MEDIAFORGE_DOWNLOAD_STALL_SECONDS")
        try:
            os.environ["MEDIAFORGE_DOWNLOAD_STALL_SECONDS"] = "15"
            self.assertEqual(stall_seconds(), 15.0)
            os.environ["MEDIAFORGE_DOWNLOAD_STALL_SECONDS"] = "not-a-number"
            self.assertEqual(stall_seconds(), DEFAULT_STALL_SECONDS)
            os.environ["MEDIAFORGE_DOWNLOAD_STALL_SECONDS"] = "0"
            self.assertEqual(stall_seconds(), DEFAULT_STALL_SECONDS)
        finally:
            if original is None:
                os.environ.pop("MEDIAFORGE_DOWNLOAD_STALL_SECONDS", None)
            else:
                os.environ["MEDIAFORGE_DOWNLOAD_STALL_SECONDS"] = original

    def test_the_background_thread_actually_flags_a_silent_file(self) -> None:
        """真起线程跑一次：0.2 秒不心跳就该被判定卡死。"""
        watchdog = ProgressWatchdog("silent.bin", timeout=0.2, poll_interval=0.02)
        with self.assertRaises(DownloadStalled):
            with watchdog:
                time.sleep(0.5)
        self.assertTrue(watchdog.stalled())


class LegacyGuardTests(unittest.TestCase):
    def test_guard_abandons_a_call_that_never_reports_progress(self) -> None:
        import worker_download

        watchdog = ProgressWatchdog("stuck.bin", timeout=0.2, poll_interval=0.02)
        with self.assertRaises(DownloadStalled):
            worker_download._download_with_stall_guard("stuck.bin", lambda: time.sleep(5), watchdog)

    def test_guard_returns_the_value_when_the_call_finishes(self) -> None:
        import worker_download

        watchdog = ProgressWatchdog("ok.bin", timeout=5, poll_interval=0.02)
        self.assertEqual(worker_download._download_with_stall_guard("ok.bin", lambda: 42, watchdog), 42)

    def test_guard_calls_straight_through_without_a_watchdog(self) -> None:
        import worker_download

        self.assertEqual(worker_download._download_with_stall_guard("x.bin", lambda: "done", None), "done")

    def test_guard_propagates_the_original_exception(self) -> None:
        import worker_download

        watchdog = ProgressWatchdog("x.bin", timeout=5, poll_interval=0.02)

        def boom() -> None:
            raise ValueError("network said no")

        with self.assertRaises(ValueError):
            worker_download._download_with_stall_guard("x.bin", boom, watchdog)


if __name__ == "__main__":
    unittest.main()
