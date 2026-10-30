"""payload 文件的编码容忍度。

2026-10-29：验证脚本用 PowerShell 5.1 写 payload（`Set-Content -Encoding UTF8` 默认带 BOM），
worker 直接崩在 `Unexpected UTF-8 BOM (decode using utf-8-sig)`。

生产路径上 payload 由 Rust 写（不带 BOM），所以线上没炸；但 payload 也常由脚本、手工、
第三方工具生成，**读端必须容忍**——否则表现是"worker 一声不响地失败"，而不是"payload 格式不对"。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from worker_protocol import load_payload  # noqa: E402


class PayloadEncodingTests(unittest.TestCase):
    def test_payload_file_with_bom_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "payload.json"
            path.write_text(json.dumps({"operation": "media_probe"}), encoding="utf-8-sig")
            self.assertEqual(self._bom(path), b"\xef\xbb\xbf")
            self.assertEqual(load_payload(str(path)), {"operation": "media_probe"})

    def test_payload_file_without_bom_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "payload.json"
            path.write_text(json.dumps({"operation": "media_probe"}), encoding="utf-8")
            self.assertEqual(load_payload(str(path)), {"operation": "media_probe"})

    def test_inline_json_still_works(self) -> None:
        self.assertEqual(load_payload('{"command": "env_info"}'), {"command": "env_info"})

    def test_empty_argument_means_empty_payload(self) -> None:
        self.assertEqual(load_payload(None), {})
        self.assertEqual(load_payload(""), {})

    @staticmethod
    def _bom(path: Path) -> bytes:
        return path.read_bytes()[:3]


if __name__ == "__main__":
    unittest.main()
