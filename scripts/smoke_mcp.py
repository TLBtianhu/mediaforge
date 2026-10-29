#!/usr/bin/env python3
"""Smoke-test the MediaForge MCP bridge over stdio.

Uses only the Python standard library and a temporary data root, so it does
not touch the developer or dogfood profiles.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "python" / "agent_bridge.py"


def send(process: subprocess.Popen[str], request: dict) -> dict:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("bridge closed stdout")
    return json.loads(line)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mediaforge-mcp-smoke-") as temporary:
        root = Path(temporary)
        image = root / "input.png"
        image.write_bytes(b"not-a-real-image")
        process = subprocess.Popen(
            [sys.executable, str(BRIDGE), "--stdio", "--data-root", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            initialized = send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "mediaforge-smoke", "version": "0.1.0"},
                    },
                },
            )
            assert initialized["result"]["serverInfo"]["name"] == "mediaforge"

            tools = send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            names = {tool["name"] for tool in tools["result"]["tools"]}
            required = {
                "mediaforge_status",
                "models_list",
                "probe",
                "convert",
                "pipeline_plan",
                "job_status",
                "job_cancel",
                "feedback_submit",
            }
            assert required <= names, sorted(required - names)

            status = send(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mediaforge_status", "arguments": {}}})
            assert status["result"]["structuredContent"]["models"]["count"] >= 330

            plan = send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "pipeline_plan",
                        "arguments": {
                            "input": str(image),
                            "steps": [{"type": "image.remove_watermark"}, {"type": "image.upscale"}],
                        },
                    },
                },
            )
            plan_value = plan["result"]["structuredContent"]
            assert plan_value["ok"] is True
            assert "requiresDownload" in plan_value

            feedback = send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "feedback_submit",
                        "arguments": {"taskId": "smoke", "notes": "smoke test"},
                    },
                },
            )
            assert feedback["result"]["structuredContent"]["ok"] is True
            assert (root / "agent-feedback.jsonl").is_file()
        finally:
            process.stdin.close()
            process.wait(timeout=10)
    print("MCP smoke test passed: initialize, tools/list, status, pipeline_plan, feedback_submit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
