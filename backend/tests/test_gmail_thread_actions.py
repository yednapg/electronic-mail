from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import gmail as gmail_routes
from app.main import create_app, settings
from app.services.integrations.google import archive_gmail_thread, mark_gmail_message_read, mark_gmail_thread_read, unarchive_gmail_thread


class GmailThreadActionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(google_configured=True)

    @patch("app.services.integrations.google.build")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_archive_removes_inbox_label(self, mock_credentials: Mock, mock_build: Mock) -> None:
        mock_credentials.return_value = object()
        execute = Mock(return_value={"id": "thread-1"})
        modify = Mock(return_value=SimpleNamespace(execute=execute))
        threads = SimpleNamespace(modify=modify)
        users = SimpleNamespace(threads=Mock(return_value=threads))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=users))

        response = archive_gmail_thread(self.settings, "thread-1")

        self.assertEqual(response, {"id": "thread-1"})
        modify.assert_called_once_with(userId="me", id="thread-1", body={"removeLabelIds": ["INBOX"]})

    @patch("app.services.integrations.google.build")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_unarchive_adds_inbox_label(self, mock_credentials: Mock, mock_build: Mock) -> None:
        mock_credentials.return_value = object()
        execute = Mock(return_value={"id": "thread-1"})
        modify = Mock(return_value=SimpleNamespace(execute=execute))
        threads = SimpleNamespace(modify=modify)
        users = SimpleNamespace(threads=Mock(return_value=threads))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=users))

        response = unarchive_gmail_thread(self.settings, "thread-1")

        self.assertEqual(response, {"id": "thread-1"})
        modify.assert_called_once_with(userId="me", id="thread-1", body={"addLabelIds": ["INBOX"]})

    @patch("app.services.integrations.google.build")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_mark_read_removes_unread_label(self, mock_credentials: Mock, mock_build: Mock) -> None:
        mock_credentials.return_value = object()
        execute = Mock(return_value={"id": "thread-1"})
        modify = Mock(return_value=SimpleNamespace(execute=execute))
        threads = SimpleNamespace(modify=modify)
        users = SimpleNamespace(threads=Mock(return_value=threads))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=users))

        response = mark_gmail_thread_read(self.settings, "thread-1")

        self.assertEqual(response, {"id": "thread-1"})
        modify.assert_called_once_with(userId="me", id="thread-1", body={"removeLabelIds": ["UNREAD"]})

    @patch("app.services.integrations.google.build")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_mark_message_read_removes_unread_label(self, mock_credentials: Mock, mock_build: Mock) -> None:
        mock_credentials.return_value = object()
        execute = Mock(return_value={"id": "msg-1"})
        modify = Mock(return_value=SimpleNamespace(execute=execute))
        messages = SimpleNamespace(modify=modify)
        users = SimpleNamespace(messages=Mock(return_value=messages))
        mock_build.return_value = SimpleNamespace(users=Mock(return_value=users))

        response = mark_gmail_message_read(self.settings, "msg-1")

        self.assertEqual(response, {"id": "msg-1"})
        modify.assert_called_once_with(userId="me", id="msg-1", body={"removeLabelIds": ["UNREAD"]})


class GmailThreadActionRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(replace(settings, app_env="local", rate_limit_enabled=False))
        )
        self.settings_patch = patch.object(
            gmail_routes,
            "settings",
            SimpleNamespace(
                google_configured=True,
                database_path="postgresql://example/db",
            ),
        )
        self.provider_lock_patch = patch.object(
            gmail_routes,
            "shared_user_mail_lock",
            return_value=nullcontext(),
        )
        self.settings_patch.start()
        self.provider_lock_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.provider_lock_patch.stop)

    @patch("app.api.routes.gmail.archive_gmail_thread_service")
    @patch("app.api.routes.gmail.require_current_user")
    def test_archive_endpoint_returns_action_payload(self, mock_user: Mock, mock_archive: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_archive.return_value = {"id": "thread-1"}

        response = self.client.post("/gmail/threads/thread-1/archive")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"thread_id": "thread-1", "action": "archive"})
        mock_archive.assert_called_once()

    @patch("app.api.routes.gmail.unarchive_gmail_thread_service")
    @patch("app.api.routes.gmail.require_current_user")
    def test_unarchive_endpoint_returns_action_payload(self, mock_user: Mock, mock_unarchive: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_unarchive.return_value = {"id": "thread-1"}

        response = self.client.post("/gmail/threads/thread-1/unarchive")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"thread_id": "thread-1", "action": "unarchive"})
        mock_unarchive.assert_called_once()

    @patch("app.api.routes.gmail.mark_gmail_thread_read")
    @patch("app.api.routes.gmail.require_current_user")
    def test_mark_read_endpoint_returns_action_payload(self, mock_user: Mock, mock_mark_read: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_mark_read.return_value = {"id": "thread-1"}

        response = self.client.post("/v1/gmail/threads/thread-1/mark-read")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"thread_id": "thread-1", "action": "mark_read"})
        mock_mark_read.assert_called_once()

    @patch("app.api.routes.gmail.archive_gmail_thread_service", side_effect=RuntimeError("Google account is not connected"))
    @patch("app.api.routes.gmail.require_current_user")
    def test_archive_endpoint_surfaces_missing_auth(self, mock_user: Mock, _mock_archive: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        response = self.client.post("/gmail/threads/thread-1/archive")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Google archive failed."})


if __name__ == "__main__":
    unittest.main()
