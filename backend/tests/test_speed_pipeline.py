from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from app.db.jobs import renew_heartbeat
from app.db.mail_groups import AppSessionSnapshotRecord, GmailMessageRecord, MailboxCursorError, MailboxThreadPage, MailGroupRecord, decode_mailbox_cursor, encode_mailbox_cursor
from app.services.auth import CurrentUser
from app.services.gmail_importer import _encode_full_mailbox_cursor, run_gmail_backfill, run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import (
    APP_SESSION_PROJECTION_VERSION,
    _candidate_groups,
    build_app_session_response,
    build_mailbox_response,
    enqueue_mailbox_sync,
    ensure_background_import_work,
    rebuild_touched_mail_groups,
    run_first_run_ai_grouping,
)
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


class SpeedPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            database_path="postgresql://example/db",
            google_configured=True,
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
    def test_first_run_import_uses_90_day_mailbox_seeds_and_thread_history(
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
        inbox = sample_message("inbox-1")
        sent = replace(sample_message("sent-1"), gmail_thread_id="thread-2", label_ids=["SENT"])
        draft = replace(sample_message("draft-1"), gmail_thread_id="thread-3", label_ids=["DRAFT"])
        spam = replace(sample_message("spam-1"), gmail_thread_id="thread-4", label_ids=["SPAM"])
        trash = replace(sample_message("trash-1"), gmail_thread_id="thread-5", label_ids=["TRASH"])
        archived = replace(sample_message("archived-1"), gmail_thread_id="thread-6", label_ids=["IMPORTANT"])
        history = replace(sample_message("history-1"), gmail_thread_id="thread-1", label_ids=["SENT"])
        mock_list.side_effect = [
            {"messages": [{"id": "inbox-1"}]},
            {"messages": [{"id": "sent-1"}]},
            {"messages": [{"id": "spam-1"}]},
            {"messages": [{"id": "trash-1"}]},
            {"messages": [{"id": "archived-1"}]},
        ]
        mock_draft_list.return_value = {"messages": [{"id": "draft-1"}]}
        mock_hydrate.side_effect = [([inbox], "10"), ([sent], "11"), ([draft], "12"), ([spam], "13"), ([trash], "14"), ([archived], "15")]
        mock_thread_history.return_value = ([history], "12")

        imported = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=500, first_run=True)

        self.assertEqual(imported, 7)
        self.assertEqual([call.kwargs["label_ids"] for call in mock_list.call_args_list], [["INBOX"], ["SENT"], ["SPAM"], ["TRASH"], None])
        mock_draft_list.assert_called_once_with(self.settings, user_id="user-1", batch_size=500, page_token=None)
        mock_thread_history.assert_called_once()
        self.assertEqual(mock_upsert.call_args.args[1], [inbox, sent, draft, spam, trash, archived, history])
        self.assertIsNotNone(mock_completed.call_args.kwargs["full_backfill_cursor"])
        self.assertFalse(mock_completed.call_args.kwargs["clear_full_backfill_cursor"])
        self.assertTrue(mock_completed.call_args.kwargs["full_backfill_started"])
        self.assertTrue(any(call.kwargs.get("kind") == "gmail_backfill" for call in mock_enqueue.call_args_list))
        self.assertTrue(any(call.kwargs.get("kind") == "first_run_ai_grouping" for call in mock_enqueue.call_args_list))

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
    @patch("app.services.mail_groups.mark_import_completed")
    @patch("app.services.mail_groups.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.mail_groups._store_ai_batch_groups", return_value=(3, 1, {"msg-1"}))
    @patch("app.services.mail_groups._ai_batch_group_messages", return_value=[])
    @patch("app.services.mail_groups._first_run_candidate_messages")
    @patch("app.services.mail_groups.list_recent_messages_since")
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    def test_first_run_creates_pending_groups_for_ai_omitted_recent_messages(
        self,
        _mock_can_write: Mock,
        mock_recent: Mock,
        mock_candidates: Mock,
        _mock_ai: Mock,
        _mock_store: Mock,
        mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        _mock_snapshot: Mock,
        mock_enqueue: Mock,
    ) -> None:
        selected = sample_message("msg-1")
        omitted = replace(sample_message("msg-2"), gmail_thread_id="thread-2")
        mock_recent.return_value = [selected, omitted]
        mock_candidates.return_value = [selected]

        created = run_first_run_ai_grouping(self.settings, user_id="user-1", limit=50)

        self.assertEqual(created, 3)
        self.assertGreaterEqual(mock_recent.call_args.kwargs["limit"], 270)
        mock_rebuild_touched.assert_called_once_with(self.settings, user_id="user-1", message_ids=["msg-2"], use_ai=False)
        self.assertTrue(any(call.kwargs.get("kind") == "mail_group_enrich" for call in mock_enqueue.call_args_list))

    def test_candidate_groups_merge_same_sender_lifecycle_threads(self) -> None:
        first = replace(
            sample_message("neo-1"),
            sender="Neo <noreply@neo.com>",
            gmail_thread_id="thread-neo-1",
            subject="Neo application received: Futuristic Intelligence",
            extracted_signals={"sender_domain": "neo.com", "normalized_subject": "neo application received: futuristic intelligence"},
        )
        second = replace(
            sample_message("neo-2"),
            sender="Neo <noreply@neo.com>",
            gmail_thread_id="thread-neo-2",
            subject="Neo -- Complete your founder profile for Neo Residency",
            extracted_signals={"sender_domain": "neo.com", "normalized_subject": "neo -- complete your founder profile for neo residency"},
        )

        groups = _candidate_groups([first, second])

        self.assertEqual(len(groups), 1)
        group_key, members = next(iter(groups.items()))
        self.assertTrue(group_key.startswith("entity-lifecycle:neo.com:neo:neo"))
        self.assertEqual({message.message_id for message in members}, {"neo-1", "neo-2"})

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
    @patch("app.services.mail_groups.oldest_imported_message_at", return_value=None)
    @patch("app.services.mail_groups.count_mailbox_threads", return_value=0)
    @patch("app.services.mail_groups.list_mail_groups_for_gmail_threads", return_value={})
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
        _mock_ai_groups: Mock,
        mock_count_threads: Mock,
        _mock_oldest: Mock,
        _mock_recovery: Mock,
    ) -> None:
        mailbox = build_mailbox_response(self.settings, user_id="user-1")

        self.assertEqual(mailbox.pending_count, 9)
        self.assertEqual(mailbox.next_cursor, "cursor-2")
        self.assertEqual(mailbox.loaded_threads, 100)
        self.assertIsNone(mailbox.window_days)
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

    def test_mailbox_uses_ai_group_rows_titles_and_summaries(self) -> None:
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
            gmail_thread_id="thread-2",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Raw second subject",
            snippet="Raw second snippet",
            ai_title="Clean second title",
        )
        group = replace(sample_group(), ai_title="AI grouped title", ai_summary="AI grouped summary")

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-1", [first]), ("thread-2", [second])], next_cursor=None, loaded_threads=2),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-1": group, "thread-2": group},
        ), patch("app.services.mail_groups.list_messages_for_groups", return_value={"group-1": [first, second]}), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "group-1")
        self.assertEqual(rows[0].title, "AI grouped title")
        self.assertEqual(rows[0].summary, "AI grouped summary")
        self.assertEqual(rows[0].ai_title, "AI grouped title")
        self.assertEqual(rows[0].ai_summary, "AI grouped summary")
        self.assertEqual(rows[0].latest_subject, "Raw second subject")
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual([child.message_id for child in rows[0].children], ["msg-1", "msg-2"])
        self.assertEqual(rows[0].children[0].subject, "Raw first subject")
        self.assertEqual(rows[0].children[0].ai_title, "Clean first title")
        self.assertEqual(rows[0].children[1].subject, "Raw second subject")
        self.assertEqual(rows[0].children[1].ai_title, "Clean second title")

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
        )
        group = replace(sample_group(), ai_title="AI thread", ai_summary="AI summary")

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-1", [inbox, sent])], next_cursor=None, loaded_threads=1),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-1": group},
        ), patch("app.services.mail_groups.list_messages_for_groups", return_value={"group-1": [inbox, sent]}), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=1,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            inbox_mailbox = build_mailbox_response(self.settings, user_id="user-1", label="inbox")
            sent_mailbox = build_mailbox_response(self.settings, user_id="user-1", label="sent")

        inbox_row = [row for section in inbox_mailbox.sections for row in section.rows][0]
        sent_row = [row for section in sent_mailbox.sections for row in section.rows][0]
        self.assertEqual(inbox_row.message_count, 1)
        self.assertEqual(inbox_row.latest_subject, "Inbox copy")
        self.assertEqual(inbox_row.label_ids, ["INBOX", "UNREAD"])
        self.assertEqual(inbox_row.participants, ["Sender"])
        self.assertEqual([child.message_id for child in inbox_row.children], ["inbox-1"])
        self.assertEqual(inbox_row.children[0].label_ids, ["INBOX", "UNREAD"])
        self.assertEqual(sent_row.message_count, 1)
        self.assertEqual(sent_row.latest_subject, "Sent reply")
        self.assertEqual(sent_row.label_ids, ["SENT"])
        self.assertEqual(sent_row.participants, ["Me"])
        self.assertEqual([child.message_id for child in sent_row.children], ["sent-1"])
        self.assertEqual(sent_row.children[0].label_ids, ["SENT"])

    def test_mailbox_skips_overlapping_group_rows(self) -> None:
        first = replace(
            sample_message("msg-1"),
            gmail_thread_id="thread-1",
            internal_date="2026-05-15T12:00:00+00:00",
            subject="Raw first subject",
        )
        second = replace(
            sample_message("msg-2"),
            gmail_thread_id="thread-2",
            internal_date="2026-05-15T13:00:00+00:00",
            subject="Raw second subject",
        )
        ai_group = replace(
            sample_group(),
            id="group-ai",
            membership_source="ai_batch",
            ai_title="AI conversation",
            ai_summary="AI summary",
        )
        lower_priority_group = replace(
            sample_group(),
            id="group-raw",
            group_key="gmail-thread:thread-2",
            membership_source="gmail_thread",
            ai_title="Raw overlap",
            ai_summary="Raw summary",
        )

        with patch("app.services.mail_groups.ensure_background_import_work"), patch(
            "app.services.mail_groups.get_import_state",
            return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None),
        ), patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 0}), patch(
            "app.services.mail_groups.list_mailbox_thread_page",
            return_value=MailboxThreadPage(threads=[("thread-1", [first]), ("thread-2", [second])], next_cursor=None, loaded_threads=2),
        ), patch(
            "app.services.mail_groups.list_mail_groups_for_gmail_threads",
            return_value={"thread-1": ai_group, "thread-2": lower_priority_group},
        ), patch(
            "app.services.mail_groups.list_messages_for_groups",
            return_value={"group-ai": [first, second], "group-raw": [second]},
        ), patch(
            "app.services.mail_groups.count_mailbox_threads",
            return_value=2,
        ), patch("app.services.mail_groups.oldest_imported_message_at", return_value=None), patch(
            "app.services.mail_groups.count_active_jobs", return_value=0
        ):
            mailbox = build_mailbox_response(self.settings, user_id="user-1")

        rows = [row for section in mailbox.sections for row in section.rows]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].thread_id, "group-ai")
        self.assertEqual(rows[0].title, "AI conversation")
        self.assertEqual(rows[0].message_count, 2)

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

    @patch("app.services.mail_groups.get_queue_health", return_value=SimpleNamespace(queue_depth={}, worker_online=False, required_queues_ready=False))
    @patch("app.services.mail_groups.latest_mail_group_ai_error", return_value=None)
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
        user = CurrentUser(id="user-1", email="me@example.com", display_name="Gaurav")

        session = build_app_session_response(self.settings, user=user)

        self.assertEqual(session.user.email, "me@example.com")
        self.assertFalse(session.readiness.ready_to_enter)
        self.assertFalse(session.readiness.dashboard_ready)
        mock_live_dashboard.assert_not_called()

    def test_heartbeat_renews_running_job_lease(self) -> None:
        connection = FakeConnection()
        with patch("app.db.jobs.get_engine", return_value=FakeEngine(connection)):
            renew_heartbeat("postgresql://example/db", worker_id="worker-1", queues=["critical"], current_job_id="job-1")

        statements = [call[0] for call in connection.calls]
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
