#!/usr/bin/env python3
"""从 audio_catalog.json 重建接口登记表中的音频投影。

媒体条目、taxonomy 与现有顺序保持不变；新音频条目插到最后一个音频条目之后。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "python" / "media_ops" / "model_registry.json"
AUDIO_CATALOG_PATH = ROOT / "python" / "audio_catalog.json"
AUDIO_SOURCE = "audio_catalog.json"


def project_audio(item: dict[str, Any]) -> dict[str, Any]:
    cleanup = item.get("primary_category") == "cleanup"
    architecture = str(item.get("architecture") or "")
    family = "unknown" if architecture == "unknown" else str(item.get("model_type") or "").removeprefix("legacy_")
    return {
        "id": f"audio:{item['name']}",
        "name": item["name"],
        "family": family,
        "task": "audio_restoration" if cleanup else "separation",
        "io": {"in": "audio", "out": "audio" if cleanup else "audio*", "arity": 1 if cleanup else -1},
        "architecture": item.get("architecture"),
        "source": {"status": "catalog", "repo": None, "file": item["relpath"]},
        "license": "pymss model license",
        "slots": ["strength"] if cleanup else ["stems", "overlap", "chunk_size"],
        "notes": None,
    }


def generate_registry(registry: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {item["name"]: item for item in catalog}
    if len(by_name) != len(catalog):
        raise ValueError("audio_catalog.json contains duplicate model names")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in registry.get("models") or []:
        if not str(item.get("id") or "").startswith("audio:"):
            models.append(item)
            continue
        name = str(item.get("name") or "")
        if name not in by_name:
            raise ValueError(f"audio registry entry is absent from audio_catalog.json: {name}")
        models.append(project_audio(by_name[name]))
        seen.add(name)
    additions = [project_audio(item) for item in catalog if item["name"] not in seen]
    audio_indices = [index for index, item in enumerate(models) if str(item.get("id") or "").startswith("audio:")]
    if audio_indices:
        models[max(audio_indices) + 1:max(audio_indices) + 1] = additions
    else:
        models = additions + models
    generated_from = list(registry.get("generatedFrom") or [])
    if AUDIO_SOURCE not in generated_from:
        generated_from.append(AUDIO_SOURCE)
    return {**registry, "generatedFrom": generated_from, "models": models}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail instead of writing when the projection is stale")
    args = parser.parse_args()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(AUDIO_CATALOG_PATH.read_text(encoding="utf-8"))["models"]
    generated = generate_registry(registry, catalog)
    if args.check:
        if generated != registry:
            print("model_registry.json is stale; run python scripts/sync_model_registry.py")
            return 1
        print(f"model registry projection is fresh ({len(catalog)} audio models)")
        return 0
    REGISTRY_PATH.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {REGISTRY_PATH} ({len(catalog)} audio models)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
