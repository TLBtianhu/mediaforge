"""MediaForge worker 入口：解析参数、包日志上下文、交给协议派发。

这里刻意保持很薄：命令来自 manifest/protocol.json，本文件不登记任何命令。
新增命令 = 改协议 + 写 handler，不碰这个文件。
"""
from __future__ import annotations

import argparse
import sys

from shared.worker_protocol import debug_log, emit_error, load_payload, protocol_logging
from shared.worker_proxy import ProxyConfigError
from surfaces.dispatch import run as dispatch_run


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pymss-studio-worker")
    parser.add_argument("command", nargs="?", default="health")
    parser.add_argument("--payload", help="JSON string or path to a JSON payload file")
    return parser.parse_args(argv[1:])


def run_command(command: str, payload: dict) -> int:
    """保留这个名字：测试与内嵌调用用它替换执行体。"""
    return dispatch_run(command, payload)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    debug_log("worker.start", command=args.command)
    try:
        payload = load_payload(args.payload)
        debug_log("worker.payload", command=args.command, hasPayload=bool(args.payload))
        with protocol_logging(
            args.command,
            task_id=payload.get("taskId"),
            job_id=payload.get("jobId"),
            batch=isinstance(payload.get("tasks"), list) and bool(payload["tasks"]),
        ):
            code = run_command(args.command, payload)
        debug_log("worker.exit", command=args.command, code=code)
        return code
    except ProxyConfigError as exc:
        debug_log("worker.exception", command=args.command, error=str(exc))
        return emit_error(exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback

        debug_log("worker.exception", command=args.command, error=str(exc), detail=traceback.format_exc())
        return emit_error("UNKNOWN_ERROR", str(exc), traceback.format_exc())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
