from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import schema_check
from app.api.routes import system as system_routes
from app.main import app


class SystemRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ready_rejects_non_postgres_runtime(self) -> None:
        settings = SimpleNamespace(
            app_env="local",
            database_path="sqlite:///dev.db",
            database_backend="sqlite",
            google_configured=False,
            openai_configured=False,
            openai_model="gpt-5.4-mini",
            openai_reasoning_effort="medium",
            readiness_errors=lambda: [],
        )

        with patch.object(system_routes, "settings", settings):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Postgres", response.json()["detail"]["errors"][0])

    def test_ready_reports_missing_production_config(self) -> None:
        settings = SimpleNamespace(
            app_env="production",
            database_path="postgresql://example/db",
            database_backend="postgres",
            google_configured=False,
            openai_configured=False,
            openai_model="gpt-5.4-mini",
            openai_reasoning_effort="medium",
            readiness_errors=lambda: ["GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required"],
        )

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(
                system_routes,
                "schema_probe_connection",
                return_value=nullcontext(FakeConnection()),
            ),
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "not_ready")
        self.assertIn("GOOGLE_CLIENT_ID", response.json()["detail"]["errors"][0])

    def test_ready_reports_postgres_database_after_migration_check_passes(self) -> None:
        settings = SimpleNamespace(
            app_env="staging",
            database_path="postgresql://example/db",
            database_backend="postgres",
            google_configured=True,
            openai_configured=True,
            openai_model="gpt-5.4-mini",
            openai_reasoning_effort="medium",
            readiness_errors=lambda: [],
        )

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(
                system_routes,
                "schema_probe_connection",
                return_value=nullcontext(FakeConnection()),
            ),
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "postgres")
        self.assertEqual(response.json()["schema_head"], system_routes.ALEMBIC_HEAD_REVISION)

    def test_ready_probes_latest_release_columns_with_bounded_lock_waits(self) -> None:
        settings = SimpleNamespace(
            app_env="production",
            database_path="postgresql://example/db",
            database_backend="postgres",
            google_configured=True,
            readiness_errors=lambda: [],
        )
        connection = FakeConnection()

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(
                schema_check,
                "connect_bounded_schema_probe",
                return_value=nullcontext(connection),
            ),
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("previous_labels_json" in query for query in connection.queries))
        self.assertTrue(any("request_hash" in query for query in connection.queries))
        self.assertTrue(any("reconcile_generation" in query for query in connection.queries))
        self.assertTrue(any("gmail_reconcile_seen" in query for query in connection.queries))
        self.assertTrue(any("SET LOCAL lock_timeout" in query for query in connection.queries))
        self.assertTrue(any("SET LOCAL statement_timeout" in query for query in connection.queries))

    def test_ready_rejects_old_migration_revision(self) -> None:
        settings = SimpleNamespace(
            app_env="local",
            database_path="postgresql://example/db",
            database_backend="postgres",
            google_configured=True,
            openai_configured=True,
            openai_model="gpt-5.4-mini",
            openai_reasoning_effort="medium",
            readiness_errors=lambda: [],
        )

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(
                system_routes,
                "schema_probe_connection",
                return_value=nullcontext(FakeConnection(revision="20260519_0009")),
            ),
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertIn(system_routes.ALEMBIC_HEAD_REVISION, response.json()["detail"]["errors"][0])

    def test_ready_does_not_expose_database_exception_details(self) -> None:
        secret_detail = "postgresql://admin:super-secret@database.internal/app"
        settings = SimpleNamespace(
            app_env="production",
            database_path="postgresql://example/db",
            database_backend="postgres",
            google_configured=True,
            readiness_errors=lambda: [],
        )

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(
                system_routes,
                "schema_probe_connection",
                return_value=FailingProbe(secret_detail),
            ),
            self.assertLogs(system_routes.logger, level="ERROR") as captured_logs,
        ):
            response = self.client.get("/ready", headers={"X-Request-ID": "readiness-safe-1"})

        response_text = json.dumps(response.json())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["errors"], ["Postgres readiness check failed"])
        self.assertNotIn(secret_detail, response_text)
        self.assertEqual(response.headers["x-request-id"], "readiness-safe-1")
        self.assertEqual(captured_logs.records[0].event_fields["exception_type"], "RuntimeError")
        self.assertNotIn(secret_detail, captured_logs.output[0])


class FakeResult:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def scalar(self) -> str | None:
        return self.value


class FakeConnection:
    def __init__(self, revision: str | None = None) -> None:
        self.revision = revision or system_routes.ALEMBIC_HEAD_REVISION
        self.queries: list[str] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def exec_driver_sql(self, sql: str) -> FakeResult:
        self.queries.append(sql)
        if "alembic_version" in sql:
            return FakeResult(self.revision)
        return FakeResult()


class FailingProbe:
    def __init__(self, detail: str) -> None:
        self.detail = detail

    def __enter__(self) -> FakeConnection:
        raise RuntimeError(self.detail)

    def __exit__(self, *_args) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
