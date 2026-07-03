from __future__ import annotations

import base64
from contextlib import ExitStack
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from app.db.jobs import renew_heartbeat
from app.db.mail_groups import AppSessionSnapshotRecord, GmailMessageRecord, MailboxCursorError, MailboxThreadPage, MailGroupRecord, VisibleMailGroupRecord, decode_mailbox_cursor, encode_mailbox_cursor
from app.services.auth import CurrentUser
from app.services.gmail_importer import FIRST_RUN_CURSOR_TYPE, _encode_first_run_cursor, _encode_full_mailbox_cursor, run_gmail_backfill, run_gmail_body_fetch, run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import (
    APP_SESSION_PROJECTION_VERSION,
    _candidate_groups,
    _first_run_candidate_message_batches,
    _first_run_candidate_messages,
    _gmail_row_from_ai_group,
    _gmail_row_from_canonical_thread,
    _mailbox_should_render_ai_group_as_group,
    _message_title_map,
    _refresh_ai_lifecycle_groups,
    _store_ai_batch_groups,
    build_app_session_response,
    build_mailbox_response,
    build_post_login_readiness_response,
    enqueue_mailbox_sync,
    ensure_background_import_work,
    rebuild_touched_mail_groups,
    refresh_app_session_snapshot,
    run_first_run_ai_grouping,
)
from app.schemas.domain import (
    GoogleAuthState,
    MailboxResponse,
    SmartInboxResponse,
    SmartReadinessResponse,
    SmartWorkQueueResponse,
)
from app.services.attention_classifier import attention_enrichment_payload, workflow_cluster_key
from app.services.mail_group_config import FIRST_RUN_AI_CANDIDATE_LIMIT, FIRST_RUN_HOT_WINDOW_BATCH_SIZE, MAIL_GROUP_ENRICH_BATCH_SIZE
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
        evidence={"strong_evidence": ["ticket_id:106400420"]},
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
            openai_model="gpt-5.4-mini",
            gmail_pubsub_topic="projects/example/topics/gmail",
            gmail_watch_renewal_hours=24,
            gmail_recent_days=90,
        )
        self.importer_event_patch = patch("app.services.gmail_importer.emit_mailbox_event")
        self.worker_event_patch = patch("app.workers.main.emit_mailbox_event")
        self.mock_importer_event = self.importer_event_patch.start()
        self.mock_worker_event = self.worker_event_patch.start()
        self.addCleanup(self.importer_event_patch.stop)
        self.addCleanup(self.worker_event_patch.stop)

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
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
        _mock_completed: Mock,
        mock_enqueue: Mock,
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
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_first_run_import_uses_inbox_and_sent_hot_window_seeds_with_thread_history(
        self,
        _mock_can_write: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_draft_list: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        mock_thread_history: Mock,
    ) -> None:
        recent_at = "2026-06-15T12:00:00+00:00"
        inbox = replace(sample_message("inbox-1"), internal_date=recent_at)
        sent = replace(sample_message("sent-1"), gmail_thread_id="thread-2", label_ids=["SENT"], internal_date=recent_at)
        history = replace(sample_message("history-1"), gmail_thread_id="thread-1", label_ids=["SENT"], internal_date=recent_at)
        mock_list.side_effect = [
            {"messages": [{"id": "inbox-1"}]},
            {"messages": [{"id": "sent-1"}]},
        ]
        mock_hydrate.side_effect = [([inbox], "10"), ([sent], "11")]
        mock_thread_history.return_value = ([history], "12")

        imported = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=500, first_run=True)

        self.assertEqual(imported, 3)
        self.assertEqual([call.kwargs["label_ids"] for call in mock_list.call_args_list], [["INBOX"], ["SENT"]])
        self.assertTrue(all(call.kwargs["batch_size"] == 150 for call in mock_list.call_args_list))
        mock_draft_list.assert_not_called()
        mock_thread_history.assert_called_once()
        self.assertEqual(mock_upsert.call_args.args[1], [inbox, sent, history])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertFalse(mock_completed.call_args.kwargs["clear_full_backfill_cursor"])
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertTrue(mock_completed.call_args.kwargs["hot_window_started"])
        self.assertTrue(mock_completed.call_args.kwargs["hot_window_completed"])
        self.assertTrue(any(call.kwargs.get("kind") == "gmail_backfill" for call in mock_enqueue.call_args_list))
        self.assertTrue(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))

    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_first_run_import_does_not_drop_fetched_seed_page_messages_when_thread_history_exceeds_batch(
        self,
        _mock_can_write: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_draft_list: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        _mock_completed: Mock,
        _mock_enqueue: Mock,
        mock_thread_history: Mock,
    ) -> None:
        recent_at = "2026-06-15T12:00:00+00:00"
        inbox = replace(sample_message("inbox-small"), gmail_thread_id="thread-inbox", internal_date=recent_at)
        sent = replace(sample_message("sent-small"), gmail_thread_id="thread-sent", label_ids=["SENT"], internal_date=recent_at)
        history = replace(sample_message("history-small"), gmail_thread_id="thread-inbox", label_ids=["SENT"], internal_date="2026-06-15T12:01:00+00:00")
        mock_list.side_effect = [
            {"messages": [{"id": "inbox-small"}]},
            {"messages": [{"id": "sent-small"}]},
        ]
        mock_hydrate.side_effect = [([inbox], "10"), ([sent], "11")]
        mock_thread_history.return_value = ([history], "14")

        imported = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=3, first_run=True)

        self.assertEqual(imported, 3)
        mock_draft_list.assert_not_called()
        self.assertEqual(
            {message.message_id for message in mock_upsert.call_args.args[1]},
            {"inbox-small", "sent-small", "history-small"},
        )

    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages", return_value=([], None))
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_draft_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_first_run_heavy_mailbox_imports_only_first_ready_batch(
        self,
        _mock_can_write: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_draft_list: Mock,
        mock_hydrate: Mock,
        mock_upsert: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        _mock_thread_history: Mock,
    ) -> None:
        def listed_page(prefix: str) -> dict[str, object]:
            return {"messages": [{"id": f"{prefix}-{index}"} for index in range(100)], "nextPageToken": f"{prefix}-next"}

        def hydrated_page(prefix: str) -> list[GmailMessageRecord]:
            return [
                replace(
                    sample_message(f"{prefix}-{index}"),
                    gmail_thread_id=f"thread-{prefix}-{index}",
                    internal_date=f"2026-06-01T12:{index % 60:02d}:00+00:00",
                )
                for index in range(100)
            ]

        mock_list.side_effect = [listed_page("inbox"), listed_page("sent")]
        mock_hydrate.side_effect = [
            (hydrated_page("inbox"), "10"),
            (hydrated_page("sent"), "11"),
        ]

        imported = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=500, first_run=True)

        self.assertEqual(imported, 200)
        self.assertEqual(len(mock_upsert.call_args.args[1]), 200)
        self.assertTrue(all(call.kwargs["batch_size"] == 150 for call in mock_list.call_args_list))
        mock_draft_list.assert_not_called()
        self.assertTrue(mock_completed.call_args.kwargs["hot_window_started"])
        self.assertFalse(mock_completed.call_args.kwargs["hot_window_completed"])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        backfill_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "gmail_backfill")
        self.assertEqual(backfill_call.kwargs["queue"], "critical")
        self.assertEqual(backfill_call.kwargs["priority"], 90)
        self.assertEqual(backfill_call.kwargs["payload"]["batch_size"], FIRST_RUN_HOT_WINDOW_BATCH_SIZE)
        self.assertFalse(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))

    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
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
        mock_state.return_value = SimpleNamespace(last_history_id="10")
        mock_history.return_value = {"message_ids": [], "deleted_message_ids": [], "latest_history_id": "11"}
        mock_hydrate_ids.return_value = ([], None)

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 0)
        mock_list_messages.assert_not_called()
        mock_upsert.assert_not_called()
        mock_rebuild.assert_not_called()
        mock_completed.assert_called_once()
        self.assertEqual(mock_completed.call_args.kwargs["last_history_id"], "11")
        mock_enqueue.assert_not_called()
        mock_projection.assert_not_called()

    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
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
        mock_state.return_value = SimpleNamespace(last_history_id="10")
        mock_history.return_value = {"message_ids": ["msg-2"], "deleted_message_ids": [], "latest_history_id": "12"}
        mock_hydrate_ids.return_value = ([message], "12")

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100, target_history_id="13")

        self.assertEqual(touched, 1)
        mock_list_messages.assert_not_called()
        mock_hydrate_ids.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], format="metadata")
        mock_rebuild.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)
        self.assertTrue(any(call.kwargs.get("kind") == "mail_group_enrich" for call in mock_enqueue.call_args_list))
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

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
    ) -> None:
        mock_state.return_value = SimpleNamespace(last_history_id="10")
        mock_history.side_effect = HttpError(Response({"status": "404"}), b"History expired")

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 2)
        mock_fallback.assert_called_once_with(self.settings, user_id="user-1", batch_size=100, can_write_checked=True)

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
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
        mock_state.return_value = SimpleNamespace(last_history_id="10")
        mock_history.return_value = {"message_ids": [], "deleted_message_ids": ["msg-1"], "latest_history_id": "11"}

        touched = run_gmail_delta_sync(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        mock_delete.assert_called_once_with(self.settings.database_path, user_id="user-1", message_ids=["msg-1"])
        mock_prune.assert_called_once_with(self.settings.database_path, user_id="user-1", group_ids=["group-1"])
        mock_pending.assert_called_once_with(self.settings.database_path, user_id="user-1", group_ids=["group-1"])
        self.assertTrue(any(call.kwargs.get("kind") == "mail_group_enrich" for call in mock_enqueue.call_args_list))
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(last_history_id="10"))
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
    def test_changed_ready_group_becomes_pending_for_fresh_ai(
        self,
        _mock_can_write: Mock,
        _mock_messages: Mock,
        _mock_group_messages: Mock,
        _mock_existing: Mock,
        mock_upsert_group: Mock,
        _mock_append: Mock,
    ) -> None:
        touched = rebuild_touched_mail_groups(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)

        self.assertEqual(touched, 1)
        self.assertEqual(mock_upsert_group.call_args.kwargs["enrichment_status"], "pending")
        self.assertFalse(mock_upsert_group.call_args.kwargs["dashboard_visible"])

    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.refresh_app_session_snapshot")
    @patch("app.services.mail_groups.refresh_visible_mail_projection")
    @patch("app.services.mail_groups.mark_import_completed")
    @patch("app.services.mail_groups.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.mail_groups._store_ai_batch_groups", return_value=(3, 1, {"msg-1"}))
    @patch("app.services.mail_groups._ai_batch_group_messages", return_value=[])
    @patch("app.services.mail_groups._first_run_candidate_message_batches")
    @patch("app.services.mail_groups.list_recent_messages_since")
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(hot_window_completed_at="2026-05-15T12:01:00+00:00"))
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_first_run_creates_pending_groups_for_ai_omitted_recent_messages(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        mock_recent: Mock,
        mock_candidate_batches: Mock,
        _mock_ai: Mock,
        _mock_store: Mock,
        mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        _mock_visible_projection: Mock,
        _mock_snapshot: Mock,
        mock_enqueue: Mock,
    ) -> None:
        selected = sample_message("msg-1")
        omitted = replace(sample_message("msg-2"), gmail_thread_id="thread-2")
        sent_omitted = replace(sample_message("sent-omitted"), gmail_thread_id="thread-sent", label_ids=["SENT"])
        draft_omitted = replace(sample_message("draft-omitted"), gmail_thread_id="thread-draft", label_ids=["DRAFT"])
        trashed_omitted = replace(sample_message("trash-omitted"), gmail_thread_id="thread-trash", label_ids=["TRASH"])
        mock_recent.return_value = [selected, omitted, sent_omitted, draft_omitted, trashed_omitted]
        mock_candidate_batches.return_value = [[selected]]

        created = run_first_run_ai_grouping(self.settings, user_id="user-1", limit=50)

        self.assertEqual(created, 3)
        self.assertIsNone(mock_recent.call_args.kwargs["limit"])
        mock_rebuild_touched.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)
        _mock_completed.assert_called_once()
        self.assertTrue(_mock_completed.call_args.kwargs["groups_ready"])
        self.assertFalse(_mock_completed.call_args.kwargs["dashboard_ready"])
        _mock_snapshot.assert_called_once_with(self.settings, user_id="user-1", include_dashboard=False)
        enrichment_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "mail_group_enrich")
        self.assertEqual(enrichment_call.kwargs["queue"], "critical")
        self.assertEqual(enrichment_call.kwargs["priority"], 96)
        self.assertEqual(enrichment_call.kwargs["dedupe_key"], "first-run-mail-group-enrich:user-1")
        self.assertEqual(enrichment_call.kwargs["payload"]["source"], "first_run_hot_window")
        self.assertTrue(any(call.kwargs.get("kind") == "app_session_snapshot_refresh" for call in mock_enqueue.call_args_list))

    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.refresh_app_session_snapshot")
    @patch("app.services.mail_groups.refresh_visible_mail_projection")
    @patch("app.services.mail_groups.mark_import_completed")
    @patch("app.services.mail_groups.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.mail_groups._store_ai_batch_groups")
    @patch("app.services.mail_groups._ai_batch_group_messages", return_value=[])
    @patch("app.services.mail_groups._first_run_candidate_message_batches")
    @patch("app.services.mail_groups.list_recent_messages_since")
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(hot_window_completed_at="2026-05-15T12:01:00+00:00"))
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_first_run_ai_grouping_processes_every_candidate_batch_before_ready(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        mock_recent: Mock,
        mock_candidate_batches: Mock,
        _mock_ai: Mock,
        mock_store: Mock,
        mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        _mock_visible_projection: Mock,
        _mock_snapshot: Mock,
        _mock_enqueue: Mock,
    ) -> None:
        batch_one_used = sample_message("batch-one-used")
        batch_one_omitted = replace(sample_message("batch-one-omitted"), gmail_thread_id="thread-batch-one-omitted")
        batch_two_used = replace(sample_message("batch-two-used"), gmail_thread_id="thread-batch-two-used")
        mock_recent.return_value = [batch_one_used, batch_one_omitted, batch_two_used]
        mock_candidate_batches.return_value = [[batch_one_used, batch_one_omitted], [batch_two_used]]
        mock_store.side_effect = [
            (1, 1, {"batch-one-used"}),
            (2, 0, {"batch-two-used"}),
        ]

        created = run_first_run_ai_grouping(self.settings, user_id="user-1", limit=50)

        self.assertEqual(created, 3)
        self.assertEqual(_mock_ai.call_count, 2)
        self.assertEqual(mock_store.call_count, 2)
        self.assertEqual([call.kwargs["messages"] for call in mock_store.call_args_list], mock_candidate_batches.return_value)
        mock_rebuild_touched.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=["batch-one-omitted"],
            use_ai=False,
        )

    def test_first_run_ai_grouping_waits_for_hot_window_completion(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at=None,
            hot_window_completed_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="first-run-cursor",
        )
        with patch("app.services.mail_groups.user_can_write_gmail", return_value=True), patch(
            "app.services.mail_groups.get_import_state",
            return_value=state,
        ), patch("app.services.mail_groups.ensure_background_import_work") as mock_ensure, patch(
            "app.services.mail_groups.list_recent_messages_since"
        ) as mock_recent:
            created = run_first_run_ai_grouping(self.settings, user_id="user-1", limit=50)

        self.assertEqual(created, 0)
        mock_ensure.assert_called_once_with(self.settings, user_id="user-1")
        mock_recent.assert_not_called()

    def test_first_run_candidates_preserve_exact_reference_clusters_beyond_top_ranked_messages(self) -> None:
        high_ranked = [
            replace(
                sample_message(f"high-{index}"),
                gmail_thread_id=f"thread-high-{index}",
                internal_date=f"2026-06-01T12:{index % 60:02d}:00+00:00",
                extracted_signals={"sender_domain": "alerts.example.com", "normalized_subject": f"action required {index}"},
            )
            for index in range(FIRST_RUN_AI_CANDIDATE_LIMIT + 20)
        ]
        order_confirmed = replace(
            sample_message("apple-order-confirmed"),
            gmail_thread_id="thread-apple-order-confirmed",
            label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            internal_date="2026-05-01T12:00:00+00:00",
            subject="Apple order confirmed",
            sender="Apple <orders@fruitco.example>",
            snippet="Your Apple order W123456789 was confirmed.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "list_id": "orders.apple.com",
                "normalized_subject": "apple order confirmed",
            },
        )
        order_delivered = replace(
            order_confirmed,
            message_id="apple-order-delivered",
            gmail_thread_id="thread-apple-order-delivered",
            subject="Apple order delivered",
            snippet="Your Apple order W123456789 was delivered.",
            internal_date="2026-05-01T13:00:00+00:00",
        )

        candidates = _first_run_candidate_messages([*high_ranked, order_confirmed, order_delivered])

        self.assertEqual(len(candidates), FIRST_RUN_AI_CANDIDATE_LIMIT)
        self.assertTrue({"apple-order-confirmed", "apple-order-delivered"} <= {message.message_id for message in candidates})

    def test_first_run_candidate_batches_cover_all_visible_hot_window_messages(self) -> None:
        messages = [
            replace(
                sample_message(f"visible-{index}"),
                gmail_thread_id=f"thread-visible-{index}",
                internal_date=f"2026-06-01T12:{index % 60:02d}:00+00:00",
                extracted_signals={"sender_domain": "alerts.example.com", "normalized_subject": f"action required {index}"},
            )
            for index in range(FIRST_RUN_AI_CANDIDATE_LIMIT + 5)
        ]
        archived = replace(
            sample_message("archived-drop"),
            gmail_thread_id="thread-archived-drop",
            label_ids=["CATEGORY_PROMOTIONS", "UNREAD"],
            extracted_signals={"sender_domain": "archive.example.com", "normalized_subject": "archived drop"},
        )

        batches = _first_run_candidate_message_batches([*messages, archived])
        flattened_ids = [message.message_id for batch in batches for message in batch]

        self.assertEqual(len(batches), 2)
        self.assertTrue(all(len(batch) <= FIRST_RUN_AI_CANDIDATE_LIMIT for batch in batches))
        self.assertEqual(set(flattened_ids), {message.message_id for message in messages})
        self.assertNotIn("archived-drop", flattened_ids)

    def test_first_run_candidates_filter_non_inbox_noise_before_ranking(self) -> None:
        archived_high_ranked = [
            replace(
                sample_message(f"archived-high-{index}"),
                gmail_thread_id=f"thread-archived-high-{index}",
                label_ids=["CATEGORY_PROMOTIONS", "UNREAD"],
                internal_date=f"2026-06-01T12:{index % 60:02d}:00+00:00",
                subject=f"Archived order update {index}",
                extracted_signals={
                    "sender_domain": "archive.example.com",
                    "order_id": f"ARCHIVE-{index}",
                    "normalized_subject": f"archived order update {index}",
                },
            )
            for index in range(FIRST_RUN_AI_CANDIDATE_LIMIT + 10)
        ]
        inbox_message = replace(
            sample_message("inbox-keep"),
            gmail_thread_id="thread-inbox-keep",
            label_ids=["INBOX"],
            internal_date="2026-06-01T13:00:00+00:00",
        )
        sent_context = replace(
            sample_message("sent-keep"),
            gmail_thread_id="thread-sent-keep",
            label_ids=["SENT"],
            internal_date="2026-06-01T13:01:00+00:00",
        )
        spam = replace(sample_message("spam-drop"), gmail_thread_id="thread-spam", label_ids=["INBOX", "SPAM", "UNREAD"])
        trash = replace(sample_message("trash-drop"), gmail_thread_id="thread-trash", label_ids=["INBOX", "TRASH", "UNREAD"])
        draft = replace(sample_message("draft-drop"), gmail_thread_id="thread-draft", label_ids=["DRAFT"])

        candidates = _first_run_candidate_messages([*archived_high_ranked, spam, trash, draft, inbox_message, sent_context])
        candidate_ids = {message.message_id for message in candidates}

        self.assertEqual(candidate_ids, {"inbox-keep", "sent-keep"})

    @patch("app.services.mail_groups.replace_group_members")
    @patch("app.services.mail_groups.upsert_mail_group", return_value=SimpleNamespace(id="ai-lifecycle-group"))
    @patch("app.services.mail_groups._ai_lifecycle_group_proposals")
    @patch("app.services.mail_groups.list_recent_messages_since")
    def test_projection_refresh_persists_ai_lifecycle_proposals(
        self,
        mock_recent: Mock,
        mock_proposals: Mock,
        mock_upsert_group: Mock,
        mock_replace_members: Mock,
    ) -> None:
        settings = SimpleNamespace(**{**self.settings.__dict__, "openai_configured": True})
        inbound = replace(
            sample_message("msg-1"),
            gmail_thread_id="thread-1",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Request for Status of International Wire Sent",
            snippet="Northstar is checking the international wire status.",
            extracted_signals={"sender_domain": "northstarbank.example"},
        )
        processed = replace(
            sample_message("msg-2"),
            gmail_thread_id="thread-2",
            sender="Tradequalityunit <tradequalityunit@northstarbank.example>",
            subject="Outward remittance processed",
            snippet="The outward remittance was processed.",
            extracted_signals={"sender_domain": "northstarbank.example"},
        )
        sent = replace(
            sample_message("msg-3"),
            gmail_thread_id="thread-3",
            label_ids=["SENT"],
            sender="TestUser <me@example.com>",
            subject="Fwd: Request for Status of International Wire Sent",
            extracted_signals={"sender_domain": "gmail.com"},
        )
        mock_recent.return_value = [inbound, processed, sent]
        mock_proposals.return_value = [
            {
                "group_kind": "lifecycle",
                "canonical_entity": "Northstar Bank",
                "shared_object": "International wire remittance status",
                "workflow_family": "financial_transfer",
                "member_ids": ["msg-1", "msg-2"],
                "context_sent_ids": ["msg-3"],
                "excluded_ids": [],
                "confidence": 0.96,
                "risk_level": "low",
                "per_message_evidence": {
                    "msg-1": "Northstar support is responding on the wire status request.",
                    "msg-2": "Northstar confirms the remittance was processed.",
                },
                "strong_evidence": ["same international wire/remittance lifecycle"],
                "weak_evidence": ["same bank and close dates"],
                "should_show_in_inbox": True,
                "should_show_in_dashboard": True,
                "workflow_state": "resolved",
                "requires_user_action": False,
                "terminal_state": True,
                "urgency": "low",
                "ai_title": "Northstar international wire remittance status",
                "ai_summary": "Northstar support and remittance mail describe the same international wire status.",
            }
        ]

        created = _refresh_ai_lifecycle_groups(settings, user_id="user-1")

        self.assertEqual(created, 1)
        self.assertEqual(mock_upsert_group.call_args.kwargs["membership_source"], "ai_lifecycle")
        self.assertEqual(mock_upsert_group.call_args.kwargs["group_type"], "financial_transfer")
        members = mock_replace_members.call_args.kwargs["members"]
        self.assertEqual({message.message_id for message, _source, _confidence in members}, {"msg-1", "msg-2", "msg-3"})

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

    def test_candidate_groups_merge_booking_lifecycle_threads_by_booking_id(self) -> None:
        booked = replace(
            sample_message("transit-booked"),
            sender="TransitCo <noreply@transitco.example.com>",
            gmail_thread_id="thread-transit-booked",
            subject="Your ride is booked",
            extracted_signals={"sender_domain": "transitco.example.com", "booking_id": "CF123"},
        )
        modified = replace(
            sample_message("transit-modified"),
            sender="TransitCo <noreply@transitco.example.com>",
            gmail_thread_id="thread-transit-modified",
            subject="Your ride was modified",
            extracted_signals={"sender_domain": "transitco.example.com", "booking_id": "CF123"},
        )
        cancelled = replace(
            sample_message("transit-cancelled"),
            sender="TransitCo <noreply@transitco.example.com>",
            gmail_thread_id="thread-transit-cancelled",
            subject="Your ride was cancelled",
            extracted_signals={"sender_domain": "transitco.example.com", "booking_id": "CF123"},
        )

        groups = _candidate_groups([booked, modified, cancelled])

        self.assertEqual(set(groups.keys()), {"booking_id:transitco:CF123"})
        self.assertEqual({message.message_id for message in groups["booking_id:transitco:CF123"]}, {"transit-booked", "transit-modified", "transit-cancelled"})

    def test_candidate_groups_merge_repair_lifecycle_threads_by_repair_id(self) -> None:
        opened = replace(
            sample_message("repair-opened"),
            sender="RepairCo <support@repairco.example.com>",
            gmail_thread_id="thread-repair-opened",
            subject="Repair request opened",
            extracted_signals={"sender_domain": "repairco.example.com", "repair_id": "SR99887766"},
        )
        updated = replace(
            sample_message("repair-updated"),
            sender="RepairCo <support@repairco.example.com>",
            gmail_thread_id="thread-repair-updated",
            subject="Repair request updated",
            extracted_signals={"sender_domain": "repairco.example.com", "repair_id": "SR99887766"},
        )

        groups = _candidate_groups([opened, updated])

        self.assertEqual(set(groups.keys()), {"repair_id:repairco:SR99887766"})
        self.assertEqual({message.message_id for message in groups["repair_id:repairco:SR99887766"]}, {"repair-opened", "repair-updated"})

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.mark_gmail_messages_body_fetch_state")
    @patch("app.services.gmail_importer._batch_get_message_payloads", side_effect=RuntimeError("gmail quota"))
    @patch("app.services.gmail_importer.build_google_service", return_value=object())
    @patch("app.services.gmail_importer.create_authorized_credentials", return_value=object())
    @patch("app.services.gmail_importer.list_messages_for_gmail_thread")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_body_fetch_failure_queues_projection_refresh_for_exact_status(
        self,
        _mock_can_write: Mock,
        mock_thread_messages: Mock,
        _mock_credentials: Mock,
        _mock_service: Mock,
        _mock_payloads: Mock,
        mock_mark: Mock,
        mock_projection: Mock,
    ) -> None:
        mock_thread_messages.return_value = [sample_message("msg-body-missing")]

        with self.assertRaises(RuntimeError):
            run_gmail_body_fetch(self.settings, user_id="user-1", gmail_thread_id="thread-1")

        self.assertEqual(mock_mark.call_args_list[0].kwargs["status"], "pending")
        self.assertEqual(mock_mark.call_args_list[1].kwargs["status"], "failed")
        self.assertEqual(mock_mark.call_args_list[1].kwargs["message_ids"], ["msg-body-missing"])
        mock_projection.assert_called_once_with(self.settings, user_id="user-1")

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
        self.assertTrue(
            any(
                call.kwargs.get("kind") == "mail_group_enrich" and call.kwargs.get("queue") == "default"
                for call in mock_enqueue.call_args_list
            )
        )
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=1)

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
        backfill_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "gmail_backfill")
        self.assertEqual(backfill_call.kwargs["queue"], "slow")
        self.assertEqual(backfill_call.kwargs["payload"]["batch_size"], 100)

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages", return_value=([], None))
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_first_run_seed_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_recent_window_cursor_completion_marks_hot_window_complete_before_full_history(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_hydrate: Mock,
        _mock_thread_history: Mock,
        _mock_upsert: Mock,
        _mock_rebuild_touched: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        cursor = _encode_first_run_cursor(
            next_tokens={"INBOX": "page-2"},
            completed_labels={"SENT", "DRAFT", "__ALL_MAIL__"},
            cutoff_ms=0,
        )
        message = sample_message("recent-last")
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
        )
        mock_list.return_value = {"messages": [{"id": "recent-last"}]}
        mock_hydrate.return_value = ([message], "30")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=30)

        self.assertEqual(touched, 1)
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertFalse(mock_completed.call_args.kwargs["full_backfill_completed"])
        self.assertTrue(mock_completed.call_args.kwargs["hot_window_completed"])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertTrue(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))
        grouping_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "first_run_ai_grouping")
        self.assertEqual(grouping_call.kwargs["queue"], "critical")
        self.assertEqual(grouping_call.kwargs["priority"], 98)
        self.assertEqual(grouping_call.kwargs["dedupe_key"], "first-run-ai-grouping-hot-window:user-1")
        backfill_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "gmail_backfill")
        self.assertEqual(backfill_call.kwargs["queue"], "slow")

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages", return_value=([], None))
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_first_run_seed_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_recent_window_cursor_continues_past_message_cap_until_hot_window_exhausted(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_hydrate: Mock,
        _mock_thread_history: Mock,
        mock_upsert: Mock,
        _mock_rebuild_touched: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        cursor = _encode_first_run_cursor(
            next_tokens={"INBOX": "page-2"},
            completed_labels={"SENT", "DRAFT", "__ALL_MAIL__"},
            cutoff_ms=0,
            imported_count=10_000,
        )
        messages = [
            replace(sample_message(f"hot-cap-{index}"), gmail_thread_id=f"thread-hot-cap-{index}")
            for index in range(10)
        ]
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
        )
        mock_list.return_value = {"messages": [{"id": message.message_id} for message in messages], "nextPageToken": "still-more-hot"}
        mock_hydrate.return_value = (messages, "40")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 1)
        mock_list.assert_called_once_with(self.settings, user_id="user-1", batch_size=100, page_token="page-2", label_id="INBOX")
        self.assertEqual(len(mock_upsert.call_args.args[1]), 10)
        self.assertFalse(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertFalse(mock_completed.call_args.kwargs["full_backfill_completed"])
        self.assertFalse(mock_completed.call_args.kwargs["hot_window_completed"])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertFalse(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages")
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_first_run_seed_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_recent_window_cursor_keeps_thread_history_even_when_page_exceeds_batch_limit(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_hydrate: Mock,
        mock_thread_history: Mock,
        mock_upsert: Mock,
        _mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        _mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        cursor = _encode_first_run_cursor(
            next_tokens={"INBOX": "page-2"},
            completed_labels={"SENT", "DRAFT", "__ALL_MAIL__"},
            cutoff_ms=0,
        )
        page_messages = [
            replace(sample_message(f"cursor-page-{index}"), gmail_thread_id=f"thread-cursor-{index}")
            for index in range(3)
        ]
        history = replace(
            sample_message("cursor-history"),
            gmail_thread_id="thread-cursor-0",
            label_ids=["SENT"],
            internal_date="2026-06-15T12:01:00+00:00",
        )
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
        )
        mock_list.return_value = {"messages": [{"id": message.message_id} for message in page_messages], "nextPageToken": "page-3"}
        mock_hydrate.return_value = (page_messages, "40")
        mock_thread_history.return_value = ([history], "41")

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=3)

        self.assertEqual(touched, 1)
        self.assertEqual(
            {message.message_id for message in mock_upsert.call_args.args[1]},
            {"cursor-page-0", "cursor-page-1", "cursor-page-2", "cursor-history"},
        )

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=0)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=0)
    @patch("app.services.gmail_importer._hydrate_thread_metadata_for_messages", return_value=([], None))
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_first_run_seed_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_legacy_non_inbox_hot_window_cursor_finishes_without_looping(
        self,
        _mock_can_write: Mock,
        mock_state: Mock,
        _mock_started: Mock,
        mock_seed_list: Mock,
        mock_hydrate: Mock,
        _mock_thread_history: Mock,
        _mock_upsert: Mock,
        _mock_rebuild_touched: Mock,
        mock_completed: Mock,
        mock_enqueue: Mock,
        _mock_projection: Mock,
    ) -> None:
        payload = {
            "type": FIRST_RUN_CURSOR_TYPE,
            "next_tokens": {"DRAFT": "draft-page-2", "__ALL_MAIL__": "all-mail-page-2"},
            "completed_labels": ["INBOX", "SENT"],
            "cutoff_ms": 0,
        }
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        mock_state.return_value = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            full_backfill_cursor=cursor,
            full_backfill_completed_at=None,
        )

        touched = run_gmail_backfill(self.settings, user_id="user-1", batch_size=100)

        self.assertEqual(touched, 0)
        mock_seed_list.assert_not_called()
        mock_hydrate.assert_not_called()
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertTrue(mock_completed.call_args.kwargs["hot_window_completed"])
        self.assertTrue(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))
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
        mock_count_threads.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            label="inbox",
            since_iso=None,
        )

    def test_mailbox_cursor_round_trips_and_rejects_invalid_values(self) -> None:
        cursor = encode_mailbox_cursor("2026-05-15T12:00:00+00:00", "thread-1")

        latest_at, thread_key = decode_mailbox_cursor(cursor)

        self.assertEqual(latest_at, "2026-05-15T12:00:00+00:00")
        self.assertEqual(thread_key, "thread-1")
        with self.assertRaises(MailboxCursorError):
            decode_mailbox_cursor("not-a-valid-cursor")

    def test_candidate_groups_normalize_exact_reference_sender_variants(self) -> None:
        grievance = replace(
            sample_message("provider-grievance"),
            gmail_thread_id="thread-grievance",
            sender="Provider Support <support@support.provider.example.com>",
            extracted_signals={
                "sender_domain": "support.provider.example.com",
                "normalized_subject": "registered support request",
                "ticket_id": "106756996",
            },
        )
        service_request = replace(
            sample_message("provider-service"),
            gmail_thread_id="thread-service",
            sender="Provider Care <care@mail.provider.example.com>",
            extracted_signals={
                "sender_domain": "mail.provider.example.com",
                "normalized_subject": "registered service request",
                "ticket_id": "106756996",
            },
        )

        candidates = _candidate_groups([grievance, service_request])

        self.assertEqual(list(candidates), ["ticket_id:provider:106756996"])
        self.assertEqual([message.message_id for message in candidates["ticket_id:provider:106756996"]], ["provider-grievance", "provider-service"])

    def test_mailbox_uses_compatible_ai_group_for_title_and_summary_without_replacing_thread_id(self) -> None:
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
        self.assertEqual(rows[0].thread_id, "group-1")
        self.assertEqual(rows[0].entity_id, "visible-1")
        self.assertEqual(rows[0].title, "AI grouped title")
        self.assertEqual(rows[0].summary, "AI grouped summary")
        self.assertEqual(rows[0].ai_group_id, "group-1")
        self.assertEqual(rows[0].ai_title, "AI grouped title")
        self.assertEqual(rows[0].ai_summary, "AI grouped summary")
        self.assertEqual(rows[0].sender, "Example Sender")
        self.assertEqual(mailbox.mailbox_revision, "rev-1")
        self.assertIsNotNone(mailbox.generated_at)
        self.assertEqual(rows[0].latest_subject, "Raw second subject")
        self.assertTrue(rows[0].has_attachments)
        self.assertEqual(rows[0].attachment_count, 1)
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["msg-1", "msg-2"])
        self.assertEqual(rows[0].grouping_metadata["source"], "visible_mail_projection")
        self.assertEqual(rows[0].grouping_metadata["workflow"]["family"], "support_case")
        self.assertEqual(rows[0].grouping_metadata["evidence"]["strong_evidence"], ["ticket_id:106400420"])
        self.assertEqual(rows[0].grouping_metadata["conversation"]["message_count"], 2)
        self.assertEqual(rows[0].children[0].subject, "Raw first subject")
        self.assertEqual(rows[0].children[0].ai_title, "Clean first title")
        self.assertEqual(rows[0].children[1].subject, "Raw second subject")
        self.assertEqual(rows[0].children[1].ai_title, "Clean second title")

    def test_mailbox_uses_thread_ai_group_for_plain_gmail_thread_rows(self) -> None:
        message = replace(
            sample_message("msg-plain"),
            gmail_thread_id="thread-plain",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Northstar support case 106400420 acknowledged",
            sender="Northstar Bank <support@northstarbank.example>",
            snippet="Northstar confirmed support case 106400420 and next steps.",
            extracted_signals={
                "sender_domain": "northstarbank.example",
                "ticket_id": "106400420",
                "normalized_subject": "northstar support case 106400420 acknowledged",
            },
        )
        ai_group = replace(
            sample_group(),
            id="group-thread-plain",
            group_key="gmail-thread:thread-plain",
            ai_title="Northstar support case 106400420 acknowledged",
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
        self.assertEqual(rows[0].entity_id, "group-thread-plain")
        self.assertEqual(rows[0].title, "Northstar support case 106400420 acknowledged")
        self.assertEqual(rows[0].summary, "Bank confirmed the support case and next steps.")
        self.assertEqual(rows[0].ai_group_id, "group-thread-plain")
        self.assertEqual(rows[0].presentation_status, "ai_ready")

    def test_ai_group_title_is_ready_without_persisted_summary(self) -> None:
        message = replace(
            sample_message("msg-title-only"),
            gmail_thread_id="thread-title-only",
            subject="Northstar wire remittance processed",
            sender="Northstar Bank <support@northstarbank.example>",
            snippet="Northstar confirmed the outward remittance was processed.",
        )
        ai_group = replace(
            sample_group(),
            id="group-title-only",
            group_key="gmail-thread:thread-title-only",
            ai_title="Northstar wire remittance processed",
            ai_summary="",
        )

        row = _gmail_row_from_canonical_thread("thread-title-only", [message], "inbox", ai_group)

        self.assertEqual(row.title, "Northstar wire remittance processed")
        self.assertEqual(row.ai_title, "Northstar wire remittance processed")
        self.assertIsNone(row.ai_summary)
        self.assertEqual(row.presentation_status, "ai_ready")

    def test_cross_thread_ai_group_can_render_without_persisted_summary(self) -> None:
        group = replace(
            sample_group(),
            group_key="ai:northstar-wire",
            membership_source="ai_batch",
            ai_title="Northstar wire remittance processed",
            ai_summary="",
            classification={
                "grouping_contract": {
                    "group_kind": "lifecycle",
                    "should_show_in_inbox": True,
                }
            },
        )

        self.assertTrue(_mailbox_should_render_ai_group_as_group(group))

    def test_ai_batch_groups_store_title_without_eager_summary(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db", openai_model="gpt-test")
        message = replace(
            sample_message("msg-apple-order"),
            sender="Apple <orders@fruitco.example>",
            subject="Your order was delivered",
            snippet="Your Apple order W123456789 was delivered.",
            extracted_signals={"sender_domain": "apple.com", "order_id": "W123456789"},
        )
        output = {
            "client_group_key": "apple-order",
            "group_kind": "single",
            "canonical_entity": "Apple",
            "shared_object": "order W123456789",
            "workflow_family": "logistics",
            "workflow_state": "resolved",
            "requires_user_action": False,
            "terminal_state": True,
            "urgency": "low",
            "confidence": 0.96,
            "risk_level": "low",
            "should_show_in_inbox": True,
            "should_show_in_dashboard": False,
            "strong_evidence": ["order_id:W123456789"],
            "weak_evidence": [],
            "excluded_ids": [],
            "reason": "The email says the Apple order was delivered.",
            "ai_title": "Apple order W123456789 delivered",
            "labels": ["logistics"],
            "member_message_ids": ["msg-apple-order"],
            "message_titles": [{"message_id": "msg-apple-order", "ai_title": "Apple order delivered"}],
        }

        with patch("app.services.mail_groups.upsert_mail_group", return_value=sample_group()) as mock_upsert, patch(
            "app.services.mail_groups.replace_group_members"
        ), patch("app.services.mail_groups._persist_message_ai_titles"):
            created, _visible_created, used_ids = _store_ai_batch_groups(
                settings,
                user_id="user-1",
                messages=[message],
                grouped_outputs=[output],
            )

        self.assertEqual(created, 1)
        self.assertEqual(used_ids, {"msg-apple-order"})
        self.assertEqual(mock_upsert.call_args.kwargs["ai_title"], "Apple order W123456789 delivered")
        self.assertEqual(mock_upsert.call_args.kwargs["ai_summary"], "")

    def test_ai_batch_rejects_cross_thread_collection_without_consuming_messages(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db", openai_model="gpt-test")
        penn = replace(
            sample_message("msg-penn"),
            gmail_thread_id="thread-penn",
            sender="State University <university-admissions@example.edu>",
            subject="Your State University application was updated",
            snippet="Your application and cost estimate were updated.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )
        emergent = replace(
            sample_message("msg-emergent"),
            gmail_thread_id="thread-emergent",
            sender="Emergent Ventures <hello@mercatus.org>",
            subject="Emergent Ventures update",
            snippet="Your application was reviewed by Emergent Ventures.",
            extracted_signals={"sender_domain": "mercatus.org", "normalized_subject": "application update"},
        )
        output = {
            "client_group_key": "application-updates",
            "group_kind": "collection",
            "canonical_entity": "Applications",
            "shared_object": "",
            "workflow_family": "application",
            "confidence": 0.88,
            "risk_level": "medium",
            "should_show_in_inbox": False,
            "should_show_in_dashboard": False,
            "strong_evidence": [],
            "per_message_evidence": {},
            "ai_title": "Application updates",
            "member_message_ids": ["msg-penn", "msg-emergent"],
        }

        with patch("app.services.mail_groups.upsert_mail_group") as mock_upsert, patch(
            "app.services.mail_groups.replace_group_members"
        ):
            created, visible_created, used_ids = _store_ai_batch_groups(
                settings,
                user_id="user-1",
                messages=[penn, emergent],
                grouped_outputs=[output],
            )

        self.assertEqual(created, 0)
        self.assertEqual(visible_created, 0)
        self.assertEqual(used_ids, set())
        mock_upsert.assert_not_called()

    def test_ai_batch_rejects_cross_thread_lifecycle_without_complete_evidence(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db", openai_model="gpt-test")
        opened = replace(
            sample_message("case-opened"),
            gmail_thread_id="thread-case-opened",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Service request 106400420 registered",
            snippet="Service request number 106400420 has been registered.",
            extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "106400420"},
        )
        updated = replace(
            sample_message("case-updated"),
            gmail_thread_id="thread-case-updated",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Service request 106400420 update",
            snippet="Service request number 106400420 is being reviewed.",
            extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "106400420"},
        )
        output = {
            "client_group_key": "northstar-case",
            "group_kind": "lifecycle",
            "canonical_entity": "Northstar Bank",
            "shared_object": "service request 106400420",
            "workflow_family": "support_case",
            "workflow_state": "waiting",
            "confidence": 0.97,
            "risk_level": "low",
            "should_show_in_inbox": True,
            "strong_evidence": ["service request 106400420"],
            "per_message_evidence": {"case-opened": "Registered the service request."},
            "ai_title": "Northstar service request 106400420 under review",
            "member_message_ids": ["case-opened", "case-updated"],
        }

        with patch("app.services.mail_groups.upsert_mail_group") as mock_upsert, patch(
            "app.services.mail_groups.replace_group_members"
        ):
            created, _visible_created, used_ids = _store_ai_batch_groups(
                settings,
                user_id="user-1",
                messages=[opened, updated],
                grouped_outputs=[output],
            )

        self.assertEqual(created, 0)
        self.assertEqual(used_ids, set())
        mock_upsert.assert_not_called()

    def test_ai_batch_stores_high_confidence_cross_thread_lifecycle_as_real_workflow(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db", openai_model="gpt-test")
        opened = replace(
            sample_message("case-opened"),
            gmail_thread_id="thread-case-opened",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Service request 106400420 registered",
            snippet="Service request number 106400420 has been registered.",
            extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "106400420"},
        )
        updated = replace(
            sample_message("case-updated"),
            gmail_thread_id="thread-case-updated",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Service request 106400420 update",
            snippet="Service request number 106400420 is being reviewed.",
            extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "106400420"},
        )
        output = {
            "client_group_key": "northstar-case",
            "group_kind": "lifecycle",
            "canonical_entity": "Northstar Bank",
            "shared_object": "service request 106400420",
            "workflow_family": "support_case",
            "workflow_state": "waiting",
            "confidence": 0.97,
            "risk_level": "low",
            "should_show_in_inbox": True,
            "should_show_in_dashboard": True,
            "strong_evidence": ["service request 106400420"],
            "per_message_evidence": {
                "case-opened": "Registered service request 106400420.",
                "case-updated": "Updated the same service request 106400420.",
            },
            "ai_title": "Northstar service request 106400420 under review",
            "member_message_ids": ["case-opened", "case-updated"],
        }

        with patch("app.services.mail_groups.upsert_mail_group", return_value=sample_group()) as mock_upsert, patch(
            "app.services.mail_groups.replace_group_members"
        ), patch("app.services.mail_groups._persist_message_ai_titles"):
            created, _visible_created, used_ids = _store_ai_batch_groups(
                settings,
                user_id="user-1",
                messages=[opened, updated],
                grouped_outputs=[output],
            )

        self.assertEqual(created, 1)
        self.assertEqual(used_ids, {"case-opened", "case-updated"})
        self.assertEqual(mock_upsert.call_args.kwargs["group_type"], "support_case")
        self.assertNotEqual(mock_upsert.call_args.kwargs["group_type"], "dashboard_bundle")

    def test_single_message_ai_group_prefers_message_title_when_group_title_has_bare_acronym_sender(self) -> None:
        message = replace(
            sample_message("psu-pin"),
            gmail_thread_id="thread-psu-pin",
            internal_date="2026-06-13T00:59:24+05:30",
            subject="Limited Services PIN",
            sender="academic-adviser@example.edu",
            snippet="International Student and Scholar Advising sent a limited services PIN.",
            ai_title="State University limited services PIN",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "limited services pin"},
        )
        ai_group = replace(
            sample_group(),
            id="group-psu-pin",
            group_key="gmail-thread:thread-psu-pin",
            ai_title="PSU advising sent your limited services PIN",
            ai_summary="You received a limited services PIN from advising.",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-psu-pin", [message])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-psu-pin": ai_group},
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
        self.assertEqual(rows[0].sender, "State University")
        self.assertEqual(rows[0].title, "State University limited services PIN")
        self.assertEqual(rows[0].ai_title, "State University limited services PIN")
        self.assertEqual(rows[0].children[0].ai_title, "State University limited services PIN")

    def test_mailbox_does_not_apply_ai_collection_title_to_excluded_thread(self) -> None:
        message = replace(
            sample_message("ibkr-openai-access"),
            gmail_thread_id="thread-ibkr-openai-access",
            internal_date="2026-06-03T15:55:40+00:00",
            sender="Interactive Brokers Client Services <brokerage-noreply@example.com>",
            subject="Access Granted to OpenAI",
            snippet="You have successfully granted access to a third party application named OpenAI.",
            extracted_signals={
                "sender_domain": "brokerage.example",
                "normalized_subject": "access granted to openai",
            },
            ai_title="IBKR third-party access granted",
        )
        broad_collection = replace(
            sample_group(),
            id="group-openai-security",
            group_key="ai:openai-security-collection",
            group_type="dashboard_bundle",
            membership_source="ai_batch",
            ai_title="OpenAI security notices",
            ai_summary="OpenAI sent two separate account/security-related notices.",
            classification={
                "workflow_family": "account_security",
                "source_message_ids": ["openai-security-update", "ibkr-openai-access"],
                "grouping_contract": {
                    "group_kind": "collection",
                    "should_show_in_inbox": False,
                    "excluded_ids": ["ibkr-openai-access"],
                },
            },
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-ibkr-openai-access", [message])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-ibkr-openai-access": broad_collection},
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
        self.assertEqual(rows[0].thread_id, "thread-ibkr-openai-access")
        self.assertIsNone(rows[0].ai_group_id)
        self.assertEqual(rows[0].title, "IBKR third-party access granted")
        self.assertEqual(
            rows[0].summary,
            "Interactive Brokers Client Services says third-party access was granted to OpenAI. Review it if you did not authorize this connection.",
        )
        self.assertEqual(rows[0].presentation_status, "ai_ready")

    def test_mailbox_renders_safe_multi_thread_ai_group_once(self) -> None:
        opened = replace(
            sample_message("wire-opened"),
            gmail_thread_id="thread-wire-opened",
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Wire status request opened",
            snippet="We received your wire status request.",
            extracted_signals={"sender_domain": "bank.example.com"},
            ai_title="Bank acknowledged the wire-status request",
        )
        confirmed = replace(
            sample_message("wire-confirmed"),
            gmail_thread_id="thread-wire-confirmed",
            internal_date="2026-06-04T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Wire processed",
            snippet="The wire has been processed.",
            extracted_signals={"sender_domain": "bank.example.com"},
            ai_title="Bank confirmed the wire was processed",
        )
        ai_group = replace(
            sample_group(),
            id="group-wire-lifecycle",
            group_key="ai:wire-lifecycle",
            group_type="financial_transfer",
            membership_source="ai_batch",
            ai_title="Bank wire transfer resolved",
            ai_summary="The bank acknowledged the wire-status request and later confirmed the transfer was processed.",
            action_needed=False,
            action_type="open",
            priority=80,
            classification={
                "workflow_family": "financial_transfer",
                "source_message_ids": ["wire-opened", "wire-confirmed"],
                "grouping_contract": {
                    "group_kind": "conversation",
                    "should_show_in_inbox": True,
                    "excluded_ids": [],
                },
            },
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-wire-confirmed", [confirmed]),
                    ("thread-wire-opened", [opened]),
                ],
                next_cursor=None,
                loaded_threads=2,
            ),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-wire-confirmed": ai_group, "thread-wire-opened": ai_group},
        ), patch(
            "app.services.mail_groups.list_messages_for_groups",
            return_value={"group-wire-lifecycle": [opened, confirmed]},
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "group-wire-lifecycle")
        self.assertEqual(rows[0].title, "Bank wire transfer resolved")
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["wire-opened", "wire-confirmed"])
        self.assertEqual(rows[0].grouping_metadata["source"], "ai_mail_group")
        self.assertEqual(rows[0].grouping_metadata["grouping_contract"]["group_kind"], "conversation")
        self.assertEqual(rows[0].grouping_metadata["workflow"]["family"], "financial_transfer")
        self.assertEqual(rows[0].grouping_metadata["conversation"]["thread_count"], 2)

    def test_mailbox_recomputes_stale_reply_action_from_noreply_ai_group(self) -> None:
        message = replace(
            sample_message("google-cloud-tls"),
            gmail_thread_id="thread-google-cloud-tls",
            label_ids=["INBOX", "IMPORTANT", "CATEGORY_UPDATES"],
            internal_date="2026-05-27T06:57:48+05:30",
            subject="[Action Advised] Ensure you trust all Google Trust Services Root CAs before Jun 15, 2026",
            sender="Google Cloud <CloudPlatform-noreply@google.com>",
            snippet="Most customers do not need to act, but if you use custom trust stores, you should review and update trust settings before June 15, 2026.",
            text_body="Most customers do not need to act, but if you use custom trust stores, you should review and update trust settings before June 15, 2026.",
            extracted_signals={"sender_domain": "google.com", "normalized_subject": "google cloud tls certificate update"},
        )
        ai_group = replace(
            sample_group(),
            id="group-google-cloud-tls",
            group_key="gmail-thread:thread-google-cloud-tls",
            group_type="account_security",
            membership_source="gmail_thread",
            ai_title="Google Cloud TLS certificate update notice",
            ai_summary="Google Cloud says custom trust store users should review and update trust settings before June 15, 2026.",
            action_needed=True,
            action_type="reply",
            priority=92,
            classification={
                "workflow_family": "account_security",
                "workflow_state": "needs_user_action",
                "requires_user_action": True,
                "source_message_ids": ["google-cloud-tls"],
            },
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-google-cloud-tls", [message])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-google-cloud-tls": ai_group},
        ), patch(
            "app.services.mail_groups.list_messages_for_groups",
            return_value={"group-google-cloud-tls": [message]},
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "thread-google-cloud-tls")
        self.assertEqual(rows[0].ai_group_id, "group-google-cloud-tls")
        self.assertTrue(rows[0].action_needed)
        self.assertEqual(rows[0].action_type, "review")
        self.assertEqual(rows[0].action_type_key, "review")

    def test_mailbox_dedupes_overlapping_ai_group_rows_by_source_messages(self) -> None:
        opened = replace(
            sample_message("wire-opened"),
            gmail_thread_id="thread-wire-opened",
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Wire status request opened",
            snippet="We received your wire status request.",
            extracted_signals={"sender_domain": "bank.example.com"},
            ai_title="Bank acknowledged the wire-status request",
        )
        confirmed = replace(
            sample_message("wire-confirmed"),
            gmail_thread_id="thread-wire-confirmed",
            internal_date="2026-06-04T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Wire processed",
            snippet="The wire has been processed.",
            extracted_signals={"sender_domain": "bank.example.com"},
            ai_title="Bank confirmed the wire was processed",
        )
        followup = replace(
            sample_message("wire-followup"),
            gmail_thread_id="thread-wire-followup",
            internal_date="2026-06-05T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Wire case closed",
            snippet="The wire case is now closed.",
            extracted_signals={"sender_domain": "bank.example.com"},
            ai_title="Bank closed the wire case",
        )
        small_group = replace(
            sample_group(),
            id="group-wire-small",
            group_key="ai:wire-small",
            group_type="financial_transfer",
            membership_source="ai_batch",
            ai_title="Bank wire transfer update",
            ai_summary="The bank updated the wire transfer.",
            priority=60,
            classification={
                "workflow_family": "financial_transfer",
                "source_message_ids": ["wire-opened", "wire-confirmed"],
                "grouping_contract": {"group_kind": "conversation", "should_show_in_inbox": True},
            },
        )
        large_group = replace(
            small_group,
            id="group-wire-large",
            group_key="ai:wire-large",
            ai_title="Bank wire transfer resolved",
            ai_summary="The bank acknowledged the wire, confirmed processing, and closed the case.",
            priority=90,
            classification={
                "workflow_family": "financial_transfer",
                "source_message_ids": ["wire-opened", "wire-confirmed", "wire-followup"],
                "grouping_contract": {"group_kind": "conversation", "should_show_in_inbox": True},
            },
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-wire-followup", [followup]),
                    ("thread-wire-confirmed", [confirmed]),
                    ("thread-wire-opened", [opened]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={
                "thread-wire-followup": large_group,
                "thread-wire-confirmed": large_group,
                "thread-wire-opened": small_group,
            },
        ), patch(
            "app.services.mail_groups.list_messages_for_groups",
            return_value={
                "group-wire-small": [opened, confirmed],
                "group-wire-large": [opened, confirmed, followup],
            },
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "group-wire-large")
        self.assertEqual(rows[0].title, "Bank wire transfer resolved")
        self.assertEqual([child.message_id for child in rows[0].children], ["wire-opened", "wire-confirmed", "wire-followup"])

    def test_mailbox_display_keeps_safe_newsletter_threads_separate_in_primary_inbox(self) -> None:
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
        self.assertEqual([row.thread_id for row in rows], ["thread-claude-2", "thread-claude-1", "thread-claude-3"])
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_mailbox_display_keeps_safe_provider_stream_with_sender_variants_separate_in_primary_inbox(self) -> None:
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
        self.assertEqual([row.thread_id for row in rows], ["thread-slashy-weekly", "thread-slashy-welcome"])
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_mailbox_display_does_not_cluster_billing_without_reference(self) -> None:
        invoice = replace(
            sample_message("billing-invoice"),
            gmail_thread_id="thread-billing-invoice",
            label_ids=["INBOX"],
            internal_date="2026-06-02T12:00:00+00:00",
            sender="Billing Team <billing@provider.example.com>",
            subject="Your June invoice is available",
            snippet="Invoice for USD 12 is now available.",
            extracted_signals={"sender_domain": "provider.example.com", "normalized_subject": "your june invoice is available"},
        )
        receipt = replace(
            sample_message("billing-receipt"),
            gmail_thread_id="thread-billing-receipt",
            label_ids=["INBOX"],
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Billing Team <billing@provider.example.com>",
            subject="Card payment receipt",
            snippet="Your card payment was received.",
            extracted_signals={"sender_domain": "provider.example.com", "normalized_subject": "card payment receipt"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-billing-receipt", [receipt]), ("thread-billing-invoice", [invoice])],
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
        self.assertEqual({row.thread_id for row in rows}, {"thread-billing-invoice", "thread-billing-receipt"})
        self.assertTrue(all(not row.thread_id.startswith("mailbox-cluster:") for row in rows))

    def test_mailbox_display_does_not_provider_stream_cluster_support_auto_acks(self) -> None:
        first = replace(
            sample_message("support-auto-1"),
            gmail_thread_id="thread-support-auto-1",
            label_ids=["INBOX", "CATEGORY_PERSONAL"],
            internal_date="2026-06-05T12:00:00+00:00",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            subject="<Auto> '1775807546' Unauthorized Credit Card Consent Request and Data Privacy Concern",
            snippet="Your complaint has been received and is under review.",
            extracted_signals={
                "sender_domain": "northstar.example",
                "normalized_subject": "auto unauthorized credit card consent request and data privacy concern",
            },
        )
        second = replace(
            sample_message("support-auto-2"),
            gmail_thread_id="thread-support-auto-2",
            label_ids=["INBOX", "CATEGORY_PERSONAL"],
            internal_date="2026-06-06T12:00:00+00:00",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            subject="<Auto> '1788458582' Unauthorized Credit Card Consent Request and Data Privacy Concern",
            snippet="A follow-up acknowledgement was sent for the complaint.",
            extracted_signals={
                "sender_domain": "northstar.example",
                "normalized_subject": "auto unauthorized credit card consent request and data privacy concern follow up",
            },
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-support-auto-2", [second]), ("thread-support-auto-1", [first])],
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
        self.assertEqual({row.thread_id for row in rows}, {"thread-support-auto-1", "thread-support-auto-2"})
        self.assertTrue(all(not row.thread_id.startswith("mailbox-cluster:") for row in rows))

    def test_mailbox_display_clusters_exact_reference_without_topic_words(self) -> None:
        opened = replace(
            sample_message("case-opened"),
            gmail_thread_id="thread-case-opened",
            label_ids=["INBOX"],
            internal_date="2026-06-02T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="We registered your query",
            ai_title="Bank registered service request 106400420",
            snippet="The request is registered and our team is working on it.",
            extracted_signals={"sender_domain": "bank.example.com", "ticket_id": "106400420"},
        )
        updated = replace(
            sample_message("case-updated"),
            gmail_thread_id="thread-case-updated",
            label_ids=["INBOX"],
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Update on your query",
            ai_title="Bank updated service request 106400420",
            snippet="Our support team has updated the request.",
            extracted_signals={"sender_domain": "bank.example.com", "ticket_id": "106400420"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-case-updated", [updated]), ("thread-case-opened", [opened])],
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
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].thread_id.startswith("mailbox-cluster:"))
        self.assertEqual(rows[0].title, "Bank Support service request 106400420 updated")
        self.assertEqual(
            rows[0].summary,
            "Service request 106400420: 2 Bank Support updates; latest: Bank updated service request 106400420.",
        )
        self.assertEqual(rows[0].grouping_metadata["reference"]["value"], "106400420")
        self.assertEqual(rows[0].grouping_metadata["reference"]["direct_message_count"], 2)
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["case-opened", "case-updated"])

    def test_mailbox_display_clusters_dispute_lifecycle_by_body_reference(self) -> None:
        acknowledged = replace(
            sample_message("dispute-ack"),
            gmail_thread_id="thread-dispute-ack",
            label_ids=["INBOX"],
            internal_date="2026-05-20T21:34:21+05:30",
            sender="TransUnion <info@cibildisputesalert.transunion.com>",
            subject="Dispute Acknowledgement -P20052026334071",
            snippet="This is an acknowledgement of dispute you raised for your CIBIL Score and Report.",
            text_body="Dispute ID P20052026334071 Submitted on 20-May-2026 Next Update On 27-May-2026.",
            extracted_signals={"sender_domain": "cibildisputesalert.transunion.com", "normalized_subject": "dispute acknowledgement p20052026334071"},
        )
        updated = replace(
            sample_message("dispute-update"),
            gmail_thread_id="thread-dispute-update",
            label_ids=["INBOX"],
            internal_date="2026-05-27T02:50:20+05:30",
            sender="TransUnion <info@cibildisputesalert.transunion.com>",
            subject="Dispute Status Update -P20052026334071",
            ai_title="TransUnion updated dispute P20052026334071",
            snippet="We have an update on the dispute you submitted on 20-May-2026.",
            text_body="Dispute ID P20052026334071 Status Open Dispute Resolved 5/6 Expected Resolution Date 19-Jun-2026.",
            extracted_signals={"sender_domain": "cibildisputesalert.transunion.com", "normalized_subject": "dispute status update p20052026334071"},
        )
        interim = replace(
            sample_message("dispute-interim"),
            gmail_thread_id="thread-dispute-interim",
            label_ids=["INBOX"],
            internal_date="2026-05-27T09:15:01+05:30",
            sender="TransUnion <info@cibildisputesalert.transunion.com>",
            subject="Dispute Interim Update -P20052026334071",
            ai_title="TransUnion interim dispute update",
            snippet="Here is a list of disputes which are open under review.",
            text_body="Here is a list of disputes which are open under review.",
            extracted_signals={"sender_domain": "cibildisputesalert.transunion.com", "normalized_subject": "dispute interim update p20052026334071"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-dispute-interim", [interim]),
                    ("thread-dispute-update", [updated]),
                    ("thread-dispute-ack", [acknowledged]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={}), patch(
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
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].thread_id.startswith("mailbox-cluster:"))
        self.assertEqual(rows[0].title, "TransUnion dispute P20052026334071 under review")
        self.assertEqual(
            rows[0].summary,
            "Dispute P20052026334071: 3 TransUnion updates; latest: Interim dispute update.",
        )
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "dispute_id")
        self.assertEqual(rows[0].grouping_metadata["reference"]["direct_message_count"], 3)
        self.assertEqual([child.message_id for child in rows[0].children], ["dispute-ack", "dispute-update", "dispute-interim"])

    def test_mailbox_display_bridges_no_ticket_auto_ack_into_exact_reference_cluster(self) -> None:
        auto_ack = replace(
            sample_message("case-auto-ack"),
            gmail_thread_id="thread-case-auto-ack",
            label_ids=["INBOX", "CATEGORY_PERSONAL"],
            internal_date="2026-06-01T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="<Auto> Privacy complaint received",
            snippet="Your complaint has been received and is under review.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "auto privacy complaint received",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )
        registered = replace(
            sample_message("case-registered"),
            gmail_thread_id="thread-case-registered",
            label_ids=["INBOX"],
            internal_date="2026-06-02T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="We registered your complaint",
            snippet="The unique reference number is 106756996.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "we registered your complaint",
                "ticket_id": "106756996",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )
        updated = replace(
            sample_message("case-updated"),
            gmail_thread_id="thread-case-updated",
            label_ids=["INBOX"],
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Update on your complaint",
            snippet="We have updated service request 106756996.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "update on your complaint",
                "ticket_id": "106756996",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 3, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-case-updated", [updated]),
                    ("thread-case-registered", [registered]),
                    ("thread-case-auto-ack", [auto_ack]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={}), patch(
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
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].thread_id.startswith("mailbox-cluster:"))
        self.assertEqual(rows[0].title, "Bank Support service request 106756996 updated")
        self.assertEqual(rows[0].message_count, 3)
        self.assertEqual([child.message_id for child in rows[0].children], ["case-auto-ack", "case-registered", "case-updated"])

    def test_mailbox_display_does_not_bridge_conflicting_visible_reference_into_exact_cluster(self) -> None:
        conflicting_auto_ack = replace(
            sample_message("case-auto-ack"),
            gmail_thread_id="thread-case-auto-ack",
            label_ids=["INBOX", "CATEGORY_PERSONAL"],
            internal_date="2026-06-01T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="<Auto> '1772351599' Privacy complaint received",
            snippet="Your complaint reference 1772351599 has been received and is under review.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "auto 1772351599 privacy complaint received",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )
        registered = replace(
            sample_message("case-registered"),
            gmail_thread_id="thread-case-registered",
            label_ids=["INBOX"],
            internal_date="2026-06-02T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="We registered your complaint",
            snippet="The unique reference number is 106756996.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "we registered your complaint",
                "ticket_id": "106756996",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )
        updated = replace(
            sample_message("case-updated"),
            gmail_thread_id="thread-case-updated",
            label_ids=["INBOX"],
            internal_date="2026-06-03T12:00:00+00:00",
            sender="Bank Support <support@bank.example.com>",
            subject="Update on your complaint",
            snippet="We have updated service request 106756996.",
            extracted_signals={
                "sender_domain": "bank.example.com",
                "normalized_subject": "update on your complaint",
                "ticket_id": "106756996",
                "references": "<root@example.com> <bridge@example.com>",
            },
            headers={"references": "<root@example.com> <bridge@example.com>"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 3, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-case-updated", [updated]),
                    ("thread-case-registered", [registered]),
                    ("thread-case-auto-ack", [conflicting_auto_ack]),
                ],
                next_cursor=None,
                loaded_threads=3,
            ),
        ), patch("app.services.mail_groups.list_visible_groups_for_gmail_threads", return_value={}), patch(
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
        clustered_rows = [row for row in rows if row.thread_id.startswith("mailbox-cluster:")]
        raw_rows = [row for row in rows if not row.thread_id.startswith("mailbox-cluster:")]
        self.assertEqual(len(clustered_rows), 1)
        self.assertEqual(len(raw_rows), 1)
        self.assertEqual(clustered_rows[0].title, "Bank Support service request 106756996 updated")
        self.assertEqual(clustered_rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in clustered_rows[0].children], ["case-registered", "case-updated"])
        self.assertEqual(raw_rows[0].thread_id, "thread-case-auto-ack")

    def test_mailbox_row_uses_only_messages_matching_requested_label(self) -> None:
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
        self.assertEqual(inbox_row.message_count, 1)
        self.assertEqual(inbox_row.latest_subject, "Inbox copy")
        self.assertEqual(inbox_row.latest_sender, "Sender <sender@example.com>")
        self.assertEqual(inbox_row.sender, "Sender")
        self.assertEqual(inbox_row.label_ids, ["INBOX", "UNREAD"])
        self.assertEqual(inbox_row.participants, ["Sender"])
        self.assertEqual([child.message_id for child in inbox_row.children], ["inbox-1"])
        self.assertEqual(inbox_row.children[0].sender, "Sender")
        self.assertEqual(inbox_row.children[0].label_ids, ["INBOX", "UNREAD"])
        self.assertEqual(sent_row.message_count, 1)
        self.assertEqual(sent_row.latest_subject, "Sent reply")
        self.assertEqual(sent_row.latest_sender, "Me <me@example.com>")
        self.assertEqual(sent_row.sender, "Recipient Person")
        self.assertEqual(sent_row.label_ids, ["SENT"])
        self.assertEqual(sent_row.participants, ["Recipient Person", "Second Person"])
        self.assertEqual([child.message_id for child in sent_row.children], ["sent-1"])
        self.assertEqual(sent_row.children[0].sender, "Recipient Person")
        self.assertEqual(sent_row.children[0].label_ids, ["SENT"])

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

    def test_mailbox_renders_northstar_workflow_ai_group_across_gmail_threads(self) -> None:
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
            canonical_entity="Hdfcbank",
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "group-northstar")
        self.assertEqual(rows[0].entity_id, "visible-northstar")
        self.assertEqual(rows[0].ai_group_id, "group-northstar")
        self.assertEqual(rows[0].sender, "Northstar Bank")
        self.assertEqual(rows[0].title, "Northstar remittance support case")
        self.assertEqual(rows[0].summary, "Northstar acknowledged and updated the wire transfer inquiry.")
        self.assertEqual(rows[0].latest_subject, "Update on your Northstar wire transfer inquiry")
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["northstar-ack", "northstar-update"])
        self.assertEqual([child.gmail_thread_id for child in rows[0].children], ["thread-northstar-ack", "thread-northstar-update"])

    def test_visible_group_service_channel_canonical_uses_provider_sender(self) -> None:
        first = replace(
            sample_message("northstar-grievance-ack"),
            gmail_thread_id="thread-northstar-grievance-ack",
            label_ids=["INBOX"],
            internal_date="2026-05-30T12:00:00+00:00",
            subject="Northstar Bank acknowledged your grievance",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            ai_title="Northstar grievance acknowledged",
        )
        second = replace(
            sample_message("northstar-grievance-update"),
            gmail_thread_id="thread-northstar-grievance-update",
            label_ids=["INBOX"],
            internal_date="2026-06-05T13:00:00+00:00",
            subject="Update on your Northstar grievance",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            ai_title="Northstar grievance updated",
        )
        visible_group = replace(
            sample_visible_group(),
            id="visible-northstar-grievance",
            source_group_id="group-northstar-grievance",
            title="Northstar Bank grievance case 105715521",
            summary="Northstar Bank replied on the grievance case.",
            canonical_entity="Grievance Redressalcc",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-northstar-grievance-update", [second]), ("thread-northstar-grievance-ack", [first])], next_cursor=None, loaded_threads=2),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-northstar-grievance-update": visible_group, "thread-northstar-grievance-ack": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_messages_for_visible_groups",
            return_value={"visible-northstar-grievance": [first, second]},
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sender, "Northstar Bank")
        self.assertEqual(rows[0].title, "Northstar Bank grievance case 105715521")
        self.assertEqual([child.sender for child in rows[0].children], ["Northstar Bank", "Northstar Bank"])

    def test_visible_group_acronym_canonical_uses_repeated_ai_organization_title(self) -> None:
        first = replace(
            sample_message("psu-application-1"),
            gmail_thread_id="thread-psu-application-1",
            label_ids=["INBOX"],
            internal_date="2026-05-29T09:31:08+05:30",
            subject="Application Update",
            sender="university-admissions@example.edu",
            ai_title="State University application update",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )
        second = replace(
            sample_message("psu-application-2"),
            gmail_thread_id="thread-psu-application-2",
            label_ids=["INBOX"],
            internal_date="2026-06-10T09:32:31+05:30",
            subject="Application Update",
            sender="university-admissions@example.edu",
            ai_title="State University application update",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )
        visible_group = replace(
            sample_visible_group(),
            id="visible-psu-application",
            source_group_id="group-psu-application",
            title="State University application update",
            summary="State University sent multiple application update emails.",
            canonical_entity="PSU",
            workflow_type="application",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-psu-application-2", [second]), ("thread-psu-application-1", [first])],
                next_cursor=None,
                loaded_threads=2,
            ),
        ), patch(
            "app.services.mail_groups.list_visible_groups_for_gmail_threads",
            return_value={"thread-psu-application-2": visible_group, "thread-psu-application-1": visible_group},
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={},
        ), patch(
            "app.services.mail_groups.list_messages_for_visible_groups",
            return_value={"visible-psu-application": [first, second]},
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sender, "State University")
        self.assertEqual(rows[0].title, "State University application update")
        self.assertEqual([child.sender for child in rows[0].children], ["State University", "State University"])

    def test_visible_group_sender_falls_back_to_inbound_sender_when_projection_entity_is_user(self) -> None:
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
            return_value=MailboxThreadPage(threads=[("thread-psu-reply", [reply]), ("thread-psu-sent", [sent])], next_cursor=None, loaded_threads=2),
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
        self.assertEqual(rows[0].sender, "State University")
        self.assertEqual(rows[0].title, "State University reconsideration request")
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
        self.assertEqual(rows[0].sender, "State University")
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

        self.assertEqual(enrichment["ai_title"], "PSU application rejection")

    def test_attention_title_does_not_turn_application_arrival_into_delivery_status(self) -> None:
        message = replace(
            sample_message("psu-i20"),
            subject="I-20 request needed",
            sender="State University <university-admissions@example.edu>",
            snippet="Submit your I-20 request before your campus arrival.",
            text_body="You have been admitted. Submit your I-20 request before your campus arrival.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "i-20 request needed"},
        )

        enrichment = attention_enrichment_payload(
            messages=[message],
            group_key="gmail-thread:psu-i20",
            ai_output={
                "workflow_family": "application",
                "workflow_state": "needs_user_action",
                "requires_user_action": True,
                "terminal_state": False,
                "urgency": "medium",
                "confidence": 0.94,
                "reason": "The student must submit an I-20 request.",
                "ai_title": "State University I-20 request needed arriving",
            },
        )

        self.assertEqual(enrichment["ai_title"], "State University I-20 request needed")

    def test_attention_title_replaces_pending_response_with_resolved(self) -> None:
        message = replace(
            sample_message("northstar-grievance"),
            subject="Northstar grievance 105715521 resolved",
            sender="Northstar Bank <support@northstarbank.example>",
            snippet="Your grievance 105715521 has been resolved and closed.",
            text_body="Your grievance 105715521 has been resolved and closed.",
            extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "105715521"},
        )

        enrichment = attention_enrichment_payload(
            messages=[message],
            group_key="gmail-thread:northstar-grievance",
            ai_output={
                "workflow_family": "support_case",
                "workflow_state": "resolved",
                "requires_user_action": False,
                "terminal_state": True,
                "urgency": "low",
                "confidence": 0.96,
                "reason": "Northstar marked the grievance resolved.",
                "ai_title": "Northstar grievance 105715521 pending response",
            },
        )

        self.assertEqual(enrichment["ai_title"], "Northstar grievance 105715521 resolved")

    def test_attention_title_does_not_stack_confirmed_onto_ready_or_successful_titles(self) -> None:
        statement = replace(
            sample_message("ibkr-statement"),
            subject="Daily activity statement ready",
            sender="Interactive Brokers <brokerage-statements@example.com>",
            snippet="Your daily activity statement is ready and confirmation is available.",
            text_body="Your daily activity statement is ready and confirmation is available.",
            extracted_signals={"sender_domain": "brokerage.example", "normalized_subject": "daily activity statement ready"},
        )
        order = replace(
            sample_message("starbucks-order"),
            subject="Order successful",
            sender="Starbucks India <orders@starbucks.in>",
            snippet="Your order was successful and confirmed.",
            text_body="Your order was successful and confirmed.",
            extracted_signals={"sender_domain": "starbucks.in", "normalized_subject": "order successful"},
        )

        statement_enrichment = attention_enrichment_payload(
            messages=[statement],
            group_key="gmail-thread:ibkr-statement",
            ai_output={
                "workflow_family": "billing",
                "workflow_state": "informational",
                "requires_user_action": False,
                "terminal_state": False,
                "urgency": "low",
                "confidence": 0.95,
                "reason": "The statement is ready.",
                "ai_title": "IBKR daily activity statement ready",
            },
        )
        order_enrichment = attention_enrichment_payload(
            messages=[order],
            group_key="gmail-thread:starbucks-order",
            ai_output={
                "workflow_family": "logistics",
                "workflow_state": "resolved",
                "requires_user_action": False,
                "terminal_state": True,
                "urgency": "low",
                "confidence": 0.95,
                "reason": "The order was successful and confirmed.",
                "ai_title": "Starbucks India order successful",
            },
        )

        self.assertEqual(statement_enrichment["ai_title"], "IBKR daily activity statement ready")
        self.assertEqual(order_enrichment["ai_title"], "Starbucks India order successful")

    def test_attention_title_does_not_stack_action_required_on_needed_title(self) -> None:
        message = replace(
            sample_message("github-sponsors"),
            subject="Finish setting up GitHub Sponsors",
            sender="GitHub <github-noreply@example.com>",
            snippet="Finish setting up your GitHub Sponsors profile.",
            text_body="You need to finish setting up your GitHub Sponsors profile.",
            extracted_signals={"sender_domain": "github.com", "normalized_subject": "finish setting up github sponsors"},
        )

        enrichment = attention_enrichment_payload(
            messages=[message],
            group_key="gmail-thread:github-sponsors",
            ai_output={
                "workflow_family": "account_security",
                "workflow_state": "needs_user_action",
                "requires_user_action": True,
                "terminal_state": False,
                "urgency": "medium",
                "confidence": 0.93,
                "reason": "The user needs to finish profile setup.",
                "ai_title": "GitHub Sponsors setup needed",
            },
        )

        self.assertEqual(enrichment["ai_title"], "GitHub Sponsors setup needed")

    def test_attention_title_does_not_append_reply_to_answered_reconsideration(self) -> None:
        message = replace(
            sample_message("psu-reconsideration"),
            subject="Re: Reconsideration request",
            sender="State University Campus <admissions@behrend.university.example>",
            snippet="We are replying to your reconsideration request.",
            text_body="We are replying to your reconsideration request with our answer.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "reconsideration request"},
        )

        enrichment = attention_enrichment_payload(
            messages=[message],
            group_key="gmail-thread:psu-reconsideration",
            ai_output={
                "workflow_family": "application",
                "workflow_state": "informational",
                "requires_user_action": False,
                "terminal_state": False,
                "urgency": "low",
                "confidence": 0.93,
                "reason": "State University replied to the reconsideration request.",
                "ai_title": "State University Campus reconsideration request answered",
            },
        )

        self.assertEqual(enrichment["ai_title"], "State University Campus reconsideration request answered")

    def test_attention_action_type_ignores_noreply_sender_for_review_notice(self) -> None:
        message = replace(
            sample_message("google-cloud-tls"),
            subject="Action may be required: Google Cloud TLS certificate update",
            sender="Google Cloud <CloudPlatform-noreply@google.com>",
            snippet="Most customers do not need to act, but if you use custom trust stores, you should review and update trust settings before June 15, 2026.",
            text_body="Most customers do not need to act, but if you use custom trust stores, you should review and update trust settings before June 15, 2026.",
            extracted_signals={"sender_domain": "google.com", "normalized_subject": "google cloud tls certificate update"},
        )

        enrichment = attention_enrichment_payload(messages=[message], group_key="gmail-thread:google-cloud-tls")

        self.assertTrue(enrichment["action_needed"])
        self.assertEqual(enrichment["action_type"], "review")

    def test_attention_action_type_keeps_real_reply_request(self) -> None:
        message = replace(
            sample_message("support-reply"),
            subject="Additional details needed",
            sender="Provider Support <no-reply@provider.example.com>",
            snippet="Please reply with the requested documents so we can continue.",
            text_body="Please reply with the requested documents so we can continue.",
            extracted_signals={"sender_domain": "provider.example.com", "normalized_subject": "additional details needed"},
        )

        enrichment = attention_enrichment_payload(messages=[message], group_key="gmail-thread:support-reply")

        self.assertTrue(enrichment["action_needed"])
        self.assertEqual(enrichment["action_type"], "reply")

    def test_generic_ai_message_title_is_replaced_before_persistence(self) -> None:
        message = replace(
            sample_message("psu-app"),
            subject="Application Update",
            sender="State University <university-admissions@example.edu>",
            snippet="We regret to inform you that your application was not selected.",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "application update"},
        )

        titles = _message_title_map([{"message_id": "psu-app", "ai_title": "Application Update"}], [message])

        self.assertEqual(titles, {"psu-app": "PSU application rejection"})

    def test_provider_update_ai_message_title_is_replaced_before_persistence(self) -> None:
        message = replace(
            sample_message("apple-order"),
            subject="Apple order delivered",
            sender="Apple <orders@fruitco.example>",
            snippet="Your Apple order W123456789 has been delivered.",
            text_body="Your Apple order W123456789 has been delivered.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "apple order delivered",
            },
        )

        titles = _message_title_map([{"message_id": "apple-order", "ai_title": "Apple updates"}], [message])

        self.assertEqual(titles, {"apple-order": "Apple order delivered"})

    def test_weak_single_message_ai_title_does_not_leak_into_inbox_row(self) -> None:
        message = replace(
            sample_message("apple-order"),
            gmail_thread_id="thread-apple-order",
            subject="Apple order delivered",
            sender="Apple <orders@fruitco.example>",
            ai_title="Apple updates",
            snippet="Your Apple order W123456789 has been delivered.",
            text_body="Your Apple order W123456789 has been delivered.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "apple order delivered",
            },
        )

        row = _gmail_row_from_canonical_thread("thread-apple-order", [message], "inbox", None)

        self.assertEqual(row.title, "Apple order delivered")
        self.assertEqual(row.ai_title, "Apple order delivered")
        self.assertEqual(row.presentation_status, "ai_ready")

    def test_vague_ai_group_title_falls_back_to_message_specific_title(self) -> None:
        message = replace(
            sample_message("apple-order"),
            gmail_thread_id="thread-apple-order",
            subject="Apple order delivered",
            sender="Apple <orders@fruitco.example>",
            ai_title="Apple order delivered",
            snippet="Your Apple order W123456789 has been delivered.",
            text_body="Your Apple order W123456789 has been delivered.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "apple order delivered",
            },
        )
        group = replace(
            sample_group(),
            ai_title="Apple updates",
            ai_summary="Apple sent a delivery update for order W123456789.",
        )

        row = _gmail_row_from_canonical_thread("thread-apple-order", [message], "inbox", group)

        self.assertEqual(row.title, "Apple order delivered")
        self.assertIsNone(row.ai_title)
        self.assertEqual(row.presentation_status, "fallback")

    def test_unsupported_ai_group_title_falls_back_to_message_specific_title(self) -> None:
        message = replace(
            sample_message("apple-order"),
            gmail_thread_id="thread-apple-order",
            subject="Apple order delivered",
            sender="Apple <orders@fruitco.example>",
            ai_title="Apple order delivered",
            snippet="Your Apple order W123456789 has been delivered.",
            text_body="Your Apple order W123456789 has been delivered.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "apple order delivered",
            },
        )
        group = replace(
            sample_group(),
            ai_title="Bank transfer acknowledged",
            ai_summary="A bank acknowledged a transfer.",
        )

        row = _gmail_row_from_canonical_thread("thread-apple-order", [message], "inbox", group)

        self.assertEqual(row.title, "Apple order delivered")
        self.assertIsNone(row.ai_title)
        self.assertEqual(row.presentation_status, "fallback")

    def test_cross_thread_unsupported_ai_group_title_does_not_render_as_ai_ready(self) -> None:
        message = replace(
            sample_message("apple-order"),
            gmail_thread_id="thread-apple-order",
            subject="Apple order delivered",
            sender="Apple <orders@fruitco.example>",
            ai_title="Apple order delivered",
            snippet="Your Apple order W123456789 has been delivered.",
            text_body="Your Apple order W123456789 has been delivered.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "apple order delivered",
            },
        )
        group = replace(
            sample_group(),
            id="group-wrong-title",
            group_key="ai:apple-order",
            membership_source="ai_batch",
            ai_title="Bank transfer acknowledged",
            ai_summary="",
        )

        row = _gmail_row_from_ai_group(group, [message], "inbox")

        self.assertEqual(row.title, "Apple order delivered")
        self.assertIsNone(row.ai_title)
        self.assertEqual(row.presentation_status, "fallback")

    def test_generic_ai_group_title_is_rewritten_with_delivery_state(self) -> None:
        message = replace(
            sample_message("apple-order"),
            subject="Your Apple order is out for delivery",
            sender="Apple <orders@fruitco.example>",
            snippet="Your Apple order W123456789 is out for delivery and arriving today.",
            text_body="Your Apple order W123456789 is out for delivery and arriving today.",
            extracted_signals={
                "sender_domain": "apple.com",
                "order_id": "W123456789",
                "normalized_subject": "your apple order is out for delivery",
            },
        )

        enrichment = attention_enrichment_payload(
            messages=[message],
            group_key="gmail-thread:apple-order",
            ai_output={
                "workflow_family": "logistics",
                "workflow_state": "informational",
                "requires_user_action": False,
                "terminal_state": False,
                "urgency": "low",
                "confidence": 0.96,
                "reason": "The order is currently out for delivery.",
                "ai_title": "Apple order status",
                "ai_summary": "Your Apple order W123456789 is out for delivery and arriving today.",
            },
        )

        self.assertEqual(enrichment["ai_title"], "Apple order out for delivery")

    def test_ai_group_title_replaces_status_with_resolved_outcome(self) -> None:
        opened = replace(
            sample_message("northstar-opened"),
            gmail_thread_id="thread-northstar-opened",
            sender="Northstar Bank <support@northstarbank.example>",
            subject="Request for Status of International Wire Sent",
            snippet="Northstar acknowledged the international wire status request.",
            text_body="Northstar acknowledged the international wire status request.",
            extracted_signals={"sender_domain": "northstarbank.example"},
        )
        processed = replace(
            sample_message("northstar-processed"),
            gmail_thread_id="thread-northstar-processed",
            sender="Northstar Bank <tradequalityunit@northstarbank.example>",
            subject="Outward remittance processed",
            snippet="The outward remittance was processed.",
            text_body="The outward remittance was processed.",
            extracted_signals={"sender_domain": "northstarbank.example"},
        )

        enrichment = attention_enrichment_payload(
            messages=[opened, processed],
            group_key="ai-lifecycle:northstar-wire",
            ai_output={
                "workflow_family": "financial_transfer",
                "workflow_state": "resolved",
                "requires_user_action": False,
                "terminal_state": True,
                "urgency": "low",
                "confidence": 0.97,
                "reason": "Northstar confirmed the remittance was processed.",
                "ai_title": "Northstar international wire remittance status",
                "ai_summary": "Northstar acknowledged the wire request and later confirmed the outward remittance was processed.",
            },
        )

        self.assertEqual(enrichment["ai_title"], "Northstar international wire remittance processed")

    def test_workflow_cluster_key_requires_reference_not_provider_window(self) -> None:
        self.assertIsNone(
            workflow_cluster_key(
                "financial_transfer",
                {"workflow_family": "financial_transfer", "facts": {"provider": "bank"}},
                "2026-06-11T10:00:00+00:00",
            )
        )
        self.assertEqual(
            workflow_cluster_key(
                "billing",
                {"workflow_family": "billing", "facts": {"provider": "provider", "reference_id": "invoice_id:INV10001"}},
                "2026-06-11T10:00:00+00:00",
            ),
            "ref:provider:invoice-id-inv10001",
        )

    def test_mailbox_keeps_related_transport_threads_separate_without_validated_projection(self) -> None:
        booking = replace(
            sample_message("transit-booking"),
            gmail_thread_id="thread-transit-booking",
            internal_date="2026-06-05T07:00:00+00:00",
            subject="Your booking",
            sender="TransitCo <support@transitco.example.com>",
            snippet="Your bus booking is confirmed.",
            extracted_signals={"sender_domain": "transitco.example.com", "normalized_subject": "your booking"},
        )
        modified = replace(
            sample_message("transit-modified"),
            gmail_thread_id="thread-transit-modified",
            internal_date="2026-06-05T08:00:00+00:00",
            subject="Your trip was modified successfully",
            sender="TransitCo <support@transitco.example.com>",
            snippet="Your ride time changed.",
            extracted_signals={"sender_domain": "transitco.example.com", "normalized_subject": "your trip was modified successfully"},
        )
        cancelled = replace(
            sample_message("transit-cancelled"),
            gmail_thread_id="thread-transit-cancelled",
            internal_date="2026-06-05T09:00:00+00:00",
            subject="Your trip on 05 Jun was cancelled",
            sender="TransitCo <support@transitco.example.com>",
            snippet="Your ride was cancelled.",
            extracted_signals={"sender_domain": "transitco.example.com", "normalized_subject": "your trip was cancelled"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[
                    ("thread-transit-cancelled", [cancelled]),
                    ("thread-transit-modified", [modified]),
                    ("thread-transit-booking", [booking]),
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
        self.assertEqual([row.thread_id for row in rows], ["thread-transit-cancelled", "thread-transit-modified", "thread-transit-booking"])
        self.assertEqual([row.ai_group_id for row in rows], [None, None, None])

    def test_mailbox_display_keeps_safe_job_alert_streams_separate_in_primary_inbox(self) -> None:
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
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_mailbox_display_keeps_action_required_job_alerts_separate(self) -> None:
        first = replace(
            sample_message("job-alert-action-1"),
            gmail_thread_id="thread-job-alert-action-1",
            internal_date="2026-05-28T08:00:00+00:00",
            subject="New job alert: AI automation engineer",
            sender="TalentBoard Alerts <alerts@jobs.example.com>",
            snippet="This job matches your alert settings. Please reply to confirm your availability.",
            extracted_signals={"sender_domain": "jobs.example.com", "normalized_subject": "new job alert ai automation engineer"},
        )
        second = replace(
            sample_message("job-alert-action-2"),
            gmail_thread_id="thread-job-alert-action-2",
            internal_date="2026-05-28T09:00:00+00:00",
            subject="New job alert: AI consultant",
            sender="TalentBoard Alerts <alerts@jobs.example.com>",
            snippet="This job matches your alert settings. Please reply to confirm your availability.",
            extracted_signals={"sender_domain": "jobs.example.com", "normalized_subject": "new job alert ai consultant"},
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(
                threads=[("thread-job-alert-action-2", [second]), ("thread-job-alert-action-1", [first])],
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
        self.assertEqual([row.thread_id for row in rows], ["thread-job-alert-action-2", "thread-job-alert-action-1"])
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

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
        self.assertIn("mail_group_enrich", kinds)
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"pending": 0, "ready": 4})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch(
        "app.services.mail_groups.get_import_state",
        return_value=SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            hot_window_completed_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="first-run-cursor",
        ),
    )
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_resumes_unfinished_hot_window_on_critical_queue(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        ensure_background_import_work(self.settings, user_id="user-1")

        backfill_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "gmail_backfill")
        self.assertEqual(backfill_call.kwargs["queue"], "critical")
        self.assertEqual(backfill_call.kwargs["priority"], 90)
        self.assertEqual(backfill_call.kwargs["payload"]["batch_size"], FIRST_RUN_HOT_WINDOW_BATCH_SIZE)
        mock_projection.assert_not_called()

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"pending": 0, "ready": 0})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch(
        "app.services.mail_groups.get_import_state",
        return_value=SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at=None,
            hot_window_completed_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="first-run-cursor",
        ),
    )
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_waits_for_hot_window_before_first_run_grouping(
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
        self.assertIn("gmail_backfill", kinds)
        self.assertNotIn("first_run_ai_grouping", kinds)
        mock_projection.assert_not_called()

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"pending": 0, "ready": 0})
    @patch("app.services.mail_groups.count_active_jobs", return_value=1)
    @patch(
        "app.services.mail_groups.get_import_state",
        return_value=SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at=None,
            hot_window_completed_at="2026-05-15T12:01:00+00:00",
            full_backfill_completed_at=None,
            full_backfill_cursor="full-cursor",
        ),
    )
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_enqueues_first_run_grouping_after_hot_window_complete(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        ensure_background_import_work(self.settings, user_id="user-1")

        grouping_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "first_run_ai_grouping")
        self.assertEqual(grouping_call.kwargs["queue"], "critical")
        self.assertEqual(grouping_call.kwargs["priority"], 98)
        self.assertEqual(grouping_call.kwargs["dedupe_key"], "first-run-ai-grouping-hot-window:user-1")
        self.assertEqual(grouping_call.kwargs["payload"]["source"], "hot_window_recovery")
        mock_projection.assert_not_called()

    @patch("app.services.mail_groups.enqueue_projection_refresh")
    @patch("app.services.mail_groups.enqueue_job")
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"pending": 7, "ready": 4})
    @patch("app.services.mail_groups.count_active_jobs", return_value=0)
    @patch(
        "app.services.mail_groups.get_import_state",
        return_value=SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            hot_window_completed_at="2026-05-15T12:01:00+00:00",
            full_backfill_completed_at=None,
            full_backfill_cursor="full-cursor",
        ),
    )
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_recovery_keeps_hot_window_enrichment_on_critical_queue(
        self,
        _mock_can_write: Mock,
        _mock_state: Mock,
        _mock_active_jobs: Mock,
        _mock_counts: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        ensure_background_import_work(self.settings, user_id="user-1")

        enrichment_call = next(call for call in mock_enqueue.call_args_list if call.kwargs.get("kind") == "mail_group_enrich")
        self.assertEqual(enrichment_call.kwargs["queue"], "critical")
        self.assertEqual(enrichment_call.kwargs["priority"], 96)
        self.assertEqual(enrichment_call.kwargs["dedupe_key"], "first-run-mail-group-enrich:user-1")
        self.assertEqual(enrichment_call.kwargs["payload"]["source"], "first_run_hot_window")
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

    @patch("app.workers.main.enqueue_projection_refresh")
    @patch("app.workers.main.enqueue_job")
    @patch("app.workers.main.enrich_pending_mail_groups", return_value=MAIL_GROUP_ENRICH_BATCH_SIZE)
    def test_worker_keeps_first_run_enrichment_continuations_on_critical_queue(
        self,
        _mock_enrich: Mock,
        mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        job = SimpleNamespace(
            kind="mail_group_enrich",
            user_id="user-1",
            payload_version=1,
            payload={"user_id": "user-1", "source": "first_run_hot_window"},
        )

        _run_job(self.settings, job)

        continuation = mock_enqueue.call_args
        self.assertEqual(continuation.kwargs["kind"], "mail_group_enrich")
        self.assertEqual(continuation.kwargs["queue"], "critical")
        self.assertEqual(continuation.kwargs["priority"], 96)
        self.assertEqual(continuation.kwargs["payload"]["source"], "first_run_hot_window")
        self.assertTrue(str(continuation.kwargs["dedupe_key"]).startswith("first-run-mail-group-enrich:user-1:"))
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=False, required_queues_ready=False))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status_since", return_value={"ready": 0, "pending": 0, "failed": 0})
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 0, "pending": 0})
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.get_import_state", return_value=None)
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.missing_google_scopes", return_value=[])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    @patch("app.services.mail_groups.build_dashboard_response")
    def test_app_session_reads_snapshot_without_live_dashboard_rebuild(
        self,
        mock_live_dashboard: Mock,
        _mock_can_write: Mock,
        _mock_missing_scopes: Mock,
        mock_snapshot: Mock,
        _mock_state: Mock,
        _mock_recovery: Mock,
        _mock_counts: Mock,
        _mock_recent_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        mock_snapshot.return_value = AppSessionSnapshotRecord(
            user_id="user-1",
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
        mock_live_dashboard.assert_not_called()

    def test_refresh_app_session_snapshot_can_defer_dashboard_work(self) -> None:
        session_marker = object()
        import_state = SimpleNamespace(
            last_import_completed_at="2026-05-15T12:00:00+00:00",
            last_sync_error=None,
            full_backfill_completed_at=None,
            hot_window_started_at="2026-05-15T12:00:00+00:00",
            hot_window_completed_at="2026-05-15T12:01:00+00:00",
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.db.repository.get_user",
                    return_value=SimpleNamespace(id="user-1", email="me@example.com", display_name="TestUser"),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.mail_groups._google_auth_state",
                    return_value=GoogleAuthState(available=True, connected=True, can_send_mail=True),
                )
            )
            mock_dashboard = stack.enter_context(patch("app.services.mail_groups.build_dashboard_response"))
            stack.enter_context(
                patch(
                    "app.services.mail_groups.build_mailbox_response",
                    return_value=MailboxResponse(label="inbox", total_threads=3, loaded_threads=3),
                )
            )
            stack.enter_context(patch("app.services.mail_groups._enqueue_body_fetch_for_mailbox_rows"))
            stack.enter_context(
                patch(
                    "app.services.mail_groups.count_mail_groups_by_enrichment_status",
                    return_value={"ready": 3, "pending": 0, "failed": 0},
                )
            )
            stack.enter_context(patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None))
            stack.enter_context(patch("app.services.mail_groups._mail_runtime_status", return_value={"worker_online": True}))
            stack.enter_context(patch("app.services.mail_groups.get_import_state", return_value=import_state))
            stack.enter_context(patch("app.services.mail_groups.oldest_imported_message_at", return_value=None))
            stack.enter_context(patch("app.services.mail_groups.count_pending_thread_actions", return_value=0))
            stack.enter_context(
                patch(
                    "app.services.mail_groups._smart_inbox_from_mailbox_and_objects",
                    return_value=SmartInboxResponse(total_rows=3, ready_count=3),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.mail_groups._smart_inbox_with_offline_status",
                    return_value=SmartInboxResponse(total_rows=3, ready_count=3),
                )
            )
            stack.enter_context(patch("app.services.mail_groups._enqueue_body_fetch_for_smart_inbox"))
            stack.enter_context(
                patch(
                    "app.services.mail_groups._smart_work_queue_from_smart_inbox",
                    return_value=SmartWorkQueueResponse(total_open=0),
                )
            )
            stack.enter_context(
                patch(
                    "app.services.mail_groups._smart_readiness_from_mailbox",
                    return_value=SmartReadinessResponse(
                        stage="snapshot_ready",
                        first_ready_complete=True,
                        hot_window_complete=True,
                    ),
                )
            )
            stack.enter_context(patch("app.services.mail_groups._smart_inbox_storage_rows", return_value=[]))
            stack.enter_context(patch("app.services.mail_groups._smart_related_storage_suggestions", return_value=[]))
            stack.enter_context(patch("app.services.mail_groups._smart_work_storage_items", return_value=[]))
            stack.enter_context(patch("app.services.mail_groups.replace_smart_inbox_projection"))
            mock_upsert = stack.enter_context(patch("app.services.mail_groups.upsert_app_session_snapshot"))
            stack.enter_context(patch("app.services.mail_groups.build_app_session_response", return_value=session_marker))
            result = refresh_app_session_snapshot(self.settings, user_id="user-1", include_dashboard=False)

        self.assertIs(result, session_marker)
        mock_dashboard.assert_not_called()
        dashboard_payload = mock_upsert.call_args.kwargs["dashboard"]
        self.assertEqual(dashboard_payload["runtime_status"]["feed_source"], "deferred")
        self.assertTrue(dashboard_payload["runtime_status"]["worker_online"])
        self.assertEqual(dashboard_payload["feed"]["now"], [])
        self.assertEqual(dashboard_payload["feed"]["today"], [])
        self.assertEqual(dashboard_payload["feed"]["worth_knowing"], [])

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=True, required_queues_ready=True))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status_since", return_value={"ready": 3, "pending": 104, "failed": 0})
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 3, "pending": 104, "failed": 0})
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.get_import_state")
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.missing_google_scopes", return_value=[])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    @patch("app.services.mail_groups.smart_inbox_response_is_product_ready", return_value=False)
    def test_app_session_readiness_waits_when_cached_inbox_quality_has_blockers(
        self,
        mock_product_ready: Mock,
        _mock_can_write: Mock,
        _mock_missing_scopes: Mock,
        mock_snapshot: Mock,
        mock_state: Mock,
        _mock_recovery: Mock,
        _mock_counts: Mock,
        _mock_recent_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        mock_state.return_value = _ready_import_state()
        mock_snapshot.return_value = _app_session_snapshot_with_mailbox(_snapshot_mailbox_row(title="Account update", presentation_status="fallback"))
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        session = build_app_session_response(self.settings, user=user)

        self.assertFalse(session.readiness.ready_to_enter)
        self.assertEqual(session.readiness.stage, "preparing_inbox")
        self.assertEqual(session.readiness.mode, "first_time")
        mock_product_ready.assert_called_once()

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=True, required_queues_ready=True))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status_since", return_value={"ready": 3, "pending": 0, "failed": 0})
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 3, "pending": 0, "failed": 0})
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.get_import_state")
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.missing_google_scopes", return_value=[])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    @patch("app.services.mail_groups.smart_inbox_response_is_product_ready", return_value=True)
    def test_app_session_readiness_enters_when_cached_inbox_quality_passes(
        self,
        mock_product_ready: Mock,
        _mock_can_write: Mock,
        _mock_missing_scopes: Mock,
        mock_snapshot: Mock,
        mock_state: Mock,
        _mock_recovery: Mock,
        _mock_counts: Mock,
        _mock_recent_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        mock_state.return_value = _ready_import_state()
        mock_snapshot.return_value = _app_session_snapshot_with_mailbox(
            _snapshot_mailbox_row(
                title="Apple order W123456789 out for delivery",
                presentation_status="ai_ready",
                grouping_metadata={"reference": {"signal_name": "order_id", "value": "W123456789"}},
            )
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        session = build_app_session_response(self.settings, user=user)

        self.assertTrue(session.readiness.ready_to_enter)
        self.assertEqual(session.readiness.mode, "returning")
        mock_product_ready.assert_called_once()

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=True, required_queues_ready=True))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status_since", return_value={"ready": 71, "pending": 104, "failed": 0})
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 413, "pending": 9, "failed": 0})
    @patch("app.services.mail_groups.ensure_background_import_work")
    @patch("app.services.mail_groups.get_import_state")
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.missing_google_scopes", return_value=[])
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_app_session_readiness_waits_when_smart_inbox_has_partial_rows_even_if_raw_mailbox_has_backlog(
        self,
        _mock_can_write: Mock,
        _mock_missing_scopes: Mock,
        mock_snapshot: Mock,
        mock_state: Mock,
        _mock_recovery: Mock,
        _mock_counts: Mock,
        _mock_recent_counts: Mock,
        _mock_ai_error: Mock,
        _mock_health: Mock,
    ) -> None:
        mock_state.return_value = _ready_import_state()
        mock_snapshot.return_value = _app_session_snapshot_with_mailbox(
            _snapshot_mailbox_row(title="Account update", presentation_status="fallback"),
            smart_inbox=_snapshot_smart_inbox(ready_count=30, partial_count=18),
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        session = build_app_session_response(self.settings, user=user)

        self.assertFalse(session.readiness.ready_to_enter)
        self.assertEqual(session.readiness.stage, "preparing_inbox")
        self.assertEqual(session.readiness.ready_mail_group_count, 71)
        self.assertEqual(session.smart_inbox.total_rows, 48)

    def test_post_login_readiness_waits_for_hot_window_before_dashboard(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            first_dashboard_ready_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="cursor-1",
            hot_window_completed_at=None,
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        with patch("app.services.mail_groups.get_import_state", return_value=state), patch(
            "app.services.mail_groups.count_mail_groups",
            return_value=3,
        ), patch("app.services.mail_groups.count_ready_mail_groups_since", return_value=3), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status_since",
            return_value={"ready": 3, "pending": 0, "failed": 0},
        ), patch(
            "app.services.mail_groups.count_dashboard_mail_groups",
            return_value=0,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user)

        self.assertFalse(readiness.ready_to_enter)
        self.assertEqual(readiness.stage, "preparing_inbox")
        self.assertEqual(readiness.mode, "first_time")
        self.assertTrue(readiness.mailbox_ready)
        self.assertFalse(readiness.dashboard_ready)

    def test_post_login_readiness_counts_only_inbox_visible_hot_window_groups(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            first_dashboard_ready_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="cursor-1",
            hot_window_completed_at="2026-05-15T12:00:45+00:00",
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        with patch("app.services.mail_groups.get_import_state", return_value=state), patch(
            "app.services.mail_groups.count_mail_groups",
            return_value=8,
        ), patch("app.services.mail_groups.count_ready_mail_groups_since", return_value=0) as mock_recent_ready, patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status_since",
            return_value={"ready": 0, "pending": 0, "failed": 0},
        ) as mock_recent_status, patch(
            "app.services.mail_groups.count_dashboard_mail_groups",
            return_value=0,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user)

        self.assertFalse(readiness.ready_to_enter)
        self.assertFalse(readiness.mailbox_ready)
        self.assertEqual(readiness.ready_mail_group_count, 0)
        self.assertEqual(mock_recent_ready.call_args.kwargs["mailbox_label"], "inbox")
        self.assertEqual(mock_recent_status.call_args.kwargs["mailbox_label"], "inbox")

    def test_post_login_readiness_enters_when_hot_window_has_pending_background_enrichment(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            first_dashboard_ready_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="cursor-1",
            hot_window_completed_at="2026-05-15T12:00:45+00:00",
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        with patch("app.services.mail_groups.get_import_state", return_value=state), patch(
            "app.services.mail_groups.count_mail_groups",
            return_value=3,
        ), patch("app.services.mail_groups.count_ready_mail_groups_since", return_value=3), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status_since",
            return_value={"ready": 3, "pending": 1, "failed": 0},
        ), patch(
            "app.services.mail_groups.count_dashboard_mail_groups",
            return_value=0,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=1,
        ), patch(
            "app.services.mail_groups.audit_smart_inbox_quality",
            return_value=SimpleNamespace(total_rows=3, blocking_issue_count=0, issues=[]),
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user, mailbox=SimpleNamespace(total_threads=3))

        self.assertTrue(readiness.ready_to_enter)
        self.assertEqual(readiness.stage, "welcome_back")
        self.assertEqual(readiness.mode, "returning")
        self.assertTrue(readiness.mailbox_ready)
        self.assertFalse(readiness.dashboard_ready)

    def test_post_login_readiness_enters_when_hot_window_ready_before_dashboard(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            first_dashboard_ready_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="cursor-1",
            hot_window_completed_at="2026-05-15T12:00:45+00:00",
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        with patch("app.services.mail_groups.get_import_state", return_value=state), patch(
            "app.services.mail_groups.count_mail_groups",
            return_value=3,
        ), patch("app.services.mail_groups.count_ready_mail_groups_since", return_value=3), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status_since",
            return_value={"ready": 3, "pending": 0, "failed": 0},
        ), patch(
            "app.services.mail_groups.count_dashboard_mail_groups",
            return_value=0,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.audit_smart_inbox_quality",
            return_value=SimpleNamespace(total_rows=3, blocking_issue_count=0, issues=[]),
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user, mailbox=SimpleNamespace(total_threads=3))

        self.assertTrue(readiness.ready_to_enter)
        self.assertEqual(readiness.mode, "returning")
        self.assertTrue(readiness.mailbox_ready)
        self.assertFalse(readiness.dashboard_ready)

    def test_post_login_readiness_waits_when_hot_window_inbox_quality_has_blockers(self) -> None:
        state = SimpleNamespace(
            first_batch_imported_at="2026-05-15T12:00:00+00:00",
            first_groups_ready_at="2026-05-15T12:00:10+00:00",
            first_dashboard_ready_at=None,
            full_backfill_completed_at=None,
            full_backfill_cursor="cursor-1",
            hot_window_completed_at="2026-05-15T12:00:45+00:00",
            last_sync_error=None,
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="TestUser")

        with patch("app.services.mail_groups.get_import_state", return_value=state), patch(
            "app.services.mail_groups.count_mail_groups",
            return_value=3,
        ), patch("app.services.mail_groups.count_ready_mail_groups_since", return_value=3), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status_since",
            return_value={"ready": 3, "pending": 0, "failed": 0},
        ), patch(
            "app.services.mail_groups.count_dashboard_mail_groups",
            return_value=0,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.audit_smart_inbox_quality",
            return_value=SimpleNamespace(
                total_rows=3,
                blocking_issue_count=1,
                issues=[SimpleNamespace(code="vague_title", severity="blocker")],
            ),
        ), patch(
            "app.services.mail_groups.get_app_session_snapshot",
            return_value=None,
        ):
            readiness = build_post_login_readiness_response(self.settings, user=user, mailbox=SimpleNamespace(total_threads=3))

        self.assertFalse(readiness.ready_to_enter)
        self.assertEqual(readiness.stage, "preparing_inbox")
        self.assertEqual(readiness.mode, "first_time")
        self.assertTrue(readiness.mailbox_ready)
        self.assertFalse(readiness.dashboard_ready)

    def test_heartbeat_renews_running_job_lease(self) -> None:
        connection = FakeConnection()
        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            renew_heartbeat("postgresql://example/db", worker_id="worker-1", queues=["critical"], current_job_id="job-1")

        statements = [call[0] for call in connection.calls]
        self.assertTrue(any("UPDATE background_jobs" in statement and "lease_expires_at" in statement for statement in statements))


