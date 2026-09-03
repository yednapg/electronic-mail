from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from app.core.error_safety import GoogleCredentialsUnavailable
from app.db.jobs import renew_heartbeat
from app.db.mail_groups import AppSessionSnapshotRecord, GmailInitialWindowEntry, GmailMessageRecord, GmailSyncProgress, MailboxCursorError, MailboxThreadPage, MailGroupRecord, VisibleMailGroupRecord, decode_mailbox_cursor, encode_mailbox_cursor
from app.schemas.domain import GoogleAuthState
from app.services.auth import CurrentUser
from app.services.gmail_importer import GmailHistoryTraversalLimit, _encode_full_mailbox_cursor, _list_history_delta, run_gmail_backfill, run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import (
    APP_SESSION_PROJECTION_VERSION,
    _candidate_groups,
    _full_import_status,
    _gmail_row_from_canonical_thread,
    _message_title_map,
    _refresh_ai_lifecycle_groups,
    build_app_session_response,
    build_mailbox_response,
    build_post_login_readiness_response,
    enrich_group,
    enqueue_mailbox_sync,
    ensure_background_import_work,
    rebuild_touched_mail_groups,
    run_first_run_ai_grouping,
)
from app.services.attention_classifier import attention_enrichment_payload
from app.workers.main import _run_job


def sample_message(message_id: str = "msg-1") -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id="thread-1",
        history_id="10",
        label_ids=["INBOX", "UNREAD"],
        internal_date="2026-05-15T12:00:00+00:00",
        subject="Action required",
        sender="sender@example.com",
        recipients={"to": "me@example.com"},
        headers={"subject": "Action required"},
        snippet="Please review this.",
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals={"sender_domain": "example.com", "normalized_subject": "action required"},
        body_hash="hash-1",
        created_at="",
        updated_at="",
    )


