from __future__ import annotations

import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from app.db.models import StoredGmailMessageSnapshot
from app.db.repository import (
    DEFAULT_USER_ID,
    get_gmail_sync_state,
    initialize_database,
    list_gmail_history_events,
    list_gmail_message_snapshots,
    list_unlinked_source_records,
    upsert_gmail_message_snapshots,
    upsert_gmail_sync_state,
)
from app.services.integrations.google import sync_gmail_source_records


def build_message(
    *,
    message_id: str,
    thread_id: str,
    label_ids: list[str] | None = None,
    history_id: str = "12345",
) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": "1712304000000",
        "snippet": "Snippet body",
        "labelIds": label_ids or ["INBOX"],
        "historyId": history_id,
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Ledger subject"},
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "receiver@example.com"},
                {"name": "Date", "value": "Fri, 05 Apr 2024 10:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {},
        },
    }


class FakeRequest:
    def __init__(self, callback):
        self.callback = callback

    def execute(self):
        return self.callback()


class FakeMessagesResource:
    def __init__(self, service):
        self.service = service

    def list(self, **_kwargs):
        return FakeRequest(
            lambda: {"messages": [{"id": message_id} for message_id in self.service.list_ids]}
        )

    def get(self, *, id: str, **_kwargs):
        def execute():
            self.service.message_gets.append(id)
            if self.service.assert_history_persisted_before_hydration:
                events = list_gmail_history_events(self.service.database_path)
                if not events:
                    raise AssertionError("Gmail history events were not persisted before message hydration")
            if id in self.service.message_errors:
                raise RuntimeError(self.service.message_errors[id])
            return self.service.messages[id]

        return FakeRequest(execute)

    def modify(self, **_kwargs):
        self.service.mutations.append("messages.modify")
        raise AssertionError("Gmail sync must not mutate Gmail messages")


class FakeThreadsResource:
    def __init__(self, service):
        self.service = service

    def get(self, *, id: str, **_kwargs):
        def execute():
            self.service.thread_gets.append(id)
            return {"messages": self.service.threads.get(id, [])}

        return FakeRequest(execute)

    def modify(self, **_kwargs):
        self.service.mutations.append("threads.modify")
        raise AssertionError("Gmail sync must not mutate Gmail threads")


class FakeHistoryResource:
    def __init__(self, service):
        self.service = service

    def list(self, **_kwargs):
        return FakeRequest(lambda: self.service.history_response)


class FakeUsersResource:
    def __init__(self, service):
        self.service = service

    def messages(self):
        return FakeMessagesResource(self.service)

    def threads(self):
        return FakeThreadsResource(self.service)

    def history(self):
        return FakeHistoryResource(self.service)


class FakeGmailService:
    def __init__(
        self,
        *,
        database_path: str,
        list_ids: list[str] | None = None,
        messages: dict[str, dict[str, object]] | None = None,
        threads: dict[str, list[dict[str, object]]] | None = None,
        history_response: dict[str, object] | None = None,
        message_errors: dict[str, str] | None = None,
        assert_history_persisted_before_hydration: bool = False,
    ):
        self.database_path = database_path
        self.list_ids = list_ids or []
        self.messages = messages or {}
        self.threads = threads or {}
        self.history_response = history_response or {"historyId": "0", "history": []}
        self.message_errors = message_errors or {}
        self.assert_history_persisted_before_hydration = assert_history_persisted_before_hydration
        self.message_gets: list[str] = []
        self.thread_gets: list[str] = []
        self.mutations: list[str] = []

    def users(self):
        return FakeUsersResource(self)


class GmailLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_file = tempfile.NamedTemporaryFile(suffix=".sqlite3")
        self.database_path = self.database_file.name
        initialize_database(self.database_path)
        self.settings = SimpleNamespace(
            database_path=self.database_path,
            gmail_sync_scope="full",
            gmail_recent_days=30,
        )

    def tearDown(self) -> None:
        self.database_file.close()

    def test_schema_init_creates_gmail_ledger_tables(self) -> None:
        with sqlite3.connect(self.database_path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'gmail_%'"
                ).fetchall()
            }
            snapshot_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(gmail_message_snapshots)").fetchall()
            }

        self.assertIn("gmail_message_snapshots", tables)
        self.assertIn("gmail_history_events", tables)
        self.assertIn("internal_date", snapshot_columns)
        self.assertIn("label_ids", snapshot_columns)
        self.assertIn("fetch_status", snapshot_columns)
        self.assertIn("tombstoned", snapshot_columns)

    def test_snapshot_upsert_is_idempotent(self) -> None:
        first = StoredGmailMessageSnapshot(
            user_id=DEFAULT_USER_ID,
            message_id="message-1",
            thread_id="thread-1",
            history_id="100",
            internal_date="2024-04-05T10:00:00+00:00",
            label_ids=["INBOX"],
            raw_payload={"id": "message-1", "labelIds": ["INBOX"]},
            fetch_status="fetched",
            tombstoned=False,
            tombstoned_at=None,
            last_fetched_at="2024-04-05T10:00:00+00:00",
            created_at="2024-04-05T10:00:00+00:00",
            updated_at="2024-04-05T10:00:00+00:00",
        )
        second = StoredGmailMessageSnapshot(
            user_id=DEFAULT_USER_ID,
            message_id="message-1",
            thread_id="thread-1",
            history_id="101",
            internal_date="2024-04-05T10:01:00+00:00",
            label_ids=["CATEGORY_UPDATES"],
            raw_payload={"id": "message-1", "labelIds": ["CATEGORY_UPDATES"]},
            fetch_status="fetched",
            tombstoned=False,
            tombstoned_at=None,
            last_fetched_at="2024-04-05T10:01:00+00:00",
            created_at="2024-04-05T10:01:00+00:00",
            updated_at="2024-04-05T10:01:00+00:00",
        )

        upsert_gmail_message_snapshots(self.database_path, [first])
        upsert_gmail_message_snapshots(self.database_path, [second])

        [snapshot] = list_gmail_message_snapshots(self.database_path)
        self.assertEqual(snapshot.message_id, "message-1")
        self.assertEqual(snapshot.history_id, "101")
        self.assertEqual(snapshot.internal_date, "2024-04-05T10:01:00+00:00")
        self.assertEqual(snapshot.label_ids, ["CATEGORY_UPDATES"])

    def test_full_sync_keeps_archived_labels_in_snapshot_and_source_record(self) -> None:
        archived = build_message(
            message_id="message-archived",
            thread_id="thread-1",
            label_ids=["CATEGORY_UPDATES"],
            history_id="200",
        )
        service = FakeGmailService(
            database_path=self.database_path,
            list_ids=["message-archived"],
            messages={"message-archived": archived},
            threads={"thread-1": [archived]},
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual([record.id for record in records], ["message-archived"])
        [snapshot] = list_gmail_message_snapshots(self.database_path)
        self.assertEqual(snapshot.internal_date, "2024-04-05T08:00:00+00:00")
        self.assertEqual(snapshot.label_ids, ["CATEGORY_UPDATES"])
        [source_record] = list_unlinked_source_records(self.database_path)
        self.assertEqual(source_record.raw_payload["label_ids"], ["CATEGORY_UPDATES"])

    def test_incremental_sync_preserves_delete_and_label_history_events(self) -> None:
        upsert_gmail_sync_state(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            last_history_id="100",
            last_full_sync_at="2024-04-05T09:00:00+00:00",
        )
        label_added = build_message(
            message_id="message-label-added",
            thread_id="thread-label",
            label_ids=["INBOX", "IMPORTANT"],
            history_id="105",
        )
        label_removed = build_message(
            message_id="message-label-removed",
            thread_id="thread-label",
            label_ids=["CATEGORY_UPDATES"],
            history_id="106",
        )
        service = FakeGmailService(
            database_path=self.database_path,
            messages={
                "message-label-added": label_added,
                "message-label-removed": label_removed,
            },
            threads={"thread-label": [label_added, label_removed]},
            history_response={
                "historyId": "107",
                "history": [
                    {
                        "id": "105",
                        "labelsAdded": [
                            {
                                "message": {"id": "message-label-added", "threadId": "thread-label"},
                                "labelIds": ["IMPORTANT"],
                            }
                        ],
                    },
                    {
                        "id": "106",
                        "labelsRemoved": [
                            {
                                "message": {"id": "message-label-removed", "threadId": "thread-label"},
                                "labelIds": ["INBOX"],
                            }
                        ],
                        "messagesDeleted": [{"message": {"id": "message-deleted", "threadId": "thread-deleted"}}],
                    },
                ],
            },
            message_errors={"message-deleted": "deleted message cannot be fetched"},
            assert_history_persisted_before_hydration=True,
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual({record.id for record in records}, {"message-label-added", "message-label-removed"})
        events = list_gmail_history_events(self.database_path)
        self.assertEqual(
            [(event.event_type, event.message_id, event.label_ids) for event in events],
            [
                ("labelsAdded", "message-label-added", ["IMPORTANT"]),
                ("labelsRemoved", "message-label-removed", ["INBOX"]),
                ("messagesDeleted", "message-deleted", []),
            ],
        )
        snapshots = {snapshot.message_id: snapshot for snapshot in list_gmail_message_snapshots(self.database_path)}
        self.assertTrue(snapshots["message-deleted"].tombstoned)
        self.assertEqual(snapshots["message-deleted"].fetch_status, "deleted")
        self.assertEqual(get_gmail_sync_state(self.database_path, DEFAULT_USER_ID).last_history_id, "107")

    def test_gmail_sync_does_not_mutate_gmail(self) -> None:
        message = build_message(message_id="message-1", thread_id="thread-1")
        service = FakeGmailService(
            database_path=self.database_path,
            list_ids=["message-1"],
            messages={"message-1": message},
            threads={"thread-1": [message]},
        )

        sync_gmail_source_records(self.settings, service)

        self.assertEqual(service.mutations, [])


if __name__ == "__main__":
    unittest.main()
