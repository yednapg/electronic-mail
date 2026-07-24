from __future__ import annotations

"""Role-aware process liveness check for the shared API/worker image.

This deliberately does not claim worker readiness. Promotion readiness is a
cross-service property proved through fresh database heartbeats and the
authenticated exact-release verifier.
"""

import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
import sys
from typing import Any
from urllib.request import urlopen


PID_ONE_CMDLINE = Path("/proc/1/cmdline")
WORKER_MODULES = {"app.workers.main", "app.workers.gmail_poller"}


class ContainerHealthError(RuntimeError):
    pass


def read_pid_one_arguments(path: Path = PID_ONE_CMDLINE) -> tuple[str, ...]:
    raw = path.read_bytes()
    arguments = tuple(
        item.decode("utf-8", errors="strict")
        for item in raw.split(b"\0")
        if item
    )
    if not arguments:
        raise ContainerHealthError("container PID 1 has an empty command line")
    return arguments


def process_role(arguments: Sequence[str]) -> str:
    if "app.main:app" in arguments and any(
        Path(argument).name == "uvicorn" for argument in arguments
    ):
        return "api"

    if len(arguments) >= 3 and arguments[1] == "-m" and arguments[2] in WORKER_MODULES:
        return "worker"

    raise ContainerHealthError("container PID 1 is not a reviewed API, worker, or poller process")


def check_api(
    *,
    port: str,
    opener: Callable[..., Any] = urlopen,
) -> None:
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ContainerHealthError("PORT must be an integer from 1 through 65535")
    with opener(f"http://127.0.0.1:{port}/health", timeout=3) as response:
        if getattr(response, "status", None) != 200:
            raise ContainerHealthError("API liveness endpoint did not return HTTP 200")
        body = response.read(64 * 1024 + 1)
    if len(body) > 64 * 1024:
        raise ContainerHealthError("API liveness response is unexpectedly large")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContainerHealthError("API liveness response is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ContainerHealthError("API liveness response is not ok")


def main() -> int:
    try:
        role = process_role(read_pid_one_arguments())
        if role == "api":
            check_api(port=os.getenv("PORT", "3001"))
    except (ContainerHealthError, OSError, ValueError) as error:
        print(f"container health check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
