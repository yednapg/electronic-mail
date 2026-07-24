#!/usr/bin/env python3
from __future__ import annotations

"""Fail-closed promotion gate for one exact backend release.

The verifier talks only to the public API. The operational snapshot remains
behind the existing authenticated ops-admin session and proves database access,
fresh worker heartbeats, the reviewed queue-role topology, and release parity.
"""

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


RELEASE_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
MAX_RESPONSE_BYTES = 256 * 1024
FRESH_WORKER_SECONDS = 120
EXPECTED_WORKER_ROLES: dict[str, frozenset[str]] = {
    "fast": frozenset(("critical", "default")),
    "reader": frozenset(("reader",)),
    "slow": frozenset(("slow",)),
    "poller": frozenset(("gmail_poll",)),
}


class BackendReleaseVerificationError(RuntimeError):
    """The observed backend cannot yet be promoted."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def validate_backend_origin(value: str) -> str:
    if not value or value != value.strip():
        raise BackendReleaseVerificationError("BACKEND_URL must be a canonical HTTPS origin")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise BackendReleaseVerificationError("BACKEND_URL has an invalid port") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise BackendReleaseVerificationError(
            "BACKEND_URL must be an HTTPS origin without credentials, path, query, or fragment"
        )
    if port is not None and not 1 <= port <= 65535:
        raise BackendReleaseVerificationError("BACKEND_URL has an invalid port")
    return value.rstrip("/")


def validate_release_sha(value: str) -> str:
    if RELEASE_SHA_PATTERN.fullmatch(value) is None:
        raise BackendReleaseVerificationError(
            "EXPECTED_RELEASE_SHA must be a full lowercase 40- or 64-character commit SHA"
        )
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BackendReleaseVerificationError(message)


def _require_mapping(payload: Any, endpoint: str) -> Mapping[str, Any]:
    _require(isinstance(payload, dict), f"{endpoint} response must be a JSON object")
    return payload


def verify_release_payloads(
    *,
    health: Any,
    ready: Any,
    ops: Any,
    expected_release_sha: str,
    max_queue_depth_per_queue: int = 1_000,
    max_total_queue_depth: int = 2_500,
    max_oldest_queued_age_seconds: int = 900,
) -> dict[str, int]:
    """Validate one atomic-enough three-endpoint snapshot.

    Requiring several consecutive snapshots in ``wait_for_release`` protects
    against accepting a brief mixed-version overlap during Railway's
    independent service deployments.
    """

    expected_release_sha = validate_release_sha(expected_release_sha)
    health_payload = _require_mapping(health, "/health")
    ready_payload = _require_mapping(ready, "/ready")
    ops_payload = _require_mapping(ops, "/v1/ops/health")

    _require(health_payload.get("status") == "ok", "/health is not ok")
    _require(
        health_payload.get("release") == expected_release_sha,
        f"/health does not identify expected release {expected_release_sha}",
    )

    _require(ready_payload.get("status") == "ready", "/ready is not ready")
    _require(ready_payload.get("environment") == "production", "/ready is not production")
    _require(ready_payload.get("database") == "postgres", "/ready did not prove Postgres readiness")
    _require(
        ready_payload.get("release") == expected_release_sha,
        f"/ready does not identify expected release {expected_release_sha}",
    )

    _require(ops_payload.get("environment") == "production", "ops health is not production")
    _require(
        ops_payload.get("release") == expected_release_sha,
        f"ops health does not identify expected release {expected_release_sha}",
    )
    _require(ops_payload.get("worker_online") is True, "ops health reports no fresh worker")
    _require(
        ops_payload.get("worker_releases_match") is True,
        "ops health reports fresh worker releases do not all match the API release",
    )
    _require(
        ops_payload.get("required_queues_ready") is True,
        "ops health reports required queues are not ready",
    )

    for field in ("dead_jobs", "stale_running_jobs"):
        value = ops_payload.get(field)
        _require(type(value) is int and value == 0, f"ops health requires {field}=0, found {value!r}")

    depths = ops_payload.get("queue_depth")
    _require(isinstance(depths, dict), "ops queue_depth must be an object")
    total_depth = 0
    for queue, depth in depths.items():
        _require(isinstance(queue, str) and bool(queue), "ops queue_depth contains an invalid queue name")
        _require(type(depth) is int and depth >= 0, f"ops queue {queue!r} has invalid depth {depth!r}")
        _require(
            depth <= max_queue_depth_per_queue,
            f"ops queue {queue!r} depth {depth} exceeds limit {max_queue_depth_per_queue}",
        )
        total_depth += depth
    _require(
        total_depth <= max_total_queue_depth,
        f"ops total queue depth {total_depth} exceeds limit {max_total_queue_depth}",
    )

    oldest = ops_payload.get("oldest_queued_age_seconds")
    _require(
        oldest is None or (type(oldest) is int and oldest >= 0),
        f"ops oldest_queued_age_seconds is invalid: {oldest!r}",
    )
    _require(total_depth != 0 or oldest is None, "ops reports an oldest queued age with no queued jobs")
    _require(total_depth == 0 or oldest is not None, "ops omitted oldest queued age while jobs are queued")
    _require(
        oldest is None or oldest <= max_oldest_queued_age_seconds,
        f"ops oldest queued job age {oldest}s exceeds limit {max_oldest_queued_age_seconds}",
    )

    workers = ops_payload.get("workers")
    _require(isinstance(workers, list), "ops workers must be an array")
    role_counts = {role: 0 for role in EXPECTED_WORKER_ROLES}
    for worker in workers:
        _require(isinstance(worker, dict), "ops workers contains a non-object entry")
        if worker.get("fresh") is not True:
            continue
        worker_id = worker.get("worker_id")
        age_seconds = worker.get("age_seconds")
        release_sha = worker.get("release_sha")
        queues = worker.get("queues")
        _require(isinstance(worker_id, str) and bool(worker_id), "fresh worker is missing worker_id")
        _require(
            type(age_seconds) is int and 0 <= age_seconds <= FRESH_WORKER_SECONDS,
            f"fresh worker {worker_id!r} has invalid heartbeat age {age_seconds!r}",
        )
        _require(
            release_sha == expected_release_sha,
            f"fresh worker {worker_id!r} is running release {release_sha!r}",
        )
        _require(
            worker.get("release_matches_expected") is True,
            f"fresh worker {worker_id!r} does not confirm expected release",
        )
        _require(
            isinstance(queues, list)
            and all(isinstance(queue, str) and bool(queue) for queue in queues)
            and len(queues) == len(set(queues)),
            f"fresh worker {worker_id!r} has invalid queue declarations",
        )
        queue_set = frozenset(queues)
        matching_role = next(
            (role for role, expected_queues in EXPECTED_WORKER_ROLES.items() if queue_set == expected_queues),
            None,
        )
        _require(
            matching_role is not None,
            f"fresh worker {worker_id!r} has unreviewed queue role {sorted(queue_set)!r}",
        )
        role_counts[matching_role] += 1

    missing_roles = [role for role, count in role_counts.items() if count < 1]
    _require(not missing_roles, f"ops health is missing fresh worker roles: {', '.join(missing_roles)}")
    return role_counts


def _request_json(
    *,
    opener: Any,
    origin: str,
    path: str,
    bearer_token: str | None = None,
    timeout_seconds: float = 5.0,
) -> Any:
    headers = {"Accept": "application/json"}
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = Request(f"{origin}{path}", headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None)
            if status != 200:
                raise BackendReleaseVerificationError(f"{path} returned HTTP {status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise BackendReleaseVerificationError(f"{path} returned HTTP {error.code}") from error
    except (OSError, URLError) as error:
        raise BackendReleaseVerificationError(f"{path} request failed: {type(error).__name__}") from error
    _require(len(body) <= MAX_RESPONSE_BYTES, f"{path} response is unexpectedly large")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackendReleaseVerificationError(f"{path} response is not valid JSON") from error


def wait_for_release(
    *,
    backend_origin: str,
    ops_bearer_token: str,
    expected_release_sha: str,
    timeout_seconds: float,
    poll_seconds: float,
    stable_successes: int,
    max_queue_depth_per_queue: int,
    max_total_queue_depth: int,
    max_oldest_queued_age_seconds: int,
) -> dict[str, int]:
    origin = validate_backend_origin(backend_origin)
    expected_release_sha = validate_release_sha(expected_release_sha)
    _require(bool(ops_bearer_token), "OPS_BEARER_TOKEN is required")
    _require("\r" not in ops_bearer_token and "\n" not in ops_bearer_token, "OPS_BEARER_TOKEN is malformed")
    _require(timeout_seconds > 0, "timeout must be positive")
    _require(poll_seconds > 0, "poll interval must be positive")
    _require(1 <= stable_successes <= 10, "stable successes must be from 1 through 10")

    opener = build_opener(_NoRedirects())
    deadline = time.monotonic() + timeout_seconds
    consecutive_successes = 0
    last_error = ""
    role_counts: dict[str, int] = {}
    while True:
        try:
            health = _request_json(opener=opener, origin=origin, path="/health")
            ready = _request_json(opener=opener, origin=origin, path="/ready")
            ops = _request_json(
                opener=opener,
                origin=origin,
                path="/v1/ops/health",
                bearer_token=ops_bearer_token,
            )
            role_counts = verify_release_payloads(
                health=health,
                ready=ready,
                ops=ops,
                expected_release_sha=expected_release_sha,
                max_queue_depth_per_queue=max_queue_depth_per_queue,
                max_total_queue_depth=max_total_queue_depth,
                max_oldest_queued_age_seconds=max_oldest_queued_age_seconds,
            )
            consecutive_successes += 1
            if consecutive_successes >= stable_successes:
                return role_counts
        except BackendReleaseVerificationError as error:
            consecutive_successes = 0
            message = str(error)
            if message != last_error:
                print(f"Waiting for exact backend release: {message}", file=sys.stderr)
                last_error = message

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = last_error or "release did not remain stable long enough"
            raise BackendReleaseVerificationError(
                f"backend release verification timed out: {detail}"
            )
        time.sleep(min(poll_seconds, remaining))


def _bounded_integer(name: str, *, default: int, hard_max: int) -> int:
    raw = os.getenv(name, str(default))
    if not raw.isascii() or not raw.isdigit():
        raise BackendReleaseVerificationError(f"{name} must be a non-negative integer")
    value = int(raw)
    if value > hard_max:
        raise BackendReleaseVerificationError(f"{name} cannot exceed {hard_max}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--stable-successes", type=int, default=3)
    arguments = parser.parse_args(argv)
    try:
        role_counts = wait_for_release(
            backend_origin=os.getenv("BACKEND_URL", ""),
            ops_bearer_token=os.getenv("OPS_BEARER_TOKEN", ""),
            expected_release_sha=os.getenv("EXPECTED_RELEASE_SHA", ""),
            timeout_seconds=arguments.timeout_seconds,
            poll_seconds=arguments.poll_seconds,
            stable_successes=arguments.stable_successes,
            max_queue_depth_per_queue=_bounded_integer(
                "MAX_QUEUE_DEPTH_PER_QUEUE", default=1_000, hard_max=10_000
            ),
            max_total_queue_depth=_bounded_integer(
                "MAX_TOTAL_QUEUE_DEPTH", default=2_500, hard_max=25_000
            ),
            max_oldest_queued_age_seconds=_bounded_integer(
                "MAX_OLDEST_QUEUED_AGE_SECONDS", default=900, hard_max=3_600
            ),
        )
    except BackendReleaseVerificationError as error:
        print(f"backend release verification failed: {error}", file=sys.stderr)
        return 1

    counts = ", ".join(f"{role}={role_counts[role]}" for role in EXPECTED_WORKER_ROLES)
    print(
        "Backend exact-release gate passed for "
        f"{os.environ['EXPECTED_RELEASE_SHA']} ({counts}); Postgres and schema are ready."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
