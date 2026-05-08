from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import dashboard as dashboard_routes
from app.db.repository import (
    create_dashboard_import_job,
    get_dashboard_import_job,
    initialize_database,
    mark_dashboard_import_job_running,
    mark_dashboard_import_job_succeeded,
    update_dashboard_import_job_progress,
)
from app.main import app
from app.schemas.domain import DashboardBriefing, DashboardProfile, FeedResponse, GoogleAuthState


class DashboardImportJobRepositoryTests(unittest.TestCase):
    def test_job_transitions_are_persisted(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database_path = str(Path(tmp_dir) / "jobs.db")
            initialize_database(database_path)

            queued = create_dashboard_import_job(database_path)
            self.assertEqual(queued.status, "queued")
            self.assertEqual(queued.stage, "queued")
            self.assertEqual(queued.imported_count, 0)
            self.assertIsNone(queued.total_count)
            self.assertIsNone(queued.started_at)
            self.assertIsNone(queued.completed_at)

            running = mark_dashboard_import_job_running(database_path, queued.id)
            self.assertEqual(running.status, "running")
            self.assertEqual(running.stage, "starting")
            self.assertIsNotNone(running.started_at)
            self.assertIsNone(running.completed_at)

            progress = update_dashboard_import_job_progress(
                database_path,
                queued.id,
                stage="gmail_persisted",
                imported_count=2,
                total_count=5,
                source_records=2,
            )
            self.assertEqual(progress.stage, "gmail_persisted")
            self.assertEqual(progress.imported_count, 2)
            self.assertEqual(progress.total_count, 5)
            self.assertEqual(progress.source_records, 2)

            succeeded = mark_dashboard_import_job_succeeded(
                database_path,
                queued.id,
                result_status="ready",
                source_records=3,
                changed_entities=2,
                refreshed_entities=4,
            )
            self.assertEqual(succeeded.status, "succeeded")
            self.assertEqual(succeeded.stage, "completed")
            self.assertEqual(succeeded.result_status, "ready")
            self.assertEqual(succeeded.imported_count, 3)
            self.assertEqual(succeeded.total_count, 5)
            self.assertEqual(succeeded.source_records, 3)
            self.assertEqual(succeeded.changed_entities, 2)
            self.assertEqual(succeeded.refreshed_entities, 4)
            self.assertIsNotNone(succeeded.completed_at)

            reloaded = get_dashboard_import_job(database_path, queued.id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.status, "succeeded")


class DashboardImportJobRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.database_path = Path(self.tmp_dir.name) / "dashboard.db"
        initialize_database(str(self.database_path))
        self.settings = SimpleNamespace(database_path=self.database_path, google_configured=True)
        self.settings_patch = patch.object(dashboard_routes, "settings", self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.tmp_dir.cleanup)
        self.client = TestClient(app)

    @patch("app.services.dashboard.fetch_google_account_profile")
    @patch("app.services.dashboard.fetch_google_source_records")
    @patch("app.services.dashboard.generate_dashboard_briefing")
    @patch("app.services.dashboard.build_feed_from_entities")
    @patch("app.services.dashboard.get_google_auth_state")
    def test_dashboard_read_does_not_call_gmail(
        self,
        mock_auth: Mock,
        mock_build_feed: Mock,
        mock_briefing: Mock,
        mock_fetch_source_records: Mock,
        mock_fetch_profile: Mock,
    ) -> None:
        mock_auth.return_value = GoogleAuthState(available=True, connected=True)
        mock_build_feed.return_value = FeedResponse()
        mock_briefing.return_value = DashboardBriefing(headline="Morning", brief="Nothing urgent.")

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        mock_fetch_source_records.assert_not_called()
        mock_fetch_profile.assert_not_called()

    @patch("app.services.dashboard.fetch_google_account_profile")
    @patch("app.services.dashboard.generate_dashboard_briefing")
    @patch("app.services.dashboard.build_feed_from_entities")
    @patch("app.services.dashboard.refresh_ai_suggestions_for_entities")
    @patch("app.services.dashboard.list_all_loaded_entities")
    @patch("app.services.dashboard.hydrate_persistent_memory")
    @patch("app.services.dashboard.fetch_google_source_records")
    @patch("app.services.dashboard.get_google_auth_state")
    def test_prepare_compatibility_path_invokes_sync(
        self,
        mock_auth: Mock,
        mock_fetch_source_records: Mock,
        mock_hydrate: Mock,
        mock_entities: Mock,
        mock_refresh: Mock,
        mock_build_feed: Mock,
        mock_briefing: Mock,
        mock_fetch_profile: Mock,
    ) -> None:
        mock_auth.return_value = GoogleAuthState(available=True, connected=True)
        def fake_fetch_source_records(settings, *, progress_callback=None, collect_records=True):
            self.assertIs(settings, self.settings)
            self.assertFalse(collect_records)
            self.assertIsNotNone(progress_callback)
            progress_callback("gmail_listing", 0, 2)
            progress_callback("gmail_persisted", 2, 2)
            return []

        mock_fetch_source_records.side_effect = fake_fetch_source_records
        mock_hydrate.return_value = {"entity-1"}
        mock_entities.return_value = [SimpleNamespace(entity=SimpleNamespace(id="entity-1"))]
        mock_build_feed.return_value = FeedResponse()
        mock_fetch_profile.return_value = DashboardProfile(email="person@example.com")
        mock_briefing.return_value = DashboardBriefing(headline="Morning", brief="Ready.")

        response = self.client.post("/v1/dashboard/prepare")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["job_status"], "succeeded")
        self.assertEqual(payload["job_stage"], "completed")
        self.assertEqual(payload["source_records"], 2)
        self.assertEqual(payload["imported_count"], 2)
        self.assertEqual(payload["total_count"], 2)
        self.assertEqual(payload["changed_entities"], 1)
        self.assertEqual(payload["refreshed_entities"], 1)
        mock_fetch_source_records.assert_called_once()
        mock_refresh.assert_called_once_with(str(self.database_path), ["entity-1"])

    @patch("app.services.dashboard.fetch_google_source_records", side_effect=RuntimeError("sync exploded"))
    @patch("app.services.dashboard.get_google_auth_state")
    def test_job_failure_is_persisted(self, mock_auth: Mock, _mock_fetch_source_records: Mock) -> None:
        mock_auth.return_value = GoogleAuthState(available=True, connected=True)

        create_response = self.client.post("/v1/dashboard/import-jobs")

        self.assertEqual(create_response.status_code, 202)
        created = create_response.json()
        self.assertEqual(created["status"], "queued")
        self.assertIsNone(created["result_status"])
        self.assertIsNone(created["error_message"])

        status_response = self.client.get(f"/v1/dashboard/import-jobs/{created['id']}")

        self.assertEqual(status_response.status_code, 200)
        persisted = status_response.json()
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["error_message"], "sync exploded")
        self.assertIsNotNone(persisted["completed_at"])


if __name__ == "__main__":
    unittest.main()
