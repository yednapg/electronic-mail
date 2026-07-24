from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.db.mail_groups import AppSessionSnapshotRecord
from app.schemas.domain import GoogleAuthState, GmailThreadRow, GmailThreadSection, MailboxResponse
from app.services.auth import CurrentUser
from app.services.mail_groups import (
    APP_SESSION_PROJECTION_VERSION,
    build_app_session_response,
    build_dashboard_response,
    enqueue_projection_refresh,
    refresh_visible_mail_projection,
)


def queue_health() -> SimpleNamespace:
    return SimpleNamespace(queue_depth={}, worker_online=True, required_queues_ready=True)


def legacy_feed_item() -> dict[str, object]:
    return {
        "id": "mail-group:legacy-group",
        "entity_id": "legacy-group",
        "user_id": "user-1",
        "need_type": "awareness",
        "action_type": "none",
        "effort_level": "quick",
        "timing_band": "later",
        "action_confidence": "high",
        "primary_action": "open",
        "fallback_action": "open",
        "title": "Legacy AI title must not escape",
        "why_this_is_here": "Legacy AI summary must not escape",
        "detail": {
            "body": ["Legacy AI summary must not escape"],
            "action_label": "Open group",
            "source_label": "Gmail",
        },
        "source": "gmail",
        "gmail_thread_id": "legacy-group",
        "trace_id": "mail-group:legacy-group",
        "created_at": "2026-05-15T12:00:00+00:00",
    }


