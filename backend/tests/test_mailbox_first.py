from __future__ import annotations

from base64 import urlsafe_b64encode
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db.models import StoredGmailMessageSnapshot
from app.db.repository import (
    DEFAULT_USER_ID,
    get_gmail_sync_state,
    initialize_database,
    upsert_user,
    upsert_gmail_message_snapshots,
)
from app.services.gmail_view import build_gmail_view_response
from app.services.mailbox import build_mailbox_response, build_mailbox_thread_response
from app.services.mailbox_sync import decode_pubsub_notification, ensure_gmail_watch, handle_pubsub_notification


def build_snapshot(
    *,
    message_id: str,
    thread_id: str,
    label_ids: list[str],
    subject: str = "Snapshot subject",
    sender: str = "Sender <sender@example.com>",
    body: str = "Snapshot body",
    internal_date: str = "2026-05-13T10:00:00+00:00",
    tombstoned: bool = False,
) -> StoredGmailMessageSnapshot:
    return StoredGmailMessageSnapshot(
        user_id=DEFAULT_USER_ID,
        message_id=message_id,
        thread_id=thread_id,
        history_id="history-1",
        internal_date=internal_date,
        label_ids=label_ids,
        raw_payload={
            "id": message_id,
            "threadId": thread_id,
            "subject": subject,
            "from": sender,
            "to": "receiver@example.com",
            "snippet": body,
            "body": body,
            "labelIds": label_ids,
        },
        fetch_status="deleted" if tombstoned else "fetched",
        tombstoned=tombstoned,
        tombstoned_at=internal_date if tombstoned else None,
        last_fetched_at=internal_date,
        created_at=internal_date,
        updated_at=internal_date,
    )


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeUsers:
    def watch(self, **_kwargs):
        return FakeRequest({"historyId": "12345", "expiration": "1778668800000"})


class FakeGmailService:
    def users(self):
        return FakeUsers()


class MailboxFirstTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.database_path = Path(self.tmp_dir.name) / "mailbox.db"
        initialize_database(str(self.database_path))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_gmail_view_reads_snapshots_without_source_records(self) -> None:
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [
                build_snapshot(
                    message_id="message-1",
                    thread_id="thread-1",
                    label_ids=["INBOX", "UNREAD"],
                    subject="Pay the card bill",
                    body="Your payment is due today.",
                )
            ],
        )

        gmail = build_gmail_view_response(
            str(self.database_path),
            current_time="2026-05-13T12:00:00+00:00",
        )

        self.assertEqual(gmail.total_threads, 1)
        row = gmail.sections[0].rows[0]
        self.assertEqual(row.thread_id, "thread-1")
        self.assertIsNone(row.entity_id)
        self.assertEqual(row.latest_source_record_id, "message-1")
        self.assertEqual(row.latest_subject, "Pay the card bill")
        self.assertEqual(row.label_ids, ["INBOX", "UNREAD"])
        self.assertTrue(row.unread)

    def test_initialize_backfills_existing_snapshots_into_thread_projections(self) -> None:
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [
                build_snapshot(
                    message_id="message-1",
                    thread_id="thread-1",
                    label_ids=["INBOX"],
                    subject="Already mirrored",
                )
            ],
        )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DELETE FROM gmail_thread_projections")

        initialize_database(str(self.database_path))

        inbox = build_mailbox_response(str(self.database_path), label="inbox")
        self.assertEqual(inbox.total_threads, 1)
        self.assertEqual(inbox.sections[0].rows[0].thread_id, "thread-1")

    def test_mailbox_label_filters_use_gmail_labels(self) -> None:
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [
                build_snapshot(message_id="inbox", thread_id="thread-inbox", label_ids=["INBOX"]),
                build_snapshot(message_id="sent", thread_id="thread-sent", label_ids=["SENT"]),
                build_snapshot(message_id="draft", thread_id="thread-draft", label_ids=["DRAFT"]),
                build_snapshot(message_id="trash", thread_id="thread-trash", label_ids=["TRASH"]),
                build_snapshot(message_id="archive", thread_id="thread-archive", label_ids=["CATEGORY_UPDATES"]),
            ],
        )

        self.assertEqual([row.thread_id for row in build_mailbox_response(str(self.database_path), label="inbox").sections[0].rows], ["thread-inbox"])
        self.assertEqual([row.thread_id for row in build_mailbox_response(str(self.database_path), label="sent").sections[0].rows], ["thread-sent"])
        self.assertEqual([row.thread_id for row in build_mailbox_response(str(self.database_path), label="drafts").sections[0].rows], ["thread-draft"])
        self.assertEqual([row.thread_id for row in build_mailbox_response(str(self.database_path), label="trash").sections[0].rows], ["thread-trash"])
        self.assertEqual([row.thread_id for row in build_mailbox_response(str(self.database_path), label="archive").sections[0].rows], ["thread-archive"])
        self.assertEqual(
            {row.thread_id for section in build_mailbox_response(str(self.database_path), label="all").sections for row in section.rows},
            {"thread-inbox", "thread-sent", "thread-archive"},
        )

    def test_tombstoned_messages_disappear_from_active_mailbox_views(self) -> None:
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [build_snapshot(message_id="message-1", thread_id="thread-1", label_ids=["INBOX"])],
        )
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [build_snapshot(message_id="message-1", thread_id="thread-1", label_ids=["INBOX"], tombstoned=True)],
        )

        inbox = build_mailbox_response(str(self.database_path), label="inbox")

        self.assertEqual(inbox.total_threads, 0)
        self.assertEqual(inbox.sections, [])

    def test_mailbox_thread_reader_does_not_require_entity(self) -> None:
        upsert_gmail_message_snapshots(
            str(self.database_path),
            [
                build_snapshot(
                    message_id="older",
                    thread_id="thread-1",
                    label_ids=["INBOX"],
                    subject="Older",
                    body="Older body",
                    internal_date="2026-05-12T09:00:00+00:00",
                ),
                build_snapshot(
                    message_id="newer",
                    thread_id="thread-1",
                    label_ids=["INBOX"],
                    subject="Newer",
                    body="Newer body",
                    internal_date="2026-05-13T09:00:00+00:00",
                ),
            ],
        )

        thread = build_mailbox_thread_response(str(self.database_path), thread_id="thread-1")

        self.assertEqual(thread.entity_id, "gmail-thread:thread-1")
        self.assertEqual(thread.gmail_thread_id, "thread-1")
        self.assertEqual(thread.subject, "Newer")
        self.assertEqual([message.id for message in thread.messages], ["older", "newer"])
        self.assertEqual(thread.messages[1].body, "Newer body")

    def test_pubsub_decode_and_watch_state_are_persisted(self) -> None:
        encoded = urlsafe_b64encode(b'{"emailAddress":"person@example.com","historyId":"987"}').decode("utf-8").rstrip("=")
        self.assertEqual(
            decode_pubsub_notification({"data": encoded}),
            {"emailAddress": "person@example.com", "historyId": "987"},
        )
        settings = SimpleNamespace(
            database_path=self.database_path,
            app_env="local",
            gmail_pubsub_topic="projects/project/topics/gmail",
        )

        with (
            patch("app.services.mailbox_sync.create_authorized_credentials", return_value=object()),
            patch("app.services.mailbox_sync.build_google_service", return_value=FakeGmailService()),
            patch("app.services.mailbox.get_google_auth_state", return_value=SimpleNamespace(connected=True)),
        ):
            state = ensure_gmail_watch(settings)

        stored = get_gmail_sync_state(str(self.database_path), DEFAULT_USER_ID)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.last_history_id, "12345")
        self.assertIsNotNone(stored.watch_expiration_at)
        self.assertEqual(state.last_history_id, "12345")

    def test_pubsub_notification_syncs_only_notified_user(self) -> None:
        user_a = upsert_user(
            str(self.database_path),
            email="a@example.com",
            google_sub="google-a",
            display_name="A",
        )
        user_b = upsert_user(
            str(self.database_path),
            email="b@example.com",
            google_sub="google-b",
            display_name="B",
        )
        encoded = urlsafe_b64encode(b'{"emailAddress":"b@example.com","historyId":"987"}').decode("utf-8").rstrip("=")
        settings = SimpleNamespace(database_path=self.database_path)

        with patch("app.services.mailbox_sync.queueable_mailbox_sync", return_value=object()) as mock_sync:
            handle_pubsub_notification(settings, {"data": encoded})

        self.assertNotEqual(user_a.id, user_b.id)
        mock_sync.assert_called_once_with(settings, user_id=user_b.id, derive_work=True)


if __name__ == "__main__":
    unittest.main()
