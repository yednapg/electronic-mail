from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException

from app import deploy_check
from app import schema_check
from app.api.routes import jobs as jobs_routes
from app.core import config as config_module
from app.core.config import load_settings
from app.core.observability import JSONLogFormatter
from app.core.rate_limit import RateLimitMiddleware
from app.main import app
from app.services.auth import is_email_allowed
from app.workers import gmail_poller, main as queue_worker


def production_environment(**overrides: str) -> dict[str, str]:
    values = {
        "APP_ENV": "production",
        "PORT": "3001",
        "DATABASE_URL": "postgresql://database.mail-launch.co/electronic_mail",
        "CORS_ORIGIN": "https://app.mail-launch.co",
        "WEB_APP_URL": "https://app.mail-launch.co",
        "APP_SESSION_SECRET": "session-secret-material-that-is-long-and-unique",
        "APP_ENCRYPTION_KEY": "encryption-key-material-that-is-long-and-unique",
        "SESSION_COOKIE_DOMAIN": ".mail-launch.co",
        "SESSION_COOKIE_SAMESITE": "lax",
        "REGISTRATION_MODE": "allowlist",
        "ALLOWED_EMAILS": "launch@mail-launch.co",
        "GOOGLE_CLIENT_ID": "client-id",
        "GOOGLE_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "https://api.mail-launch.co/auth/google/callback",
        "MOBILE_REDIRECT_URI": "electronicmail://auth/callback",
        "GMAIL_SYNC_SCOPE": "full",
        "GMAIL_RECENT_DAYS": "90",
        "GMAIL_PUBSUB_TOPIC": "projects/example/topics/gmail",
        "GMAIL_PUBSUB_SUBSCRIPTION": "gmail-push",
        "GMAIL_PUBSUB_PUSH_AUDIENCE": "https://api.mail-launch.co/v1/mailbox/pubsub",
        "GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL": "gmail-push@mail-launch-project.iam.gserviceaccount.com",
        "GMAIL_WATCH_RENEWAL_HOURS": "24",
        "OPENAI_API_KEY": "",
        "OPENAI_REQUIRED": "false",
        "AI_GROUPING_ENABLED": "false",
        "RATE_LIMIT_ENABLED": "true",
        "RELEASE_SHA": "abcdef1234567890",
        "OPS_ADMIN_EMAILS": "launch@mail-launch.co",
    }
    values.update(overrides)
    return values


