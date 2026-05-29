from __future__ import annotations

import unittest
from unittest.mock import patch

from app.db.jobs import get_queue_health


class QueueHealthTests(unittest.TestCase):
    def test_queue_health_reports_missing_required_workers(self) -> None:
        connection = FakeConnection(workers=[])

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health("postgresql://example/db")

        self.assertFalse(health.worker_online)
        self.assertFalse(health.required_queues_ready)
        self.assertEqual(health.queue_depth, {"critical": 1, "default": 1})

    def test_queue_health_reports_required_queues_ready(self) -> None:
        connection = FakeConnection(
            workers=[
                {
                    "worker_id": "fast",
                    "queues_json": '["critical", "default"]',
                    "current_job_id": None,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 4,
                },
                {
                    "worker_id": "slow",
                    "queues_json": '["slow"]',
                    "current_job_id": None,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 8,
                },
                {
                    "worker_id": "reader",
                    "queues_json": '["reader"]',
                    "current_job_id": None,
                    "last_seen_at": "2026-05-21T10:00:00+00:00",
                    "age_seconds": 5,
                },
            ]
        )

        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            health = get_queue_health("postgresql://example/db")

        self.assertTrue(health.worker_online)
        self.assertTrue(health.required_queues_ready)


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


if __name__ == "__main__":
    unittest.main()