def sample_group() -> MailGroupRecord:
    return MailGroupRecord(
        id="group-1",
        user_id="user-1",
        group_key="gmail-thread:thread-1",
        group_type="conversation",
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model="gpt-test",
        ai_error=None,
        ai_generated_at="2026-05-15T12:00:00+00:00",
        ai_title="Old AI title",
        ai_summary="Old AI summary",
        labels=["conversation"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=True,
        latest_message_at="2026-05-15T12:00:00+00:00",
        latest_message_id="msg-1",
        generated_from_hash="old-hash",
        generated_at="2026-05-15T12:00:00+00:00",
        created_at="2026-05-15T12:00:00+00:00",
        updated_at="2026-05-15T12:00:00+00:00",
    )


def sample_visible_group() -> VisibleMailGroupRecord:
    return VisibleMailGroupRecord(
        id="visible-1",
        user_id="user-1",
        projection_key="inbox:visible-1",
        visibility="inbox",
        group_kind="lifecycle",
        status="active",
        canonical_entity="Northstar Bank",
        contact_channel="Northstar Bank Care",
        title="Northstar remittance support case",
        summary="Northstar acknowledged and updated the wire transfer inquiry.",
        workflow_type="support_case",
        confidence=0.96,
        source_group_id="group-1",
        source="validated_projection",
        evidence={"strong_evidence": ["ticket_id:900000002"]},
        latest_message_at="2026-05-15T13:00:00+00:00",
        latest_message_id="msg-2",
        generated_at="2026-05-15T13:00:00+00:00",
        created_at="2026-05-15T13:00:00+00:00",
        updated_at="2026-05-15T13:00:00+00:00",
    )


class SpeedPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            database_path="postgresql://example/db",
            google_configured=True,
            openai_configured=False,
            openai_model="gpt-5.6-luna",
            gmail_pubsub_topic="projects/example/topics/gmail",
            gmail_watch_renewal_hours=24,
            gmail_recent_days=90,
            ai_grouping_enabled=False,
        )
        self.importer_event_patch = patch("app.services.gmail_importer.emit_mailbox_event")
        self.importer_order_patch = patch("app.services.gmail_importer._refresh_gmail_thread_order_best_effort", return_value=True)
        self.worker_event_patch = patch("app.workers.main.emit_mailbox_event")
        self.watch_lock_patch = patch(
            "app.services.gmail_watch.shared_user_mail_lock",
            return_value=nullcontext(),
        )
        self.mock_importer_event = self.importer_event_patch.start()
        self.mock_importer_order = self.importer_order_patch.start()
        self.mock_worker_event = self.worker_event_patch.start()
        self.watch_lock_patch.start()
        self.addCleanup(self.importer_event_patch.stop)
        self.addCleanup(self.importer_order_patch.stop)
        self.addCleanup(self.worker_event_patch.stop)
        self.addCleanup(self.watch_lock_patch.stop)

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.get_import_state", return_value=SimpleNamespace(last_history_id="9", history_cursor_authoritative=True))
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_incremental_import_uses_metadata_and_touched_groups(
        self,
        _mock_can_write: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_draft_list: Mock,
        mock_hydrate: Mock,
        _mock_upsert: Mock,
        mock_rebuild_touched: Mock,
        _mock_state: Mock,
        _mock_completed: Mock,
        _mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        message = sample_message()
        draft = replace(sample_message("draft-1"), gmail_thread_id="thread-draft", label_ids=["DRAFT"])
        mock_list.return_value = {"messages": [{"id": message.message_id}], "nextPageToken": "next"}
        mock_draft_list.return_value = {"messages": [{"id": draft.message_id}]}
        mock_hydrate.return_value = ([message, draft], "10")

        touched = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=30, first_run=False)

        self.assertEqual(touched, 1)
        mock_hydrate.assert_called_once()
        self.assertEqual(mock_hydrate.call_args.kwargs["listed"]["messages"], [{"id": "msg-1"}, {"id": "draft-1"}])
        self.assertEqual(mock_hydrate.call_args.kwargs["format"], "metadata")
        mock_rebuild_touched.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=["msg-1", "draft-1"],
            use_ai=False,
        )
        self.mock_importer_order.assert_not_called()
        mock_projection.assert_not_called()

    def test_first_run_import_uses_one_global_recent_window_and_no_legacy_backfill(self) -> None:
        inbox = sample_message("inbox-1")
        sent = replace(sample_message("sent-1"), gmail_thread_id="thread-2", label_ids=["SENT"])
        draft = replace(sample_message("draft-1"), gmail_thread_id="thread-3", label_ids=["DRAFT"])
        spam = replace(sample_message("spam-1"), gmail_thread_id="thread-4", label_ids=["SPAM"])
        trash = replace(sample_message("trash-1"), gmail_thread_id="thread-5", label_ids=["TRASH"])
        archived = replace(sample_message("archived-1"), gmail_thread_id="thread-6", label_ids=["IMPORTANT"])
        history = replace(sample_message("history-1"), gmail_thread_id="thread-1", label_ids=["SENT"])
        messages = [inbox, sent, draft, spam, trash, archived, history]
        thread_ids = [f"thread-{index}" for index in range(1, 7)]
        entries = [
            GmailInitialWindowEntry(
                user_id="user-1",
                generation_id="generation-1",
                gmail_thread_id=thread_id,
                position=index,
                message_count=0,
                metadata_ready_at=None,
                body_ready_at=None,
            )
            for index, thread_id in enumerate(thread_ids)
        ]
        state = SimpleNamespace(
            reconcile_generation="generation-1",
            initial_target_count=0,
            initial_window_complete=False,
        )
        discovered = GmailSyncProgress(
            sync_generation="generation-1",
            phase="importing_metadata",
            initial_target_count=6,
            initial_body_target_count=6,
            estimated_total_count=6,
        )
        committed = GmailSyncProgress(
            sync_generation="generation-1",
            phase="hydrating_priority_content",
            initial_target_count=6,
            initial_metadata_count=6,
            initial_body_target_count=6,
            initial_window_complete=True,
        )
        with patch("app.services.gmail_importer.user_can_write_gmail", return_value=True), patch(
            "app.services.gmail_importer.mark_import_started"
        ), patch("app.services.gmail_importer.mark_import_error") as mark_error, patch(
            "app.services.gmail_importer._ensure_gmail_reconciliation_started",
            return_value=state,
        ) as reconcile_start, patch(
            "app.services.gmail_importer.start_gmail_sync_progress",
            return_value=state,
        ), patch(
            "app.services.gmail_importer._list_initial_window_threads",
            return_value={"threads": [{"id": item} for item in thread_ids], "resultSizeEstimate": 6},
        ) as list_recent, patch(
            "app.services.gmail_importer.initialize_gmail_initial_window",
            return_value=discovered,
        ) as initialize, patch(
            "app.services.gmail_importer.get_import_state",
            return_value=state,
        ), patch(
            "app.services.gmail_importer.list_pending_gmail_initial_window_entries",
            return_value=entries,
        ), patch(
            "app.services.gmail_importer.list_gmail_initial_window_entries_needing_body_fetch",
            side_effect=[[], entries],
        ), patch(
            "app.services.gmail_importer._hydrate_gmail_thread_ids_metadata",
            return_value=messages,
        ) as hydrate, patch(
            "app.services.gmail_importer.commit_gmail_initial_window_metadata_batch",
            return_value=committed,
        ) as commit, patch(
            "app.services.gmail_importer.rebuild_touched_mail_groups"
        ), patch(
            "app.services.gmail_importer.mark_import_completed"
        ) as completed, patch(
            "app.services.gmail_importer.enqueue_job"
        ) as enqueue, patch(
            "app.services.gmail_importer.enqueue_gmail_full_reconciliation"
        ) as enqueue_reconcile:
            imported = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=500, first_run=True)

        self.assertEqual(imported, 7)
        list_recent.assert_called_once_with(self.settings, user_id="user-1")
        initialize.assert_called_once()
        hydrate.assert_called_once_with(
            self.settings,
            user_id="user-1",
            gmail_thread_ids=thread_ids,
            successful_thread_ids=ANY,
            terminal_missing_thread_ids=ANY,
        )
        self.assertEqual(commit.call_args.kwargs["gmail_thread_ids"], thread_ids)
        self.assertEqual(commit.call_args.kwargs["messages"], messages)
        self.assertTrue(all(call.kwargs["kind"] == "gmail_body_fetch" for call in enqueue.call_args_list))
        self.assertTrue(all(call.kwargs["priority"] == 90 for call in enqueue.call_args_list))
        self.assertFalse(any(call.kwargs["kind"] == "gmail_backfill" for call in enqueue.call_args_list))
        reconcile_start.assert_called_once_with(self.settings, user_id="user-1", use_thread_listing=True)
        enqueue_reconcile.assert_called_once_with(self.settings, user_id="user-1")
        completed.assert_called_once()
        mark_error.assert_not_called()

    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_history_delta_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups")
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer.mark_mail_groups_pending", return_value=[])
    @patch("app.services.gmail_importer.prune_empty_mail_groups", return_value=[])
    @patch("app.services.gmail_importer.delete_gmail_messages", return_value=[])
    @patch("app.services.gmail_importer._list_history_delta")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_delta_sync_no_changes_advances_cursor_without_broad_listing(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_history: Mock,
        _mock_delete: Mock,
        _mock_prune: Mock,
        _mock_pending: Mock,
        mock_hydrate_ids: Mock,
        mock_upsert: Mock,
        mock_rebuild: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
        mock_list_messages: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id="10", history_cursor_authoritative=True)
        mock_history.return_value = {"message_ids": [], "deleted_message_ids": [], "latest_history_id": "11"}
        mock_hydrate_ids.return_value = ([], None)

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 0)
        mock_list_messages.assert_not_called()
        mock_upsert.assert_not_called()
        mock_rebuild.assert_not_called()
        mock_completed.assert_called_once()
        self.assertEqual(mock_completed.call_args.kwargs["last_history_id"], "11")
        self.mock_importer_order.assert_not_called()
        mock_enqueue.assert_not_called()
        mock_projection.assert_not_called()

    @patch("app.services.gmail_importer.mark_import_error")
    @patch("app.services.gmail_importer._list_history_delta")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_delta_sync_persists_safe_reauthentication_error(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_history: Mock,
        mock_error: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id="10", history_cursor_authoritative=True)
        mock_history.side_effect = GoogleCredentialsUnavailable(
            "Google credentials are not connected"
        )

        with self.assertRaises(GoogleCredentialsUnavailable):
            run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        mock_error.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            error="Google authorization expired or was revoked. Please sign in again.",
        )

    @patch("app.services.gmail_importer.enqueue_gmail_full_reconciliation", return_value="reconcile-job-1")
    @patch("app.services.gmail_importer._run_recent_metadata_sync", return_value=2)
    @patch("app.services.gmail_importer._ensure_gmail_reconciliation_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_cursorless_sync_starts_reconciliation_before_recent_hydration(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        mock_reconcile_start: Mock,
        mock_recent: Mock,
        mock_reconcile_enqueue: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id=None)
        calls = Mock()
        calls.attach_mock(mock_reconcile_start, "baseline")
        calls.attach_mock(mock_recent, "recent")

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 2)
        self.assertEqual([call[0] for call in calls.mock_calls], ["baseline", "recent"])
        mock_reconcile_enqueue.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_history_delta_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer.mark_mail_groups_pending", return_value=[])
    @patch("app.services.gmail_importer.prune_empty_mail_groups", return_value=[])
    @patch("app.services.gmail_importer.delete_gmail_messages", return_value=[])
    @patch("app.services.gmail_importer._list_history_delta")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_delta_sync_changed_messages_update_touched_groups(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_history: Mock,
        _mock_delete: Mock,
        _mock_prune: Mock,
        _mock_pending: Mock,
        mock_hydrate_ids: Mock,
        _mock_upsert: Mock,
        mock_rebuild: Mock,
        _mock_completed: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
        mock_list_messages: Mock,
    ) -> None:
        message = sample_message("msg-2")
        mock_state.return_value = SimpleNamespace(last_history_id="10", history_cursor_authoritative=True)
        mock_history.return_value = {"message_ids": ["msg-2"], "deleted_message_ids": [], "latest_history_id": "101"}
        # H102 can be observed during hydration after history.list completed
        # through H101. Publishing H102 here would permanently skip its delta.
        mock_hydrate_ids.return_value = ([replace(message, history_id="102")], "102")

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100, target_history_id="102")

        self.assertEqual(touched, 1)
        mock_list_messages.assert_not_called()
        mock_hydrate_ids.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], format="metadata")
        mock_rebuild.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)
        self.mock_importer_order.assert_called_once_with(
            self.settings,
            user_id="user-1",
            target_history_id="101",
        )
        self.assertEqual(_mock_completed.call_args.kwargs["last_history_id"], "101")
        self.assertFalse(any(call.kwargs.get("kind") == "mail_group_enrich" for call in mock_enqueue.call_args_list))
        mock_projection.assert_not_called()

    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    def test_history_delta_rejects_repeated_page_token(
        self,
        _credentials: Mock,
        build_service: Mock,
    ) -> None:
        service = build_service.return_value
        request = service.users.return_value.history.return_value.list.return_value
        request.execute.side_effect = [
            {"historyId": "101", "history": [], "nextPageToken": "repeat"},
            {"historyId": "102", "history": [], "nextPageToken": "repeat"},
        ]

        with self.assertRaisesRegex(RuntimeError, "repeated a page token"):
            _list_history_delta(
                self.settings,
                user_id="user-1",
                start_history_id="100",
                page_size=100,
            )

        self.assertEqual(service.users.return_value.history.return_value.list.call_count, 2)

    @patch("app.services.gmail_importer.GMAIL_HISTORY_MAX_PAGES_PER_SYNC", 2)
    @patch("app.services.gmail_importer.build_google_service")
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    def test_history_delta_stops_at_page_budget_without_partial_result(
        self,
        _credentials: Mock,
        build_service: Mock,
    ) -> None:
        service = build_service.return_value
        request = service.users.return_value.history.return_value.list.return_value
        request.execute.side_effect = [
            {"historyId": "101", "history": [], "nextPageToken": "page-2"},
            {"historyId": "102", "history": [], "nextPageToken": "page-3"},
        ]

        with self.assertRaises(GmailHistoryTraversalLimit):
            _list_history_delta(
                self.settings,
                user_id="user-1",
                start_history_id="100",
                page_size=100,
            )

        self.assertEqual(service.users.return_value.history.return_value.list.call_count, 2)

    @patch("app.services.gmail_importer.enqueue_gmail_full_reconciliation")
    @patch("app.services.gmail_importer._ensure_gmail_reconciliation_started")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_full_mailbox_cursor_page")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_repeated_backfill_cursor_retires_listing_and_uses_reconciliation(
        self,
        _can_write: Mock,
        _started: Mock,
        get_state: Mock,
        hydrate_page: Mock,
        _upsert: Mock,
        _rebuild: Mock,
        completed: Mock,
        reconcile_start: Mock,
        reconcile_enqueue: Mock,
    ) -> None:
        cursor = _encode_full_mailbox_cursor(page_token="repeat")
        get_state.return_value = SimpleNamespace(
            first_batch_imported_at="ready",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
            last_history_id=None,
        )
        hydrate_page.return_value = ([sample_message()], "102", cursor)

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        completed.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            clear_full_backfill_cursor=True,
            full_backfill_started=True,
            full_backfill_completed=True,
        )
        reconcile_start.assert_called_once_with(self.settings, user_id="user-1")
        reconcile_enqueue.assert_called_once_with(self.settings, user_id="user-1")

    def test_provider_rejected_backfill_token_retires_listing_and_uses_reconciliation(self) -> None:
        cursor = _encode_full_mailbox_cursor(page_token="persisted-token")
        state = SimpleNamespace(
            first_batch_imported_at="ready",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
            last_history_id="102",
            history_cursor_authoritative=False,
        )
        for status in (400, 404):
            with self.subTest(status=status), patch(
                "app.services.gmail_importer.user_can_write_gmail",
                return_value=True,
            ), patch(
                "app.services.gmail_importer.mark_import_started",
            ), patch(
                "app.services.gmail_importer.get_import_state",
                return_value=state,
            ), patch(
                "app.services.gmail_importer._list_messages",
                side_effect=HttpError(
                    Response({"status": str(status)}),
                    b"invalid page token",
                ),
            ), patch(
                "app.services.gmail_importer._retire_backfill_for_reconciliation",
            ) as retire:
                touched = run_gmail_backfill(
                    self.settings,
                    user_id="user-1",
                    batch_size=100,
                )

            self.assertEqual(touched, 0)
            retire.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.services.gmail_importer._ensure_gmail_reconciliation_started")
    @patch("app.services.gmail_importer.enqueue_gmail_full_reconciliation", return_value="reconcile-job-1")
    @patch("app.services.gmail_importer._run_recent_metadata_sync", return_value=2)
    @patch("app.services.gmail_importer._list_history_delta")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_delta_sync_expired_cursor_uses_small_recent_fallback(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_history: Mock,
        mock_fallback: Mock,
        mock_reconcile: Mock,
        mock_reconcile_start: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id="10", history_cursor_authoritative=True)
        mock_history.side_effect = HttpError(Response({"status": "404"}), b"History expired")

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 2)
        mock_fallback.assert_called_once_with(self.settings, user_id="user-1", batch_size=100, can_write_checked=True)
        mock_reconcile.assert_called_once_with(self.settings, user_id="user-1")
        mock_reconcile_start.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_history_delta_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=0)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids", return_value=([], None))
    @patch("app.services.gmail_importer.mark_mail_groups_pending", return_value=["group-1"])
    @patch("app.services.gmail_importer.prune_empty_mail_groups", return_value=[])
    @patch("app.services.gmail_importer.delete_gmail_messages", return_value=["group-1"])
    @patch("app.services.gmail_importer._list_history_delta")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_delta_sync_deleted_messages_mark_remaining_groups_pending(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_history: Mock,
        mock_delete: Mock,
        mock_prune: Mock,
        mock_pending: Mock,
        _mock_hydrate_ids: Mock,
        _mock_upsert: Mock,
        _mock_rebuild: Mock,
        _mock_completed: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id="10", history_cursor_authoritative=True)
        mock_history.return_value = {"message_ids": [], "deleted_message_ids": ["msg-1"], "latest_history_id": "11"}

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        mock_delete.assert_called_once_with(self.settings.database_path, user_id="user-1", message_ids=["msg-1"])
        mock_prune.assert_called_once_with(self.settings.database_path, user_id="user-1", group_ids=["group-1"])
        mock_pending.assert_called_once_with(self.settings.database_path, user_id="user-1", group_ids=["group-1"])
        self.mock_importer_order.assert_called_once_with(
            self.settings,
            user_id="user-1",
            target_history_id="11",
        )
        self.assertFalse(any(call.kwargs.get("kind") == "mail_group_enrich" for call in mock_enqueue.call_args_list))
        mock_projection.assert_not_called()

    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(last_history_id="10", history_cursor_authoritative=True))
    def test_manual_sync_enqueues_delta_when_history_cursor_exists(
        self,
        _mock_state: Mock,
        mock_enqueue: Mock,
        _mock_background: Mock,
    ) -> None:
        mock_enqueue.return_value = SimpleNamespace(id="job-1")

        job_id = enqueue_mailbox_sync(self.settings, user_id="user-1")

        self.assertEqual(job_id, "job-1")
        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "gmail_delta_sync")
        self.assertEqual(mock_enqueue.call_args.kwargs["queue"], "critical")

    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(last_history_id=None))
    def test_manual_sync_falls_back_before_history_cursor_exists(
        self,
        _mock_state: Mock,
        mock_enqueue: Mock,
        _mock_background: Mock,
    ) -> None:
        mock_enqueue.return_value = SimpleNamespace(id="job-1")

        enqueue_mailbox_sync(self.settings, user_id="user-1")

        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "gmail_import_batch")

    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.enqueue_job")
    @patch(
        "app.services.mail_groups.get_import_state",
        return_value=SimpleNamespace(
            last_history_id="999",
            history_cursor_authoritative=False,
        ),
    )
    def test_manual_sync_does_not_trust_legacy_history_cursor(
        self,
        _mock_state: Mock,
        mock_enqueue: Mock,
        _mock_background: Mock,
    ) -> None:
        mock_enqueue.return_value = SimpleNamespace(id="job-1")

        enqueue_mailbox_sync(self.settings, user_id="user-1")

        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "gmail_import_batch")

    @patch("app.workers.main.run_gmail_delta_sync")
    @patch("app.workers.main.refresh_app_session_snapshot")
    @patch("app.workers.main._user_id_from_pubsub", return_value="user-1")
    def test_pubsub_worker_uses_delta_sync(
        self,
        _mock_resolve_user: Mock,
        mock_snapshot: Mock,
        mock_delta: Mock,
    ) -> None:
        job = SimpleNamespace(
            payload_version=1,
            kind="gmail_pubsub_sync",
            payload={"history_id": "123", "batch_size": 100, "pubsub": {}},
            user_id=None,
        )

        _run_job(self.settings, job)

        mock_delta.assert_called_once_with(self.settings, user_id="user-1", batch_size=100, target_history_id="123")
        mock_snapshot.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.workers.main.retry_encrypted_google_token_revocation")
    def test_worker_retries_abandoned_google_grant_from_encrypted_payload(
        self,
        mock_revoke: Mock,
    ) -> None:
        job = SimpleNamespace(
            id="revocation-job-1",
            payload_version=1,
            kind="google_token_revoke",
            payload={
                "subject_hash": "subject-hash",
                "token_json_encrypted": "encrypted-token-payload",
            },
            user_id=None,
        )

        _run_job(self.settings, job)

        mock_revoke.assert_called_once_with(
            self.settings,
            job_id="revocation-job-1",
            subject_hash="subject-hash",
            token_json_encrypted="encrypted-token-payload",
        )

    @patch("app.workers.main.get_import_state", return_value=SimpleNamespace(last_history_id="123", history_cursor_authoritative=True))
    @patch("app.workers.main.refresh_gmail_thread_order")
    @patch("app.workers.main.refresh_app_session_snapshot")
    def test_thread_order_refresh_runs_only_in_slow_worker_job(
        self,
        mock_snapshot: Mock,
        mock_refresh_order: Mock,
        _mock_import_state: Mock,
    ) -> None:
        job = SimpleNamespace(
            payload_version=1,
            kind="gmail_thread_order_refresh",
            payload={"user_id": "user-1", "target_history_id": "123"},
            user_id="user-1",
        )

        _run_job(self.settings, job)

        mock_refresh_order.assert_called_once_with(self.settings, user_id="user-1")
        mock_snapshot.assert_called_once_with(self.settings, user_id="user-1")
        self.assertEqual(self.mock_worker_event.call_args.kwargs["event_type"], "mailbox-changed")
        self.assertEqual(self.mock_worker_event.call_args.kwargs["mailbox_label"], "all")

    @patch("app.services.gmail_watch.enqueue_job")
    @patch("app.services.gmail_watch.mark_gmail_watch_started")
    @patch("app.services.gmail_watch.start_gmail_watch")
    @patch("app.services.gmail_watch.get_import_state", return_value=None)
    def test_gmail_watch_starts_and_schedules_renewal(
        self,
        _mock_state: Mock,
        mock_watch: Mock,
        mock_mark: Mock,
        mock_enqueue: Mock,
    ) -> None:
        mock_watch.return_value = {"historyId": "456", "expiration": "1770000000000"}

        result = ensure_gmail_watch(self.settings, user_id="user-1")

        self.assertEqual(result.status, "started")
        self.assertEqual(result.history_id, "456")
        mock_watch.assert_called_once_with(self.settings, user_id="user-1")
        mock_mark.assert_called_once()
        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "gmail_watch_renewal")
        self.assertGreaterEqual(mock_enqueue.call_args.kwargs["run_after_seconds"], 60 * 60)

    @patch("app.services.gmail_watch.start_gmail_watch")
    @patch("app.services.gmail_watch.get_import_state")
    def test_gmail_watch_skips_when_expiration_is_fresh(
        self,
        mock_state: Mock,
        mock_watch: Mock,
    ) -> None:
        mock_state.return_value = SimpleNamespace(
            gmail_watch_history_id="456",
            gmail_watch_expiration_at="2099-01-01T00:00:00+00:00",
        )

        result = ensure_gmail_watch(self.settings, user_id="user-1")

        self.assertEqual(result.status, "active")
        mock_watch.assert_not_called()

    @patch("app.services.mail_groups.replace_group_members")
    @patch("app.services.mail_groups.upsert_mail_group", return_value=SimpleNamespace(id="group-1"))
    @patch("app.services.mail_groups.get_mail_group_by_key", return_value=sample_group())
    @patch("app.services.mail_groups.list_group_messages", return_value=[])
    @patch("app.services.mail_groups.list_messages_by_ids", return_value=[sample_message("msg-2")])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_raw_mail_change_skips_retired_group_projection_work(
        self,
        _mock_can_write: Mock,
        _mock_messages: Mock,
        _mock_group_messages: Mock,
        _mock_existing: Mock,
        mock_upsert_group: Mock,
        _mock_append: Mock,
    ) -> None:
        touched = rebuild_touched_mail_groups(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)

        self.assertEqual(touched, 0)
        mock_upsert_group.assert_not_called()

    def test_first_run_ai_entrypoint_is_hard_disabled_even_with_stale_settings(self) -> None:
        self.settings.ai_grouping_enabled = True
        self.settings.openai_configured = True

        created = run_first_run_ai_grouping(self.settings, user_id="user-1", limit=50)

        self.assertEqual(created, 0)

    def test_ai_lifecycle_projection_is_hard_disabled_with_stale_openai_key(self) -> None:
        settings = SimpleNamespace(**{**self.settings.__dict__, "openai_configured": True})
        created = _refresh_ai_lifecycle_groups(settings, user_id="user-1")

        self.assertEqual(created, 0)

    def test_no_ai_enrichment_and_legacy_jobs_never_import_openai(self) -> None:
        self.settings.ai_grouping_enabled = True
        self.settings.openai_configured = True
        self.settings.openai_api_key = "stale-key-must-not-be-used"
        real_import = __import__

        def guarded_import(name: str, *args, **kwargs):
            if name == "openai" or name.startswith("openai."):
                raise AssertionError("No-AI runtime attempted to import OpenAI")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            enrichment = enrich_group(
                self.settings,
                messages=[sample_message()],
                group_key="gmail-thread:thread-1",
                generated_from_hash="hash-1",
                use_ai=True,
            )
            self.assertFalse(enrichment.get("_ai_ready", False))
            self.assertEqual(run_first_run_ai_grouping(self.settings, user_id="user-1"), 0)
            self.assertEqual(_refresh_ai_lifecycle_groups(self.settings, user_id="user-1"), 0)

            for kind in ("mail_group_enrich", "first_run_ai_grouping", "first_run_ready_check"):
                _run_job(
                    self.settings,
                    SimpleNamespace(
                        payload_version=1,
                        kind=kind,
                        payload={"user_id": "user-1"},
                        user_id="user-1",
                    ),
                )

    def test_candidate_groups_keep_same_sender_lifecycle_threads_separate_without_exact_evidence(self) -> None:
        first = replace(
            sample_message("neo-1"),
            sender="Neo <neo-noreply@example.com>",
            gmail_thread_id="thread-neo-1",
            subject="Neo application received: Futuristic Intelligence",
            extracted_signals={"sender_domain": "neo.com", "normalized_subject": "neo application received: futuristic intelligence"},
        )
        second = replace(
            sample_message("neo-2"),
            sender="Neo <neo-noreply@example.com>",
            gmail_thread_id="thread-neo-2",
            subject="Neo -- Complete your founder profile for Neo Residency",
            extracted_signals={"sender_domain": "neo.com", "normalized_subject": "neo -- complete your founder profile for neo residency"},
        )

        groups = _candidate_groups([first, second])

        self.assertEqual(set(groups.keys()), {"gmail-thread:thread-neo-1", "gmail-thread:thread-neo-2"})
        self.assertEqual({message.message_id for message in groups["gmail-thread:thread-neo-1"]}, {"neo-1"})
        self.assertEqual({message.message_id for message in groups["gmail-thread:thread-neo-2"]}, {"neo-2"})

    def test_candidate_groups_merge_cityflo_booking_lifecycle_threads_by_booking_id(self) -> None:
        booked = replace(
            sample_message("cityflo-booked"),
            sender="Cityflo <transit-noreply@example.com>",
            gmail_thread_id="thread-cityflo-booked",
            subject="Your Cityflo ride is booked",
            extracted_signals={"sender_domain": "transit.example", "booking_id": "CF123"},
        )
        modified = replace(
            sample_message("cityflo-modified"),
            sender="Cityflo <transit-noreply@example.com>",
            gmail_thread_id="thread-cityflo-modified",
            subject="Your Cityflo ride was modified",
            extracted_signals={"sender_domain": "transit.example", "booking_id": "CF123"},
        )
        cancelled = replace(
            sample_message("cityflo-cancelled"),
            sender="Cityflo <transit-noreply@example.com>",
            gmail_thread_id="thread-cityflo-cancelled",
            subject="Your Cityflo ride was cancelled",
            extracted_signals={"sender_domain": "transit.example", "booking_id": "CF123"},
        )

        groups = _candidate_groups([booked, modified, cancelled])

        self.assertEqual(set(groups.keys()), {"booking_id:cityflo:CF123"})
        self.assertEqual({message.message_id for message in groups["booking_id:cityflo:CF123"]}, {"cityflo-booked", "cityflo-modified", "cityflo-cancelled"})

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_backfill_creates_pending_groups_and_queues_default_ai_enrichment(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_hydrate: Mock,
        _mock_upsert: Mock,
        mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        message = sample_message()
        mock_state.return_value = SimpleNamespace(first_batch_imported_at="2026-05-15T12:00:00+00:00", full_backfill_cursor="cursor-1")
        mock_list.return_value = {"messages": [{"id": message.message_id}], "nextPageToken": None}
        mock_hydrate.return_value = ([message], "10")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=30)

        self.assertEqual(touched, 1)
        mock_rebuild_touched.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=["msg-1"],
            use_ai=False,
        )
        self.assertFalse(
            any(
                call.kwargs.get("kind") == "mail_group_enrich" and call.kwargs.get("queue") == "default"
                for call in mock_enqueue.call_args_list
            )
        )
        mock_projection.assert_not_called()

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer.existing_gmail_message_ids", return_value={"existing-1"})
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_full_backfill_starts_after_recent_window_and_skips_existing_messages(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_drafts: Mock,
        mock_existing: Mock,
        mock_hydrate: Mock,
        _mock_upsert: Mock,
        mock_rebuild_touched: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        message = sample_message("older-1")
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=None,
            full_backfill_completed_at=None,
        )
        mock_list.return_value = {"messages": [{"id": "existing-1"}, {"id": "older-1"}], "nextPageToken": "next-full"}
        mock_drafts.return_value = {"messages": []}
        mock_hydrate.return_value = ([message], "20")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        mock_existing.assert_called_once_with(self.settings.database_path, user_id="user-1", message_ids=["existing-1", "older-1"])
        self.assertEqual(mock_hydrate.call_args.kwargs["listed"]["messages"], [{"id": "older-1"}])
        mock_rebuild_touched.assert_called_once_with(self.settings, user_id="user-1", message_ids=["older-1"], use_ai=False)
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertFalse(mock_completed.call_args.kwargs["full_backfill_completed"])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertTrue(any(call.kwargs.get("kind") == "gmail_backfill" for call in mock_enqueue.call_args_list))

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer.existing_gmail_message_ids", return_value=set())
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_full_backfill_draft_cursor_does_not_restart_completed_all_mail(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_drafts: Mock,
        _mock_existing: Mock,
        mock_hydrate: Mock,
        _mock_upsert: Mock,
        _mock_rebuild_touched: Mock,
        mock_completed: Mock,
        _mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        draft = replace(sample_message("draft-2"), gmail_thread_id="thread-draft", label_ids=["DRAFT"])
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=_encode_full_mailbox_cursor(
                all_mail_completed=True,
                draft_page_token="draft-page-2",
                drafts_completed=False,
            ),
            full_backfill_completed_at=None,
        )
        mock_drafts.return_value = {"messages": [{"id": "draft-2"}]}
        mock_hydrate.return_value = ([draft], "22")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        mock_list.assert_not_called()
        mock_drafts.assert_called_once_with(self.settings, user_id="user-1", batch_size=100, page_token="draft-page-2")
        self.assertIsNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_completed"])
        self.mock_importer_order.assert_called_once_with(
            self.settings,
            user_id="user-1",
            target_history_id=None,
        )
        self.assertNotIn("last_history_id", mock_completed.call_args.kwargs)

    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1")
    @patch("app.services.mail_groups.oldest_imported_message_at", return_value=None)
    @patch("app.services.mail_groups.count_mailbox_threads", return_value=0)
    @patch("app.services.mail_groups.list_mail_groups_for_gmail_threads", return_value={})
    @patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={})
    @patch("app.services.mail_groups.list_mailbox_thread_page", return_value=MailboxThreadPage(threads=[], next_cursor="cursor-2", loaded_threads=100))
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 9})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None))
    def test_mailbox_includes_pending_groups_for_fast_arrival(
        self,
        _mock_state: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        mock_thread_page: Mock,
        _mock_visible_groups: Mock,
        _mock_thread_groups: Mock,
        mock_count_threads: Mock,
        _mock_oldest: Mock,
        _mock_revision: Mock,
        _mock_recovery: Mock,
    ) -> None:
        mailbox = build_mailbox_response(self.settings, user_id="user-1")

        self.assertEqual(mailbox.pending_count, 9)
        self.assertEqual(mailbox.next_cursor, "cursor-2")
        self.assertEqual(mailbox.loaded_threads, 100)
        self.assertIsNone(mailbox.window_days)
        self.assertEqual(mailbox.mailbox_revision, "rev-1")
        self.assertIsNotNone(mailbox.generated_at)
        mock_thread_page.assert_called_once()
        self.assertEqual(mock_thread_page.call_args.kwargs["user_id"], "user-1")
        self.assertEqual(mock_thread_page.call_args.kwargs["label"], "inbox")
        self.assertEqual(mock_thread_page.call_args.kwargs["limit"], 150)
        self.assertIsNone(mock_thread_page.call_args.kwargs["since_iso"])
        self.assertEqual(mock_count_threads.call_count, 2)
        self.assertFalse(mock_count_threads.call_args_list[0].kwargs.get("unread_only", False))
        self.assertTrue(mock_count_threads.call_args_list[1].kwargs["unread_only"])

    def test_mailbox_cursor_round_trips_and_rejects_invalid_values(self) -> None:
        cursor = encode_mailbox_cursor("2026-05-15T12:00:00+00:00", "thread-1")

        latest_at, thread_key = decode_mailbox_cursor(cursor)

        self.assertEqual(latest_at, "2026-05-15T12:00:00+00:00")
        self.assertEqual(thread_key, "thread-1")
        with self.assertRaises(MailboxCursorError):
            decode_mailbox_cursor("not-a-valid-cursor")

    def test_mailbox_response_does_not_resort_authoritative_gmail_order(self) -> None:
        gmail_first = replace(
            sample_message("gmail-first"),
            gmail_thread_id="thread-gmail-first",
            internal_date="2026-05-15T10:00:00+00:00",
        )
        gmail_second = replace(
            sample_message("gmail-second"),
            gmail_thread_id="thread-gmail-second",
            internal_date="2026-05-15T12:00:00+00:00",
        )
        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_active_jobs", return_value=0), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status",
            return_value={"ready": 2, "pending": 0},
        ), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-gmail-first", [gmail_first]),
                    ("thread-gmail-second", [gmail_second]),
                ],
                next_cursor=None,
                loaded_threads=2,
                order_source="gmail",
            ),
        ), patch("app.services.mail_groups.count_mailbox_threads", return_value=2), patch(
            "app.services.mail_groups.oldest_imported_message_at", return_value=None
        ), patch("app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual([row.thread_id for row in rows], ["thread-gmail-first", "thread-gmail-second"])

    def test_gmail_order_survives_reappearing_date_sections_and_pagination(self) -> None:
        first = replace(
            sample_message("gmail-rank-1"),
            gmail_thread_id="thread-gmail-rank-1",
            internal_date="2026-05-15T10:00:00+00:00",
        )
        second = replace(
            sample_message("gmail-rank-2"),
            gmail_thread_id="thread-gmail-rank-2",
            internal_date="2025-04-15T12:00:00+00:00",
        )
        third = replace(
            sample_message("gmail-rank-3"),
            gmail_thread_id="thread-gmail-rank-3",
            internal_date="2026-05-14T09:00:00+00:00",
        )
        fourth = replace(
            sample_message("gmail-rank-4"),
            gmail_thread_id="thread-gmail-rank-4",
            internal_date="2025-04-14T08:00:00+00:00",
        )
        pages = [
            MailboxThreadPage(
                threads=[
                    ("thread-gmail-rank-1", [first]),
                    ("thread-gmail-rank-2", [second]),
                    ("thread-gmail-rank-3", [third]),
                ],
                next_cursor="ranked-page-2",
                loaded_threads=3,
                order_source="gmail",
            ),
            MailboxThreadPage(
                threads=[("thread-gmail-rank-4", [fourth])],
                next_cursor=None,
                loaded_threads=1,
                order_source="gmail",
            ),
        ]
        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_active_jobs", return_value=0), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status",
            return_value={"ready": 4, "pending": 0},
        ), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            side_effect=pages,
        ), patch("app.services.mail_groups.count_mailbox_threads", return_value=4), patch(
            "app.services.mail_groups.oldest_imported_message_at", return_value=None
        ), patch("app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-order-1"):
            first_page = build_mailbox_response(self.settings, user_id="user-1", label="inbox")
            second_page = build_mailbox_response(
                self.settings,
                user_id="user-1",
                label="inbox",
                cursor=first_page.next_cursor,
            )

        first_ids = [row.thread_id for section in first_page.sections for row in section.rows]
        second_ids = [row.thread_id for section in second_page.sections for row in section.rows]
        self.assertEqual(
            first_ids,
            ["thread-gmail-rank-1", "thread-gmail-rank-2", "thread-gmail-rank-3"],
        )
        self.assertEqual(second_ids, ["thread-gmail-rank-4"])
        self.assertEqual(len(first_page.sections), 3)
        section_ids = [section.id for section in [*first_page.sections, *second_page.sections]]
        self.assertEqual(len(section_ids), len(set(section_ids)))
        combined_ids = [
            row.thread_id
            for section in [*first_page.sections, *second_page.sections]
            for row in section.rows
        ]
        self.assertEqual(combined_ids, [*first_ids, *second_ids])

    def test_candidate_groups_normalize_exact_reference_sender_variants(self) -> None:
        grievance = replace(
            sample_message("northstar-grievance"),
            gmail_thread_id="thread-grievance",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            extracted_signals={
                "sender_domain": "northstar.example",
                "normalized_subject": "unauthorized credit card consent request",
                "ticket_id": "900000003",
            },
        )
        service_request = replace(
            sample_message("northstar-service"),
            gmail_thread_id="thread-service",
            sender="Northstar Bank Care <care@northstarbank.example>",
            extracted_signals={
                "sender_domain": "northstarbank.example",
                "normalized_subject": "registered service request",
                "ticket_id": "900000003",
            },
        )

        candidates = _candidate_groups([grievance, service_request])

        self.assertEqual(list(candidates), ["ticket_id:northstar-bank:900000003"])
        self.assertEqual([message.message_id for message in candidates["ticket_id:northstar-bank:900000003"]], ["northstar-grievance", "northstar-service"])

    def test_mailbox_ignores_ai_projection_even_when_stale_flag_is_true(self) -> None:
        self.settings.ai_grouping_enabled = True
        first = replace(
            sample_message("msg-1"),
            gmail_thread_id="thread-1",
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Raw first subject",
            snippet="Raw first snippet",
            ai_title="Clean first title",
        )
        second = replace(
            sample_message("msg-2"),
            gmail_thread_id="thread-1",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Raw second subject",
            snippet="Raw second snippet",
            ai_title="Clean second title",
            raw_payload={
                "payload": {
                    "parts": [
                        {
                            "partId": "1",
                            "filename": "statement.pdf",
                            "mimeType": "application/pdf",
                            "headers": [{"name": "Content-Disposition", "value": "attachment"}],
                            "body": {"attachmentId": "attach-1", "size": 2048},
                        }
                    ]
                }
            },
        )
        visible_group = replace(
            sample_visible_group(),
            title="AI grouped title",
            summary="AI grouped summary",
            canonical_entity="Example Sender",
            source_group_id="group-1",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-1", [first, second])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-1": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch("app.services.mail_groups.list_messages_for_visible_groups", return_value={"visible-1": [first, second]}), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=1,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "thread-1")
        self.assertEqual(rows[0].entity_id, "thread-1")
        self.assertEqual(rows[0].title, "Raw second subject")
        self.assertEqual(rows[0].summary, "Raw second snippet")
        self.assertIsNone(rows[0].ai_group_id)
        self.assertIsNone(rows[0].ai_title)
        self.assertIsNone(rows[0].ai_summary)
        self.assertEqual(mailbox.mailbox_revision, "rev-1")
        self.assertIsNotNone(mailbox.generated_at)
        self.assertEqual(rows[0].latest_subject, "Raw second subject")
        self.assertTrue(rows[0].has_attachments)
        self.assertEqual(rows[0].attachment_count, 1)
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["msg-1", "msg-2"])
        self.assertEqual(rows[0].children[0].subject, "Raw first subject")
        self.assertIsNone(rows[0].children[0].ai_title)
        self.assertEqual(rows[0].children[1].subject, "Raw second subject")
        self.assertIsNone(rows[0].children[1].ai_title)

    def test_mailbox_ignores_thread_ai_group_for_plain_gmail_thread_rows(self) -> None:
        self.settings.ai_grouping_enabled = True
        message = replace(
            sample_message("msg-plain"),
            gmail_thread_id="thread-plain",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Raw bank subject",
            snippet="Raw Gmail snippet",
        )
        ai_group = replace(
            sample_group(),
            id="group-thread-plain",
            group_key="gmail-thread:thread-plain",
            ai_title="Clean bank support title",
            ai_summary="Bank confirmed the support case and next steps.",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-plain", [message])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-plain": ai_group},
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=1,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(rows[0].thread_id, "thread-plain")
        self.assertEqual(rows[0].entity_id, "thread-plain")
        self.assertEqual(rows[0].title, "Raw bank subject")
        self.assertEqual(rows[0].summary, "Raw Gmail snippet")
        self.assertIsNone(rows[0].ai_group_id)
        self.assertEqual(rows[0].presentation_status, "fallback")

    def test_no_ai_mailbox_keeps_newsletter_threads_separate(self) -> None:
        self.settings.ai_grouping_enabled = True
        first = replace(
            sample_message("claude-1"),
            gmail_thread_id="thread-claude-1",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            internal_date="2026-06-01T12:00:00+00:00",
            sender="Claude Team <claude-noreply@example.com>",
            subject="Using Claude for your everyday life",
            snippet="Tips for using Claude in everyday work.",
            extracted_signals={"sender_domain": "email.claude.com", "list_id": "claude.email.claude.com"},
        )
        second = replace(
            sample_message("claude-2"),
            gmail_thread_id="thread-claude-2",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            internal_date="2026-06-04T12:00:00+00:00",
            sender="Claude Team <claude-noreply@example.com>",
            subject="Get more from Claude with these power moves",
            snippet="More Claude product tips.",
            extracted_signals={"sender_domain": "email.claude.com", "list_id": "claude.email.claude.com"},
        )
        older = replace(
            sample_message("claude-3"),
            gmail_thread_id="thread-claude-3",
            label_ids=["INBOX", "CATEGORY_UPDATES"],
            internal_date="2026-05-24T12:00:00+00:00",
            sender="Claude Team <claude-noreply@example.com>",
            subject="Welcome to Claude. Let's get you set up.",
            snippet="Your step-by-step list to make Claude work better with you.",
            extracted_signals={"sender_domain": "email.claude.com", "normalized_subject": "welcome to claude lets get you set up"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-claude-2", [second]), ("thread-claude-1", [first]), ("thread-claude-3", [older])],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={}), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.thread_id for row in rows], ["thread-claude-2", "thread-claude-1", "thread-claude-3"])

    def test_no_ai_mailbox_keeps_provider_threads_separate(self) -> None:
        self.settings.ai_grouping_enabled = True
        weekly = replace(
            sample_message("slashy-weekly"),
            gmail_thread_id="thread-slashy-weekly",
            label_ids=["INBOX", "CATEGORY_UPDATES"],
            internal_date="2026-06-02T12:00:00+00:00",
            sender='"TestUser @ Slashy" <startup-founders@example.com>',
            subject="Your meeting notes and your CRM now live in your inbox (Slashy Weekly #4)",
            snippet="Slashy Weekly product update.",
            extracted_signals={"sender_domain": "mail.startup.example", "domains": ["startup.example", "www.startup.example"]},
        )
        welcome = replace(
            sample_message("slashy-welcome"),
            gmail_thread_id="thread-slashy-welcome",
            label_ids=["INBOX", "CATEGORY_PERSONAL"],
            internal_date="2026-05-24T12:00:00+00:00",
            sender="Slashy Team <startup-founders@example.com>",
            subject="Welcome to Slashy!",
            snippet="We're very excited to have you on Slashy.",
            extracted_signals={"sender_domain": "startup.example", "normalized_subject": "welcome to slashy"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-slashy-weekly", [weekly]), ("thread-slashy-welcome", [welcome])],
                next_cursor=None,
                loaded_threads=2,
            ),
        ), patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={}), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.thread_id for row in rows], ["thread-slashy-weekly", "thread-slashy-welcome"])

    def test_mailbox_folder_eligibility_renders_canonical_whole_thread(self) -> None:
        inbox = replace(
            sample_message("inbox-1"),
            gmail_thread_id="thread-1",
            label_ids=["INBOX", "UNREAD"],
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Inbox copy",
            sender="Sender <sender@example.com>",
        )
        sent = replace(
            sample_message("sent-1"),
            gmail_thread_id="thread-1",
            label_ids=["SENT"],
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Sent reply",
            sender="Me <me@example.com>",
            recipients={
                "to": "Recipient Person <recipient@example.com>, Second Person <second@example.com>",
            },
        )
        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-1", [inbox, sent])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=1,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            inbox_mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")
            sent_mailbox = build_mailbox_response(self.settings, user_id="user-1", label="sent")

        inbox_row = [row for section in inbox_mailbox.sections for row in section.rows][0]
        sent_row = [row for section in sent_mailbox.sections for row in section.rows][0]
        self.assertEqual(inbox_row.message_count, 2)
        self.assertEqual(inbox_row.latest_subject, "Sent reply")
        self.assertEqual(inbox_row.latest_sender, "Me <me@example.com>")
        self.assertEqual(inbox_row.sender, "Me <me@example.com>")
        self.assertEqual(inbox_row.label_ids, ["INBOX", "SENT", "UNREAD"])
        self.assertEqual(inbox_row.participants, ["Sender", "Me"])
        self.assertEqual([child.message_id for child in inbox_row.children], ["inbox-1", "sent-1"])
        self.assertEqual(inbox_row.children[0].sender, "Sender <sender@example.com>")
        self.assertEqual(inbox_row.children[0].label_ids, ["INBOX", "UNREAD"])
        self.assertEqual(sent_row.message_count, 2)
        self.assertEqual(sent_row.latest_subject, "Sent reply")
        self.assertEqual(sent_row.latest_sender, "Me <me@example.com>")
        self.assertEqual(sent_row.sender, "Recipient Person")
        self.assertEqual(sent_row.label_ids, ["INBOX", "SENT", "UNREAD"])
        self.assertEqual([child.message_id for child in sent_row.children], ["inbox-1", "sent-1"])
        self.assertEqual(sent_row.children[1].sender, "Recipient Person")
        self.assertEqual(sent_row.children[1].label_ids, ["SENT"])

    def test_sent_mailbox_display_sender_falls_back_to_recipient_email(self) -> None:
        sent = replace(
            sample_message("sent-1"),
            gmail_thread_id="thread-1",
            label_ids=["SENT"],
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Sent reply",
            sender="Me <me@example.com>",
            recipients={"to": "recipient@example.com"},
        )

        row = _gmail_row_from_canonical_thread("thread-1", [sent], "sent", None)

        self.assertEqual(row.latest_sender, "Me <me@example.com>")
        self.assertEqual(row.sender, "recipient@example.com")
        self.assertEqual(row.participants, ["recipient@example.com"])
        self.assertEqual(row.children[0].sender, "recipient@example.com")

    def test_no_ai_mailbox_does_not_merge_workflows_across_gmail_threads(self) -> None:
        self.settings.ai_grouping_enabled = True
        first = replace(
            sample_message("northstar-ack"),
            gmail_thread_id="thread-northstar-ack",
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Northstar Bank acknowledged your service request",
            sender="Northstar Bank <support@northstarbank.example>",
            ai_title="Bank acknowledged service request",
        )
        second = replace(
            sample_message("northstar-update"),
            gmail_thread_id="thread-northstar-update",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Update on your Northstar wire transfer inquiry",
            sender="Northstar Bank <support@northstarbank.example>",
            ai_title="Bank updated wire transfer inquiry",
        )
        visible_group = replace(
            sample_visible_group(),
            id="visible-northstar",
            source_group_id="group-northstar",
            title="Northstar remittance support case",
            summary="Northstar acknowledged and updated the wire transfer inquiry.",
            canonical_entity="Northstar Bank",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-northstar-update", [second]), ("thread-northstar-ack", [first])], next_cursor=None, loaded_threads=2),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-northstar-update": visible_group, "thread-northstar-ack": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_messages_for_visible_groups",
            return_value={"visible-northstar": [first, second]},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.thread_id for row in rows], ["thread-northstar-update", "thread-northstar-ack"])
        self.assertTrue(all(row.ai_group_id is None for row in rows))

    def test_visible_group_sender_falls_back_to_inbound_sender_when_projection_entity_is_user(self) -> None:
        self.settings.ai_grouping_enabled = True
        sent = replace(
            sample_message("psu-sent"),
            gmail_thread_id="thread-psu-sent",
            label_ids=["SENT"],
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Question About Reconsideration Request",
            sender="TestUser <hi@example.com>",
            recipients={"to": "State University <university-admissions@example.edu>"},
        )
        reply = replace(
            sample_message("psu-reply"),
            gmail_thread_id="thread-psu-reply",
            label_ids=["INBOX"],
            internal_date="2026-05-15T13:00:00+00:00",
            subject="RE: Question About Reconsideration Request",
            sender="State University <university-admissions@example.edu>",
        )
        visible_group = replace(
            sample_visible_group(),
            id="visible-psu",
            source_group_id="group-psu",
            title="State University reconsideration request",
            summary="State University replied to the reconsideration request.",
            canonical_entity="TestUser",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-psu-reply", [reply])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-psu-reply": visible_group, "thread-psu-sent": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_messages_for_visible_groups",
            return_value={"visible-psu": [sent, reply]},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].latest_sender, "State University <university-admissions@example.edu>")
        self.assertEqual(rows[0].sender, "State University <university-admissions@example.edu>")
        self.assertEqual(rows[0].title, "RE: Question About Reconsideration Request")
        self.assertEqual(rows[0].message_count, 1)
        self.assertEqual([child.message_id for child in rows[0].children], ["psu-reply"])

    def test_sent_only_visible_group_falls_back_to_canonical_inbox_thread(self) -> None:
        sent = replace(
            sample_message("psu-sent"),
            gmail_thread_id="thread-psu-sent",
            label_ids=["SENT"],
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Question About Reconsideration Request",
            sender="TestUser <hi@example.com>",
            recipients={"to": "State University <university-admissions@example.edu>"},
        )
        reply = replace(
            sample_message("psu-reply"),
            gmail_thread_id="thread-psu-reply",
            label_ids=["INBOX"],
            internal_date="2026-05-15T13:00:00+00:00",
            subject="RE: Question About Reconsideration Request",
            sender="State University <university-admissions@example.edu>",
        )
        visible_group = replace(
            sample_visible_group(),
            id="visible-psu",
            source_group_id="group-psu",
            title="State University reconsideration request",
            canonical_entity="TestUser",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-psu-reply", [reply])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-psu-reply": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_messages_for_visible_groups",
            return_value={"visible-psu": [sent]},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=1,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "thread-psu-reply")
        self.assertIsNone(rows[0].ai_group_id)
        self.assertEqual(rows[0].sender, "State University <university-admissions@example.edu>")
        self.assertEqual([child.message_id for child in rows[0].children], ["psu-reply"])

    def test_attention_title_fallback_rejects_generic_application_update_subject(self) -> None:
        message = replace(
            sample_message("psu-app"),
            subject="Application Update",
            sender="State University <university-admissions@example.edu>",
            snippet="We regret to inform you that your application was not selected.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )

        enrichment = attention_enrichment_payload(messages=[message], group_key="gmail-thread:thread-psu")

        self.assertEqual(enrichment["ai_title"], "State University application rejection")

    def test_generic_ai_message_title_is_replaced_before_persistence(self) -> None:
        message = replace(
            sample_message("psu-app"),
            subject="Application Update",
            sender="State University <university-admissions@example.edu>",
            snippet="We regret to inform you that your application was not selected.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )

        titles = _message_title_map([{"message_id": "psu-app", "ai_title": "Application Update"}], [message])

        self.assertEqual(titles, {"psu-app": "State University application rejection"})

    def test_mailbox_keeps_related_cityflo_threads_separate_without_validated_projection(self) -> None:
        booking = replace(
            sample_message("cityflo-booking"),
            gmail_thread_id="thread-cityflo-booking",
            internal_date="2026-06-05T07:00:00+00:00",
            subject="Your booking with Cityflo",
            sender="Cityflo <transit-support@example.com>",
            snippet="Your bus booking is confirmed.",
            extracted_signals={"sender_domain": "transit.example", "normalized_subject": "your booking with cityflo"},
        )
        modified = replace(
            sample_message("cityflo-modified"),
            gmail_thread_id="thread-cityflo-modified",
            internal_date="2026-06-05T08:00:00+00:00",
            subject="Your trip was modified successfully",
            sender="Cityflo <transit-support@example.com>",
            snippet="Your ride time changed.",
            extracted_signals={"sender_domain": "transit.example", "normalized_subject": "your trip was modified successfully"},
        )
        cancelled = replace(
            sample_message("cityflo-cancelled"),
            gmail_thread_id="thread-cityflo-cancelled",
            internal_date="2026-06-05T09:00:00+00:00",
            subject="Your trip on 05 Jun was cancelled",
            sender="Cityflo <transit-support@example.com>",
            snippet="Your Cityflo ride was cancelled.",
            extracted_signals={"sender_domain": "transit.example", "normalized_subject": "your trip was cancelled"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-cityflo-cancelled", [cancelled]),
                    ("thread-cityflo-modified", [modified]),
                    ("thread-cityflo-booking", [booking]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=3,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual([row.thread_id for row in rows], ["thread-cityflo-cancelled", "thread-cityflo-modified", "thread-cityflo-booking"])
        self.assertEqual([row.ai_group_id for row in rows], [None, None, None])

    def test_mailbox_does_not_cluster_job_alerts_as_billing(self) -> None:
        first = replace(
            sample_message("upwork-1"),
            gmail_thread_id="thread-upwork-1",
            internal_date="2026-05-28T08:00:00+00:00",
            subject="New job alert: AI automation engineer",
            sender="Upwork Notification <freelance-noreply@example.com>",
            snippet="This job matches your alert settings. Payment verified client.",
            extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "new job alert ai automation engineer"},
        )
        second = replace(
            sample_message("upwork-2"),
            gmail_thread_id="thread-upwork-2",
            internal_date="2026-05-28T09:00:00+00:00",
            subject="New job alert: AI consultant",
            sender="Upwork Notification <freelance-noreply@example.com>",
            snippet="This job matches your alert settings. Payment verified client.",
            extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "new job alert ai consultant"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-upwork-2", [second]), ("thread-upwork-1", [first])],
                next_cursor=None,
                loaded_threads=2,
            ),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"
        ), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual([row.thread_id for row in rows], ["thread-upwork-2", "thread-upwork-1"])

    def test_mailbox_keeps_psu_application_reconsideration_and_costs_as_separate_threads(self) -> None:
        application = replace(
            sample_message("psu-app"),
            gmail_thread_id="thread-application",
            internal_date="2026-05-29T08:00:00+00:00",
            subject="Application Update",
            sender="university-admissions@example.edu",
        )
        sent_question = replace(
            sample_message("psu-sent"),
            gmail_thread_id="thread-reconsideration",
            label_ids=["SENT"],
            internal_date="2026-05-29T08:30:00+00:00",
            subject="Question About Reconsideration Request",
            sender="TestUser <hi@example.com>",
        )
        reply = replace(
            sample_message("psu-reply"),
            gmail_thread_id="thread-reconsideration",
            internal_date="2026-05-29T21:12:00+00:00",
            subject="RE: Question About Reconsideration Request",
            sender="State University Campus <behrend.university-admissions@example.edu>",
        )
        costs = replace(
            sample_message("psu-costs"),
            gmail_thread_id="thread-costs",
            internal_date="2026-05-29T22:00:00+00:00",
            subject="View Your Estimated Costs",
            sender="osa-noreply@university.example",
        )
        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-costs", [costs]),
                    ("thread-reconsideration", [sent_question, reply]),
                    ("thread-application", [application]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch("app.services.mail_groups.count_mailbox_threads", return_value=3), patch(
            "app.services.mail_groups.oldest_imported_message_at", return_value=None
        ), patch("app.services.mail_groups.latest_gmail_mailbox_revision", return_value="rev-1"), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual([row.thread_id for row in rows], ["thread-costs", "thread-reconsideration", "thread-application"])
        self.assertEqual([row.ai_group_id for row in rows], [None, None, None])
        self.assertEqual([row.latest_subject for row in rows], ["View Your Estimated Costs", "RE: Question About Reconsideration Request", "Application Update"])

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"pending": 4, "ready": 0})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(first_batch_imported_at=None, first_groups_ready_at=None, full_backfill_cursor=None))
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_enqueues_first_run_and_enrichment_work(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        ensure_background_import_work(self.settings, user_id="user-1")

        kinds = [call.kwargs.get("kind") for call in mock_enqueue.call_args_list]
        self.assertIn("gmail_import_batch", kinds)
        self.assertNotIn("mail_group_enrich", kinds)
        mock_projection.assert_not_called()

    def test_full_import_completion_requires_trusted_cursor_and_no_reconciliation(self) -> None:
        completed_state = SimpleNamespace(
            first_batch_imported_at="2026-07-24T00:00:00+00:00",
            full_backfill_cursor=None,
            full_backfill_completed_at="2026-07-24T00:01:00+00:00",
            last_history_id="101",
            history_cursor_authoritative=True,
            reconcile_generation=None,
        )

        self.assertEqual(
            _full_import_status(
                completed_state,
                active_backfill_jobs=0,
                active_reconciliation_jobs=0,
            ),
            (False, True, "2026-07-24T00:01:00+00:00"),
        )
        self.assertEqual(
            _full_import_status(
                completed_state,
                active_backfill_jobs=0,
                active_reconciliation_jobs=1,
            ),
            (True, False, None),
        )
        self.assertEqual(
            _full_import_status(
                SimpleNamespace(
                    **{
                        **completed_state.__dict__,
                        "history_cursor_authoritative": False,
                    }
                ),
                active_backfill_jobs=0,
                active_reconciliation_jobs=1,
            ),
            (True, False, None),
        )
        self.assertEqual(
            _full_import_status(
                SimpleNamespace(
                    **{
                        **completed_state.__dict__,
                        "reconcile_generation": "generation-1",
                    }
                ),
                active_backfill_jobs=0,
                active_reconciliation_jobs=0,
            ),
            (True, False, None),
        )

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch(
        "app.services.mail_groups.count_mail_groups_by_enrichment_status",
        return_value={"pending": 0, "ready": 10},
    )
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.get_import_state")
    @patch("app.services.mail_groups.activate_existing_gmail_progressive_sync")
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_queues_reconciliation_for_completed_backfill_with_untrusted_cursor(
        self,
        _can_write: Mock,
        activate_progressive: Mock,
        get_state: Mock,
        enqueue: Mock,
        _active_jobs: Mock,
        _counts: Mock,
        projection: Mock,
    ) -> None:
        get_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-07-24T00:00:00+00:00",
            first_groups_ready_at=None,
            full_backfill_cursor=None,
            full_backfill_completed_at="2026-07-24T00:01:00+00:00",
            last_history_id="102",
            history_cursor_authoritative=False,
            reconcile_generation=None,
        )
        activate_progressive.return_value = SimpleNamespace(
            **{
                **get_state.return_value.__dict__,
                "sync_generation": "upgrade-generation",
                "initial_body_target_count": 0,
                "initial_body_ready_count": 0,
                "initial_window_complete": True,
                "history_metadata_complete": True,
                "history_body_complete": True,
            }
        )

        ensure_background_import_work(self.settings, user_id="user-1")

        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(enqueue.call_args.kwargs["kind"], "gmail_full_reconcile")
        self.assertEqual(enqueue.call_args.kwargs["queue"], "slow")
        self.assertEqual(
            enqueue.call_args.kwargs["dedupe_key"],
            "gmail-full-reconcile:user-1:trigger",
        )
        projection.assert_not_called()

    def test_empty_app_snapshot_reports_queued_reconciliation_as_running(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-07-24T00:00:00+00:00",
            full_backfill_cursor=None,
            full_backfill_completed_at="2026-07-24T00:01:00+00:00",
            last_history_id="102",
            history_cursor_authoritative=False,
            reconcile_generation=None,
        )

        def active_jobs(_database_url: str, **kwargs) -> int:
            return 1 if kwargs.get("kinds") == ["gmail_full_reconcile"] else 0

        with patch(
            "app.services.mail_groups._google_auth_state",
            return_value=GoogleAuthState(
                available=True,
                connected=True,
                reauth_required=False,
                connect_url=None,
            ),
        ), patch(
            "app.services.mail_groups.ensure_background_import_work",
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ), patch(
            "app.services.mail_groups.get_import_state",
            return_value=state,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            side_effect=active_jobs,
        ):
            session = build_app_session_response(
                self.settings,
                user=CurrentUser(
                    id="user-1",
                    email="me@example.com",
                    display_name="TestUser",
                ),
            )

        self.assertTrue(session.mailbox.full_import_running)
        self.assertFalse(session.mailbox.full_import_completed)
        self.assertTrue(session.sync.full_import_running)
        self.assertFalse(session.sync.full_import_completed)

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=False, required_queues_ready=False))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.get_import_state", return_value=None)
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.missing_google_scopes", return_value=[])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    @patch("app.services.mail_groups.build_dashboard_response")
    def test_empty_snapshot_allows_entry_once_first_mail_import_completes(
        self,
        mock_live_dashboard: Mock,
        _mock_can_write: Mock,
        _mock_missing_scopes: Mock,
        mock_snapshot: Mock,
        _mock_state: Mock,
        _mock_recovery: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        mock_snapshot.return_value = AppSessionSnapshotRecord(
            user_id="user-1",
            gmail_account_id="account-1",
            dashboard={"auth": {"available": True, "connected": True, "connect_url": None}, "feed": {}, "runtime_status": {}},
            mailbox={"label": "inbox", "total_threads": 0, "sections": []},
            sync={
                "last_sync_at": None,
                "last_error": None,
                "enrichment_pending_count": 0,
                "ready_group_count": 0,
                "oldest_imported_at": None,
                "full_import_running": False,
                "full_import_completed": False,
                "projection_version": APP_SESSION_PROJECTION_VERSION,
            },
            updated_at="2026-05-15T12:00:00+00:00",
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        session = build_app_session_response(self.settings, user=user)

        self.assertEqual(session.user.email, "me@example.com")
        self.assertFalse(session.readiness.ready_to_enter)
        self.assertFalse(session.readiness.dashboard_ready)

        _mock_state.return_value = SimpleNamespace(first_batch_imported_at="2026-05-15T12:00:00+00:00")
        completed_session = build_app_session_response(self.settings, user=user)

        self.assertTrue(completed_session.readiness.ready_to_enter)
        self.assertTrue(completed_session.readiness.mailbox_ready)
        self.assertTrue(completed_session.readiness.dashboard_ready)
        mock_live_dashboard.assert_not_called()

    def test_post_login_readiness_allows_connected_empty_gmail_after_first_batch(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_completed_at=None,
            full_backfill_cursor=None,
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="empty@example.com", display_name="Empty Mailbox")
        with (
            patch("app.services.mail_groups.get_import_state", return_value=state),
            patch("app.services.mail_groups.count_mail_groups", return_value=0),
            patch("app.services.mail_groups.count_mailbox_threads", return_value=0),
            patch("app.services.mail_groups.count_dashboard_mail_groups", return_value=0),
            patch("app.services.mail_groups.count_active_jobs", return_value=0),
            patch(
                "app.services.mail_groups._google_auth_state",
                return_value=SimpleNamespace(connected=True, reauth_required=False),
            ),
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user)

        self.assertTrue(readiness.ready_to_enter)
        self.assertTrue(readiness.mailbox_ready)
        self.assertTrue(readiness.dashboard_ready)

    def test_post_login_readiness_keeps_disconnected_empty_account_blocked(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_completed_at=None,
            full_backfill_cursor=None,
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="empty@example.com", display_name="Empty Mailbox")
        with (
            patch("app.services.mail_groups.get_import_state", return_value=state),
            patch("app.services.mail_groups.count_mail_groups", return_value=0),
            patch("app.services.mail_groups.count_mailbox_threads", return_value=0),
            patch("app.services.mail_groups.count_dashboard_mail_groups", return_value=0),
            patch("app.services.mail_groups.count_active_jobs", return_value=0),
            patch(
                "app.services.mail_groups._google_auth_state",
                return_value=SimpleNamespace(connected=False, reauth_required=True),
            ),
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user)

        self.assertFalse(readiness.ready_to_enter)
        self.assertFalse(readiness.mailbox_ready)

    def test_heartbeat_renews_running_job_lease(self) -> None:
        connection = FakeConnection()
        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            renew_heartbeat(
                "postgresql://example/db",
                worker_id="worker-1",
                queues=["critical"],
                release_sha="release-abc123",
                current_job_id="job-1",
            )

        statements = [call[0] for call in connection.calls]
        heartbeat_statement, heartbeat_params = connection.calls[0]
        self.assertIn("release_sha", heartbeat_statement)
        self.assertEqual(heartbeat_params["release_sha"], "release-abc123")
        self.assertTrue(any("UPDATE background_jobs" in statement and "lease_expires_at" in statement for statement in statements))


class FakeResult:
    pass


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, params=None) -> FakeResult:
        self.calls.append((str(statement), dict(params or {})))
        return FakeResult()


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> FakeConnection:
        return self.connection


if __name__ == "__main__":
    unittest.main()
