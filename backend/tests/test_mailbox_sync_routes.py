from __future__ import annotations

from base64 import urlsafe_b64encode
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import mailbox as mailbox_routes
from app.main import app
from app.schemas.domain import MailboxSyncStateResponse
from app.services.mailbox_events import format_sse_event, parse_last_event_id


class MailboxSyncRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_sync_now_returns_not_connected_when_credentials_are_missing(self) -> None:
        state = MailboxSyncStateResponse(connected=True, total_threads=12)

        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "build_mailbox_sync_state", return_value=state),
            patch.object(mailbox_routes, "ensure_gmail_watch", side_effect=RuntimeError("Google credentials are not connected")),
            patch.object(mailbox_routes, "run_gmail_delta_sync") as mock_delta,
            patch.object(mailbox_routes, "run_gmail_import_batch") as mock_import,
        ):
            response = self.client.post("/v1/mailbox/sync-now")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_connected")
        mock_delta.assert_not_called()
        mock_import.assert_not_called()

    def test_pubsub_route_requires_push_token_in_production(self) -> None:
        production_settings = SimpleNamespace(
            is_production_like=True,
            resolved_gmail_pubsub_push_audience="https://backend.example.com/v1/mailbox/pubsub",
            gmail_pubsub_push_service_account_email="",
            database_path="postgresql://example/db",
        )
        with patch.object(mailbox_routes, "settings", production_settings):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data()}})

        self.assertEqual(response.status_code, 401)

    def test_pubsub_route_accepts_payload_and_enqueues_sync(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with (
            patch.object(mailbox_routes, "settings", local_settings),
            patch.object(mailbox_routes, "get_user_by_email", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "enqueue_job", return_value=SimpleNamespace(id="job-1")) as enqueue,
        ):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data(history_id="123")}})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "queued", "job_id": "job-1"})
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["kind"], "gmail_pubsub_sync")
        self.assertEqual(enqueue.call_args.kwargs["dedupe_key"], "gmail-pubsub:user-1:123")

    def test_pubsub_route_rejects_missing_gmail_history_payload(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with patch.object(mailbox_routes, "settings", local_settings):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data(history_id="")}})

        self.assertEqual(response.status_code, 400)

    def test_pubsub_route_ignores_unknown_user(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with (
            patch.object(mailbox_routes, "settings", local_settings),
            patch.object(mailbox_routes, "get_user_by_email", return_value=None),
            patch.object(mailbox_routes, "enqueue_job") as enqueue,
        ):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data()}})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "ignored")
        enqueue.assert_not_called()

    def test_sse_format_and_last_event_id_parser(self) -> None:
        self.assertEqual(parse_last_event_id("42"), 42)
        self.assertIsNone(parse_last_event_id("not-a-number"))
        event = format_sse_event("mailbox-changed", {"ok": True}, event_id=42)
        self.assertIn("id: 42\n", event)
        self.assertIn("event: mailbox-changed\n", event)
        self.assertIn('data: {"ok":true}', event)


def _pubsub_data(email: str = "me@example.com", history_id: str = "123") -> str:
    payload = {"emailAddress": email, "historyId": history_id}
    return urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


if __name__ == "__main__":
    unittest.main()
