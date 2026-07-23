from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.core.error_safety import GoogleCredentialsUnavailable
from app.db import jobs
from app.services.mailbox_sends import MailSendConfirmationPending
from app.workers import main as worker


class JobTerminalStateTests(unittest.TestCase):
    def test_deduped_reader_job_preserves_worker_retry_backoff(self) -> None:
        existing = _job_row(
            kind="gmail_body_fetch",
            status="queued",
            priority=90,
            attempt_count=2,
            run_after="2026-07-23T12:10:00+00:00",
        )
        connection = _EnqueueConnection(existing)

        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            result = jobs.enqueue_job(
                "postgresql://example/db",
                kind="gmail_body_fetch",
                queue="reader",
                dedupe_key="gmail-body-fetch-thread:user-1:thread-1",
                priority=90,
                payload={"user_id": "user-1", "gmail_thread_id": "thread-1"},
                wake_existing=False,
            )

        self.assertEqual(result.id, "job-1")
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.run_after, "2026-07-23T12:10:00+00:00")
        self.assertEqual(len(connection.calls), 1)
        self.assertIn("SELECT * FROM background_jobs", connection.calls[0][0])

    def test_cancelled_job_cannot_be_completed_by_its_former_worker(self) -> None:
        connection = _TransitionConnection(updated_id=None)

        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            changed = jobs.complete_job("postgresql://example/db", "job-1", worker_id="worker-1")

        self.assertFalse(changed)
        self._assert_owned_running_transition(connection)
        self.assertEqual(len(connection.calls), 1)

    def test_cancelled_job_cannot_be_requeued_by_its_former_worker(self) -> None:
        connection = _TransitionConnection(updated_id=None)

        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            changed = jobs.fail_job(
                "postgresql://example/db",
                _job(attempt_count=1, max_attempts=3),
                "provider failed",
                worker_id="worker-1",
            )

        self.assertFalse(changed)
        self._assert_owned_running_transition(connection)
        self.assertEqual(len(connection.calls), 1)

    def test_cancelled_job_cannot_be_marked_dead_by_its_former_worker(self) -> None:
        connection = _TransitionConnection(updated_id=None)

        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            changed = jobs.fail_job(
                "postgresql://example/db",
                _job(attempt_count=3, max_attempts=3),
                "provider failed",
                worker_id="worker-1",
            )

        self.assertFalse(changed)
        self._assert_owned_running_transition(connection)
        self.assertEqual(len(connection.calls), 1)

    def test_success_event_is_written_only_after_owned_running_transition(self) -> None:
        connection = _TransitionConnection(updated_id="job-1")

        with patch.object(jobs, "get_engine", return_value=_TransitionEngine(connection)):
            changed = jobs.complete_job("postgresql://example/db", "job-1", worker_id="worker-1")

        self.assertTrue(changed)
        self._assert_owned_running_transition(connection)
        self.assertEqual(len(connection.calls), 2)
        self.assertIn("INSERT INTO background_job_events", connection.calls[1][0])
        self.assertEqual(connection.calls[1][1]["event_type"], "succeeded")

    def _assert_owned_running_transition(self, connection: _TransitionConnection) -> None:
        sql, params = connection.calls[0]
        self.assertIn("status = 'running'", sql)
        self.assertIn("lease_owner = :worker_id", sql)
        self.assertEqual(params["worker_id"], "worker-1")

    def test_terminal_thread_action_failure_rolls_back_optimistic_projection(self) -> None:
        action_job = SimpleNamespace(
            id="job-1",
            kind="gmail_thread_action",
            user_id="user-1",
            payload={"user_id": "user-1", "server_action_id": "action-1"},
            attempt_count=5,
            max_attempts=5,
        )
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=action_job),
            patch.object(worker, "_run_job", side_effect=RuntimeError("provider failed")),
            patch.object(worker, "fail_job", return_value=True) as fail,
            patch.object(worker, "rollback_failed_thread_action") as rollback,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["default"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        fail.assert_called_once()
        rollback.assert_called_once_with(
            settings,
            user_id="user-1",
            server_action_id="action-1",
        )

    def test_google_reauthentication_failure_cancels_job_without_retry(self) -> None:
        gmail_job = _job(attempt_count=1, max_attempts=5)
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=gmail_job),
            patch.object(
                worker,
                "_run_job",
                side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
            ),
            patch.object(worker, "cancel_claimed_job") as cancel,
            patch.object(worker, "fail_job") as fail,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["critical"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        cancel.assert_called_once_with(
            "postgresql://example/db",
            "job-1",
            worker_id="worker-1",
        )
        fail.assert_not_called()

    def test_ambiguous_send_worker_cycle_requeues_instead_of_completing(self) -> None:
        gmail_job = replace(
            _job(attempt_count=1, max_attempts=5),
            kind="gmail_send_message",
            payload={"user_id": "user-1", "server_send_id": "send-1"},
        )
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(worker, "renew_heartbeat"),
            patch.object(worker, "claim_job", return_value=gmail_job),
            patch.object(
                worker,
                "run_pending_send",
                side_effect=MailSendConfirmationPending("delivery confirmation pending"),
            ),
            patch.object(worker, "fail_job", return_value=True) as fail,
            patch.object(worker, "complete_job") as complete,
        ):
            worked = worker._run_worker_cycle(
                settings,
                worker_id="worker-1",
                queues=["critical"],
                heartbeat_interval=30,
            )

        self.assertTrue(worked)
        fail.assert_called_once()
        complete.assert_not_called()


