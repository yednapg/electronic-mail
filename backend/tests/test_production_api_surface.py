from __future__ import annotations

from dataclasses import replace
import unittest

from fastapi.testclient import TestClient

from app.main import create_app, settings


REQUIRED_PATHS = {
    "/",
    "/health",
    "/ready",
    "/auth/google",
    "/auth/google/callback",
    "/auth/mobile/complete",
    "/v1/auth/mobile/exchange",
    "/v1/auth/mobile/handoff/{handoff_id}",
    "/v1/auth/logout",
    "/v1/auth/google",
    "/v1/auth/account",
    "/v1/auth/google/state",
    "/v1/app/session",
    "/v1/mailbox",
    "/v1/mailbox/search",
    "/v1/mailbox/threads/{group_id}",
    "/v1/mailbox/thread-actions",
    "/v1/mailbox/compose",
    "/v1/mailbox/sync-state",
    "/v1/mailbox/sync-now",
    "/v1/events/mailbox",
    "/v1/mailbox/pubsub",
    "/v1/jobs/{job_id}",
    "/v1/ops/health",
}

LEGACY_PATHS = {
    "/dashboard",
    "/v1/dashboard",
    "/v1/first-run/import-jobs",
    "/v1/post-login/readiness",
    "/gmail/threads/{thread_id}/archive",
    "/v1/gmail/threads/{thread_id}/archive",
    "/v1/tasks",
    "/v1/entities/{entity_id}/complete",
    "/v1/mail-groups",
    "/v1/gmail-view",
}


def registered_paths(application) -> set[str]:
    return set(application.openapi()["paths"])


class ProductionAPISurfaceTests(unittest.TestCase):
    def test_production_like_surface_hides_legacy_routes_and_schema_browsers(self) -> None:
        for environment in ("staging", "production"):
            with self.subTest(environment=environment):
                application = create_app(
                    replace(settings, app_env=environment, rate_limit_enabled=False)
                )
                client = TestClient(application)
                paths = registered_paths(application)

                self.assertTrue(REQUIRED_PATHS.issubset(paths))
                self.assertTrue(LEGACY_PATHS.isdisjoint(paths))
                hidden_requests = (
                    ("GET", "/docs"),
                    ("GET", "/redoc"),
                    ("GET", "/openapi.json"),
                    ("GET", "/dashboard"),
                    ("GET", "/v1/first-run/import-jobs/latest"),
                    ("GET", "/v1/post-login/readiness"),
                    ("POST", "/v1/gmail/threads/thread-1/archive"),
                    ("POST", "/v1/tasks"),
                    ("POST", "/v1/entities/entity-1/complete"),
                    ("GET", "/v1/mail-groups"),
                    ("GET", "/v1/gmail-view"),
                )
                for method, path in hidden_requests:
                    self.assertEqual(client.request(method, path).status_code, 404, path)

                discovery = client.get("/").json()
                self.assertEqual(discovery["service"], "Electronic Mail Backend")
                self.assertEqual(discovery["app_session"], "/v1/app/session")
                self.assertEqual(discovery["mailbox"], "/v1/mailbox")
                self.assertNotIn("dashboard", discovery)
                self.assertNotIn("docs", discovery)

    def test_local_surface_keeps_legacy_routes_and_schema_browsers(self) -> None:
        application = create_app(
            replace(settings, app_env="local", rate_limit_enabled=False)
        )
        client = TestClient(application)
        paths = registered_paths(application)

        self.assertTrue(REQUIRED_PATHS.issubset(paths))
        self.assertTrue(LEGACY_PATHS.issubset(paths))
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(client.get("/dashboard").status_code, 200)

        discovery = client.get("/").json()
        self.assertEqual(discovery["dashboard"], "/dashboard")
        self.assertEqual(discovery["first_run"], "/v1/first-run/import-jobs")
        self.assertEqual(discovery["tasks"], "/v1/tasks")
        self.assertEqual(discovery["docs"], "/docs")


if __name__ == "__main__":
    unittest.main()
