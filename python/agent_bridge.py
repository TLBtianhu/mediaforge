#!/usr/bin/env python3
"""MediaForge local MCP bridge.

A small, dependency-free MCP Streamable HTTP server. It exposes MediaForge
operations as tools so DSH (or another MCP client) can drive the media
pipeline without embedding MediaForge.

The bridge is intentionally a thin adapter over the existing worker command
protocol: UI actions and agent tools can later share the same operations.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "worker.py"
VERSION = "0.1.0"

sys.path.insert(0, str(ROOT))
import model_hub  # noqa: E402
from media_ops import capabilities  # noqa: E402


def _data_root(override: str = "") -> Path:
    explicit = str(override or "").strip() or os.environ.get("MEDIAFORGE_DATA_DIR") or os.environ.get("MEDIAFORGE_DATA_ROOT")
    root = Path(explicit).expanduser() if explicit else Path.home() / ".mediaforge"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _active_runtime_python(runtime_envs: Path) -> str | None:
    state_path = runtime_envs / "active-runtime.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    python_path = state.get("pythonPath") if isinstance(state, dict) else None
    if not isinstance(python_path, str):
        return None
    candidate = Path(python_path).expanduser()
    return str(candidate) if candidate.is_file() else None


from operation_kernel import Job, OperationKernel


class Bridge:
    def __init__(self, token: str | None, port: int, data_root: Path):
        self.token = token
        self.port = port
        self.data_root = data_root
        runtime_envs = Path(os.environ.get("PYMSS_STUDIO_RUNTIME_ENVS_DIR") or data_root / "runtime-envs")
        self.python_path = _active_runtime_python(runtime_envs)
        self.jobs = OperationKernel(data_root, self.python_path)
        explicit_model = os.environ.get("MEDIAFORGE_MODEL_DIR") or os.environ.get("PYMSS_MODEL_DIR")
        self.model_root = Path(explicit_model).expanduser() if explicit_model else data_root / "models"
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.feedback_path = data_root / "agent-feedback.jsonl"
        self._feedback_lock = threading.Lock()

    # ---- tool implementations ------------------------------------------------

    def status(self) -> dict[str, Any]:
        try:
            models = model_hub.list_models(root=str(self.model_root))
            model_count = models["count"]
            downloaded = models["downloadedCount"]
        except Exception as exc:  # noqa: BLE001
            model_count = 0
            downloaded = 0
            model_error = str(exc)
        else:
            model_error = None
        return {
            "ok": True,
            "app": "MediaForge",
            "bridgeVersion": VERSION,
            "dataRoot": str(self.data_root),
            "modelDir": str(self.model_root),
            "models": {"count": model_count, "downloaded": downloaded, "error": model_error},
            "jobs": self.jobs.count(),
            "pid": os.getpid(),
        }

    def models_list(self, query: str = "", modality: str = "all") -> dict[str, Any]:
        data = model_hub.list_models(query, modality, root=str(self.model_root))
        return {
            "ok": True,
            "modelDir": data["modelDir"],
            "count": data["count"],
            "downloadedCount": data["downloadedCount"],
            "environment": data["environment"],
            "models": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "modalities": item["modalities"],
                    "task": item["task"],
                    "size": item["size"],
                    "downloaded": item["downloaded"],
                    "implemented": item["implemented"],
                }
                for item in data["models"]
            ],
        }

    def capabilities(self) -> dict[str, Any]:
        data = capabilities.list_capabilities()
        models = {item["id"]: item for item in model_hub.list_models(root=str(self.model_root))["models"]}
        for cap in data["capabilities"]:
            for model in cap["models"]:
                source = models.get(model["id"])
                model["downloaded"] = bool(source and source["downloaded"])
        data["modelDir"] = str(self.model_root)
        return data

    def download_model(self, model_id: str, source: str = "modelscope") -> dict[str, Any]:
        if not model_id:
            return {"ok": False, "error": "modelId is required"}
        data = model_hub.list_models(root=str(self.model_root))
        record = next((item for item in data["models"] if item["id"] == model_id), None)
        if record is None:
            return {"ok": False, "error": f"unknown model: {model_id}"}
        if not record.get("implemented", True):
            return {"ok": False, "error": f"model adapter is not implemented yet: {model_id}"}
        job = self.jobs.start(
            "model_download",
            {
                "operation": "model_download",
                "requestId": f"model-{uuid.uuid4().hex[:12]}",
                "modelId": model_id,
                "modelDir": data["modelDir"],
                "source": source or "modelscope",
            },
        )
        return {"ok": True, "jobId": job.job_id, "status": job.snapshot(), "model": record["label"]}

    def probe(self, inputs: list[str]) -> dict[str, Any]:
        if not inputs:
            return {"ok": False, "error": "inputs must contain at least one file"}
        job = self.jobs.start("audio_tools", {"operation": "media_probe", "inputs": inputs})
        return self._wait(job, timeout=55)

    def convert(
        self,
        inputs: list[str],
        output_dir: str,
        format: str = "mp4",
        crf: int = 20,
        preset: str = "medium",
    ) -> dict[str, Any]:
        if not inputs:
            return {"ok": False, "error": "inputs must contain at least one file"}
        if not output_dir:
            return {"ok": False, "error": "outputDir is required"}
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        job = self.jobs.start(
            "audio_tools",
            {
                "operation": "media_convert",
                "inputs": inputs,
                "outputDir": output_dir,
                "format": format,
                "crf": int(crf),
                "preset": preset,
            },
        )
        return {"ok": True, "jobId": job.job_id, "status": job.snapshot()}

    def pipeline_start(
        self,
        input_path: str,
        output_dir: str,
        modality: str,
        steps: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not input_path or not Path(input_path).is_file():
            return {"ok": False, "error": f"input not found: {input_path}"}
        if not output_dir:
            return {"ok": False, "error": "outputDir is required"}
        modality = str(modality or "").strip().lower()
        if modality not in {"image", "video"}:
            return {"ok": False, "error": "modality must be image or video"}
        try:
            normalized = capabilities.normalize_steps(steps)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        models = model_hub.list_models(root=str(self.model_root))
        by_id = {item["id"]: item for item in models["models"]}
        required = sorted({model_id for step in normalized for model_id in step["bindings"].values()})
        missing = [model_id for model_id in required if not (by_id.get(model_id) or {}).get("downloaded")]
        if missing:
            return {"ok": False, "error": "missing models", "requiresDownload": missing}
        options = options or {}
        payload: dict[str, Any] = {
            "operation": f"media_{modality}",
            "requestId": f"pipeline-{uuid.uuid4().hex[:12]}",
            "inputs": [input_path],
            "outputDir": output_dir,
            "modelDir": str(self.model_root),
            "steps": normalized,
        }
        if modality == "video":
            watermark = next((step for step in normalized if step["capability"] == "watermark_removal"), None)
            upscale = next((step for step in normalized if step["capability"] == "upscale"), None)
            payload.update(
                {
                    "scale": 2 if upscale else 1,
                    "previewSeconds": float(options.get("previewSeconds") or 0),
                    "crf": int(options.get("crf") or 18),
                    "preset": str(options.get("preset") or "fast"),
                    "detectMode": str((watermark or {}).get("params", {}).get("detectMode") or "fixed"),
                    "conf": float((watermark or {}).get("params", {}).get("conf") or 0.25),
                    "expand": int((watermark or {}).get("params", {}).get("expand") or 8),
                }
            )
        job = self.jobs.start("audio_tools", payload)
        return {"ok": True, "jobId": job.job_id, "status": job.snapshot(), "steps": normalized}

    def plan(self, input_path: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        if not input_path:
            return {"ok": False, "error": "input is required"}
        if not Path(input_path).is_file():
            return {"ok": False, "error": f"input not found: {input_path}"}
        models = model_hub.list_models(root=str(self.model_root))
        by_id = {item["id"]: item for item in models["models"]}
        if any(isinstance(step, dict) and "capability" in step for step in steps):
            try:
                normalized = capabilities.normalize_steps(steps)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
            capability_plan = []
            required: list[str] = []
            for step in normalized:
                cap = capabilities.capability(step["capability"])
                bindings = []
                for role, model_id in step["bindings"].items():
                    record = by_id.get(model_id)
                    required.append(model_id)
                    bindings.append(
                        {
                            "role": role,
                            "model": model_id,
                            "downloaded": bool(record and record["downloaded"]),
                            "implemented": bool(record and record.get("implemented", True)),
                        }
                    )
                capability_plan.append(
                    {
                        "capability": step["capability"],
                        "label": cap["label"],
                        "bindings": bindings,
                        "params": step["params"],
                    }
                )
            unique = sorted(set(required))
            return {
                "ok": True,
                "input": input_path,
                "plan": capability_plan,
                "requiresDownload": [model_id for model_id in unique if not (by_id.get(model_id) or {}).get("downloaded")],
                "canRun": all(bool((by_id.get(model_id) or {}).get("downloaded")) for model_id in unique),
                "note": "Capability plan. Download missing models before pipeline execution.",
            }
        plan: list[dict[str, Any]] = []
        missing: list[str] = []
        not_ready: list[str] = []
        for step in steps:
            kind = str(step.get("type") or "").strip()
            if kind in {"video.remove_watermark", "image.remove_watermark"}:
                for model_id in ("media:watermark-yolo11x", "media:lama"):
                    model = by_id.get(model_id)
                    if model is None:
                        missing.append(model_id)
                    elif not model["downloaded"]:
                        not_ready.append(model_id)
                    plan.append({"step": f"{kind}:{model_id}", "model": model_id, "downloaded": bool(model and model["downloaded"])})
            elif kind in {"video.upscale", "image.upscale"}:
                model_id = str(step.get("model") or "media:real-esrgan-x4")
                model = by_id.get(model_id)
                if model is None:
                    missing.append(model_id)
                elif not model["downloaded"]:
                    not_ready.append(model_id)
                plan.append({"step": f"{kind}:{model_id}", "model": model_id, "downloaded": bool(model and model["downloaded"])})
            elif kind == "convert":
                plan.append({"step": "ffmpeg.convert", "format": str(step.get("format") or "mp4")})
            else:
                return {"ok": False, "error": f"unknown pipeline step: {kind}"}
        missing = sorted(set(missing))
        not_ready = sorted(set(not_ready))
        return {
            "ok": True,
            "input": input_path,
            "plan": plan,
            "missingModels": missing,
            "requiresDownload": not_ready,
            "canRun": not missing and not not_ready,
            "note": "Plan is read-only. Use models_list to inspect availability; download missing models before running pipeline steps.",
        }

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": f"fb_{uuid.uuid4().hex[:12]}",
            "timestamp": time.time(),
            "build": str(payload.get("build") or VERSION),
            "taskId": payload.get("taskId"),
            "blockers": payload.get("blockers") or [],
            "friction": payload.get("friction") or [],
            "proposals": payload.get("proposals") or [],
            "notes": str(payload.get("notes") or ""),
        }
        with self._feedback_lock:
            self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
            with self.feedback_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "feedbackId": record["id"], "path": str(self.feedback_path)}

    def _wait(self, job: Job, timeout: float) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            snapshot = job.snapshot()
            if snapshot["status"] in {"completed", "failed", "cancelled"}:
                return {"ok": snapshot["status"] == "completed", **snapshot}
            time.sleep(0.1)
        return {"ok": True, "jobId": job.job_id, "status": "running", "message": "job is still running; poll job_status"}

    # ---- MCP tool registry ---------------------------------------------------

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "mediaforge_status",
                "description": "Show MediaForge bridge and model-library status. Read-only.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "models_list",
                "description": "List the unified MediaForge model catalog with download status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Optional name/category search."},
                        "modality": {"type": "string", "enum": ["all", "audio", "image", "video"], "default": "all"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "models_download",
                "description": "Download one model from the unified catalog. Starts an asynchronous job; poll job_status. Ask the user before downloading a large model.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "modelId": {"type": "string", "description": "Exact id from models_list, for example media:lama."},
                        "source": {"type": "string", "enum": ["modelscope", "huggingface", "hf-mirror"], "default": "modelscope"},
                    },
                    "required": ["modelId"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "capabilities_list",
                "description": "List MediaForge capabilities, their roles, parameter schemas, and compatible models. Use this to build a pipeline instead of hardcoding modes.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "probe",
                "description": "Read media metadata with ffprobe. Returns a compact probe summary.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                    "required": ["inputs"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "convert",
                "description": "Start an asynchronous FFmpeg conversion and return a jobId. Poll job_status for completion.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "outputDir": {"type": "string"},
                        "format": {"type": "string", "default": "mp4"},
                        "crf": {"type": "integer", "default": 20},
                        "preset": {"type": "string", "default": "medium"},
                    },
                    "required": ["inputs", "outputDir"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "pipeline_plan",
                "description": "Validate a requested media pipeline and report model availability without running it.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Absolute path to a media file."},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "capability": {"type": "string"},
                                    "bindings": {"type": "object", "additionalProperties": {"type": "string"}},
                                    "params": {"type": "object"},
                                    "type": {
                                        "type": "string",
                                        "enum": [
                                            "video.remove_watermark",
                                            "video.upscale",
                                            "image.remove_watermark",
                                            "image.upscale",
                                            "convert",
                                        ],
                                    },
                                    "model": {"type": "string"},
                                    "format": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["input", "steps"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "pipeline_start",
                "description": "Start an asynchronous capability pipeline for an image or video. The input must use steps from capabilities_list; returns a jobId.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Absolute path to an image or video."},
                        "outputDir": {"type": "string"},
                        "modality": {"type": "string", "enum": ["image", "video"]},
                        "steps": {"type": "array", "items": {"type": "object"}},
                        "options": {"type": "object"},
                    },
                    "required": ["input", "outputDir", "modality", "steps"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "job_status",
                "description": "Read an asynchronous MediaForge job's status and result.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"jobId": {"type": "string"}},
                    "required": ["jobId"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "job_cancel",
                "description": "Request cancellation of an asynchronous MediaForge job.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"jobId": {"type": "string"}},
                    "required": ["jobId"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "feedback_submit",
                "description": "Record structured harness feedback from the agent. Local-only; never changes code or permissions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "build": {"type": "string"},
                        "taskId": {"type": "string"},
                        "blockers": {"type": "array", "items": {"type": "object"}},
                        "friction": {"type": "array", "items": {"type": "object"}},
                        "proposals": {"type": "array", "items": {"type": "object"}},
                        "notes": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "mediaforge_status":
            return self.status()
        if name == "models_list":
            return self.models_list(str(arguments.get("query") or ""), str(arguments.get("modality") or "all"))
        if name == "models_download":
            return self.download_model(str(arguments.get("modelId") or ""), str(arguments.get("source") or "modelscope"))
        if name == "capabilities_list":
            return self.capabilities()
        if name == "probe":
            return self.probe([str(value) for value in arguments.get("inputs") or []])
        if name == "convert":
            return self.convert(
                [str(value) for value in arguments.get("inputs") or []],
                str(arguments.get("outputDir") or ""),
                str(arguments.get("format") or "mp4"),
                int(arguments.get("crf") or 20),
                str(arguments.get("preset") or "medium"),
            )
        if name == "pipeline_plan":
            return self.plan(str(arguments.get("input") or ""), list(arguments.get("steps") or []))
        if name == "pipeline_start":
            return self.pipeline_start(
                str(arguments.get("input") or ""),
                str(arguments.get("outputDir") or ""),
                str(arguments.get("modality") or ""),
                list(arguments.get("steps") or []),
                arguments.get("options") if isinstance(arguments.get("options"), dict) else {},
            )
        if name == "job_status":
            job = self.jobs.get(str(arguments.get("jobId") or ""))
            return {"ok": job is not None, **job.snapshot()} if job else {"ok": False, "error": "unknown job"}
        if name == "job_cancel":
            return self.jobs.cancel(str(arguments.get("jobId") or ""))
        if name == "feedback_submit":
            return self.feedback(dict(arguments))
        raise ValueError(f"unknown tool: {name}")

    # ---- JSON-RPC ------------------------------------------------------------

    def rpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if method == "initialize":
            offered = str(params.get("protocolVersion") or "2025-06-18")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": offered,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "mediaforge", "version": VERSION},
                    "instructions": "MediaForge local media tools. Call capabilities_list to compose a pipeline, models_list to inspect availability, and pipeline_plan before processing. If requiresDownload is reported, use models_download only after the user agrees. Poll job_status after starting long jobs.",
                },
            }
        if method == "notifications/initialized":
            return None
        if request_id is None and method.startswith("notifications/"):
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tools()}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                value = self.call(name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": _json_text(value)}],
                        "structuredContent": value,
                        "isError": False,
                    },
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": f"{name} failed: {exc}"}],
                        "isError": True,
                    },
                }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaForgeMCP/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _write(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = getattr(self.server, "token", None)
        if not token:
            return True
        expected = f"Bearer {token}"
        return self.headers.get("Authorization") == expected

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            self._write(200, json.dumps({"ok": True, "name": "mediaforge-mcp", "version": VERSION}).encode())
            return
        self._write(405, b'{"error":"method not allowed"}')

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/mcp":
            self._write(404, b'{"error":"not found"}')
            return
        if not self._authorized():
            self._write(401, b'{"error":"unauthorized"}')
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 4 * 1024 * 1024:
                self._write(413, b'{"error":"invalid body size"}')
                return
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            response = self.server.bridge.rpc(request)
            if response is None:
                self._write(202, b"")
                return
            self._write(200, json.dumps(response, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._write(
                400,
                json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}).encode(),
            )


def serve_stdio(bridge: Bridge) -> int:
    """Line-delimited JSON-RPC over stdio for DSH's stdio MCP transport."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            response = bridge.rpc(request)
        except Exception as exc:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MediaForge local MCP bridge")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEDIAFORGE_MCP_PORT") or 1421))
    parser.add_argument("--token", default=os.environ.get("MEDIAFORGE_MCP_TOKEN") or "")
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--pair-file", default="")
    parser.add_argument("--data-root", default="")
    parser.add_argument("--stdio", action="store_true", help="Serve line-delimited JSON-RPC over stdio.")
    args = parser.parse_args()

    data_root = _data_root(args.data_root)
    if args.stdio:
        return serve_stdio(Bridge(token=None, port=0, data_root=data_root))

    token = "" if args.no_auth else (args.token or os.environ.get("MEDIAFORGE_MCP_TOKEN") or "")
    if not token and not args.no_auth:
        token = secrets.token_urlsafe(32)
    bridge = Bridge(token=token or None, port=args.port, data_root=data_root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.bridge = bridge  # type: ignore[attr-defined]
    server.token = token or None  # type: ignore[attr-defined]

    pair_path = Path(args.pair_file).expanduser() if args.pair_file else data_root / "mcp-bridge.json"
    pair_path.parent.mkdir(parents=True, exist_ok=True)
    pair_path.write_text(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{args.port}/mcp",
                "token": token,
                "serverName": "mediaforge",
                "version": VERSION,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"MediaForge MCP bridge: http://127.0.0.1:{args.port}/mcp", flush=True)
    print(f"Pairing file: {pair_path}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
