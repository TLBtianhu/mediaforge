from __future__ import annotations

import json
import time
from typing import Any

from shared.worker_protocol import emit
from shared.worker_proxy import (
    ProxyConfigError,
    configure_process_proxy,
    parse_proxy_config,
    proxy_urlopen,
    redacted_proxy,
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _test_url_for_source(source: str) -> str:
    urls = {
        "modelscope": "https://www.modelscope.cn/api/v1/models/baicai1145/pymss/repo/files?Revision=master&Recursive=true",
        "huggingface": "https://huggingface.co/api/models/baicai1145/pymss",
        "hf-mirror": "https://hf-mirror.com/api/models/baicai1145/pymss",
    }
    return urls.get(source, urls["modelscope"])


def cmd_test_connection(payload: dict[str, Any]) -> int:
    mode = str(payload.get("mode") or "system").strip()
    raw_url = str(payload.get("url") or "")
    bypass = payload.get("bypass") or ""
    source = str(payload.get("source") or "modelscope").strip()
    timeout = _safe_int(payload.get("timeout"), 15) or 15
    try:
        config = parse_proxy_config({"mode": mode, "url": raw_url, "bypass": bypass})
    except ProxyConfigError as exc:
        emit("test_connection_result", {
            "ok": False,
            "code": exc.code,
            "error": str(exc),
            "elapsedMs": 0,
            "mode": mode,
            "proxy": raw_url,
        })
        return 0

    try:
        # The test payload is authoritative even if the debounced settings sync
        # has not reached Rust yet.
        configure_process_proxy(config)
    except ProxyConfigError as exc:
        emit("test_connection_result", {
            "ok": False,
            "code": exc.code,
            "error": str(exc),
            "elapsedMs": 0,
            "mode": mode,
            "proxy": raw_url,
        })
        return 0

    test_url = _test_url_for_source(source)
    started = time.time()
    try:
        with proxy_urlopen(test_url, timeout, config) as response:
            status_code = getattr(response, "status", 200)
            raw = response.read().decode("utf-8", errors="replace")
            elapsed = time.time() - started
            ip_addr = ""
            try:
                ip_addr = response.fp.raw._sock.getpeername()[0] if hasattr(response, "fp") else ""
            except Exception:
                pass
            data = {}
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                pass
            files_count = len(data.get("Data", {}).get("Files", [])) if isinstance(data, dict) else 0
            emit("test_connection_result", {
                "ok": True,
                "status": int(status_code),
                "ip": ip_addr,
                "filesCount": files_count,
                "elapsedMs": int(elapsed * 1000),
                "mode": mode,
                "proxy": redacted_proxy(config),
            })
            return 0
    except Exception as exc:
        elapsed = time.time() - started
        emit("test_connection_result", {
            "ok": False,
            "error": str(exc),
            "elapsedMs": int(elapsed * 1000),
            "mode": mode,
            "proxy": redacted_proxy(config),
        })
        return 0
