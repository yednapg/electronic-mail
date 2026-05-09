from __future__ import annotations

import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from app.db.models import StoredGmailMessageSnapshot, StoredSourceRecord
from app.db.repository import (
    DEFAULT_USER_ID,
    attach_record_to_entity,
    create_entity,
    get_feed_projection_count,
    get_gmail_sync_state,
    initialize_database,
    list_gmail_history_events,
    list_gmail_message_snapshots,
    list_source_records_for_entity,
    list_source_records_by_ids,
    list_unlinked_source_records,
    upsert_feed_projection,
    upsert_gmail_message_snapshots,
    upsert_gmail_sync_state,
    upsert_source_records,
)
from app.services.integrations.google import sync_gmail_source_records
from app.services.integrations.google import persist_gmail_message_id_batch


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

    def list(self, **kwargs):
        def execute():
            page_token = kwargs.get("pageToken")
            self.service.list_calls.append(page_token)
            if page_token in self.service.list_errors:
                raise RuntimeError(self.service.list_errors[page_token])
            if self.service.list_pages is not None:
                return self.service.list_pages.get(page_token, {"messages": []})
            return {"messages": [{"id": message_id} for message_id in self.service.list_ids]}

        return FakeRequest(execute)

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
        list_pages: dict[str | None, dict[str, object]] | None = None,
        list_errors: dict[str | None, str] | None = None,
        messages: dict[str, dict[str, object]] | None = None,
        threads: dict[str, list[dict[str, object]]] | None = None,
        history_response: dict[str, object] | None = None,
        message_errors: dict[str, str] | None = None,
        assert_history_persisted_before_hydration: bool = False,
    ):
        self.database_path = database_path
        self.list_ids = list_ids or []
        self.list_pages = list_pages
        self.list_errors = list_errors or {}
        self.messages = messages or {}
        self.threads = threads or {}
        self.history_response = history_response or {"historyId": "0", "history": []}
        self.message_errors = message_errors or {}
        self.assert_history_persisted_before_hydration = assert_history_persisted_before_hydration
        self.message_gets: list[str] = []
        self.thread_gets: list[str] = []
        self.list_calls: list[str | None] = []
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

    def test_full_sync_processes_paginated_mailbox_pages(self) -> None:
        first = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        second = build_message(message_id="message-2", thread_id="thread-2", history_id="201")
        third = build_message(message_id="message-3", thread_id="thread-3", history_id="202")
        progress: list[tuple[str, int, int | None]] = []
        service = FakeGmailService(
            database_path=self.database_path,
            list_pages={
                None: {
                    "messages": [{"id": "message-1"}, {"id": "message-2"}],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 3,
                },
                "page-2": {
                    "messages": [{"id": "message-3"}],
                    "resultSizeEstimate": 3,
                },
            },
            messages={"message-1": first, "message-2": second, "message-3": third},
            threads={"thread-1": [first], "thread-2": [second], "thread-3": [third]},
        )

        records = sync_gmail_source_records(
            self.settings,
            service,
            progress_callback=lambda stage, imported, total: progress.append((stage, imported, total)),
        )

        self.assertEqual([record.id for record in records], ["message-1", "message-2", "message-3"])
        self.assertEqual(service.list_calls, [None, "page-2"])
        self.assertEqual([snapshot.message_id for snapshot in list_gmail_message_snapshots(self.database_path)], ["message-1", "message-2", "message-3"])
        self.assertIn(("gmail_persisted", 2, 3), progress)
        self.assertIn(("gmail_persisted", 3, 3), progress)
        self.assertEqual(get_gmail_sync_state(self.database_path, DEFAULT_USER_ID).last_history_id, "202")

    def test_full_sync_deduplicates_thread_messages_across_pages(self) -> None:
        first = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        second = build_message(message_id="message-2", thread_id="thread-1", history_id="201")
        progress: list[tuple[str, int, int | None]] = []
        service = FakeGmailService(
            database_path=self.database_path,
            list_pages={
                None: {
                    "messages": [{"id": "message-1"}],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 2,
                },
                "page-2": {
                    "messages": [{"id": "message-2"}],
                    "resultSizeEstimate": 2,
                },
            },
            messages={"message-1": first, "message-2": second},
            threads={"thread-1": [first, second]},
        )

        records = sync_gmail_source_records(
            self.settings,
            service,
            progress_callback=lambda stage, imported, total: progress.append((stage, imported, total)),
        )

        self.assertEqual([record.id for record in records], ["message-1", "message-2"])
        self.assertEqual([snapshot.message_id for snapshot in list_gmail_message_snapshots(self.database_path)], ["message-1", "message-2"])
        self.assertIn(("gmail_persisted", 1, 2), progress)
        self.assertIn(("gmail_persisted", 2, 2), progress)

    def test_full_sync_fetches_thread_once_when_message_ids_span_pages(self) -> None:
        first = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        second = build_message(message_id="message-2", thread_id="thread-1", history_id="201")
        third = build_message(message_id="message-3", thread_id="thread-1", history_id="202")
        service = FakeGmailService(
            database_path=self.database_path,
            list_pages={
                None: {
                    "messages": [{"id": "message-1"}],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 3,
                },
                "page-2": {
                    "messages": [{"id": "message-2"}],
                    "nextPageToken": "page-3",
                    "resultSizeEstimate": 3,
                },
                "page-3": {
                    "messages": [{"id": "message-3"}],
                    "resultSizeEstimate": 3,
                },
            },
            messages={"message-1": first, "message-2": second, "message-3": third},
            threads={"thread-1": [first, second, third]},
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual(service.list_calls, [None, "page-2", "page-3"])
        self.assertEqual(service.message_gets, ["message-1", "message-2", "message-3"])
        self.assertEqual(service.thread_gets, ["thread-1"])
        self.assertEqual([record.id for record in records], ["message-1", "message-2", "message-3"])
        self.assertEqual(
            [snapshot.message_id for snapshot in list_gmail_message_snapshots(self.database_path)],
            ["message-1", "message-2", "message-3"],
        )
        self.assertEqual(get_gmail_sync_state(self.database_path, DEFAULT_USER_ID).last_history_id, "202")

    def test_collect_records_false_returns_only_changed_records_for_hydration(self) -> None:
        old_record = StoredSourceRecord(
            id="old-message",
            source="gmail",
            thread_id="old-thread",
            subject="Old message",
            sender="old@example.com",
            timestamp="2024-04-04T08:00:00+00:00",
            raw_payload={"message_id": "old-message", "subject": "Old message"},
            created_at="2024-04-04T08:00:00+00:00",
        )
        upsert_source_records(self.database_path, [old_record])
        message = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        service = FakeGmailService(
            database_path=self.database_path,
            list_ids=["message-1"],
            messages={"message-1": message},
            threads={"thread-1": [message]},
        )

        records = sync_gmail_source_records(self.settings, service, collect_records=False)

        self.assertEqual([record.id for record in records], ["message-1"])
        self.assertEqual(
            {record.id for record in list_unlinked_source_records(self.database_path)},
            {"old-message", "message-1"},
        )

    def test_batch_persistence_is_idempotent(self) -> None:
        message = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        service = FakeGmailService(
            database_path=self.database_path,
            messages={"message-1": message},
            threads={"thread-1": [message]},
        )

        first = persist_gmail_message_id_batch(
            self.settings,
            service,
            ["message-1"],
            scope="full",
            recent_days=30,
        )
        second = persist_gmail_message_id_batch(
            self.settings,
            service,
            ["message-1"],
            scope="full",
            recent_days=30,
        )

        self.assertEqual(first.new_count, 1)
        self.assertEqual(second.new_count, 0)
        self.assertEqual([record.id for record in list_unlinked_source_records(self.database_path)], ["message-1"])
        self.assertEqual([snapshot.message_id for snapshot in list_gmail_message_snapshots(self.database_path)], ["message-1"])

    def test_full_sync_preserves_committed_pages_when_later_page_fails(self) -> None:
        message = build_message(message_id="message-1", thread_id="thread-1", history_id="200")
        service = FakeGmailService(
            database_path=self.database_path,
            list_pages={
                None: {
                    "messages": [{"id": "message-1"}],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 2,
                },
            },
            list_errors={"page-2": "page failed"},
            messages={"message-1": message},
            threads={"thread-1": [message]},
        )

        with self.assertRaisesRegex(RuntimeError, "page failed"):
            sync_gmail_source_records(self.settings, service)

        self.assertEqual([record.id for record in list_unlinked_source_records(self.database_path)], ["message-1"])
        self.assertEqual([snapshot.message_id for snapshot in list_gmail_message_snapshots(self.database_path)], ["message-1"])
        self.assertIsNone(get_gmail_sync_state(self.database_path, DEFAULT_USER_ID))

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

    def test_incremental_delete_removes_local_work_projection_without_mutating_gmail(self) -> None:
        upsert_gmail_sync_state(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            last_history_id="100",
            last_full_sync_at="2024-04-05T09:00:00+00:00",
        )
        source_record = StoredSourceRecord(
            id="message-deleted",
            source="gmail",
            thread_id="thread-deleted",
            subject="Deleted task",
            sender="sender@example.com",
            timestamp="2024-04-05T10:00:00+00:00",
            raw_payload={"user_id": DEFAULT_USER_ID, "message_id": "message-deleted", "subject": "Deleted task"},
            created_at="2024-04-05T10:00:00+00:00",
        )
        upsert_source_records(self.database_path, [source_record])
        entity = create_entity(self.database_path, "gmail-thread:thread-deleted")
        attach_record_to_entity(self.database_path, entity.id, source_record.id)
        upsert_feed_projection(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            entity_id=entity.id,
            pipeline_output={"entity": {"id": entity.id}, "attention_item": None, "suppressed": True},
        )
        service = FakeGmailService(
            database_path=self.database_path,
            history_response={
                "historyId": "107",
                "history": [
                    {
                        "id": "106",
                        "messagesDeleted": [{"message": {"id": "message-deleted", "threadId": "thread-deleted"}}],
                    }
                ],
            },
            message_errors={"message-deleted": "deleted message cannot be fetched"},
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual(records, [])
        self.assertEqual(list_source_records_by_ids(self.database_path, ["message-deleted"]), [])
        self.assertEqual(list_unlinked_source_records(self.database_path), [])
        self.assertEqual(get_feed_projection_count(self.database_path), 0)
        [snapshot] = list_gmail_message_snapshots(self.database_path)
        self.assertTrue(snapshot.tombstoned)
        self.assertEqual(service.mutations, [])

    def test_incremental_delete_keeps_multi_message_entity_visible(self) -> None:
        upsert_gmail_sync_state(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            last_history_id="100",
            last_full_sync_at="2024-04-05T09:00:00+00:00",
        )
        deleted_record = StoredSourceRecord(
            id="message-deleted",
            source="gmail",
            thread_id="thread-order",
            subject="Order shipped",
            sender="sender@example.com",
            timestamp="2024-04-05T10:00:00+00:00",
            raw_payload={"user_id": DEFAULT_USER_ID, "message_id": "message-deleted", "subject": "Order shipped"},
            created_at="2024-04-05T10:00:00+00:00",
        )
        active_record = StoredSourceRecord(
            id="message-active",
            source="gmail",
            thread_id="thread-order",
            subject="Order delivered",
            sender="sender@example.com",
            timestamp="2024-04-06T10:00:00+00:00",
            raw_payload={"user_id": DEFAULT_USER_ID, "message_id": "message-active", "subject": "Order delivered"},
            created_at="2024-04-06T10:00:00+00:00",
        )
        upsert_source_records(self.database_path, [deleted_record, active_record])
        entity = create_entity(self.database_path, "gmail-thread:thread-order")
        attach_record_to_entity(self.database_path, entity.id, deleted_record.id)
        attach_record_to_entity(self.database_path, entity.id, active_record.id)
        upsert_feed_projection(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            entity_id=entity.id,
            pipeline_output={"entity": {"id": entity.id}, "attention_item": None, "suppressed": True},
        )
        service = FakeGmailService(
            database_path=self.database_path,
            history_response={
                "historyId": "107",
                "history": [
                    {
                        "id": "106",
                        "messagesDeleted": [{"message": {"id": "message-deleted", "threadId": "thread-order"}}],
                    }
                ],
            },
            message_errors={"message-deleted": "deleted message cannot be fetched"},
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual(records, [])
        self.assertEqual(list_source_records_by_ids(self.database_path, ["message-deleted"]), [])
        self.assertEqual([record.id for record in list_source_records_for_entity(self.database_path, entity.id)], ["message-active"])
        self.assertEqual(get_feed_projection_count(self.database_path), 1)
        self.assertEqual(service.mutations, [])

    def test_incremental_sync_keeps_cursor_when_changed_message_fetch_fails(self) -> None:
        upsert_gmail_sync_state(
            self.database_path,
            user_id=DEFAULT_USER_ID,
            last_history_id="100",
            last_full_sync_at="2024-04-05T09:00:00+00:00",
        )
        service = FakeGmailService(
            database_path=self.database_path,
            history_response={
                "historyId": "107",
                "history": [
                    {
                        "id": "105",
                        "messagesAdded": [
                            {
                                "message": {
                                    "id": "message-transient-failure",
                                    "threadId": "thread-transient-failure",
                                },
                            }
                        ],
                    }
                ],
            },
            message_errors={"message-transient-failure": "temporary fetch failure"},
        )

        records = sync_gmail_source_records(self.settings, service)

        self.assertEqual(records, [])
        self.assertEqual(service.message_gets, ["message-transient-failure"])
        self.assertEqual(list_gmail_message_snapshots(self.database_path), [])
        self.assertEqual(list_unlinked_source_records(self.database_path), [])
        [event] = list_gmail_history_events(self.database_path)
        self.assertEqual((event.event_type, event.message_id), ("messagesAdded", "message-transient-failure"))
        sync_state = get_gmail_sync_state(self.database_path, DEFAULT_USER_ID)
        self.assertEqual(sync_state.last_history_id, "100")
        self.assertEqual(sync_state.last_full_sync_at, "2024-04-05T09:00:00+00:00")

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
