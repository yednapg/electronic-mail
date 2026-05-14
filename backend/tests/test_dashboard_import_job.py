from __future__ import annotations

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import dashboard as dashboard_routes
from app.db.repository import (
    DEFAULT_USER_ID,
    create_dashboard_import_job,
    get_dashboard_import_job,
    initialize_database,
    mark_dashboard_import_job_running,
    mark_dashboard_import_job_succeeded,
    update_dashboard_import_job_progress,
    upsert_gmail_sync_state,
)
from app.main import app
from app.schemas.domain import DashboardBriefing, DashboardProfile, FeedResponse, GoogleAuthState, SourceRecord
from app.services.dashboard import (
    STALE_IMPORT_JOB_MESSAGE,
    create_or_reuse_dashboard_import_job,
    run_gmail_full_history_backfill,
)


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
            self.assertIsNotNone(queued.stage_started_at)
            self.assertEqual(queued.stage_durations, {})
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
            self.assertIn("starting", progress.stage_durations)

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
            self.assertIn("gmail_persisted", succeeded.stage_durations)
            self.assertIsNotNone(succeeded.stage_started_at)
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

    def test_create_or_reuse_import_job_keeps_single_active_job(self) -> None:
        existing = create_dashboard_import_job(str(self.database_path))

        job, should_start = create_or_reuse_dashboard_import_job(self.settings)

        self.assertFalse(should_start)
        self.assertEqual(job.id, existing.id)
        self.assertEqual(job.status, "queued")

    def test_stale_import_job_is_failed_before_new_job_is_created(self) -> None:
        stale = mark_dashboard_import_job_running(
            str(self.database_path),
            create_dashboard_import_job(str(self.database_path)).id,
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE dashboard_import_jobs
                SET updated_at = ?, started_at = ?
                WHERE id = ?
                """,
                ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", stale.id),
            )

        job, should_start = create_or_reuse_dashboard_import_job(self.settings)

        self.assertTrue(should_start)
        self.assertNotEqual(job.id, stale.id)
        failed_stale = get_dashboard_import_job(str(self.database_path), stale.id)
        self.assertIsNotNone(failed_stale)
        self.assertEqual(failed_stale.status, "failed")
        self.assertEqual(failed_stale.error_message, STALE_IMPORT_JOB_MESSAGE)

    def test_import_job_status_marks_stale_running_job_failed(self) -> None:
        stale = mark_dashboard_import_job_running(
            str(self.database_path),
            create_dashboard_import_job(str(self.database_path)).id,
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                UPDATE dashboard_import_jobs
                SET updated_at = ?, started_at = ?, stage_started_at = ?
                WHERE id = ?
                """,
                (
                    "2000-01-01T00:00:00+00:00",
                    "2000-01-01T00:00:00+00:00",
                    "2000-01-01T00:00:00+00:00",
                    stale.id,
                ),
            )

        response = self.client.get(f"/v1/dashboard/import-jobs/{stale.id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_message"], STALE_IMPORT_JOB_MESSAGE)

    @patch("app.services.dashboard.backfill_full_gmail_source_records")
    def test_full_history_backfill_runs_only_after_recent_first_sync(self, mock_backfill: Mock) -> None:
        settings = SimpleNamespace(
            database_path=self.database_path,
            google_configured=True,
            gmail_sync_scope="recent",
        )
        upsert_gmail_sync_state(
            str(self.database_path),
            user_id=DEFAULT_USER_ID,
            last_history_id="123",
            last_full_sync_at=None,
        )

        run_gmail_full_history_backfill(settings)

        mock_backfill.assert_called_once_with(settings)

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
    @patch("app.services.dashboard.hydrate_persistent_memory")
    @patch("app.services.dashboard.fetch_google_source_records")
    @patch("app.services.dashboard.get_google_auth_state")
    def test_prepare_compatibility_path_invokes_sync(
        self,
        mock_auth: Mock,
        mock_fetch_source_records: Mock,
        mock_hydrate: Mock,
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
            return [
                SourceRecord(
                    id="gmail-1",
                    user_id="local-user",
                    source="gmail",
                    thread_id="thread-1",
                    raw_payload={"subject": "One"},
                    received_at="2026-04-17T10:00:00+00:00",
                ),
                SourceRecord(
                    id="gmail-2",
                    user_id="local-user",
                    source="gmail",
                    thread_id="thread-2",
                    raw_payload={"subject": "Two"},
                    received_at="2026-04-17T11:00:00+00:00",
                ),
            ]

        mock_fetch_source_records.side_effect = fake_fetch_source_records
        changed_entity_ids = {"entity-1", "entity-3"}
        mock_hydrate.return_value = changed_entity_ids
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
        self.assertEqual(payload["changed_entities"], len(changed_entity_ids))
        self.assertEqual(payload["refreshed_entities"], len(changed_entity_ids))
        self.assertIn("stage_durations", payload)
        mock_fetch_source_records.assert_called_once()
        mock_hydrate.assert_called_once()
        hydrated_records = mock_hydrate.call_args.args[1]
        self.assertEqual([record.id for record in hydrated_records], ["gmail-1", "gmail-2"])
        self.assertTrue(mock_hydrate.call_args.kwargs["include_unlinked"])
        mock_refresh.assert_called_once()
        refresh_args = mock_refresh.call_args.args
        self.assertEqual(refresh_args[0], str(self.database_path))
        self.assertEqual(set(refresh_args[1]), changed_entity_ids)

    @patch("app.services.dashboard.fetch_google_account_profile")
    @patch("app.services.dashboard.generate_dashboard_briefing")
    @patch("app.services.dashboard.build_feed_from_entities")
    @patch("app.services.dashboard.refresh_ai_suggestions_for_entities")
    @patch("app.services.dashboard.hydrate_persistent_memory")
    @patch("app.services.dashboard.fetch_google_source_records")
    @patch("app.services.dashboard.get_google_auth_state")
    def test_import_job_refreshes_only_changed_entities(
        self,
        mock_auth: Mock,
        mock_fetch_source_records: Mock,
        mock_hydrate: Mock,
        mock_refresh: Mock,
        mock_build_feed: Mock,
        mock_briefing: Mock,
        mock_fetch_profile: Mock,
    ) -> None:
        mock_auth.return_value = GoogleAuthState(available=True, connected=True)
        mock_fetch_source_records.return_value = []
        changed_entity_ids = {"entity-2", "entity-4"}
        mock_hydrate.return_value = changed_entity_ids
        mock_build_feed.return_value = FeedResponse()
        mock_fetch_profile.return_value = DashboardProfile(email="person@example.com")
        mock_briefing.return_value = DashboardBriefing(headline="Morning", brief="Ready.")

        create_response = self.client.post("/v1/dashboard/import-jobs")

        self.assertEqual(create_response.status_code, 202)
        created = create_response.json()
        status_response = self.client.get(f"/v1/dashboard/import-jobs/{created['id']}")

        self.assertEqual(status_response.status_code, 200)
        persisted = status_response.json()
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["changed_entities"], len(changed_entity_ids))
        self.assertEqual(persisted["refreshed_entities"], len(changed_entity_ids))
        mock_hydrate.assert_called_once()
        self.assertEqual(mock_hydrate.call_args.args[1], [])
        self.assertTrue(mock_hydrate.call_args.kwargs["include_unlinked"])
        mock_refresh.assert_called_once()
        refresh_args = mock_refresh.call_args.args
        self.assertEqual(refresh_args[0], str(self.database_path))
        self.assertEqual(set(refresh_args[1]), changed_entity_ids)

    @patch("app.services.dashboard.fetch_google_account_profile")
    @patch("app.services.dashboard.generate_dashboard_briefing")
    @patch("app.services.dashboard.build_feed_from_entities")
    @patch("app.services.dashboard.refresh_feed_projections_for_entities")
    @patch("app.services.dashboard.refresh_ai_suggestions_for_entities")
    @patch("app.services.dashboard.list_stale_feed_projection_entity_ids")
    @patch("app.services.dashboard.list_entity_ids_needing_ai_refresh")
    @patch("app.services.dashboard.hydrate_persistent_memory")
    @patch("app.services.dashboard.fetch_google_source_records")
    @patch("app.services.dashboard.get_google_auth_state")
    def test_import_job_refreshes_changed_entities_and_stale_copy_caches(
        self,
        mock_auth: Mock,
        mock_fetch_source_records: Mock,
        mock_hydrate: Mock,
        mock_stale_ai: Mock,
        mock_stale_projection: Mock,
        mock_refresh_ai: Mock,
        mock_refresh_projection: Mock,
        mock_build_feed: Mock,
        mock_briefing: Mock,
        mock_fetch_profile: Mock,
    ) -> None:
        mock_auth.return_value = GoogleAuthState(available=True, connected=True)
        mock_fetch_source_records.return_value = []
        mock_hydrate.return_value = {"changed-entity"}
        mock_stale_ai.return_value = ["stale-ai-entity"]
        mock_stale_projection.return_value = ["stale-projection-entity", "changed-entity"]
        mock_build_feed.return_value = FeedResponse()
        mock_fetch_profile.return_value = DashboardProfile(email="person@example.com")
        mock_briefing.return_value = DashboardBriefing(headline="Morning", brief="Ready.")

        create_response = self.client.post("/v1/dashboard/import-jobs")

        self.assertEqual(create_response.status_code, 202)
        persisted = self.client.get(f"/v1/dashboard/import-jobs/{create_response.json()['id']}").json()
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(persisted["changed_entities"], 1)
        self.assertEqual(persisted["refreshed_entities"], 3)
        expected = {"changed-entity", "stale-ai-entity", "stale-projection-entity"}
        self.assertEqual(set(mock_refresh_ai.call_args.args[1]), expected)
        self.assertEqual(set(mock_refresh_projection.call_args.args[1]), expected)

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
