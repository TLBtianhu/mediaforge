"""按 manifest/protocol.json 查表派发命令。

这是唯一的执行入口：worker.py 只负责解析参数、包一层日志上下文，
具体命令由这里的表决定。表来自协议，不是代码里的 if 链。

三条规则：
  * 协议里没有的命令 = 未知命令，返回可读错误；
  * handler 写成 `module:function`，按需 import，避免启动时加载整棵依赖树；
  * proxy 字段决定执行前怎么配网络：configured / payload / none。
"""
from __future__ import annotations

import importlib
from typing import Any, Callable

from shared.protocol_contract import ProtocolError, command_entries, entry_for
from shared.worker_proxy import ProxyConfigError, configure_process_proxy, load_proxy_config, parse_proxy_config
from shared.worker_protocol import emit_error

_CACHE: dict[str, Callable[[dict[str, Any]], int]] = {}


def _resolve(handler: str) -> Callable[[dict[str, Any]], int]:
    """`module:function` → 可调用对象。只在这里 import，启动时不碰重依赖。"""
    if ":" not in handler:
        raise ProtocolError(
            "PROTOCOL_INVALID",
            f"handler 必须是 module:function 形式，收到 {handler!r}",
        )
    module_name, function_name = handler.split(":", 1)
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ProtocolError(
            "PROTOCOL_INVALID",
            f"{handler} 里的 {function_name} 不存在或不可调用",
        )
    return function


def _apply_proxy(entry: dict[str, Any], payload: dict[str, Any]) -> None:
    mode = str(entry.get("proxy") or "none")
    if mode == "configured":
        configure_process_proxy(load_proxy_config())
    elif mode == "payload":
        configure_process_proxy(
            parse_proxy_config(
                {
                    "mode": payload.get("mode"),
                    "url": payload.get("url"),
                    "bypass": payload.get("bypass"),
                }
            )
        )
    # none：不碰网络配置


def _callable_for(command: str, entry: dict[str, Any]) -> Callable[[dict[str, Any]], int]:
    """只为被请求的命令 import handler：启动时不加载整棵依赖树。

    这条曾经很关键：`test_connection` 必须能在 `python -S`（无 site-packages）下跑，
    而其它 handler 可能 import yaml/torch 之类。按需解析才不会被它们拖垮。
    """
    if command not in _CACHE:
        _CACHE[command] = _resolve(str(entry["handler"]))
    return _CACHE[command]


def run(command: str, payload: dict[str, Any] | None = None) -> int:
    payload = payload or {}
    if command == "protocol_call":
        # 逻辑入口：domain + command 名 → 协议里的条目 → 真正的 handler
        domain = str(payload.get("domain") or "").strip()
        name = str(payload.get("command") or "").strip()
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        try:
            entry = entry_for(domain, name)
        except ProtocolError as exc:
            return emit_error(exc.code, str(exc))
        target = str(entry.get("command") or name)
        if target == "protocol_call":
            return emit_error("PROTOCOL_INVALID", "protocol_call 不能指向自己")
        return run(target, inner)

    try:
        entries = command_entries()
    except ProtocolError as exc:
        return emit_error(exc.code, str(exc))
    if command not in entries:
        return emit_error("UNKNOWN_COMMAND", f"Unknown command: {command}")
    entry = entries[command]
    try:
        function = _callable_for(command, entry)
    except ProtocolError as exc:
        return emit_error(exc.code, str(exc))
    _apply_proxy(entry, payload)
    return function(payload)