def _ready_import_state() -> SimpleNamespace:
    return SimpleNamespace(
        first_batch_imported_at="2026-05-15T12:00:00+00:00",
        first_groups_ready_at="2026-05-15T12:00:10+00:00",
        first_dashboard_ready_at=None,
        full_backfill_completed_at=None,
        full_backfill_cursor="cursor-1",
        hot_window_completed_at="2026-05-15T12:00:45+00:00",
        last_sync_error=None,
    )


def _app_session_snapshot_with_mailbox(row: dict[str, object], *, smart_inbox: dict[str, object] | None = None) -> AppSessionSnapshotRecord:
    return AppSessionSnapshotRecord(
        user_id="user-1",
        dashboard={"auth": {"available": True, "connected": True, "connect_url": None}, "feed": {}, "runtime_status": {}},
        mailbox={
            "label": "inbox",
            "total_threads": 1,
            "loaded_threads": 1,
            "sections": [{"id": "today", "title": "Today", "rows": [row]}],
        },
        sync={
            "last_sync_at": "2026-05-15T12:00:45+00:00",
            "last_error": None,
            "enrichment_pending_count": 0,
            "ready_group_count": 3,
            "oldest_imported_at": "2026-05-01T12:00:00+00:00",
            "full_import_running": True,
            "full_import_completed": False,
            "hot_window_completed_at": "2026-05-15T12:00:45+00:00",
            "projection_version": APP_SESSION_PROJECTION_VERSION,
        },
        updated_at="2026-05-15T12:01:00+00:00",
        smart_inbox=smart_inbox or {},
    )


