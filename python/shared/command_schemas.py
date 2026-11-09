"""Validate command payloads against the schemas declared in protocol.json.

This is intentionally a small subset of JSON Schema: type, properties,
required, additionalProperties, items and enum. It exists so a caller gets
`PAYLOAD_INVALID` with a field path instead of a KeyError deep inside a worker.

Result schemas are used by `scripts/sync_worker_contracts.py` to generate the
frontend types; handlers still emit their events and are not re-validated here.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from shared.protocol_contract import load_protocol


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _validate(value: Any, schema: dict[str, Any], path: str, problems: list[str]) -> None:
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        matches = 0
        for branch in branches:
            branch_problems: list[str] = []
            _validate(value, branch, path, branch_problems)
            if not branch_problems:
                matches += 1
        if matches != 1:
            problems.append(f"{path}: expected exactly one oneOf branch (matched {matches})")
        return

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        problems.append(f"{path}: expected one of {enum!r}")
        return

    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_ok(value, item) for item in expected):
            problems.append(f"{path}: expected {' | '.join(expected)}")
            return
    elif isinstance(expected, str) and not _type_ok(value, expected):
        problems.append(f"{path}: expected {expected}")
        return

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                problems.append(f"{path}.{key}: required")
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            for key, item in value.items():
                if key not in properties:
                    _validate(item, additional, f"{path}.{key}", problems)
        elif additional is False:
            for key in value:
                if key not in properties:
                    problems.append(f"{path}.{key}: unknown field")
        for key, child in properties.items():
            if key in value:
                _validate(value[key], child, f"{path}.{key}", problems)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, f"{path}[{index}]", problems)


@lru_cache(maxsize=1)
def _payload_schemas() -> dict[str, dict[str, Any]]:
    data = load_protocol()
    schemas: dict[str, dict[str, Any]] = {}
    for domain_id, domain in (data.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            schema = entry.get("payload")
            if not isinstance(schema, dict):
                continue
            command = str(entry.get("command") or name)
            if command in schemas:
                raise ValueError(f"duplicate command schema: {command}")
            schemas[command] = schema
    return schemas


def validate_payload(command: str, payload: Any) -> list[str]:
    """Return field-level problems; an empty list means the payload is valid."""
    schema = _payload_schemas().get(command)
    if schema is None:
        return []
    problems: list[str] = []
    _validate(payload, schema, "payload", problems)
    return problems

@lru_cache(maxsize=1)
def _result_schemas() -> dict[str, dict[str, Any]]:
    data = load_protocol()
    schemas: dict[str, dict[str, Any]] = {}
    for domain_id, domain in (data.get("domains") or {}).items():
        for name, entry in (domain.get("commands") or {}).items():
            schema = entry.get("result")
            if not isinstance(schema, dict):
                continue
            command = str(entry.get("command") or name)
            if command in schemas:
                raise ValueError(f"duplicate command result schema: {command}")
            schemas[command] = schema
    return schemas


class ResultContractError(ValueError):
    """A query handler emitted a result event that does not match protocol.json."""

    def __init__(self, command: str, event: str, problems: list[str]) -> None:
        self.command = command
        self.event = event
        self.problems = problems
        super().__init__(f"{command} emitted {event} with invalid result: " + "; ".join(problems))


def validate_result(command: str, payload: Any) -> list[str]:
    """Return field-level problems for a query result; empty means valid."""
    schema = _result_schemas().get(command)
    if schema is None:
        return []
    problems: list[str] = []
    _validate(payload, schema, "result", problems)
    return problems


def result_event_validator(command: str, expected_event: str):
    """Build a validator for the one event that carries a query result."""

    def validator(event_type: str, payload: Any) -> None:
        if event_type != expected_event:
            return
        problems = validate_result(command, payload)
        if problems:
            raise ResultContractError(command, event_type, problems)

    return validator