class NoAIResponsePurityTests(unittest.TestCase):
    @patch("app.services.mail_groups.list_mail_groups")
    @patch("app.services.mail_groups.enqueue_job")
    def test_raw_mail_release_skips_retired_projection_reads_and_jobs(
        self,
        enqueue_job: Mock,
        list_mail_groups: Mock,
    ) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")

        self.assertEqual(enqueue_projection_refresh(settings, user_id="user-1"), "")
        self.assertEqual(refresh_visible_mail_projection(settings, user_id="user-1"), 0)

        enqueue_job.assert_not_called()
        list_mail_groups.assert_not_called()

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 4, "pending": 1})
    @patch("app.services.mail_groups.list_messages_for_groups", return_value={})
    @patch("app.services.mail_groups.get_latest_entity_outcomes", return_value={})
    @patch("app.services.mail_groups.list_open_manual_tasks", return_value=[])
    @patch("app.services.mail_groups.list_dashboard_mail_groups")
    def test_dashboard_does_not_read_or_return_legacy_mail_groups(
        self,
        list_legacy_groups: Mock,
        _list_tasks: Mock,
        _outcomes: Mock,
        _messages: Mock,
        _counts: Mock,
        latest_ai_error: Mock,
        _health: Mock,
    ) -> None:
        list_legacy_groups.return_value = [object()]

        dashboard = build_dashboard_response(
            SimpleNamespace(database_path="postgresql://example/db"),
            user_id="user-1",
            auth=GoogleAuthState(available=True, connected=True),
            debug_classification=True,
        )

        list_legacy_groups.assert_not_called()
        latest_ai_error.assert_not_called()
        self.assertEqual(dashboard.feed.now, [])
        self.assertEqual(dashboard.feed.today, [])
        self.assertEqual(dashboard.feed.worth_knowing, [])
        self.assertEqual(dashboard.runtime_status["feed_source"], "empty")
        self.assertNotIn("classification_debug", dashboard.runtime_status)
        self.assertNotIn("ai_groups_ready", dashboard.runtime_status)
        self.assertNotIn("last_ai_error", dashboard.runtime_status)

    @patch("app.services.mail_groups.get_queue_health", return_value=queue_health())
    @patch("app.services.mail_groups.latest_mail_group_ai_error")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 3, "pending": 2})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch("app.services.mail_groups.get_import_state")
    @patch("app.services.mail_groups.build_mailbox_response")
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups._google_auth_state")
    def test_app_session_rebuilds_legacy_mailbox_and_scrubs_generated_dashboard_content(
        self,
        google_auth: Mock,
        _background_work: Mock,
        get_snapshot: Mock,
        build_mailbox: Mock,
        get_import_state: Mock,
        _active_jobs: Mock,
        _counts: Mock,
        latest_ai_error: Mock,
        _health: Mock,
    ) -> None:
        google_auth.return_value = GoogleAuthState(available=True, connected=True)
        get_import_state.return_value = SimpleNamespace(first_batch_imported_at="2026-05-15T12:00:00+00:00")
        get_snapshot.return_value = AppSessionSnapshotRecord(
            user_id="user-1",
            dashboard={
                "auth": {"available": True, "connected": True},
                "profile": {"email": "me@example.com", "display_name": "Gaurav"},
                "briefing": {
                    "headline": "Legacy AI title must not escape",
                    "brief": "Legacy AI summary must not escape",
                    "important": {"text": "Legacy AI title must not escape"},
                },
                "feed": {"worth_knowing": [legacy_feed_item()]},
                "runtime_status": {
                    "feed_source": "ai_mail_groups",
                    "ai_groups_ready": True,
                    "last_ai_error": "legacy-ai-error",
                    "classification_debug": [{"title": "Legacy AI title must not escape"}],
                },
            },
            mailbox={
                "label": "inbox",
                "total_threads": 1,
                "sections": [
                    {
                        "id": "today",
                        "title": "Today",
                        "rows": [
                            {
                                "thread_id": "legacy-group",
                                "entity_id": "legacy-group",
                                "title": "Legacy AI title must not escape",
                                "latest_source_record_id": "msg-1",
                                "latest_received_at": "2026-05-15T12:00:00+00:00",
                                "message_count": 1,
                                "summary": "Legacy AI summary must not escape",
                                "ai_group_id": "legacy-group",
                                "ai_title": "Legacy AI title must not escape",
                                "ai_summary": "Legacy AI summary must not escape",
                                "presentation_status": "ai_ready",
                            }
                        ],
                    }
                ],
            },
            sync={
                "last_sync_at": None,
                "last_error": "legacy-ai-error",
                "enrichment_pending_count": 2,
                "ready_group_count": 3,
                "oldest_imported_at": None,
                "full_import_running": False,
                "full_import_completed": True,
                "last_ai_error": "legacy-ai-error",
                "projection_version": APP_SESSION_PROJECTION_VERSION,
            },
            updated_at="2026-05-15T12:00:00+00:00",
        )
        build_mailbox.return_value = MailboxResponse(
            label="inbox",
            total_threads=1,
            sections=[
                GmailThreadSection(
                    id="today",
                    title="Today",
                    rows=[
                        GmailThreadRow(
                            thread_id="thread-1",
                            entity_id="thread-1",
                            title="Original Gmail subject",
                            latest_source_record_id="msg-1",
                            latest_received_at="2026-05-15T12:00:00+00:00",
                            latest_subject="Original Gmail subject",
                            message_count=1,
                            snippet="Original Gmail snippet",
                            presentation_status="fallback",
                        )
                    ],
                )
            ],
        )

        session = build_app_session_response(
            SimpleNamespace(database_path="postgresql://example/db"),
            user=CurrentUser(id="user-1", email="me@example.com", display_name="Gaurav"),
        )

        build_mailbox.assert_called_once_with(
            unittest.mock.ANY,
            user_id="user-1",
            label="inbox",
            limit=100,
        )
        latest_ai_error.assert_not_called()
        self.assertEqual(session.dashboard.feed.now, [])
        self.assertEqual(session.dashboard.feed.today, [])
        self.assertEqual(session.dashboard.feed.worth_knowing, [])
        self.assertEqual(session.dashboard.runtime_status["feed_source"], "empty")
        self.assertNotIn("classification_debug", session.dashboard.runtime_status)
        self.assertNotIn("ai_groups_ready", session.dashboard.runtime_status)
        self.assertNotIn("last_ai_error", session.dashboard.runtime_status)
        self.assertEqual(session.mailbox.sections[0].rows[0].title, "Original Gmail subject")
        self.assertIsNone(session.mailbox.sections[0].rows[0].ai_title)
        self.assertIsNone(session.mailbox.sections[0].rows[0].ai_summary)
        self.assertEqual(session.sync.enrichment_pending_count, 0)
        self.assertEqual(session.sync.ready_group_count, 1)
        self.assertIsNone(session.sync.last_ai_error)
        self.assertIsNone(session.sync.last_error)
        serialized = session.model_dump_json()
        self.assertNotIn("Legacy AI title must not escape", serialized)
        self.assertNotIn("Legacy AI summary must not escape", serialized)
        self.assertNotIn("legacy-ai-error", serialized)


if __name__ == "__main__":
    unittest.main()
