from __future__ import annotations

from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import entities as entity_routes
from app.db.models import StoredSourceRecord
from app.db.repository import attach_record_to_entity, create_entity, initialize_database, upsert_source_records
from app.main import app


class EntityThreadReaderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db")
        initialize_database(self.db_file.name)
        settings = SimpleNamespace(database_path=self.db_file.name, google_configured=True)
        self.settings_patcher = patch.object(entity_routes, "settings", settings)
        self.settings_patcher.start()
        self.addCleanup(self.settings_patcher.stop)
        self.addCleanup(self.db_file.close)
        self.client = TestClient(app)

    def test_thread_reader_returns_persisted_messages_in_chronological_order(self) -> None:
        older = StoredSourceRecord(
            id="message-older",
            source="gmail",
            thread_id="thread-1",
            subject="Apple order placed",
            sender="orders@apple.com",
            timestamp="2023-05-05T09:00:00+00:00",
            raw_payload={
                "user_id": "local-user",
                "from": "orders@apple.com",
                "to": "gaurav@example.com",
                "subject": "Apple order placed",
                "body": "Your iPhone order was placed.",
                "snippet": "Order placed",
                "label_ids": ["INBOX"],
            },
            created_at="2023-05-05T09:00:00+00:00",
        )
        newer = StoredSourceRecord(
            id="message-newer",
            source="gmail",
            thread_id="thread-1",
            subject="Apple order delivered",
            sender="orders@apple.com",
            timestamp="2023-05-14T17:30:00+00:00",
            raw_payload={
                "user_id": "local-user",
                "from": "orders@apple.com",
                "to": "gaurav@example.com",
                "subject": "Apple order delivered",
                "body": "Your iPhone order was delivered.",
                "snippet": "Delivered",
                "label_ids": ["INBOX", "CATEGORY_UPDATES"],
            },
            created_at="2023-05-14T17:30:00+00:00",
        )

        upsert_source_records(self.db_file.name, [newer, older])
        entity = create_entity(self.db_file.name, "gmail-thread:thread-1")
        attach_record_to_entity(self.db_file.name, entity.id, newer.id)
        attach_record_to_entity(self.db_file.name, entity.id, older.id)

        response = self.client.get(f"/v1/entities/{entity.id}/thread")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["entity_id"], entity.id)
        self.assertEqual(payload["gmail_thread_id"], "thread-1")
        self.assertEqual(payload["subject"], "Apple order delivered")
        self.assertEqual(payload["total_messages"], 2)
        self.assertEqual(payload["limit"], 25)
        self.assertEqual(payload["offset"], 0)
        self.assertFalse(payload["has_more"])
        self.assertEqual([message["id"] for message in payload["messages"]], ["message-older", "message-newer"])
        self.assertEqual(payload["messages"][0]["body"], "Your iPhone order was placed.")
        self.assertEqual(payload["messages"][1]["label_ids"], ["INBOX", "CATEGORY_UPDATES"])

    def test_thread_reader_paginates_large_threads_without_fetching_every_message(self) -> None:
        records = [
            StoredSourceRecord(
                id=f"message-{index}",
                source="gmail",
                thread_id="thread-1",
                subject=f"Message {index}",
                sender="orders@apple.com",
                timestamp=f"2023-05-{index + 1:02d}T09:00:00+00:00",
                raw_payload={
                    "user_id": "local-user",
                    "from": "orders@apple.com",
                    "to": "gaurav@example.com",
                    "subject": f"Message {index}",
                    "body": f"Body {index}",
                    "snippet": f"Snippet {index}",
                    "label_ids": ["INBOX"],
                },
                created_at=f"2023-05-{index + 1:02d}T09:00:00+00:00",
            )
            for index in range(4)
        ]

        upsert_source_records(self.db_file.name, records)
        entity = create_entity(self.db_file.name, "gmail-thread:thread-1")
        for record in records:
            attach_record_to_entity(self.db_file.name, entity.id, record.id)

        response = self.client.get(f"/v1/entities/{entity.id}/thread?limit=2&offset=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_messages"], 4)
        self.assertEqual(payload["limit"], 2)
        self.assertEqual(payload["offset"], 1)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["subject"], "Message 3")
        self.assertEqual([message["id"] for message in payload["messages"]], ["message-1", "message-2"])

    def test_thread_reader_can_scope_to_one_gmail_thread_inside_entity(self) -> None:
        first_thread = StoredSourceRecord(
            id="message-thread-1",
            source="gmail",
            thread_id="thread-1",
            subject="First thread",
            sender="sender@example.com",
            timestamp="2026-05-11T09:00:00+00:00",
            raw_payload={"user_id": "local-user", "subject": "First thread", "body": "First body."},
            created_at="2026-05-11T09:00:00+00:00",
        )
        second_thread = StoredSourceRecord(
            id="message-thread-2",
            source="gmail",
            thread_id="thread-2",
            subject="Second thread",
            sender="sender@example.com",
            timestamp="2026-05-12T09:00:00+00:00",
            raw_payload={"user_id": "local-user", "subject": "Second thread", "body": "Second body."},
            created_at="2026-05-12T09:00:00+00:00",
        )

        upsert_source_records(self.db_file.name, [first_thread, second_thread])
        entity = create_entity(self.db_file.name, "sender:sender@example.com")
        attach_record_to_entity(self.db_file.name, entity.id, first_thread.id)
        attach_record_to_entity(self.db_file.name, entity.id, second_thread.id)

        response = self.client.get(f"/v1/entities/{entity.id}/thread?threadId=thread-2")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["gmail_thread_id"], "thread-2")
        self.assertEqual(payload["subject"], "Second thread")
        self.assertEqual(payload["total_messages"], 1)
        self.assertEqual([message["id"] for message in payload["messages"]], ["message-thread-2"])

    def test_thread_reader_rejects_unbounded_page_size(self) -> None:
        entity = create_entity(self.db_file.name, "gmail-thread:thread-1")

        response = self.client.get(f"/v1/entities/{entity.id}/thread?limit=500")

        self.assertEqual(response.status_code, 422)

    def test_unknown_entity_returns_404(self) -> None:
        response = self.client.get("/v1/entities/missing/thread")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Entity not found")


if __name__ == "__main__":
    unittest.main()