def _snapshot_smart_inbox(*, ready_count: int, partial_count: int = 0) -> dict[str, object]:
    rows = [
        {
            "id": "smart-row:apple-order",
            "row_key": "thread-apple-order",
            "row_type": "verified_group",
            "title": "Apple order W123456789 is out for delivery",
            "summary": "Apple says order W123456789 is out for delivery today.",
            "primary_sender": "Apple <orders@fruitco.example>",
            "latest_message_at": "2026-05-15T12:00:00+00:00",
            "latest_message_id": "msg-apple-order-2",
            "reader_thread_id": "thread-apple-order",
            "source_thread_ids": ["thread-apple-order"],
            "source_message_ids": ["msg-apple-order-1", "msg-apple-order-2"],
            "confidence_tier": "strong",
            "confidence": 0.94,
            "grouping_reason": {"source": "mailbox_projection"},
            "offline_status": "ready",
            "readiness": "ready",
            "action_type": "track",
            "priority": 20,
        }
    ]
    return {
        "total_rows": ready_count + partial_count,
        "sections": [{"id": "today", "title": "Today", "rows": rows}],
        "related_suggestions": [],
        "ready_count": ready_count,
        "partial_count": partial_count,
        "failed_count": 0,
        "generated_at": "2026-05-15T12:01:00+00:00",
        "hot_window_days": 30,
        "hot_window_message_cap": 0,
        "hot_window_thread_cap": 0,
    }


