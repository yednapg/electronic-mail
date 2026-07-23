from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.routes import mailbox as mailbox_routes
from app.db.models import StoredAppSession, StoredUser
from app.main import app
from app.schemas.domain import MailboxResponse, MailboxSyncStateResponse
from app.services import auth as auth_service


class MailboxUserIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_web_cookie_and_bearer_sessions_resolve_to_same_backend_user(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db", app_session_secret="secret")
        stored_user = StoredUser(
            id="user-1",
            email="same@example.com",
            display_name="Same User",
            google_sub="google-sub-1",
            access_enabled=True,
            created_at="2026-07-03T00:00:00+00:00",
            updated_at="2026-07-03T00:00:00+00:00",
        )

        def session_for_hash(*_args, token_hash: str, **_kwargs) -> StoredAppSession:
            platform = "web" if token_hash == auth_service.hash_token(settings, "web-token") else "ios"
            return StoredAppSession(
                id=f"{platform}-session",
                user_id="user-1",
                token_hash=token_hash,
                platform=platform,
                expires_at="2026-08-03T00:00:00+00:00",
                revoked_at=None,
                last_seen_at=None,
                created_at="2026-07-03T00:00:00+00:00",
            )

        web_request = SimpleNamespace(cookies={auth_service.SESSION_COOKIE_NAME: "web-token"}, headers={})
        ios_request = SimpleNamespace(cookies={}, headers={"authorization": "Bearer ios-token"})

        with (
            patch.object(auth_service, "get_active_app_session", side_effect=session_for_hash),
            patch.object(auth_service, "get_user", return_value=stored_user),
        ):
            web_user = auth_service.get_current_user(settings, web_request)
            ios_user = auth_service.get_current_user(settings, ios_request)

        self.assertIsNotNone(web_user)
        self.assertIsNotNone(ios_user)
        self.assertEqual(web_user.id, "user-1")
        self.assertEqual(ios_user.id, "user-1")

    def test_mailbox_route_reads_only_authenticated_users_mailbox(self) -> None:
        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-b")),
            patch.object(mailbox_routes, "build_mailbox_response", return_value=MailboxResponse(label="inbox", total_threads=0)) as build_mailbox,
        ):
            response = self.client.get("/v1/mailbox?label=inbox&limit=50")

        self.assertEqual(response.status_code, 200)
        build_mailbox.assert_called_once()
        self.assertEqual(build_mailbox.call_args.kwargs["user_id"], "user-b")
        self.assertEqual(build_mailbox.call_args.kwargs["label"], "inbox")
        self.assertEqual(build_mailbox.call_args.kwargs["limit"], 50)

    def test_other_user_cannot_open_thread_detail_when_lookup_is_user_scoped(self) -> None:
        def detail_for_user(_settings, *, user_id: str, **_kwargs):
            return SimpleNamespace() if user_id == "user-a" else None

        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-b")),
            patch.object(mailbox_routes, "build_group_detail_response", side_effect=detail_for_user) as build_detail,
        ):
            response = self.client.get("/v1/mailbox/threads/thread-owned-by-user-a")

        self.assertEqual(response.status_code, 404)
        build_detail.assert_called_once()
        self.assertEqual(build_detail.call_args.kwargs["user_id"], "user-b")

    def test_attachment_lookup_is_scoped_to_authenticated_user(self) -> None:
        route_settings = SimpleNamespace(database_path="postgresql://example/db")
        with (
            patch.object(mailbox_routes, "settings", route_settings),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-b")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[]) as list_messages,
        ):
            response = self.client.get("/v1/mailbox/messages/msg-user-a/attachments/att-1")

        self.assertEqual(response.status_code, 404)
        list_messages.assert_called_once_with("postgresql://example/db", user_id="user-b", message_ids=["msg-user-a"])

    def test_attachment_download_safely_encodes_hostile_filename(self) -> None:
        route_settings = SimpleNamespace(database_path="postgresql://example/db")
        hostile = 'résumé"; foo=bar; filename="pwn.txt\r\nX-Evil: yes'
        attachment = SimpleNamespace(filename=hostile, mime_type="text/plain", attachment_id="att-1")
        with (
            patch.object(mailbox_routes, "settings", route_settings),
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-b")),
            patch.object(mailbox_routes, "list_messages_by_ids", return_value=[SimpleNamespace(message_id="msg-1")]),
            patch.object(mailbox_routes, "gmail_attachments_for_message", return_value=[attachment]),
            patch.object(mailbox_routes, "fetch_gmail_attachment", return_value={"data": "aGVsbG8="}),
        ):
            response = self.client.get("/v1/mailbox/messages/msg-1/attachments/att-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"hello")
        disposition = response.headers["content-disposition"]
        self.assertNotIn("foo=bar", disposition)
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertIn("filename*=UTF-8''r%C3%A9sum%C3%A9_", disposition)

    def test_sync_job_is_enqueued_for_authenticated_user(self) -> None:
        state = MailboxSyncStateResponse(connected=True, total_threads=3)

        with (
            patch.object(mailbox_routes, "require_current_user", return_value=SimpleNamespace(id="user-b")),
            patch.object(mailbox_routes, "build_mailbox_sync_state", return_value=state),
            patch.object(mailbox_routes, "enqueue_mailbox_sync", return_value="job-user-b") as enqueue_sync,
        ):
            response = self.client.post("/v1/mailbox/sync")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["job_id"], "job-user-b")
        enqueue_sync.assert_called_once_with(mailbox_routes.settings, user_id="user-b")

    def test_schema_keeps_gmail_messages_owned_by_user_and_message(self) -> None:
        baseline = Path("backend/migrations/versions/20260514_0001_mail_groups_baseline.py").read_text()
        repository = Path("backend/app/db/mail_groups.py").read_text()

        self.assertIn("PRIMARY KEY(user_id, message_id)", baseline)
        self.assertIn("ON CONFLICT (user_id, message_id)", repository)


if __name__ == "__main__":
    unittest.main()
