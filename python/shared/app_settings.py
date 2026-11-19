"""Validation for the current application settings document."""
from __future__ import annotations

import math
from typing import Any


_FIELDS = {
    "themeMode": "string",
    "themeAccent": "string",
    "scaleFactor": "number",
    "locale": "string",
    "animationsEnabled": "boolean",
    "developerMode": "boolean",
    "modelDir": "string",
    "outputDir": "string",
    "defaultDevice": "string",
    "defaultFormat": "string",
    "downloadSource": "string",
    "downloadMethod": "string",
    "maxConcurrentSeparations": "number",
    "clearInputAfterSubmit": "boolean",
    "wavBitDepth": "string",
    "flacBitDepth": "string",
    "mp3BitRate": "string",
    "m4aBitRate": "string",
    "m4aCodec": "string",
    "startupOnboardingSeen": "boolean",
    "beginnerGuideInvitationSeen": "boolean",
    "beginnerGuideProgress": "object",
    "proxyMode": "string",
    "proxyUrl": "string",
    "proxyBypass": "string",
    "updateChannel": "string",
    "updateSource": "string",
    "updateEndpointOverride": "string",
}


def _is_number(value: Any) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


_ENUMS = {
    "themeMode": {"system", "dark", "light"},
    "themeAccent": {"blue", "pink", "sky", "teal"},
    "locale": {"system", "zh-CN", "en"},
    "downloadMethod": {"aria2c", "urllib"},
    "wavBitDepth": {"PCM_16", "PCM_24", "FLOAT"},
    "flacBitDepth": {"PCM_16", "PCM_24"},
    "mp3BitRate": {"128k", "192k", "256k", "320k"},
    "m4aCodec": {"aac", "alac"},
    "proxyMode": {"system", "none", "custom"},
    "updateChannel": {"stable", "prerelease"},
    "updateSource": {"auto", "github", "modelscope"},
}


def validate_app_settings(value: Any, *, max_workers: int) -> None:
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported app settings schema; original file preserved")
    if value.keys() - {"version", *_FIELDS}:
        raise ValueError("unsupported app settings schema; original file preserved")
    for name, expected in _FIELDS.items():
        if name not in value:
            continue
        item = value[name]
        valid = (
            isinstance(item, str) if expected == "string"
            else type(item) is bool if expected == "boolean"
            else isinstance(item, dict) if expected == "object"
            else _is_number(item)
        )
        if not valid or name in _ENUMS and item not in _ENUMS[name]:
            raise ValueError("unsupported app settings schema; original file preserved")
    if "maxConcurrentSeparations" in value:
        limit = value["maxConcurrentSeparations"]
        if type(limit) is not int or not 1 <= limit <= max_workers:
            raise ValueError("unsupported app settings schema; original file preserved")
