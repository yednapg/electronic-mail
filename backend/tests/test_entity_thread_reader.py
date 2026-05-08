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
            sender="orders@fruitco.example",
            timestamp="2023-05-05T09:00:00+00:00",
            raw_payload={
                "user_id": "local-user",
                "from": "orders@fruitco.example",
                "to": "demo@example.test",
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
            sender="orders@fruitco.example",
            timestamp="2023-05-14T17:30:00+00:00",
            raw_payload={
                "user_id": "local-user",
                "from": "orders@fruitco.example",
                "to": "demo@example.test",
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
        self.assertEqual([message["id"] for message in payload["messages"]], ["message-older", "message-newer"])
        self.assertEqual(payload["messages"][0]["body"], "Your iPhone order was placed.")
        self.assertEqual(payload["messages"][1]["label_ids"], ["INBOX", "CATEGORY_UPDATES"])

    def test_unknown_entity_returns_404(self) -> None:
        response = self.client.get("/v1/entities/missing/thread")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Entity not found")


if __name__ == "__main__":
    unittest.main()
