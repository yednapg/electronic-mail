from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
from httplib2 import Response

from app.db.jobs import renew_heartbeat
from app.db.mail_groups import AppSessionSnapshotRecord, GmailMessageRecord, MailGroupRecord
from app.services.auth import CurrentUser
from app.services.gmail_importer import run_gmail_backfill, run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import (
    _candidate_groups,
    build_app_session_response,
    build_mailbox_response,
    enqueue_mailbox_sync,
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
        )

    @patch("app.services.gmail_importer.enqueue_projection_refresh")
    @patch("app.services.gmail_importer.enqueue_job")
    @patch("app.services.gmail_importer.mark_import_completed")
    @patch("app.services.gmail_importer.rebuild_touched_mail_groups", return_value=1)
    @patch("app.services.gmail_importer.upsert_gmail_messages", return_value=1)
    @patch("app.services.gmail_importer._hydrate_messages")
    @patch("app.services.gmail_importer._list_messages")
    @patch("app.services.gmail_importer.mark_import_started")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_incremental_import_uses_metadata_and_touched_groups(
        self,
        _mock_can_write: Mock,
        _mock_started: Mock,
        mock_list: Mock,
        mock_hydrate: Mock,
        _mock_upsert: Mock,
        mock_rebuild_touched: Mock,
        _mock_completed: Mock,
        _mock_enqueue: Mock,
        mock_projection: Mock,
    ) -> None:
        message = sample_message()
        mock_list.return_value = {"messages": [{"id": message.message_id}], "nextPageToken": "next"}
        mock_hydrate.return_value = ([message], "10")

        touched = run_gmail_import_batch(self.settings, user_id="user-1", batch_size=30, first_run=False)

        self.assertEqual(touched, 1)
        mock_hydrate.assert_called_once()
        self.assertEqual(mock_hydrate.call_args.kwargs["format"], "metadata")
        mock_rebuild_touched.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_ids=["msg-1"],
            use_ai=False,
        )
        mock_projection.assert_called_once_with(self.settings, user_id="user-1", priority=20)

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

    @patch("app.services.mail_groups.oldest_imported_message_at", return_value=None)
    @patch("app.services.mail_groups.count_mail_groups_by_enrichment_status", return_value={"ready": 2, "pending": 9})
    @patch("app.services.mail_groups.list_messages_for_groups", return_value={})
    @patch("app.services.mail_groups.list_mail_groups", return_value=[])
    @patch("app.services.mail_groups.get_import_state", return_value=SimpleNamespace(first_batch_imported_at="ready", full_backfill_cursor=None))
    def test_mailbox_includes_pending_groups_for_fast_arrival(
        self,
        _mock_state: Mock,
        mock_list_groups: Mock,
        _mock_group_messages: Mock,
        _mock_counts: Mock,
        _mock_oldest: Mock,
    ) -> None:
        mailbox = build_mailbox_response(self.settings, user_id="user-1")

        self.assertEqual(mailbox.pending_count, 9)
        mock_list_groups.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            limit=150,
            include_pending=True,
        )

    @patch("app.services.mail_groups.get_import_state", return_value=None)
    @patch("app.services.mail_groups.get_app_session_snapshot")
    @patch("app.services.mail_groups.user_can_write_gmail", return_value=True)
    @patch("app.services.mail_groups.build_dashboard_response")
    def test_app_session_reads_snapshot_without_live_dashboard_rebuild(
        self,
        mock_live_dashboard: Mock,
        _mock_can_write: Mock,
        mock_snapshot: Mock,
        _mock_state: Mock,
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
            },
            updated_at="2026-05-15T12:00:00+00:00",
        )
        user = CurrentUser(id="user-1", email="me@example.com", display_name="Gaurav")

        session = build_app_session_response(self.settings, user=user)

        self.assertEqual(session.user.email, "me@example.com")
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
