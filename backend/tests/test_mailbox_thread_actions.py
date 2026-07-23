from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.db.mail_groups import GmailMessageRecord, MailGroupDetail, MailGroupRecord, PendingThreadActionRecord
from app.main import app
from app.services.mailbox_actions import ThreadActionIdempotencyConflict, _labels_after_action, enqueue_thread_action, rollback_failed_thread_action, run_pending_thread_action


def pending_action(state: str = "queued", action: str = "archive") -> PendingThreadActionRecord:
    return PendingThreadActionRecord(
        server_action_id="server-1",
        client_action_id="client-1",
        user_id="user-1",
        mailbox_thread_id="group-1",
        target_message_id=None,
        action=action,
        state=state,
        created_at="2026-05-21T09:00:00+00:00",
        queued_at="2026-05-21T09:00:00+00:00",
        applied_at=None,
        error=None,
        updated_at="2026-05-21T09:00:00+00:00",
    )


def sample_group() -> MailGroupRecord:
    return MailGroupRecord(
        id="group-1",
        user_id="user-1",
        group_key="gmail-thread:thread-a",
        group_type="conversation",
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="Queued action",
        ai_summary="Queued action test",
        labels=["inbox"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=False,
        latest_message_at="2026-05-21T09:00:00+00:00",
        latest_message_id="msg-1",
        generated_from_hash="hash-1",
        generated_at="2026-05-21T09:00:00+00:00",
        created_at="2026-05-21T09:00:00+00:00",
        updated_at="2026-05-21T09:00:00+00:00",
    )


def sample_message(message_id: str, gmail_thread_id: str) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id=gmail_thread_id,
        history_id="10",
        label_ids=["INBOX", "UNREAD"],
        internal_date="2026-05-21T09:00:00+00:00",
        subject="Queued action",
        sender="sender@example.com",
        recipients={"to": "me@example.com"},
        headers={"subject": "Queued action"},
        snippet="Queued action",
        raw_payload={"payload": {"headers": []}},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="Queued action",
        extracted_signals={},
        body_hash="hash-1",
        created_at="2026-05-21T09:00:00+00:00",
        updated_at="2026-05-21T09:00:00+00:00",
    )


class MailboxThreadActionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event_patch = patch("app.services.mailbox_actions.emit_mailbox_event")
        self.event_patch.start()
        self.addCleanup(self.event_patch.stop)

    def test_launch_actions_update_local_labels_optimistically(self) -> None:
        self.assertEqual(_labels_after_action(["INBOX"], "mark_unread"), ["INBOX", "UNREAD"])
        self.assertEqual(_labels_after_action(["TRASH"], "restore_trash"), ["INBOX"])
        self.assertEqual(_labels_after_action(["INBOX", "UNREAD"], "mark_spam"), ["UNREAD", "SPAM"])
        self.assertEqual(_labels_after_action(["SPAM", "UNREAD"], "not_spam"), ["UNREAD", "INBOX"])
        self.assertEqual(_labels_after_action(["INBOX"], "star"), ["INBOX", "STARRED"])
        self.assertEqual(_labels_after_action(["INBOX", "STARRED"], "unstar"), ["INBOX"])

    def test_enqueue_thread_action_stores_local_state_and_enqueues_worker(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action()

        with patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, True)) as upsert, patch(
            "app.services.mailbox_actions._apply_local_action"
        ) as apply_local, patch("app.services.mailbox_actions.enqueue_job") as enqueue_job, patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ) as refresh, patch(
            "app.services.mailbox_actions._resolve_action_messages", return_value=([], [])
        ):
            response = enqueue_thread_action(
                settings,
                user_id="user-1",
                request=SimpleNamespace(
                    client_action_id="client-1",
                    mailbox_thread_id="group-1",
                    action="archive",
                    created_at="2026-05-21T09:00:00+00:00",
                ),
            )

        self.assertEqual(response.server_action_id, "server-1")
        self.assertEqual(response.state, "queued")
        upsert.assert_called_once()
        apply_local.assert_called_once_with(
            settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            action="archive",
            target_message_id=None,
            resolved_messages=[],
        )
        enqueue_job.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_thread_action",
            queue="default",
            user_id="user-1",
            dedupe_key="gmail-thread-action:user-1:server-1",
            priority=70,
            payload={"user_id": "user-1", "server_action_id": "server-1"},
        )
        refresh.assert_called_once_with(settings, user_id="user-1", priority=10)

    def test_exact_client_action_replay_returns_existing_without_side_effects(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action()
        request = SimpleNamespace(
            client_action_id="client-1",
            mailbox_thread_id="group-1",
            target_message_id=None,
            action="archive",
            created_at="2026-05-21T09:00:00Z",
        )
        with (
            patch("app.services.mailbox_actions._resolve_action_messages", return_value=([], [])),
            patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, False)),
            patch("app.services.mailbox_actions._apply_local_action") as apply_local,
            patch("app.services.mailbox_actions.enqueue_job") as enqueue,
            patch("app.services.mailbox_actions.enqueue_projection_refresh") as refresh,
        ):
            response = enqueue_thread_action(settings, user_id="user-1", request=request)

        self.assertEqual(response.server_action_id, "server-1")
        apply_local.assert_not_called()
        enqueue.assert_not_called()
        refresh.assert_not_called()

    def test_client_action_replay_rejects_different_thread_or_action(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action()
        mismatches = [
            {"mailbox_thread_id": "group-2", "action": "archive"},
            {"mailbox_thread_id": "group-1", "action": "mark_read"},
        ]
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                with (
                    patch("app.services.mailbox_actions._resolve_action_messages", return_value=([], [])),
                    patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, False)),
                    patch("app.services.mailbox_actions._apply_local_action") as apply_local,
                    patch("app.services.mailbox_actions.enqueue_job") as enqueue,
                ):
                    with self.assertRaises(ThreadActionIdempotencyConflict):
                        enqueue_thread_action(
                            settings,
                            user_id="user-1",
                            request=SimpleNamespace(
                                client_action_id="client-1",
                                mailbox_thread_id=mismatch["mailbox_thread_id"],
                                target_message_id=None,
                                action=mismatch["action"],
                                created_at="2026-05-21T09:00:00+00:00",
                            ),
                        )
                    apply_local.assert_not_called()
                    enqueue.assert_not_called()

    def test_worker_resolves_mailbox_group_to_gmail_thread_ids(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action()
        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        detail = MailGroupDetail(
            group=sample_group(),
            messages=[
                sample_message("msg-1", "thread-b"),
                sample_message("msg-2", "thread-a"),
                sample_message("msg-3", "thread-a"),
            ],
        )

        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ) as mark_applying, patch("app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=[]), patch(
            "app.services.mailbox_actions.get_mail_group_detail", return_value=detail
        ), patch(
            "app.services.mailbox_actions.archive_gmail_thread"
        ) as archive, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ) as mark_applied, patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ) as refresh:
            response = run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        self.assertEqual(response.state, "applied")
        mark_applying.assert_called_once_with("postgresql://example/db", user_id="user-1", server_action_id="server-1")
        self.assertEqual([call.args[1] for call in archive.call_args_list], ["thread-a", "thread-b"])
        mark_applied.assert_called_once_with("postgresql://example/db", user_id="user-1", server_action_id="server-1")
        refresh.assert_called_once_with(settings, user_id="user-1", priority=10)

    def test_worker_resolves_canonical_gmail_thread_before_legacy_group_lookup(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = replace(pending_action(action="mark_read"), mailbox_thread_id="thread-a")
        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        thread_messages = [sample_message("msg-1", "thread-a"), sample_message("msg-2", "thread-a")]

        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ), patch(
            "app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=thread_messages
        ) as list_thread, patch(
            "app.services.mailbox_actions.get_mail_group_detail"
        ) as get_detail, patch(
            "app.services.mailbox_actions.mark_gmail_thread_read"
        ) as mark_read, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            response = run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        self.assertEqual(response.state, "applied")
        list_thread.assert_called_once_with("postgresql://example/db", user_id="user-1", gmail_thread_id="thread-a")
        get_detail.assert_not_called()
        mark_read.assert_called_once_with(settings, "thread-a", user_id="user-1")

    def test_terminal_failure_rolls_back_optimistic_labels(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = replace(
            pending_action(state="failed"),
            previous_labels={"msg-1": ["INBOX", "UNREAD"]},
        )
        # A later star action succeeded after the archive was applied locally.
        # Rolling back archive must restore INBOX without removing STARRED.
        optimistic = replace(sample_message("msg-1", "thread-a"), label_ids=["UNREAD", "STARRED"])

        with (
            patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record),
            patch("app.services.mailbox_actions.list_messages_by_ids", return_value=[optimistic]),
            patch("app.services.mailbox_actions.upsert_gmail_messages") as upsert_messages,
            patch("app.services.mailbox_actions.rebuild_touched_mail_groups") as rebuild,
            patch("app.services.mailbox_actions.enqueue_projection_refresh") as refresh,
        ):
            restored = rollback_failed_thread_action(
                settings,
                user_id="user-1",
                server_action_id="server-1",
            )

        self.assertTrue(restored)
        self.assertEqual(
            upsert_messages.call_args.args[1][0].label_ids,
            ["INBOX", "UNREAD", "STARRED"],
        )
        rebuild.assert_called_once_with(
            settings,
            user_id="user-1",
            message_ids=["msg-1"],
            use_ai=False,
        )
        refresh.assert_called_once_with(settings, user_id="user-1", priority=10)

    def test_move_trash_updates_local_labels_and_calls_gmail_trash(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action(action="move_trash")
        detail = MailGroupDetail(group=sample_group(), messages=[sample_message("msg-1", "thread-a")])

        with patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, True)), patch(
            "app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=[]
        ), patch(
            "app.services.mailbox_actions.get_mail_group_detail", return_value=detail
        ), patch(
            "app.services.mailbox_actions.upsert_gmail_messages"
        ) as upsert_messages, patch(
            "app.services.mailbox_actions.rebuild_touched_mail_groups"
        ), patch("app.services.mailbox_actions.enqueue_job"), patch("app.services.mailbox_actions.enqueue_projection_refresh"):
            enqueue_thread_action(
                settings,
                user_id="user-1",
                request=SimpleNamespace(
                    client_action_id="client-1",
                    mailbox_thread_id="group-1",
                    action="move_trash",
                    created_at="2026-05-21T09:00:00+00:00",
                ),
            )

        changed = upsert_messages.call_args.args[1][0]
        self.assertEqual(changed.label_ids, ["UNREAD", "TRASH"])

        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ), patch("app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=[]), patch(
            "app.services.mailbox_actions.get_mail_group_detail", return_value=detail
        ), patch(
            "app.services.mailbox_actions.move_gmail_thread_to_trash"
        ) as move_trash, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            response = run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        self.assertEqual(response.action, "move_trash")
        move_trash.assert_called_once_with(settings, "thread-a", user_id="user-1")

    def test_delete_forever_calls_gmail_delete_before_removing_local_rows(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action(action="delete_forever")
        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        detail = MailGroupDetail(group=sample_group(), messages=[sample_message("msg-1", "thread-a")])

        with patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, True)), patch(
            "app.services.mailbox_actions._apply_local_action"
        ) as apply_local, patch("app.services.mailbox_actions.enqueue_job"), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            enqueue_thread_action(
                settings,
                user_id="user-1",
                request=SimpleNamespace(
                    client_action_id="client-1",
                    mailbox_thread_id="group-1",
                    action="delete_forever",
                    created_at="2026-05-21T09:00:00+00:00",
                ),
            )
        apply_local.assert_not_called()

        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ), patch("app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=[]), patch(
            "app.services.mailbox_actions.get_mail_group_detail", return_value=detail
        ), patch(
            "app.services.mailbox_actions.delete_gmail_thread_forever"
        ) as delete_forever, patch(
            "app.services.mailbox_actions.delete_gmail_messages", return_value=["group-1"]
        ) as delete_messages, patch(
            "app.services.mailbox_actions.prune_empty_mail_groups"
        ) as prune_groups, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            response = run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        self.assertEqual(response.action, "delete_forever")
        delete_forever.assert_called_once_with(settings, "thread-a", user_id="user-1")
        delete_messages.assert_called_once_with("postgresql://example/db", user_id="user-1", message_ids=["msg-1"])
        prune_groups.assert_called_once_with("postgresql://example/db", user_id="user-1", group_ids=["group-1"])

    def test_delete_forever_treats_gmail_not_found_as_crash_recovery_success(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = pending_action(action="delete_forever")
        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        detail = MailGroupDetail(group=sample_group(), messages=[sample_message("msg-1", "thread-a")])
        missing = HttpError(Response({"status": "404"}), b"already deleted")

        with (
            patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record),
            patch("app.services.mailbox_actions.mark_pending_thread_action_applying"),
            patch("app.services.mailbox_actions.list_messages_for_gmail_thread", return_value=[]),
            patch("app.services.mailbox_actions.get_mail_group_detail", return_value=detail),
            patch("app.services.mailbox_actions.delete_gmail_thread_forever", side_effect=missing),
            patch("app.services.mailbox_actions.delete_gmail_messages", return_value=["group-1"]) as delete_messages,
            patch("app.services.mailbox_actions.prune_empty_mail_groups"),
            patch("app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied),
            patch("app.services.mailbox_actions.enqueue_projection_refresh"),
        ):
            response = run_pending_thread_action(
                settings,
                user_id="user-1",
                server_action_id="server-1",
            )

        self.assertEqual(response.state, "applied")
        delete_messages.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            message_ids=["msg-1"],
        )

    def test_target_message_action_only_trashes_selected_message(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = replace(pending_action(action="move_trash"), target_message_id="msg-2")
        selected = sample_message("msg-2", "thread-b")

        with patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, True)), patch(
            "app.services.mailbox_actions.list_messages_by_ids", return_value=[selected]
        ) as list_by_ids, patch("app.services.mailbox_actions.upsert_gmail_messages") as upsert_messages, patch(
            "app.services.mailbox_actions.rebuild_touched_mail_groups"
        ), patch("app.services.mailbox_actions.enqueue_job"), patch("app.services.mailbox_actions.enqueue_projection_refresh"):
            enqueue_thread_action(
                settings,
                user_id="user-1",
                request=SimpleNamespace(
                    client_action_id="client-1",
                    mailbox_thread_id="group-1",
                    target_message_id="msg-2",
                    action="move_trash",
                    created_at="2026-05-21T09:00:00+00:00",
                ),
            )

        list_by_ids.assert_called_once_with("postgresql://example/db", user_id="user-1", message_ids=["msg-2"])
        self.assertEqual([message.message_id for message in upsert_messages.call_args.args[1]], ["msg-2"])

        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ), patch("app.services.mailbox_actions.list_messages_by_ids", return_value=[selected]), patch(
            "app.services.mailbox_actions.move_gmail_message_to_trash"
        ) as move_message, patch(
            "app.services.mailbox_actions.move_gmail_thread_to_trash"
        ) as move_thread, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        move_message.assert_called_once_with(settings, "msg-2", user_id="user-1")
        move_thread.assert_not_called()

    def test_target_message_mark_read_only_marks_selected_message(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        record = replace(pending_action(action="mark_read"), target_message_id="msg-2")
        selected = sample_message("msg-2", "thread-b")

        with patch("app.services.mailbox_actions.upsert_pending_thread_action", return_value=(record, True)), patch(
            "app.services.mailbox_actions.list_messages_by_ids", return_value=[selected]
        ) as list_by_ids, patch("app.services.mailbox_actions.upsert_gmail_messages") as upsert_messages, patch(
            "app.services.mailbox_actions.rebuild_touched_mail_groups"
        ), patch("app.services.mailbox_actions.enqueue_job"), patch("app.services.mailbox_actions.enqueue_projection_refresh"):
            enqueue_thread_action(
                settings,
                user_id="user-1",
                request=SimpleNamespace(
                    client_action_id="client-1",
                    mailbox_thread_id="group-1",
                    target_message_id="msg-2",
                    action="mark_read",
                    created_at="2026-05-21T09:00:00+00:00",
                ),
            )

        list_by_ids.assert_called_once_with("postgresql://example/db", user_id="user-1", message_ids=["msg-2"])
        self.assertEqual(upsert_messages.call_args.args[1][0].label_ids, ["INBOX"])

        applied = replace(record, state="applied", applied_at="2026-05-21T09:01:00+00:00")
        with patch("app.services.mailbox_actions.get_pending_thread_action", return_value=record), patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applying"
        ), patch("app.services.mailbox_actions.list_messages_by_ids", return_value=[selected]), patch(
            "app.services.mailbox_actions.mark_gmail_message_read"
        ) as mark_message, patch(
            "app.services.mailbox_actions.mark_gmail_thread_read"
        ) as mark_thread, patch(
            "app.services.mailbox_actions.mark_pending_thread_action_applied", return_value=applied
        ), patch(
            "app.services.mailbox_actions.enqueue_projection_refresh"
        ):
            run_pending_thread_action(settings, user_id="user-1", server_action_id="server-1")

        mark_message.assert_called_once_with(settings, "msg-2", user_id="user-1")
        mark_thread.assert_not_called()


class MailboxThreadActionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.api.routes.mailbox.enqueue_thread_action")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_thread_action_endpoint_queues_mailbox_group_action(self, mock_user: Mock, mock_enqueue: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_enqueue.return_value = {
            "client_action_id": "client-1",
            "server_action_id": "server-1",
            "mailbox_thread_id": "group-1",
            "action": "archive",
            "state": "queued",
            "queued_at": "2026-05-21T09:00:00+00:00",
            "applied_at": None,
            "error": None,
        }

        response = self.client.post(
            "/v1/mailbox/thread-actions",
            json={
                "client_action_id": "client-1",
                "mailbox_thread_id": "group-1",
                "action": "archive",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["server_action_id"], "server-1")
        request = mock_enqueue.call_args.kwargs["request"]
        self.assertEqual(request.mailbox_thread_id, "group-1")
        self.assertEqual(request.action, "archive")

    @patch(
        "app.api.routes.mailbox.enqueue_thread_action",
        side_effect=ThreadActionIdempotencyConflict(
            "client_action_id is already bound to a different mailbox action"
        ),
    )
    @patch("app.api.routes.mailbox.require_current_user", return_value=SimpleNamespace(id="user-1"))
    def test_thread_action_endpoint_rejects_idempotency_payload_mismatch(
        self,
        _mock_user: Mock,
        _mock_enqueue: Mock,
    ) -> None:
        response = self.client.post(
            "/v1/mailbox/thread-actions",
            json={
                "client_action_id": "client-1",
                "mailbox_thread_id": "group-2",
                "action": "mark_read",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
