"""Small executable fixtures for the real worker and supervisor lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


FIXTURE = Path(__file__).resolve()
sys.path.insert(0, str(FIXTURE.parents[2] / "python"))

from shared.worker_subprocess import hidden_subprocess_kwargs


def _wait(path: Path, *, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(path.name)
        time.sleep(0.015)


def _publish(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


def _spawn(mode: str, root: Path, *pids: int) -> subprocess.Popen:
    # Keep the actual worker's stdout and stderr open in both descendants.
    return subprocess.Popen(
        [sys.executable, "-B", str(FIXTURE), mode, str(root), *map(str, pids)],
        stdin=subprocess.DEVNULL,
        **hidden_subprocess_kwargs(),
    )


def _run_operation(command: str, payload: dict) -> int:
    from shared.worker_protocol import emit

    if command != "run_operation":
        raise ValueError(command)
    root = Path(payload["fixtureRoot"])
    child = _spawn("--test-child", root, os.getpid())
    _wait(root / "tree.json")
    if payload["fixtureMode"] == "normal":
        emit("task.result", {"outputs": {"value": 42}})
        _wait(root / "finish-worker")
        # Leave both descendants holding the protocol stdout and diagnostics.
        # A root exit alone must not be mistaken for a fully reaped tree.
        os._exit(0)
    _wait(root / "stop-fixtures")
    child.wait(timeout=10)
    return 0


def _descendant(mode: str, root: Path, pids: list[int]) -> int:
    if mode == "--test-child":
        grandchild = _spawn("--test-grandchild", root, *pids, os.getpid())
        _wait(root / "stop-fixtures")
        grandchild.wait(timeout=10)
        return 0
    print("grandchild diagnostics", file=sys.stderr, flush=True)
    _publish(root / "tree.json", [*pids, os.getpid()])
    _wait(root / "stop-fixtures")
    return 0


def _owner(root: Path) -> int:
    from core import operation_kernel

    operation_kernel.WORKER = FIXTURE
    operation_kernel.SUPERVISOR = FIXTURE
    kernel = operation_kernel.OperationKernel(root, sys.executable)
    job = kernel.start("run_operation", {
        "requestId": "executor_lifecycle", "fixtureRoot": str(root), "fixtureMode": "crash",
    })
    _wait(root / "tree.json")
    assert job.process is not None
    _publish(root / "owner.json", {"owner": os.getpid(), "supervisor": job.process.pid})
    _wait(root / "stop-fixtures")
    kernel.close()
    return 0


def _supervisor() -> int:
    from core import worker_supervisor

    root = Path(os.environ["MEDIAFORGE_DATA_ROOT"])
    terminate = worker_supervisor.ProcessTree.terminate

    def gated_terminate(tree):
        (root / "cleanup-started").touch()
        try:
            _wait(root / "allow-reap")
        finally:
            terminate(tree)

    worker_supervisor.ProcessTree.terminate = gated_terminate
    return worker_supervisor.main(sys.argv[1:])


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "--test-owner":
        raise SystemExit(_owner(Path(sys.argv[2])))
    if mode in {"--test-child", "--test-grandchild"}:
        raise SystemExit(_descendant(mode, Path(sys.argv[2]), list(map(int, sys.argv[3:]))))
    if mode.startswith("--"):
        raise SystemExit(_supervisor())
    import worker

    worker.run_command = _run_operation
    raise SystemExit(worker.main(sys.argv))
