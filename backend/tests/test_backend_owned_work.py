from __future__ import annotations

from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import entities as entity_routes
from app.api.routes import gmail as gmail_routes
from app.api.routes import tasks as task_routes
from app.db.repository import initialize_database, list_trace_records_for_entity
from app.main import app
from app.services.feed.memory_pipeline import build_feed_from_entities, rebuild_persistent_memory


class BackendOwnedWorkRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db")
        initialize_database(self.db_file.name)
        settings = SimpleNamespace(database_path=self.db_file.name, google_configured=True)
        self.patches = [
            patch.object(task_routes, "settings", settings),
            patch.object(entity_routes, "settings", settings),
            patch.object(gmail_routes, "settings", settings),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.db_file.close)
        self.client = TestClient(app)

    def test_manual_task_is_backend_owned_and_completion_suppresses_feed_item(self) -> None:
        create_response = self.client.post(
            "/v1/tasks",
            json={"title": "Review launch notes", "notes": "Check blockers", "section": "today"},
        )

        self.assertEqual(create_response.status_code, 200)
        task = create_response.json()
        self.assertEqual(task["title"], "Review launch notes")
        self.assertEqual(task["status"], "open")

        initial_feed = build_feed_from_entities(self.db_file.name, "2026-05-05T00:00:00+00:00")
        self.assertEqual([item.entity_id for item in initial_feed.today], [task["entity_id"]])

        complete_response = self.client.post(f"/v1/entities/{task['entity_id']}/complete", json={})

        self.assertEqual(complete_response.status_code, 200)
        self.assertEqual(complete_response.json()["outcome_type"], "complete")

        completed_feed = build_feed_from_entities(self.db_file.name, "2026-05-05T00:00:00+00:00")
        self.assertEqual(completed_feed.now, [])
        self.assertEqual(completed_feed.today, [])
        self.assertEqual(completed_feed.worth_knowing, [])

    def test_rebuild_memory_preserves_manual_tasks(self) -> None:
        create_response = self.client.post(
            "/v1/tasks",
            json={"title": "Keep this task", "section": "today"},
        )
        task = create_response.json()

        rebuild_persistent_memory(self.db_file.name)
        feed = build_feed_from_entities(self.db_file.name, "2026-05-05T00:00:00+00:00")

        self.assertEqual([item.entity_id for item in feed.today], [task["entity_id"]])

    def test_read_only_feed_build_does_not_append_trace_rows(self) -> None:
        create_response = self.client.post(
            "/v1/tasks",
            json={"title": "Trace only during prep", "section": "today"},
        )
        task = create_response.json()

        feed = build_feed_from_entities(
            self.db_file.name,
            "2026-05-05T00:00:00+00:00",
            record_trace=False,
        )

        self.assertEqual([item.entity_id for item in feed.today], [task["entity_id"]])
        self.assertEqual(list_trace_records_for_entity(self.db_file.name, task["entity_id"]), [])

        build_feed_from_entities(self.db_file.name, "2026-05-05T00:00:00+00:00")

        self.assertGreater(len(list_trace_records_for_entity(self.db_file.name, task["entity_id"])), 0)

    @patch("app.api.routes.gmail.create_gmail_draft")
    def test_gmail_draft_creation_is_explicit_and_persisted(self, mock_create_draft: Mock) -> None:
        mock_create_draft.return_value = {
            "id": "gmail-draft-1",
            "message": {"id": "message-1", "threadId": "thread-1"},
        }

        response = self.client.post(
            "/v1/gmail/drafts",
            json={
                "to": "person@example.com",
                "subject": "Hello",
                "body": "Draft body",
                "thread_id": "thread-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["gmail_draft_id"], "gmail-draft-1")
        self.assertEqual(payload["status"], "draft")
        mock_create_draft.assert_called_once()


if __name__ == "__main__":
    unittest.main()
