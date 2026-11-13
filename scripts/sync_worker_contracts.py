"""Generate the frontend worker command contract from manifest/protocol.json.

Only commands that declare both `payload` and `result` schemas are included.
The protocol is the single source; the generated TypeScript must not be edited
by hand. `--check` fails when the checked-in file is stale.

Schema subset (deliberately small):
    type, properties, required, additionalProperties, items, enum

Command payloads are validated by `python/shared/command_schemas.py`; result
schemas are currently type/documentation contracts for the frontend.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest" / "protocol.json"
OUTPUT = ROOT / "src" / "types" / "workerContracts.generated.ts"

TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def _fail(path: str, message: str) -> None:
    raise ValueError(f"{path}: {message}")


def validate_schema(schema: object, path: str) -> None:
    if not isinstance(schema, dict):
        _fail(path, "schema must be an object")
    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not isinstance(branches, list) or not branches:
            _fail(path, "oneOf must be a non-empty array")
        for index, branch in enumerate(branches):
            validate_schema(branch, f"{path}.oneOf[{index}]")
        return
    node_type = schema.get("type")
    if node_type is None and "properties" in schema:
        node_type = "object"
    if node_type is None and "enum" in schema:
        pass
    elif isinstance(node_type, list):
        if not node_type or any(item not in TYPES for item in node_type):
            _fail(path, f"invalid type list: {node_type!r}")
    elif node_type is not None and node_type not in TYPES:
        _fail(path, f"invalid type: {node_type!r}")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            _fail(path, "enum must be a non-empty array")
    if node_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            _fail(path, "properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            _fail(path, "required must be an array of strings")
        missing = sorted(set(required) - set(properties))
        if missing:
            _fail(path, f"required keys missing from properties: {missing}")
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, bool):
            if not isinstance(additional, dict):
                _fail(path, "additionalProperties must be a boolean or a schema")
            validate_schema(additional, f"{path}.additionalProperties")
        for key, child in properties.items():
            if not isinstance(key, str) or not key:
                _fail(path, "property names must be non-empty strings")
            validate_schema(child, f"{path}.properties.{key}")
    if node_type == "array" or "items" in schema:
        if "items" not in schema:
            _fail(path, "array schema needs items")
        validate_schema(schema["items"], f"{path}.items")


def command_contracts(manifest: dict) -> list[tuple[str, dict, dict]]:
    contracts: list[tuple[str, dict, dict]] = []
    seen: set[str] = set()
    for domain_id, domain in (manifest.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            payload = entry.get("payload")
            result = entry.get("result")
            if payload is None and result is None:
                continue
            if payload is None or result is None:
                _fail(f"{domain_id}.{name}", "must declare both payload and result")
            validate_schema(payload, f"{domain_id}.{name}.payload")
            validate_schema(result, f"{domain_id}.{name}.result")
            command = str(entry.get("command") or name)
            if command in seen:
                _fail(f"{domain_id}.{name}", f"duplicate command id {command!r}")
            seen.add(command)
            contracts.append((command, payload, result))
    contracts.sort(key=lambda item: item[0])
    return contracts


def _literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError(f"unsupported enum value: {value!r}")


def _type_name(value: str) -> str:
    if value in {"number", "integer"}:
        return "number"
    if value == "null":
        return "null"
    return value


def render_type(schema: dict, indent: int = 0) -> str:
    pad = "  " * indent
    if "oneOf" in schema:
        return " | ".join(render_type(branch, indent) for branch in schema["oneOf"])
    if "enum" in schema:
        return " | ".join(_literal(item) for item in schema["enum"])
    node_type = schema.get("type")
    if node_type is None and "properties" in schema:
        node_type = "object"
    if isinstance(node_type, list):
        return " | ".join(render_type({**schema, "type": item}, indent) for item in node_type)
    if node_type == "array":
        return f"Array<{render_type(schema['items'], indent)}>"
    if node_type in {"string", "number", "integer", "boolean", "null"}:
        return _type_name(node_type)
    if node_type != "object":
        raise ValueError(f"unsupported type: {node_type!r}")

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    additional = schema.get("additionalProperties")
    if not properties:
        if isinstance(additional, dict):
            return f"Record<string, {render_type(additional, indent)}>"
        return "Record<string, never>" if additional is False else "Record<string, unknown>"

    lines = ["{"]
    for key, child in properties.items():
        optional = "" if key in required else "?"
        rendered = render_type(child, indent + 1)
        if "\n" in rendered:
            first, *rest = rendered.splitlines()
            lines.append(f"{pad}  {key}{optional}: {first}")
            lines.extend(f"{pad}  {line}" for line in rest)
        else:
            lines.append(f"{pad}  {key}{optional}: {rendered}")
    if isinstance(additional, dict):
        lines.append(f"{pad}  [key: string]: {render_type(additional, indent + 1)}")
    elif additional is True:
        lines.append(f"{pad}  [key: string]: unknown")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def render_contracts(contracts: list[tuple[str, dict, dict]]) -> str:
    lines = [
        "// Generated by scripts/sync_worker_contracts.py from manifest/protocol.json.",
        "// Do not edit by hand; run the script after changing the protocol.",
        "export interface WorkerCommandContracts {",
    ]
    for command, payload, result in contracts:
        lines.append(f"  {json.dumps(command)}: {{")
        lines.append("    payload: " + render_type(payload, 2))
        lines.append("    result: " + render_type(result, 2))
        lines.append("  }")
    lines.extend([
        "}",
        "",
        "export type WorkerCommandName = keyof WorkerCommandContracts",
        "export type WorkerCommandPayload<C extends WorkerCommandName> = WorkerCommandContracts[C]['payload']",
        "export type WorkerCommandResult<C extends WorkerCommandName> = WorkerCommandContracts[C]['result']",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    contracts = command_contracts(manifest)
    rendered = render_contracts(contracts)
    if "--check" in sys.argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != rendered:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date; run scripts/sync_worker_contracts.py")
            return 1
        print(f"{len(contracts)} command contracts")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(contracts)} commands)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