class ReleaseReadinessTests(unittest.TestCase):
    def test_valid_allowlisted_production_configuration_is_ready(self) -> None:
        with patch.dict(os.environ, production_environment(), clear=True):
            self.assertEqual(load_settings().readiness_errors(), [])

    def test_open_registration_does_not_require_allowlist(self) -> None:
        with patch.dict(
            os.environ,
            production_environment(REGISTRATION_MODE="open", ALLOWED_EMAILS=""),
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.readiness_errors(), [])
        self.assertTrue(is_email_allowed(settings, "new-user@example.net"))

    def test_production_rejects_partial_sync_weak_secrets_and_ai(self) -> None:
        with patch.dict(
            os.environ,
            production_environment(
                GMAIL_SYNC_SCOPE="recent",
                APP_SESSION_SECRET="replace-me",
                APP_ENCRYPTION_KEY="replace-me",
                OPENAI_API_KEY="must-not-ship",
            ),
            clear=True,
        ):
            errors = load_settings().readiness_errors()

        self.assertTrue(any("GMAIL_SYNC_SCOPE=full" in error for error in errors))
        self.assertTrue(any("APP_SESSION_SECRET" in error for error in errors))
        self.assertTrue(any("APP_ENCRYPTION_KEY" in error for error in errors))
        self.assertTrue(any("AI/OpenAI" in error for error in errors))

    def test_production_rejects_cross_site_session_cookies(self) -> None:
        with patch.dict(
            os.environ,
            production_environment(SESSION_COOKIE_SAMESITE="none"),
            clear=True,
        ):
            errors = load_settings().readiness_errors()

        self.assertTrue(any("SESSION_COOKIE_SAMESITE=lax" in error for error in errors))

    def test_production_rejects_template_placeholders_and_reused_key_material(self) -> None:
        placeholder_environment = production_environment(
            DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/electronic_mail",
            APP_SESSION_SECRET="generate-at-least-32-random-characters",
            APP_ENCRYPTION_KEY="generate-at-least-32-random-characters",
            GOOGLE_CLIENT_ID="replace-in-secret-store",
            GMAIL_PUBSUB_TOPIC="projects/PROJECT_ID/topics/gmail-push",
            GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL="gmail-push@PROJECT_ID.iam.gserviceaccount.com",
            RELEASE_SHA="set-to-deployed-git-sha",
        )
        with patch.dict(os.environ, placeholder_environment, clear=True):
            errors = load_settings().readiness_errors()

        self.assertTrue(any("placeholder" in error.lower() for error in errors))
        self.assertTrue(any("different key material" in error for error in errors))
        self.assertTrue(any("Google OAuth" in error for error in errors))
        self.assertTrue(any("GMAIL_PUBSUB_TOPIC" in error for error in errors))
        self.assertTrue(any("RELEASE_SHA" in error for error in errors))

    def test_production_rejects_reserved_service_url_hostnames(self) -> None:
        cases = (
            (
                {"CORS_ORIGIN": "https://app.example.com"},
                "CORS_ORIGIN must use a concrete non-reserved hostname",
            ),
            (
                {"WEB_APP_URL": "https://app.placeholder.example"},
                "WEB_APP_URL must use a concrete non-reserved hostname",
            ),
            (
                {"GOOGLE_REDIRECT_URI": "https://api.example.org/auth/google/callback"},
                "GOOGLE_REDIRECT_URI must use a concrete non-reserved hostname",
            ),
            (
                {"GMAIL_PUBSUB_PUSH_AUDIENCE": "https://localhost/v1/mailbox/pubsub"},
                "GMAIL_PUBSUB_PUSH_AUDIENCE must use a concrete non-reserved hostname",
            ),
        )

        for overrides, expected_error in cases:
            with self.subTest(overrides=overrides):
                with patch.dict(os.environ, production_environment(**overrides), clear=True):
                    errors = load_settings().readiness_errors()

                self.assertIn(expected_error, errors)

    def test_deploy_guard_rejects_example_service_url_template(self) -> None:
        with patch.dict(
            os.environ,
            production_environment(
                CORS_ORIGIN="https://app.example.com",
                WEB_APP_URL="https://app.example.com",
                SESSION_COOKIE_DOMAIN=".example.com",
                GOOGLE_REDIRECT_URI="https://api.example.com/auth/google/callback",
                GMAIL_PUBSUB_PUSH_AUDIENCE="https://api.example.com/v1/mailbox/pubsub",
            ),
            clear=True,
        ):
            errors = load_settings().readiness_errors()
            deploy_result = deploy_check.main()

        self.assertIn("CORS_ORIGIN must use a concrete non-reserved hostname", errors)
        self.assertIn("WEB_APP_URL must use a concrete non-reserved hostname", errors)
        self.assertIn("GOOGLE_REDIRECT_URI must use a concrete non-reserved hostname", errors)
        self.assertIn("GMAIL_PUBSUB_PUSH_AUDIENCE must use a concrete non-reserved hostname", errors)
        self.assertEqual(deploy_result, 1)

    def test_process_environment_wins_over_local_dotenv_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.local").write_text("DATABASE_URL=postgresql:///local-file\nLOCAL_ONLY=yes\n")
            (root / ".env").write_text("DATABASE_URL=postgresql:///base-file\nBASE_ONLY=yes\n")
            with (
                patch.object(config_module, "BACKEND_DIR", root),
                patch.dict(os.environ, {"DATABASE_URL": "postgresql:///explicit-process"}, clear=True),
            ):
                config_module._load_environment_files()
                self.assertEqual(os.environ["DATABASE_URL"], "postgresql:///explicit-process")
                self.assertEqual(os.environ["LOCAL_ONLY"], "yes")
                self.assertEqual(os.environ["BASE_ONLY"], "yes")

    def test_staging_must_stay_allowlisted(self) -> None:
        with patch.dict(
            os.environ,
            production_environment(APP_ENV="staging", REGISTRATION_MODE="open", ALLOWED_EMAILS=""),
            clear=True,
        ):
            errors = load_settings().readiness_errors()

        self.assertTrue(any("required in staging" in error for error in errors))

    def test_deploy_guard_accepts_complete_environment_and_requires_explicit_registration(self) -> None:
        with patch.dict(os.environ, production_environment(), clear=True):
            self.assertEqual(deploy_check.main(), 0)
        with patch.dict(os.environ, production_environment(REGISTRATION_MODE=""), clear=True):
            self.assertEqual(deploy_check.main(), 1)
        with patch.dict(os.environ, production_environment(OPS_ADMIN_EMAILS=","), clear=True):
            self.assertEqual(deploy_check.main(), 1)
        with patch.dict(
            os.environ,
            production_environment(GMAIL_POLL_INTERVAL_SECONDS="not-a-number"),
            clear=True,
        ):
            self.assertEqual(deploy_check.main(), 1)

    def test_railway_cannot_bypass_production_guards_with_local_app_env(self) -> None:
        with patch.dict(
            os.environ,
            {"APP_ENV": "local", "RAILWAY_ENVIRONMENT": "production"},
            clear=True,
        ):
            self.assertEqual(deploy_check.main(), 1)

    def test_health_response_has_request_correlation_and_security_headers(self) -> None:
        response = TestClient(app).get("/health", headers={"X-Request-ID": "release-smoke-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "release-smoke-1")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("release", response.json())

    def test_sensitive_write_routes_are_rate_limited(self) -> None:
        limited_app = FastAPI()
        limited_app.add_middleware(RateLimitMiddleware, enabled=True)

        @limited_app.post("/v1/mailbox/sync-now")
        def sync_now() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(limited_app)
        responses = [client.post("/v1/mailbox/sync-now") for _ in range(13)]

        self.assertTrue(all(response.status_code == 200 for response in responses[:12]))
        self.assertEqual(responses[12].status_code, 429)
        self.assertIn("retry-after", responses[12].headers)

    def test_public_mobile_exchange_cannot_bypass_limit_by_rotating_fake_credentials(self) -> None:
        limited_app = FastAPI()
        limited_app.add_middleware(RateLimitMiddleware, enabled=True)

        @limited_app.post("/v1/auth/mobile/exchange")
        def exchange() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(limited_app)
        responses = [
            client.post(
                "/v1/auth/mobile/exchange",
                headers={"Authorization": f"Bearer attacker-controlled-{index}"},
            )
            for index in range(21)
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses[:20]))
        self.assertEqual(responses[20].status_code, 429)

    def test_authenticated_limit_uses_only_stable_app_session_cookie(self) -> None:
        limited_app = FastAPI()
        limited_app.add_middleware(RateLimitMiddleware, enabled=True)

        @limited_app.post("/v1/mailbox/sync-now")
        def sync_now() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(limited_app)
        responses = [
            client.post(
                "/v1/mailbox/sync-now",
                headers={"Cookie": f"dp_session=stable-token; noise={index}"},
            )
            for index in range(13)
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses[:12]))
        self.assertEqual(responses[12].status_code, 429)

    def test_local_public_limit_does_not_trust_rotating_forwarded_headers(self) -> None:
        limited_app = FastAPI()
        limited_app.add_middleware(RateLimitMiddleware, enabled=True)

        @limited_app.get("/auth/google")
        def oauth_start() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(limited_app)
        responses = [
            client.get("/auth/google", headers={"X-Forwarded-For": f"192.0.2.{index}"})
            for index in range(21)
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses[:20]))
        self.assertEqual(responses[20].status_code, 429)

    def test_mailbox_event_stream_openings_are_rate_limited_per_session(self) -> None:
        limited_app = FastAPI()
        limited_app.add_middleware(RateLimitMiddleware, enabled=True)

        @limited_app.get("/v1/events/mailbox")
        def events() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(limited_app)
        responses = [
            client.get(
                "/v1/events/mailbox",
                headers={"Cookie": "dp_session=stable-token"},
            )
            for _index in range(13)
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses[:12]))
        self.assertEqual(responses[12].status_code, 429)

    def test_json_logs_do_not_serialize_exception_messages_or_tracebacks(self) -> None:
        secret = "postgresql://admin:do-not-log@database.internal/app"
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            record = logging.LogRecord(
                name="release-test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="request.failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        payload = json.loads(JSONLogFormatter(release_sha="abcdef123").format(record))
        serialized = json.dumps(payload)
        self.assertEqual(payload["exception_type"], "RuntimeError")
        self.assertNotIn(secret, serialized)
        self.assertNotIn("Traceback", serialized)

    def test_worker_fatal_errors_report_only_exception_category(self) -> None:
        secret = "postgresql://admin:worker-secret@database.internal/app"
        for module in (queue_worker, gmail_poller):
            with (
                patch.object(module, "main", side_effect=RuntimeError(secret)),
                self.assertLogs(module.logger, level="ERROR") as captured,
            ):
                self.assertEqual(module._run_cli(), 1)

            self.assertEqual(captured.records[0].event_fields["exception_type"], "RuntimeError")
            self.assertNotIn(secret, captured.output[0])

    def test_background_job_heartbeat_keeps_worker_release_identity(self) -> None:
        stop_event = Mock()
        stop_event.wait.side_effect = [False, True]

        with patch.object(queue_worker, "renew_heartbeat") as renew:
            queue_worker._job_heartbeat_loop(
                "postgresql://example/db",
                "worker-1",
                ["critical"],
                "abcdef123",
                "job-1",
                30.0,
                stop_event,
            )

        renew.assert_called_once_with(
            "postgresql://example/db",
            worker_id="worker-1",
            queues=["critical"],
            release_sha="abcdef123",
            current_job_id="job-1",
        )

    def test_ops_health_fails_closed_when_admin_allowlist_is_empty(self) -> None:
        with (
            patch.dict(os.environ, {"OPS_ADMIN_EMAILS": ","}, clear=False),
            patch.object(
                jobs_routes,
                "require_current_user",
                return_value=type("User", (), {"email": "user@example.com"})(),
            ),
            self.assertRaises(HTTPException) as captured,
        ):
            jobs_routes.ops_health(type("Request", (), {})())

        self.assertEqual(getattr(captured.exception, "status_code", None), 403)

    def test_ops_health_identifies_api_release_and_worker_release_match(self) -> None:
        queue_health = SimpleNamespace(
            queue_depth={},
            dead_jobs=0,
            stale_running_jobs=0,
            oldest_queued_age_seconds=None,
            workers=[{"worker_id": "worker-1", "release_sha": "abcdef123"}],
            worker_online=True,
            worker_releases_match=True,
            required_queues_ready=True,
        )
        route_settings = SimpleNamespace(
            app_env="production",
            release_sha="abcdef123",
            database_path="postgresql://example/db",
        )
        with (
            patch.dict(os.environ, {"OPS_ADMIN_EMAILS": "launch@mail-launch.co"}, clear=False),
            patch.object(jobs_routes, "settings", route_settings),
            patch.object(
                jobs_routes,
                "require_current_user",
                return_value=SimpleNamespace(email="launch@mail-launch.co"),
            ),
            patch.object(jobs_routes, "get_queue_health", return_value=queue_health) as get_health,
        ):
            response = jobs_routes.ops_health(type("Request", (), {})())

        self.assertEqual(response.environment, "production")
        self.assertEqual(response.release, "abcdef123")
        self.assertTrue(response.worker_releases_match)
        get_health.assert_called_once_with(
            "postgresql://example/db", expected_release_sha="abcdef123"
        )

    def test_all_hosted_services_wait_for_schema_before_starting(self) -> None:
        backend_dir = Path(__file__).resolve().parents[1]
        config_paths = [
            backend_dir / "railway.json",
            backend_dir / "railway.worker-fast.json",
            backend_dir / "railway.worker-reader.json",
            backend_dir / "railway.worker-slow.json",
            backend_dir / "railway.worker-poller.json",
        ]

        for path in config_paths:
            config = json.loads(path.read_text())
            command = config["deploy"]["startCommand"]
            self.assertIn("python -m app.deploy_check", command)
            self.assertIn("python -m app.schema_check --wait-seconds 120", command)
            self.assertIn("exec ", command)

    def test_schema_startup_gate_checks_revision_and_launch_columns(self) -> None:
        settings = type(
            "Settings",
            (),
            {"database_backend": "postgres", "database_path": "postgresql://example/db"},
        )()
        connection = _SchemaConnection(revision=schema_check.ALEMBIC_HEAD_REVISION)
        with patch.object(schema_check, "get_engine", return_value=_SchemaEngine(connection)):
            self.assertTrue(schema_check.schema_is_current(settings))

        self.assertTrue(any("gmail_client_drafts" in query for query in connection.queries))
        self.assertTrue(any("exchange_code_challenge" in query for query in connection.queries))
        self.assertTrue(any("gmail_thread_order_state" in query for query in connection.queries))
        self.assertTrue(any("gmail_thread_order_entries" in query for query in connection.queries))
        self.assertTrue(any("started_epoch FROM oauth_login_sessions" in query for query in connection.queries))
        self.assertTrue(any("google_subject_deletion_tombstones" in query for query in connection.queries))
        self.assertTrue(any("release_sha FROM worker_heartbeats" in query for query in connection.queries))

        with patch.object(
            schema_check,
            "get_engine",
            return_value=_SchemaEngine(_SchemaConnection(revision="20260713_0019")),
        ):
            self.assertFalse(schema_check.schema_is_current(settings))


class _SchemaResult:
    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def scalar(self) -> str | None:
        return self.value


class _SchemaConnection:
    def __init__(self, *, revision: str) -> None:
        self.revision = revision
        self.queries: list[str] = []

    def __enter__(self) -> "_SchemaConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def exec_driver_sql(self, query: str) -> _SchemaResult:
        self.queries.append(query)
        return _SchemaResult(self.revision if "alembic_version" in query else None)


class _SchemaEngine:
    def __init__(self, connection: _SchemaConnection) -> None:
        self.connection = connection

    def connect(self) -> _SchemaConnection:
        return self.connection


if __name__ == "__main__":
    unittest.main()
