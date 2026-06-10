from __future__ import annotations

from base64 import urlsafe_b64encode
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

from app.api.routes import mailbox as mailbox_routes
from app.main import app
from app.schemas.domain import MailboxRealtimeStateResponse, MailboxSyncStateResponse
from app.services import auth as auth_service
from app.services import mail_groups as mail_group_service
from app.services.integrations import google as google_integration
from app.services.integrations.google import GOOGLE_REAUTH_REQUIRED_MESSAGE, GoogleCredentialStatus
from app.services.mailbox_events import emit_mailbox_event, format_sse_event, parse_last_event_id


class _FakeSettings(SimpleNamespace):
    @property
    def google_configured(self) -> bool:
        return True


class _ExpiredCredentials:
    expired = True
    refresh_token = "refresh-token"

    def refresh(self, request) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked")


class MailboxSyncRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_google_credential_check_reports_revoked_refresh_token(self) -> None:
        settings = _FakeSettings(
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        token_row = SimpleNamespace(token_json_encrypted="encrypted")

        with (
            patch.object(google_integration, "get_google_oauth_token", return_value=token_row),
            patch.object(google_integration, "decrypt_json", return_value={"token": "access-token", "refresh_token": "refresh-token"}),
            patch.object(google_integration.Credentials, "from_authorized_user_info", return_value=_ExpiredCredentials()),
            patch.object(google_integration, "persist_token_payload") as persist,
        ):
            status = google_integration.check_user_google_credentials(settings, user_id="user-1")

        self.assertFalse(status.connected)
        self.assertTrue(status.has_stored_tokens)
        self.assertTrue(status.reauth_required)
        self.assertEqual(status.error, GOOGLE_REAUTH_REQUIRED_MESSAGE)
        persist.assert_not_called()

    def test_sync_state_marks_revoked_credentials_disconnected(self) -> None:
        settings = _FakeSettings(
            database_path="postgresql://example/db",
            gmail_pubsub_topic="projects/example/topics/gmail",
        )

        with (
            patch.object(mail_group_service, "get_import_state", return_value=None),
            patch.object(mail_group_service, "user_can_write_gmail", return_value=True),
            patch.object(
                mail_group_service,
                "check_user_google_credentials",
                return_value=GoogleCredentialStatus(
                    connected=False,
                    has_stored_tokens=True,
                    reauth_required=True,
                    error=GOOGLE_REAUTH_REQUIRED_MESSAGE,
                ),
            ),
            patch.object(mail_group_service, "ensure_background_import_work") as ensure_background,
            patch.object(mail_group_service, "count_mail_groups_by_enrichment_status", return_value={"ready": 1, "pending": 0}),
            patch.object(mail_group_service, "latest_mail_group_ai_error", return_value=None),
            patch.object(mail_group_service, "get_queue_health", return_value=SimpleNamespace(workers=[])),
            patch.object(mail_group_service, "latest_gmail_mailbox_revision", return_value="rev-1"),
            patch.object(mail_group_service, "count_mailbox_threads", return_value=12),
            patch.object(mail_group_service, "count_pending_thread_actions", return_value=0),
        ):
            state = mail_group_service.build_mailbox_sync_state(settings, user_id="user-1")

        self.assertFalse(state.connected)
        self.assertEqual(state.last_sync_error, GOOGLE_REAUTH_REQUIRED_MESSAGE)
        ensure_background.assert_not_called()

    def test_verified_auth_state_reports_reauth_required(self) -> None:
        settings = _FakeSettings(database_path="postgresql://example/db", backend_origin="http://127.0.0.1:3001")
        request = SimpleNamespace(cookies={}, headers={})

        with (
            patch.object(auth_service, "get_current_user", return_value=SimpleNamespace(id="user-1", legacy_local=False)),
            patch.object(auth_service, "get_google_oauth_token", return_value=SimpleNamespace(token_json_encrypted="encrypted")),
            patch.object(
                auth_service,
                "check_user_google_credentials",
                return_value=GoogleCredentialStatus(
                    connected=False,
                    has_stored_tokens=True,
                    reauth_required=True,
                    error=GOOGLE_REAUTH_REQUIRED_MESSAGE,
                ),
            ),
            patch.object(auth_service, "missing_google_scopes") as missing_scopes,
        ):
            state = auth_service.auth_state_for_request(settings, request, verify_google_credentials=True)

        self.assertFalse(state.connected)
        self.assertTrue(state.reauth_required)
        self.assertEqual(state.error, GOOGLE_REAUTH_REQUIRED_MESSAGE)
        self.assertEqual(state.connect_url, "http://127.0.0.1:3001/auth/google")
        missing_scopes.assert_not_called()

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
            patch.object(mailbox_routes, "emit_mailbox_event") as emit_event,
        ):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data(history_id="123")}})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "queued", "job_id": "job-1"})
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["kind"], "gmail_pubsub_sync")
        self.assertEqual(enqueue.call_args.kwargs["dedupe_key"], "gmail-pubsub:user-1:123")
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.kwargs["event_type"], "gmail-pubsub-received")
        self.assertEqual(emit_event.call_args.kwargs["payload"]["history_id"], "123")

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

    def test_emit_mailbox_event_adds_revision_and_label_payload(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        with (
            patch("app.services.mailbox_events.latest_gmail_mailbox_revision", return_value="rev-1"),
            patch("app.services.mailbox_events.insert_mailbox_event", return_value=SimpleNamespace(id=1)) as insert,
        ):
            emit_mailbox_event(settings, user_id="user-1", event_type="mailbox-changed", mailbox_label="inbox", payload={"source": "test"})

        payload = insert.call_args.kwargs["payload"]
        self.assertEqual(payload["mailbox_revision"], "rev-1")
        self.assertEqual(payload["mailbox_labels"], ["inbox"])

    def test_realtime_state_exposes_backend_diagnostics(self) -> None:
        realtime = MailboxRealtimeStateResponse(
            watch_status="active",
            watch_expiration_at="2026-06-06T00:00:00+00:00",
            last_history_id="100",
            last_pubsub_received_at="2026-05-30T00:00:00+00:00",
            last_pubsub_history_id="101",
            last_delta_sync_at="2026-05-30T00:00:01+00:00",
            last_mailbox_event_id=7,
            last_mailbox_event_at="2026-05-30T00:00:02+00:00",
            last_mailbox_event_type="mailbox-changed",
            mailbox_revision="2026-05-30T00:00:02+00:00",
            poller_online=True,
            total_threads=12,
            last_sync_error=None,
        )
        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "build_mailbox_realtime_state", return_value=realtime),
        ):
            response = self.client.get("/v1/mailbox/realtime-state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_pubsub_history_id"], "101")
        self.assertEqual(response.json()["last_mailbox_event_id"], 7)


def _pubsub_data(email: str = "me@example.com", history_id: str = "123") -> str:
    payload = {"emailAddress": email, "historyId": history_id}
    return urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


if __name__ == "__main__":
    unittest.main()
