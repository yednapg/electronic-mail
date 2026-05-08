from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import feed as feed_routes
from app.main import app


class RawGmailApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.settings_patch = patch.object(
            feed_routes,
            "settings",
            SimpleNamespace(google_configured=True, database_path="test.sqlite3"),
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    @patch("app.api.routes.feed.has_stored_google_tokens", return_value=True)
    @patch("app.api.routes.feed.fetch_raw_gmail_api_messages")
    def test_raw_gmail_api_returns_full_payloads(self, mock_fetch: Mock, _mock_tokens: Mock) -> None:
        mock_fetch.return_value = [
            {
                "id": "message-1",
                "threadId": "thread-1",
                "payload": {"headers": [{"name": "Subject", "value": "Hello"}]},
            }
        ]

        response = self.client.get("/raw-gmail-api")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": "message-1",
                    "threadId": "thread-1",
                    "payload": {"headers": [{"name": "Subject", "value": "Hello"}]},
                }
            ],
        )

    @patch("app.api.routes.feed.has_stored_google_tokens", return_value=False)
    def test_raw_gmail_api_returns_empty_when_not_connected(self, _mock_tokens: Mock) -> None:
        response = self.client.get("/raw-gmail-api")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch("app.api.routes.feed.has_stored_google_tokens", return_value=True)
    @patch("app.api.routes.feed.fetch_clean_gmail_api_messages")
    def test_raw_gmail_api_clean_returns_readable_payloads(self, mock_fetch: Mock, _mock_tokens: Mock) -> None:
        mock_fetch.return_value = [
            {
                "id": "message-1",
                "thread_id": "thread-1",
                "subject": "Hello",
                "body": {"chosen": "Readable body"},
                "parts": [{"part_id": "1", "mime_type": "text/html", "text_preview": "Readable body"}],
            }
        ]

        response = self.client.get("/raw-gmail-api-clean")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": "message-1",
                    "thread_id": "thread-1",
                    "subject": "Hello",
                    "body": {"chosen": "Readable body"},
                    "parts": [{"part_id": "1", "mime_type": "text/html", "text_preview": "Readable body"}],
                }
            ],
        )

    @patch("app.api.routes.feed.build_feed_from_entities")
    @patch("app.api.routes.feed.build_feed_from_projection_cache", return_value=None)
    @patch("app.api.routes.feed.refresh_feed_projections_for_entities")
    @patch("app.api.routes.feed.refresh_ai_suggestions_for_entities")
    @patch("app.api.routes.feed.rebuild_persistent_memory")
    @patch("app.api.routes.feed.refresh_source_record_summaries")
    @patch("app.api.routes.feed.list_source_record_ids", return_value=["record-1"])
    def test_rebuild_memory_recomputes_feed_from_stored_records(
        self,
        mock_list_source_record_ids: Mock,
        mock_refresh_source_summaries: Mock,
        mock_rebuild: Mock,
        mock_refresh: Mock,
        mock_refresh_projection: Mock,
        _mock_projection_cache: Mock,
        mock_build_feed: Mock,
    ) -> None:
        mock_rebuild.return_value = ["entity-1"]
        mock_build_feed.return_value = {"now": [], "today": [], "worth_knowing": []}

        response = self.client.post("/rebuild-memory")

        self.assertEqual(response.status_code, 200)
        mock_list_source_record_ids.assert_called_once()
        mock_refresh_source_summaries.assert_called_once()
        mock_rebuild.assert_called_once()
        mock_refresh.assert_called_once()
        mock_refresh_projection.assert_called_once()
        mock_build_feed.assert_called_once()
        self.assertEqual(response.json(), {"now": [], "today": [], "worth_knowing": []})


if __name__ == "__main__":
    unittest.main()
