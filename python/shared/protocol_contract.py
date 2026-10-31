"""读取并校验 manifest/protocol.json —— 命令与事件的唯一契约。

这一层只做三件事：
  1. 找到 manifest（安装目录 / 仓库根，两者布局一致）；
  2. 校验 schemaVersion 与结构，坏数据要报得清楚；
  3. 提供 domain/command 解析与历史事件名映射。

它不执行业务逻辑：真正的 handler 仍在 worker 的分发里。这样 P1 引入契约时
不需要同时搬动推理代码，出了问题也能一眼看出是"契约"还是"实现"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MCP_SCHEMA_VERSION = 1


class ProtocolError(ValueError):
    """清单不满足契约。带 code，便于前端/命令行给出可操作的提示。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _candidates() -> list[Path]:
    here = Path(__file__).resolve()
    # python/shared/protocol_contract.py → parents[2] 是仓库/安装根
    return [
        here.parents[2] / "manifest" / "protocol.json",
        here.parents[1] / "manifest" / "protocol.json",
        here.parent / "manifest" / "protocol.json",
    ]


def manifest_dir() -> Path:
    for candidate in _candidates():
        if candidate.is_file():
            return candidate.parent
    raise ProtocolError(
        "PROTOCOL_MISSING",
        "找不到 manifest/protocol.json；安装包与开发树都应带有 manifest/ 目录",
    )


def load_protocol() -> dict[str, Any]:
    path = manifest_dir() / "protocol.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProtocolError("PROTOCOL_INVALID", f"{path} 不是合法 JSON：{exc}") from exc

    version = data.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise ProtocolError(
            "PROTOCOL_SCHEMA_MISMATCH",
            f"协议 schemaVersion={version!r}，本程序支持 {SCHEMA_VERSION}；"
            "1.0.0 之前不做兼容，请更新应用或重新生成 manifest",
        )
    domains = data.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise ProtocolError("PROTOCOL_INVALID", "protocol.json 缺少 domains")
    return data


def command(domain: str, name: str) -> dict[str, Any]:
    data = load_protocol()
    entry = ((data.get("domains") or {}).get(domain) or {}).get("commands", {}).get(name)
    if not isinstance(entry, dict) or not entry.get("handler"):
        raise ProtocolError(
            "PROTOCOL_UNKNOWN_COMMAND",
            f"协议里没有 {domain}.{name}；先补 manifest/protocol.json 再加代码",
        )
    return entry


def entry_for(domain: str, name: str) -> dict[str, Any]:
    """逻辑入口（domain + 名字）→ 协议条目。"""
    return command(domain, name)


def handler_for(domain: str, name: str) -> str:
    return str(command(domain, name)["handler"])


def command_entries() -> dict[str, dict[str, Any]]:
    """全局命令表：命令 id → 条目。dispatch 唯一的数据来源。"""
    data = load_protocol()
    table: dict[str, dict[str, Any]] = {}
    for domain_id, domain in (data.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            if not isinstance(entry, dict):
                raise ProtocolError("PROTOCOL_INVALID", f"{domain_id}.{name} 不是对象")
            command_id = str(entry.get("command") or name)
            handler = str(entry.get("handler") or "")
            if ":" not in handler:
                raise ProtocolError(
                    "PROTOCOL_INVALID",
                    f"{domain_id}.{name} 的 handler 必须是 module:function，收到 {handler!r}",
                )
            if command_id in table:
                raise ProtocolError("PROTOCOL_INVALID", f"命令重复：{command_id}")
            table[command_id] = entry
    return table


def canonical_event(legacy_name: str) -> str | None:
    """历史事件名 → 规范事件名（emit 在边界处使用）。"""
    return (load_protocol().get("emitAliases") or {}).get(legacy_name)


def declared_legacy_events() -> set[str]:
    return set((load_protocol().get("emitAliases") or {}).keys())


def canonical_events() -> list[str]:
    data = load_protocol()
    return list((data.get("events") or {}).get("canonical") or [])


def mcp_schema_version() -> int:
    """MCP 工具面的协议版本。与 protocol.json 的声明必须一致。"""
    data = load_protocol()
    version = data.get("mcpSchemaVersion")
    if version != MCP_SCHEMA_VERSION:
        raise ProtocolError(
            "MCP_SCHEMA_MISMATCH",
            f"protocol.json 的 mcpSchemaVersion={version!r}，本程序支持 {MCP_SCHEMA_VERSION}；"
            "工具面不兼容，请更新应用或重新生成 manifest",
        )
    return int(version)
