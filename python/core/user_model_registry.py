"""Strict current pymss registry JSON, shared by app readers and writers."""
from __future__ import annotations

import json
import math
from pathlib import Path

from shared.asset_references import ensure_plain_path


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate user model registry key: {key}")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"Invalid registry number: {value}")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Invalid registry number")
    return number


def _unicode(value):
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, item in value.items():
            _unicode(key)
            _unicode(item)
    elif isinstance(value, list):
        for item in value:
            _unicode(item)


def read_registry(path: Path) -> dict:
    """Caller holds the app asset lease; missing is distinct from invalid."""
    ensure_plain_path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version":1, "models":[]}
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_reject_constant, parse_float=_finite_float)
    _unicode(value)
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("models"), list)):
        raise ValueError("Unsupported user model registry version or models; original file preserved")
    for entry in value["models"]:
        if (not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key].strip()
                                              for key in ("name", "model_type", "model_path"))):
            raise ValueError("Invalid user model registry entry; original file preserved")
        for key in ("model_path", "config_path"):
            item = entry.get(key)
            if key == "config_path" and (item is None or item == ""):
                continue
            if (not isinstance(item, str) or not item.strip() or "\0" in item or item.startswith("\ufeff")
                    or not Path(item).is_absolute()):
                raise ValueError("Unresolved user model registry path; original file preserved")
    return value
