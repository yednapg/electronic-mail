from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import system as system_routes
from app.db.repository import initialize_database
from app.main import app


class SystemRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_ready_returns_ready_for_initialized_local_database(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "ready.db"
            initialize_database(str(database_path))
            settings = SimpleNamespace(
                app_env="local",
                database_url=f"file:{database_path}",
                database_path=database_path,
                database_backend="sqlite",
                sqlite_database_path=database_path,
                google_configured=False,
                openai_configured=False,
                openai_model="gpt-5.4-mini",
                openai_reasoning_effort="medium",
                readiness_errors=lambda: [],
            )

            with patch.object(system_routes, "settings", settings):
                response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["database"], "sqlite")
        self.assertFalse(response.json()["openai_configured"])
        self.assertEqual(response.json()["openai_model"], "gpt-5.4-mini")
        self.assertEqual(response.json()["openai_reasoning_effort"], "medium")

    def test_ready_reports_missing_production_config(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "ready.db"
            initialize_database(str(database_path))
            settings = SimpleNamespace(
                app_env="production",
                database_url=f"file:{database_path}",
                database_path=database_path,
                database_backend="sqlite",
                sqlite_database_path=database_path,
                google_configured=False,
                openai_configured=False,
                openai_model="gpt-5.4-mini",
                openai_reasoning_effort="medium",
                readiness_errors=lambda: ["GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required"],
            )

            with patch.object(system_routes, "settings", settings):
                response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "not_ready")
        self.assertIn("GOOGLE_CLIENT_ID", response.json()["detail"]["errors"][0])

    def test_ready_reports_missing_database(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            settings = SimpleNamespace(
                app_env="local",
                database_url=f"file:{Path(tmp_dir) / 'missing.db'}",
                database_path=Path(tmp_dir) / "missing.db",
                database_backend="sqlite",
                sqlite_database_path=Path(tmp_dir) / "missing.db",
                google_configured=False,
                openai_configured=False,
                openai_model="gpt-5.4-mini",
                openai_reasoning_effort="medium",
                readiness_errors=lambda: [],
            )

            with patch.object(system_routes, "settings", settings):
                response = self.client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "not_ready")
        self.assertIn("Database file does not exist", response.json()["detail"]["errors"][0])

    def test_ready_reports_postgres_database_after_migration_check_passes(self) -> None:
        settings = SimpleNamespace(
            app_env="staging",
            database_url="postgresql://example/db",
            database_path="postgresql://example/db",
            database_backend="postgres",
            sqlite_database_path=None,
            google_configured=True,
            openai_configured=True,
            openai_model="gpt-5.4-mini",
            openai_reasoning_effort="medium",
            readiness_errors=lambda: [],
        )

        with (
            patch.object(system_routes, "settings", settings),
            patch.object(system_routes, "get_engine", return_value=FakeEngine()),
        ):
            response = self.client.get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "postgres")


class FakeResult:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def scalar(self) -> str | None:
        return self.value


class FakeConnection:
    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def exec_driver_sql(self, sql: str) -> FakeResult:
        if "alembic_version" in sql:
            return FakeResult(system_routes.ALEMBIC_BASELINE_REVISION)
        return FakeResult()


class FakeEngine:
    def connect(self) -> FakeConnection:
        return FakeConnection()


if __name__ == "__main__":
    unittest.main()