def _snapshot_mailbox_row(
    *,
    title: str,
    presentation_status: str,
    grouping_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "thread_id": "thread-apple-order",
        "entity_id": "thread-apple-order",
        "title": title,
        "latest_source_record_id": "msg-apple-order",
        "latest_received_at": "2026-05-15T12:00:00+00:00",
        "latest_subject": title,
        "latest_sender": "Apple <orders@fruitco.example>",
        "sender": "Apple",
        "message_count": 2,
        "presentation_status": presentation_status,
        "grouping_metadata": grouping_metadata or {},
        "children": [
            {
                "message_id": "msg-apple-order-1",
                "gmail_thread_id": "thread-apple-order",
                "sender": "Apple <orders@fruitco.example>",
                "subject": "Apple order W123456789 shipped",
                "ai_title": "Apple order W123456789 shipped",
                "snippet": "Your Apple order W123456789 has shipped.",
                "received_at": "2026-05-15T11:00:00+00:00",
            },
            {
                "message_id": "msg-apple-order-2",
                "gmail_thread_id": "thread-apple-order",
                "sender": "Apple <orders@fruitco.example>",
                "subject": title,
                "ai_title": title,
                "snippet": title,
                "received_at": "2026-05-15T12:00:00+00:00",
            },
        ],
    }


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
