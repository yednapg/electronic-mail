from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import patch

from app.db import jobs


class GoogleTokenRevocationJobTests(unittest.TestCase):
    def test_active_guard_filters_queued_or_running_jobs_by_subject_payload(self) -> None:
        connection = _RevocationConnection(active=True)

        with patch.object(jobs, "get_engine", return_value=_RevocationEngine(connection)):
            active = jobs.has_active_google_token_revocation(
                "postgresql://example/db",
                subject_hash="subject-hash",
            )

        self.assertTrue(active)
        sql, params = connection.calls[0]
        self.assertIn("kind = 'google_token_revoke'", sql)
        self.assertIn("status IN ('queued', 'running')", sql)
        self.assertIn("payload_json::jsonb ->> 'subject_hash'", sql)
        self.assertEqual(params, {"subject_hash": "subject-hash"})

    def test_subject_wide_completion_resolves_sibling_revocation_jobs(self) -> None:
        connection = _RevocationConnection(completed_ids=["job-1", "job-2"])

        with patch.object(jobs, "get_engine", return_value=_RevocationEngine(connection)):
            completed = jobs.complete_google_token_revocations_for_subject(
                "postgresql://example/db",
                subject_hash="subject-hash",
            )

        self.assertEqual(completed, 2)
        update_sql, update_params = connection.calls[0]
        self.assertIn("status IN ('queued', 'running')", update_sql)
        self.assertIn("payload_json::jsonb ->> 'subject_hash'", update_sql)
        self.assertEqual(update_params, {"subject_hash": "subject-hash"})
        event_calls = [params for sql, params in connection.calls if "background_job_events" in sql]
        self.assertEqual([params["job_id"] for params in event_calls], ["job-1", "job-2"])
        self.assertTrue(all(params["event_type"] == "succeeded" for params in event_calls))

    def test_invalid_credential_completion_resolves_only_requested_job(self) -> None:
        connection = _RevocationConnection(completed_ids=["job-1"])

        with patch.object(jobs, "get_engine", return_value=_RevocationEngine(connection)):
            completed = jobs.complete_google_token_revocation_job(
                "postgresql://example/db",
                job_id="job-1",
            )

        self.assertTrue(completed)
        update_sql, update_params = connection.calls[0]
        self.assertIn("id = :job_id", update_sql)
        self.assertIn("kind = 'google_token_revoke'", update_sql)
        self.assertNotIn("subject_hash", update_sql)
        self.assertEqual(update_params, {"job_id": "job-1"})
        event_calls = [params for sql, params in connection.calls if "background_job_events" in sql]
        self.assertEqual(len(event_calls), 1)
        self.assertEqual(event_calls[0]["metadata_json"], '{"reason": "google_credential_already_invalid"}')


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _RowsResult:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def scalars(self) -> "_RowsResult":
        return self

    def all(self) -> list[str]:
        return self.values

    def scalar_one_or_none(self) -> str | None:
        return self.values[0] if self.values else None


class _RevocationConnection:
    def __init__(self, *, active: bool = False, completed_ids: list[str] | None = None) -> None:
        self.active = active
        self.completed_ids = completed_ids or []
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> "_RevocationConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "SELECT EXISTS" in sql:
            return _ScalarResult(self.active)
        if "UPDATE background_jobs" in sql:
            return _RowsResult(self.completed_ids)
        if "INSERT INTO background_job_events" in sql:
            return _RowsResult([])
        raise AssertionError(f"Unexpected SQL: {sql}")


class _RevocationEngine:
    def __init__(self, connection: _RevocationConnection) -> None:
        self.connection = connection

    def connect(self) -> _RevocationConnection:
        return self.connection

    def begin(self):
        return nullcontext(self.connection)


if __name__ == "__main__":
    unittest.main()
