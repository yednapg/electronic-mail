from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import patch

from app.db.jobs import cleanup_old_jobs, get_queue_health

EXPECTED_RELEASE = "release-current"


class QueueHealthTests(unittest.TestCase):
    def test_queue_health_reports_missing_required_workers(self) -> None:
        connection = FakeConnection(workers=[])

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health(
                "postgresql://example/db", expected_release_sha=EXPECTED_RELEASE
            )

        self.assertFalse(health.worker_online)
        self.assertFalse(health.worker_releases_match)
        self.assertFalse(health.required_queues_ready)
        self.assertEqual(health.queue_depth, {"critical": 1, "default": 1})

    def test_queue_health_reports_required_queues_ready(self) -> None:
        connection = FakeConnection(
            workers=[
                {
                    "worker_id": "fast",
                    "queues_json": '["critical", "default"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 4,
                },
                {
                    "worker_id": "slow",
                    "queues_json": '["slow"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 8,
                },
                {
                    "worker_id": "reader",
                    "queues_json": '["reader"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 5,
                },
                {
                    "worker_id": "ai",
                    "queues_json": '["ai"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 6,
                },
                {
                    "worker_id": "poller",
                    "queues_json": '["gmail_poll"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 7,
                },
            ]
        )

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health(
                "postgresql://example/db", expected_release_sha=EXPECTED_RELEASE
            )

        self.assertTrue(health.worker_online)
        self.assertTrue(health.worker_releases_match)
        self.assertTrue(health.required_queues_ready)

    def test_queue_health_requires_the_gmail_poller(self) -> None:
        connection = FakeConnection(
            workers=[
                {
                    "worker_id": "workers-without-poller",
                    "queues_json": '["critical", "default", "reader", "slow"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 4,
                }
            ]
        )

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health(
                "postgresql://example/db", expected_release_sha=EXPECTED_RELEASE
            )

        self.assertTrue(health.worker_online)
        self.assertTrue(health.worker_releases_match)
        self.assertFalse(health.required_queues_ready)

    def test_queue_health_rejects_fresh_workers_from_another_release(self) -> None:
        connection = FakeConnection(
            workers=[
                {
                    "worker_id": "current-queues",
                    "queues_json": '["critical", "default", "reader", "slow"]',
                    "current_job_id": None,
                    "release_sha": EXPECTED_RELEASE,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 4,
                },
                {
                    "worker_id": "old-poller",
                    "queues_json": '["gmail_poll"]',
                    "current_job_id": None,
                    "release_sha": "release-old",
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 5,
                },
            ]
        )

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health(
                "postgresql://example/db", expected_release_sha=EXPECTED_RELEASE
            )

        self.assertTrue(health.worker_online)
        self.assertFalse(health.worker_releases_match)
        self.assertFalse(health.required_queues_ready)
        self.assertEqual(health.workers[1]["release_sha"], "release-old")
        self.assertFalse(health.workers[1]["release_matches_expected"])

    def test_retention_cleanup_covers_ephemeral_auth_mail_and_worker_records(self) -> None:
        connection = CleanupConnection()

        with patch("app.db.jobs.get_engine", return_value=CleanupEngine(connection)):
            deleted = cleanup_old_jobs("postgresql://example/db")

        sql = "\n".join(connection.statements)
        for table in (
            "oauth_login_sessions",
            "mobile_login_codes",
            "mobile_oauth_handoffs",
            "app_sessions",
            "mailbox_events",
            "worker_heartbeats",
            "gmail_pending_thread_actions",
            "gmail_pending_sends",
            "gmail_client_drafts",
            "background_job_events",
            "background_jobs",
        ):
            self.assertIn(table, sql)
        self.assertEqual(deleted, len(connection.statements))


class FakeScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar(self):
        return self.value


class FakeRowsResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, workers) -> None:
        self.workers = workers

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement):
        sql = str(statement)
        if "GROUP BY queue" in sql:
            return FakeRowsResult([{"queue": "critical", "count": 1}, {"queue": "default", "count": 1}])
        if "status = 'dead'" in sql:
            return FakeScalarResult(0)
        if "lease_expires_at < now()" in sql:
            return FakeScalarResult(0)
        if "MIN(created_at)" in sql:
            return FakeScalarResult(12)
        if "FROM worker_heartbeats" in sql:
            return FakeRowsResult(self.workers)
        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection


class CleanupResult:
    rowcount = 1


class CleanupConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement, _params=None) -> CleanupResult:
        self.statements.append(str(statement))
        return CleanupResult()


class CleanupEngine:
    def __init__(self, connection: CleanupConnection) -> None:
        self.connection = connection

    def begin(self):
        return nullcontext(self.connection)


if __name__ == "__main__":
    unittest.main()
