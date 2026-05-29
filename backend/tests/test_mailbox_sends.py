from __future__ import annotations

import base64
from dataclasses import replace
from email import message_from_bytes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.db.mail_groups import GmailMessageRecord, MailGroupDetail, MailGroupRecord, PendingSendRecord
from app.main import app
from app.schemas.domain import MailComposeRequest, MailReplyRequest, MailSendResponse
from app.services.mailbox_sends import _raw_message, send_compose, send_reply


def pending_send(state: str = "queued") -> PendingSendRecord:
    return PendingSendRecord(
        server_send_id="server-send-1",
        client_send_id="client-send-1",
        user_id="user-1",
        send_type="compose",
        mailbox_thread_id=None,
        gmail_thread_id=None,
        to=["Recipient <recipient@example.com>"],
        cc=[],
        bcc=[],
        subject="Hello",
        body_text="Body",
        body_html=None,
        headers={},
        state=state,
        created_at="2026-05-21T09:00:00+00:00",
        queued_at="2026-05-21T09:00:00+00:00",
        sent_at=None,
        gmail_message_id=None,
        error=None,
        updated_at="2026-05-21T09:00:00+00:00",
    )


def sample_group() -> MailGroupRecord:
    return MailGroupRecord(
        id="group-1",
        user_id="user-1",
        group_key="gmail-thread:thread-a",
        group_type="conversation",
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="Project check-in",
        ai_summary="Reply test",
        labels=["inbox"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=False,
        latest_message_at="2026-05-21T09:00:00+00:00",
        latest_message_id="msg-1",
        generated_from_hash="hash-1",
        generated_at="2026-05-21T09:00:00+00:00",
        created_at="2026-05-21T09:00:00+00:00",
        updated_at="2026-05-21T09:00:00+00:00",
    )


def sample_message() -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id="msg-1",
        gmail_thread_id="thread-a",
        history_id="10",
        label_ids=["INBOX"],
        internal_date="2026-05-21T09:00:00+00:00",
        subject="Project check-in",
        sender="Partner <partner@example.com>",
        recipients={"to": "Me <me@example.com>", "cc": "Observer <observer@example.com>"},
        headers={"message-id": "<msg-1@example.com>", "references": "<root@example.com>"},
        snippet="Reply test",
        raw_payload={"payload": {"headers": []}},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="Reply test",
        extracted_signals={},
        body_hash="hash-1",
        created_at="2026-05-21T09:00:00+00:00",
        updated_at="2026-05-21T09:00:00+00:00",
    )


class MailboxSendServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db", backend_origin="http://127.0.0.1:3001")

    @patch("app.services.mailbox_sends.upsert_pending_send")
    @patch("app.services.mailbox_sends.user_can_write_gmail", return_value=True)
    @patch("app.services.mailbox_sends.missing_google_scopes", return_value=["https://www.googleapis.com/auth/gmail.send"])
    def test_compose_requires_send_scope_before_storing_send(
        self,
        _mock_missing_scopes: Mock,
        _mock_can_write: Mock,
        mock_upsert: Mock,
    ) -> None:
        response = send_compose(
            self.settings,
            user_id="user-1",
            request=MailComposeRequest(
                client_send_id="client-send-1",
                to=["recipient@example.com"],
                subject="Hello",
                body_text="Body",
                created_at="2026-05-21T09:00:00+00:00",
            ),
        )

        self.assertEqual(response.state, "reauth_required")
        self.assertEqual(response.reauth_url, "http://127.0.0.1:3001/auth/google")
        mock_upsert.assert_not_called()

    @patch("app.services.mailbox_sends._perform_send", side_effect=TimeoutError("gmail timeout"))
    @patch("app.services.mailbox_sends.enqueue_job")
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_transient_compose_failure_queues_critical_send_job(
        self,
        _mock_can_send: Mock,
        _mock_upsert: Mock,
        mock_enqueue: Mock,
        _mock_perform: Mock,
    ) -> None:
        response = send_compose(
            self.settings,
            user_id="user-1",
            request=MailComposeRequest(
                client_send_id="client-send-1",
                to=["recipient@example.com"],
                subject="Hello",
                body_text="Body",
                created_at="2026-05-21T09:00:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        mock_enqueue.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_send_message",
            queue="critical",
            user_id="user-1",
            dedupe_key="gmail-send:user-1:server-send-1",
            priority=90,
            payload={
                "user_id": "user-1",
                "server_send_id": "server-send-1",
                "last_error": "TimeoutError: gmail timeout",
            },
        )

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_resolves_headers_recipients_and_gmail_thread(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        _mock_user: Mock,
        mock_upsert: Mock,
        _mock_send_or_queue: Mock,
    ) -> None:
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[sample_message()])

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(mock_upsert.call_args.kwargs["gmail_thread_id"], "thread-a")
        self.assertEqual(mock_upsert.call_args.kwargs["to"], ["Partner <partner@example.com>"])
        self.assertEqual(mock_upsert.call_args.kwargs["cc"], ["Observer <observer@example.com>"])
        self.assertEqual(mock_upsert.call_args.kwargs["subject"], "Re: Project check-in")
        self.assertEqual(
            mock_upsert.call_args.kwargs["headers"],
            {"In-Reply-To": "<msg-1@example.com>", "References": "<root@example.com> <msg-1@example.com>"},
        )

    def test_raw_reply_message_preserves_threading_headers(self) -> None:
        record = replace(
            pending_send(),
            headers={"In-Reply-To": "<msg-1@example.com>", "References": "<root@example.com> <msg-1@example.com>"},
        )

        raw = _raw_message(record)
        parsed = message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))

        self.assertEqual(parsed["To"], "Recipient <recipient@example.com>")
        self.assertEqual(parsed["Subject"], "Hello")
        self.assertEqual(parsed["In-Reply-To"], "<msg-1@example.com>")
        self.assertEqual(parsed["References"], "<root@example.com> <msg-1@example.com>")


class MailboxSendRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.api.routes.mailbox.send_compose")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_compose_endpoint_dispatches_native_send_payload(self, mock_user: Mock, mock_send: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_send.return_value = MailSendResponse(
            client_send_id="client-send-1",
            server_send_id="server-send-1",
            state="queued",
        )

        response = self.client.post(
            "/v1/mailbox/compose",
            json={
                "client_send_id": "client-send-1",
                "to": ["recipient@example.com"],
                "subject": "Hello",
                "body_text": "Body",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["server_send_id"], "server-send-1")
        request = mock_send.call_args.kwargs["request"]
        self.assertEqual(request.to, ["recipient@example.com"])
        self.assertEqual(request.body_text, "Body")

    @patch("app.api.routes.mailbox.send_reply")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_reply_endpoint_dispatches_thread_reply_payload(self, mock_user: Mock, mock_send: Mock) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_send.return_value = MailSendResponse(
            client_send_id="client-send-1",
            mailbox_thread_id="group-1",
            state="queued",
        )

        response = self.client.post(
            "/v1/mailbox/threads/group-1/reply",
            json={
                "client_send_id": "client-send-1",
                "body_text": "Reply body",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["mailbox_thread_id"], "group-1")
        self.assertEqual(mock_send.call_args.kwargs["mailbox_thread_id"], "group-1")


if __name__ == "__main__":
    unittest.main()