class _TransitionResult:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class _EnqueueResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row

    def mappings(self) -> "_EnqueueResult":
        return self

    def first(self) -> dict[str, object] | None:
        return self.row


class _EnqueueConnection:
    def __init__(self, existing: dict[str, object]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params=None) -> _EnqueueResult:
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "SELECT * FROM background_jobs" in sql:
            return _EnqueueResult(self.existing)
        raise AssertionError(f"Unexpected SQL: {sql}")


class _TransitionConnection:
    def __init__(self, *, updated_id: str | None) -> None:
        self.updated_id = updated_id
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "UPDATE background_jobs" in sql:
            return _TransitionResult(self.updated_id)
        return _TransitionResult(None)


class _TransitionEngine:
    def __init__(self, connection: _TransitionConnection) -> None:
        self.connection = connection

    def begin(self):
        return nullcontext(self.connection)


def _job(*, attempt_count: int, max_attempts: int) -> jobs.BackgroundJob:
    return jobs.BackgroundJob(
        id="job-1",
        kind="gmail_delta_sync",
        queue="critical",
        status="running",
        user_id="user-1",
        dedupe_key=None,
        priority=0,
        payload_version=1,
        payload={"user_id": "user-1"},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        run_after="2026-07-21T00:00:00+00:00",
        lease_owner="worker-1",
        lease_expires_at="2026-07-21T00:05:00+00:00",
        last_error=None,
        trace_id=None,
        created_at="2026-07-21T00:00:00+00:00",
        started_at="2026-07-21T00:00:00+00:00",
        completed_at=None,
        updated_at="2026-07-21T00:00:00+00:00",
    )


def _job_row(
    *,
    kind: str,
    status: str,
    priority: int,
    attempt_count: int,
    run_after: str,
) -> dict[str, object]:
    return {
        "id": "job-1",
        "kind": kind,
        "queue": "reader",
        "status": status,
        "user_id": None,
        "dedupe_key": "gmail-body-fetch-thread:user-1:thread-1",
        "priority": priority,
        "payload_version": 1,
        "payload_json": '{"user_id":"user-1","gmail_thread_id":"thread-1"}',
        "attempt_count": attempt_count,
        "max_attempts": 5,
        "run_after": run_after,
        "lease_owner": None,
        "lease_expires_at": None,
        "last_error": "gmail timeout",
        "trace_id": None,
        "created_at": "2026-07-23T12:00:00+00:00",
        "started_at": "2026-07-23T12:00:01+00:00",
        "completed_at": None,
        "updated_at": "2026-07-23T12:00:02+00:00",
    }


if __name__ == "__main__":
    unittest.main()
