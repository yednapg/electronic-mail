from __future__ import annotations

from base64 import urlsafe_b64encode
from contextlib import nullcontext
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

from app.api.routes import mailbox as mailbox_routes
from app.db.user_mail_guard import UserMailWorkBlocked
from app.main import app
from app.schemas.domain import MailboxRealtimeStateResponse, MailboxSyncStateResponse
from app.services import auth as auth_service
from app.services import mail_groups as mail_group_service
from app.services.integrations import google as google_integration
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_WRITE_SCOPE,
    GOOGLE_REAUTH_REQUIRED_MESSAGE,
    GOOGLE_SCOPE_REAUTH_REQUIRED_MESSAGE,
    GoogleCredentialStatus,
)
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
            patch.object(google_integration, "shared_user_mail_lock", return_value=nullcontext()),
            patch.object(google_integration, "get_google_oauth_token", return_value=token_row),
            patch.object(
                google_integration,
                "decrypt_json",
                return_value={"token": "access-token", "refresh_token": "refresh-token", "scopes": [GMAIL_FULL_SCOPE]},
            ),
            patch.object(google_integration.Credentials, "from_authorized_user_info", return_value=_ExpiredCredentials()),
            patch.object(google_integration, "persist_token_payload") as persist,
        ):
            status = google_integration.check_user_google_credentials(settings, user_id="user-1")

        self.assertFalse(status.connected)
        self.assertTrue(status.has_stored_tokens)
        self.assertTrue(status.reauth_required)
        self.assertEqual(status.error, GOOGLE_REAUTH_REQUIRED_MESSAGE)
        persist.assert_not_called()

    def test_missing_scope_metadata_is_not_upgraded_to_full_mail_permission(self) -> None:
        settings = _FakeSettings(
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        token_row = SimpleNamespace(token_json_encrypted="encrypted")

        with (
            patch.object(google_integration, "shared_user_mail_lock", return_value=nullcontext()),
            patch.object(google_integration, "get_google_oauth_token", return_value=token_row),
            patch.object(google_integration, "decrypt_json", return_value={"token": "legacy-access-token", "refresh_token": "legacy-refresh-token"}),
            patch.object(google_integration.Credentials, "from_authorized_user_info") as credentials_from_info,
            patch.object(google_integration, "persist_token_payload") as persist,
        ):
            status = google_integration.check_user_google_credentials(settings, user_id="user-1")

        self.assertFalse(status.connected)
        self.assertTrue(status.reauth_required)
        self.assertEqual(status.error, GOOGLE_SCOPE_REAUTH_REQUIRED_MESSAGE)
        credentials_from_info.assert_not_called()
        persist.assert_not_called()

    def test_legacy_modify_and_send_scopes_require_new_full_mail_consent(self) -> None:
        settings = _FakeSettings(
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        token_row = SimpleNamespace(token_json_encrypted="encrypted")

        with (
            patch.object(google_integration, "shared_user_mail_lock", return_value=nullcontext()),
            patch.object(google_integration, "get_google_oauth_token", return_value=token_row),
            patch.object(
                google_integration,
                "decrypt_json",
                return_value={
                    "token": "legacy-access-token",
                    "refresh_token": "legacy-refresh-token",
                    "scopes": [GMAIL_WRITE_SCOPE, GMAIL_SEND_SCOPE],
                },
            ),
            patch.object(google_integration.Credentials, "from_authorized_user_info") as credentials_from_info,
        ):
            status = google_integration.check_user_google_credentials(settings, user_id="user-1")

        self.assertFalse(status.connected)
        self.assertTrue(status.reauth_required)
        self.assertEqual(status.error, GOOGLE_SCOPE_REAUTH_REQUIRED_MESSAGE)
        credentials_from_info.assert_not_called()

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
            patch.object(mail_group_service, "count_mail_groups_by_enrichment_status") as legacy_ai_counts,
            patch.object(mail_group_service, "latest_mail_group_ai_error") as legacy_ai_error,
            patch.object(mail_group_service, "get_queue_health", return_value=SimpleNamespace(workers=[])),
            patch.object(mail_group_service, "latest_gmail_mailbox_revision", return_value="rev-1"),
            patch.object(mail_group_service, "count_mailbox_threads", return_value=12),
            patch.object(mail_group_service, "count_pending_thread_actions", return_value=0),
        ):
            state = mail_group_service.build_mailbox_sync_state(settings, user_id="user-1")

        self.assertFalse(state.connected)
        self.assertEqual(state.last_sync_error, GOOGLE_REAUTH_REQUIRED_MESSAGE)
        self.assertIsNone(state.last_ai_error)
        ensure_background.assert_not_called()
        legacy_ai_counts.assert_not_called()
        legacy_ai_error.assert_not_called()

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

    def test_verified_auth_state_reports_guarded_retained_token_as_disconnected(self) -> None:
        settings = _FakeSettings(database_path="postgresql://example/db", backend_origin="http://127.0.0.1:3001")
        request = SimpleNamespace(cookies={}, headers={})

        with (
            patch.object(auth_service, "get_current_user", return_value=SimpleNamespace(id="user-1", legacy_local=False)),
            patch.object(auth_service, "get_google_oauth_token", return_value=SimpleNamespace(token_json_encrypted="encrypted")),
            patch.object(
                auth_service,
                "check_user_google_credentials",
                side_effect=UserMailWorkBlocked("Google credentials are no longer connected for this user"),
            ) as check_credentials,
            patch.object(auth_service, "missing_google_scopes") as missing_scopes,
        ):
            state = auth_service.auth_state_for_request(settings, request, verify_google_credentials=True)

        self.assertFalse(state.connected)
        self.assertFalse(state.can_send_mail)
        self.assertTrue(state.reauth_required)
        self.assertEqual(state.error, "Google is disconnected. Please sign in with Google again.")
        self.assertEqual(state.connect_url, "http://127.0.0.1:3001/auth/google")
        check_credentials.assert_called_once_with(settings, user_id="user-1", refresh_expired=True)
        missing_scopes.assert_not_called()

    def test_auth_state_requires_full_mail_scope_for_legacy_tokens(self) -> None:
        settings = _FakeSettings(database_path="postgresql://example/db", backend_origin="http://127.0.0.1:3001")
        request = SimpleNamespace(cookies={}, headers={})

        with (
            patch.object(auth_service, "get_current_user", return_value=SimpleNamespace(id="user-1", legacy_local=False)),
            patch.object(auth_service, "get_google_oauth_token", return_value=SimpleNamespace(token_json_encrypted="encrypted")),
            patch.object(auth_service, "missing_google_scopes", return_value=[GMAIL_FULL_SCOPE]) as missing_scopes,
        ):
            state = auth_service.auth_state_for_request(settings, request)

        self.assertTrue(state.connected)
        self.assertFalse(state.can_send_mail)
        self.assertTrue(state.reauth_required)
        self.assertEqual(state.missing_scopes, [GMAIL_FULL_SCOPE])
        self.assertEqual(state.connect_url, "http://127.0.0.1:3001/auth/google")
        missing_scopes.assert_called_once_with(settings, user_id="user-1", required_scopes=[GMAIL_FULL_SCOPE])

    def test_sync_now_returns_not_connected_when_credentials_are_missing(self) -> None:
        state = MailboxSyncStateResponse(connected=True, total_threads=12)

        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-1")),
            patch.object(mailbox_routes, "build_mailbox_sync_state", return_value=state),
            patch.object(mailbox_routes, "ensure_gmail_watch", side_effect=RuntimeError("Google credentials are not connected")),
            patch.object(mailbox_routes, "run_gmail_delta_sync") as mock_delta,
        ):
            response = self.client.post("/v1/mailbox/sync-now")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_connected")
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(response.headers["pragma"], "no-cache")
        mock_delta.assert_not_called()

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

    def test_pubsub_route_requires_explicitly_verified_service_account_email(self) -> None:
        production_settings = SimpleNamespace(
            is_production_like=True,
            resolved_gmail_pubsub_push_audience="https://backend.example.com/v1/mailbox/pubsub",
            gmail_pubsub_push_service_account_email="pubsub@example.iam.gserviceaccount.com",
            database_path="postgresql://example/db",
        )
        claims = {"email": "pubsub@example.iam.gserviceaccount.com"}
        with (
            patch.object(mailbox_routes, "settings", production_settings),
            patch.object(mailbox_routes.id_token, "verify_oauth2_token", return_value=claims),
        ):
            response = self.client.post(
                "/v1/mailbox/pubsub",
                headers={"Authorization": "Bearer signed-token"},
                json={"message": {"data": _pubsub_data()}},
            )

        self.assertEqual(response.status_code, 403)

    def test_pubsub_route_rejects_oversized_body_before_parsing(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with patch.object(mailbox_routes, "settings", local_settings):
            response = self.client.post(
                "/v1/mailbox/pubsub",
                content=b"x" * (mailbox_routes.MAX_PUBSUB_BODY_BYTES + 1),
                headers={"Content-Type": "application/json"},
            )

        self.assertEqual(response.status_code, 413)

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
        self.assertEqual(
            enqueue.call_args.kwargs["payload"],
            {
                "user_id": "user-1",
                "history_id": "123",
                "batch_size": 100,
                "source": "pubsub",
            },
        )
        emit_event.assert_called_once()
        self.assertEqual(emit_event.call_args.kwargs["event_type"], "gmail-pubsub-received")
        self.assertEqual(emit_event.call_args.kwargs["payload"]["history_id"], "123")

    def test_pubsub_route_ignores_user_whose_mail_guard_closed(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with (
            patch.object(mailbox_routes, "settings", local_settings),
            patch.object(mailbox_routes, "get_user_by_email", return_value=SimpleNamespace(id="user-1")),
            patch.object(
                mailbox_routes,
                "emit_mailbox_event",
                side_effect=mailbox_routes.UserMailWorkBlocked("disconnected"),
            ),
            patch.object(mailbox_routes, "enqueue_job") as enqueue,
        ):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data()}})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "ignored", "reason": "disconnected"})
        enqueue.assert_not_called()

    def test_pubsub_route_rejects_missing_gmail_history_payload(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with patch.object(mailbox_routes, "settings", local_settings):
            response = self.client.post("/v1/mailbox/pubsub", json={"message": {"data": _pubsub_data(history_id="")}})

        self.assertEqual(response.status_code, 400)

    def test_pubsub_route_rejects_non_numeric_gmail_history_id(self) -> None:
        local_settings = SimpleNamespace(is_production_like=False, database_path="postgresql://example/db")
        with patch.object(mailbox_routes, "settings", local_settings):
            response = self.client.post(
                "/v1/mailbox/pubsub",
                json={"message": {"data": _pubsub_data(history_id="not-a-history-id")}},
            )

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
        self.assertEqual(parse_last_event_id("0"), 0)
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


class MailboxSSEConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_stream_offloads_blocking_database_poll(self) -> None:
        request = SimpleNamespace(
            headers={"last-event-id": "41"},
            is_disconnected=AsyncMock(return_value=False),
        )
        with (
            patch.object(
                mailbox_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(mailbox_routes.asyncio, "to_thread", new=AsyncMock(return_value=[])) as to_thread,
        ):
            response = await mailbox_routes.mailbox_events(request)
            first_chunk = await response.body_iterator.__anext__()
            await response.body_iterator.aclose()

        self.assertIn("event: heartbeat", first_chunk)
        to_thread.assert_awaited_once_with(
            mailbox_routes.list_events_after,
            mailbox_routes.settings,
            user_id="user-1",
            after_id=41,
            limit=100,
        )

    async def test_fresh_event_stream_uses_server_baseline_and_revision_handshake(self) -> None:
        request = SimpleNamespace(
            headers={},
            is_disconnected=AsyncMock(return_value=False),
        )
        baseline = SimpleNamespace(id=88)
        baseline_event = SimpleNamespace(
            id=88,
            event_type="thread-content-hydrated",
            mailbox_label=None,
            created_at="2026-07-23T10:00:00+00:00",
            payload={"thread_id": "thread-88"},
        )
        state = SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "connected": True,
                "mailbox_revision": "rev-88",
                "total_threads": 12,
            }
        )
        with (
            patch.object(
                mailbox_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(
                mailbox_routes.asyncio,
                "to_thread",
                new=AsyncMock(side_effect=[baseline, state, [baseline_event]]),
            ) as to_thread,
        ):
            response = await mailbox_routes.mailbox_events(request)
            first_chunk = await response.body_iterator.__anext__()
            second_chunk = await response.body_iterator.__anext__()
            await response.body_iterator.aclose()

        self.assertIn("event: sync-state", first_chunk)
        self.assertIn("id: 87", first_chunk)
        self.assertIn('"mailbox_revision":"rev-88"', first_chunk)
        self.assertIn("event: thread-content-hydrated", second_chunk)
        self.assertIn("id: 88", second_chunk)
        self.assertEqual(to_thread.await_count, 3)
        self.assertEqual(to_thread.await_args_list[0].args[0], mailbox_routes.latest_event)
        self.assertEqual(to_thread.await_args_list[0].kwargs["user_id"], "user-1")
        self.assertEqual(to_thread.await_args_list[1].args[0], mailbox_routes.build_mailbox_sync_state)
        self.assertEqual(to_thread.await_args_list[2].args[0], mailbox_routes.list_events_after)
        self.assertEqual(to_thread.await_args_list[2].kwargs["after_id"], 87)

    async def test_fresh_empty_event_stream_establishes_zero_cursor(self) -> None:
        request = SimpleNamespace(
            headers={},
            is_disconnected=AsyncMock(return_value=False),
        )
        state = SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "connected": True,
                "mailbox_revision": None,
                "total_threads": 0,
            }
        )
        with (
            patch.object(
                mailbox_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(
                mailbox_routes.asyncio,
                "to_thread",
                new=AsyncMock(side_effect=[None, state]),
            ),
        ):
            response = await mailbox_routes.mailbox_events(request)
            first_chunk = await response.body_iterator.__anext__()
            await response.body_iterator.aclose()

        self.assertIn("event: sync-state", first_chunk)
        self.assertIn("id: 0", first_chunk)


def _pubsub_data(email: str = "me@example.com", history_id: str = "123") -> str:
    payload = {"emailAddress": email, "historyId": history_id}
    return urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


if __name__ == "__main__":
    unittest.main()
