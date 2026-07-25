from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from app.db import jobs
from app.workers import main as worker
from app.workers.retry_policy import (
    GMAIL_RETRY_DELAY_CAP_SECONDS,
    gmail_is_authorization_failure,
    gmail_retry_delay_seconds,
)


class GmailRetryDelayPolicyTests(unittest.TestCase):
    def test_numeric_retry_after_can_raise_exponential_delay(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_delta_sync",
            _http_error(429, retry_after="900"),
            exponential_delay_seconds=120,
        )

        self.assertEqual(delay, 900)

    def test_retry_after_never_shortens_exponential_delay(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_import_batch",
            _http_error(503, retry_after="15"),
            exponential_delay_seconds=120,
        )

        self.assertEqual(delay, 120)

    def test_http_date_retry_after_is_parsed(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_body_fetch",
            _http_error(503, retry_after="Sat, 25 Jul 2026 12:05:00 GMT"),
            exponential_delay_seconds=30,
            now=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(delay, 300)

    def test_retry_after_is_bounded(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_backfill",
            _http_error(429, retry_after="86400"),
            exponential_delay_seconds=600,
        )

        self.assertEqual(delay, GMAIL_RETRY_DELAY_CAP_SECONDS)

    def test_response_status_code_and_headers_are_supported(self) -> None:
        error = RuntimeError("provider failed")
        error.response = SimpleNamespace(  # type: ignore[attr-defined]
            status_code=429,
            headers={"Retry-After": "75"},
        )

        delay = gmail_retry_delay_seconds(
            "gmail_delta_sync",
            error,
            exponential_delay_seconds=30,
        )

        self.assertEqual(delay, 75)

    def test_wrapped_http_failure_is_recognized(self) -> None:
        wrapped = RuntimeError("sync failed")
        wrapped.__cause__ = _http_error(500, retry_after="240")

        delay = gmail_retry_delay_seconds(
            "gmail_full_reconcile",
            wrapped,
            exponential_delay_seconds=30,
        )

        self.assertEqual(delay, 240)

    def test_network_failure_uses_exponential_delay(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_search_hydrate",
            ConnectionResetError("connection reset"),
            exponential_delay_seconds=120,
        )

        self.assertEqual(delay, 120)

    def test_non_transient_http_failure_uses_default_queue_policy(self) -> None:
        delay = gmail_retry_delay_seconds(
            "gmail_delta_sync",
            _http_error(400, retry_after="900"),
            exponential_delay_seconds=120,
        )

        self.assertIsNone(delay)

    def test_non_gmail_job_is_unchanged(self) -> None:
        delay = gmail_retry_delay_seconds(
            "projection_refresh",
            _http_error(429, retry_after="900"),
            exponential_delay_seconds=120,
        )

        self.assertIsNone(delay)

    def test_raw_and_wrapped_google_auth_failures_are_terminal(self) -> None:
        self.assertTrue(gmail_is_authorization_failure(_http_error(401)))
        wrapped = RuntimeError("wrapped without provider details")
        wrapped.__cause__ = _http_error(403)
        self.assertTrue(gmail_is_authorization_failure(wrapped))
        self.assertFalse(gmail_is_authorization_failure(_http_error(429)))


class GmailWorkerRetryWiringTests(unittest.TestCase):
    def test_queue_wait_metric_uses_durable_job_timestamps(self) -> None:
        self.assertEqual(
            worker._job_queue_wait_ms(
                "2026-07-25T12:00:00+00:00",
                "2026-07-25T12:00:01.250000+00:00",
            ),
            1_250,
        )
        self.assertIsNone(worker._job_queue_wait_ms("invalid", None))

    def test_worker_passes_gmail_retry_after_to_failure_transition(self) -> None:
        job = _job(kind="gmail_delta_sync", attempt_count=1)
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=job),
            patch.object(
                worker,
                "_run_job",
                side_effect=_http_error(429, retry_after="900"),
            ),
            patch.object(worker, "fail_job", return_value=True) as fail,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["critical"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        self.assertEqual(fail.call_args.kwargs["retry_delay_seconds"], 900)

    def test_worker_leaves_non_gmail_failure_delay_unset(self) -> None:
        job = _job(kind="projection_refresh", attempt_count=2)
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=job),
            patch.object(worker, "_run_job", side_effect=TimeoutError("timed out")),
            patch.object(worker, "fail_job", return_value=True) as fail,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["default"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        self.assertIsNone(fail.call_args.kwargs["retry_delay_seconds"])

    def test_worker_pauses_raw_401_and_requests_reauthorization(self) -> None:
        job = _job(kind="gmail_body_backfill", attempt_count=1)
        settings = SimpleNamespace(database_path="postgresql://example/db", release_sha="release-1")
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=job),
            patch.object(worker, "_run_job", side_effect=_http_error(401)),
            patch.object(worker, "cancel_claimed_job") as cancel,
            patch.object(worker, "fail_job") as fail,
            patch.object(worker, "mark_google_disconnected") as disconnect,
            patch.object(worker, "mark_import_error") as mark_error,
            patch.object(worker, "emit_mailbox_event") as emit,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["slow"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        disconnect.assert_called_once_with("postgresql://example/db", user_id="user-1")
        mark_error.assert_called_once()
        self.assertTrue(emit.call_args.kwargs["payload"]["reauthorization_required"])
        cancel.assert_called_once()
        fail.assert_not_called()

    def test_dead_progressive_job_persists_failed_phase_and_event(self) -> None:
        job = _job(kind="gmail_full_reconcile", attempt_count=5)
        settings = SimpleNamespace(database_path="postgresql://example/db", release_sha="release-1")
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=job),
            patch.object(worker, "_run_job", side_effect=RuntimeError("terminal")),
            patch.object(worker, "fail_job", return_value=True),
            patch.object(worker, "mark_import_error") as mark_error,
            patch.object(worker, "emit_mailbox_event") as emit,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["slow"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        mark_error.assert_called_once()
        self.assertEqual(emit.call_args.kwargs["payload"]["phase"], "failed")
        self.assertTrue(emit.call_args.kwargs["payload"]["terminal"])


class DurableRetryDelayTests(unittest.TestCase):
    def test_retry_delay_override_is_persisted(self) -> None:
        connection = _TransitionConnection()
        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            changed = jobs.fail_job(
                "postgresql://example/db",
                _job(kind="gmail_delta_sync", attempt_count=1),
                "HttpError: Background task failed (HTTP 429).",
                worker_id="worker-1",
                retry_delay_seconds=900,
            )

        self.assertTrue(changed)
        self.assertEqual(connection.calls[0][1]["delay_seconds"], 900)
        self.assertEqual(connection.calls[1][1]["metadata_json"], '{"delay_seconds": 900}')

    def test_retry_delay_override_cannot_shorten_default_backoff(self) -> None:
        connection = _TransitionConnection()
        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            jobs.fail_job(
                "postgresql://example/db",
                _job(kind="gmail_delta_sync", attempt_count=2),
                "TimeoutError: Background task failed.",
                worker_id="worker-1",
                retry_delay_seconds=30,
            )

        self.assertEqual(connection.calls[0][1]["delay_seconds"], 120)


class _TransitionResult:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class _TransitionConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params=None) -> _TransitionResult:
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        return _TransitionResult("job-1" if "UPDATE background_jobs" in sql else None)


class _TransitionEngine:
    def __init__(self, connection: _TransitionConnection) -> None:
        self.connection = connection

    def begin(self):
        return nullcontext(self.connection)


def _http_error(status: int, *, retry_after: str | None = None) -> HttpError:
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return HttpError(Response(headers), b"provider response")


def _job(*, kind: str, attempt_count: int):
    return SimpleNamespace(
        id="job-1",
        kind=kind,
        user_id="user-1",
        payload={"user_id": "user-1"},
        attempt_count=attempt_count,
        max_attempts=5,
    )


if __name__ == "__main__":
    unittest.main()
