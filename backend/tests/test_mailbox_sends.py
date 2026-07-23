from __future__ import annotations

import base64
from contextlib import nullcontext
from dataclasses import replace
from email import message_from_bytes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.core.error_safety import GoogleCredentialsUnavailable
from app.db.mail_groups import (
    GmailMessageRecord,
    MailGroupDetail,
    MailGroupRecord,
    MailSendIdempotencyConflict,
    PendingSendRecord,
    claim_pending_send,
    mark_pending_send_sent,
)
from app.main import app
from app.schemas.domain import MailComposeRequest, MailReplyRequest, MailSendResponse
from app.services.mail_groups import _thread_message_from_gmail
from app.services.mailbox_sends import (
    _import_sent_message,
    _perform_send,
    _raw_message,
    MailSendConfirmationPending,
    retry_send,
    run_pending_send,
    send_compose,
    send_reply,
)


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


def google_http_error(status: int) -> HttpError:
    return HttpError(SimpleNamespace(status=status, reason="Provider rejection"), b"provider rejection")


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
        self.provider_lock_patch = patch(
            "app.services.mailbox_sends.shared_user_mail_lock",
            return_value=nullcontext(),
        )
        self.provider_lock_patch.start()
        self.addCleanup(self.provider_lock_patch.stop)

    def test_sent_import_resolves_large_text_body_and_marks_it_terminal(self) -> None:
        encoded_body = base64.urlsafe_b64encode(b"Complete large sent body").decode("ascii").rstrip("=")
        payload = {
            "id": "sent-message-1",
            "threadId": "sent-thread-1",
            "labelIds": ["SENT"],
            "payload": {
                "mimeType": "text/plain",
                "body": {"attachmentId": "large-sent-body", "size": 4096},
            },
        }
        with patch(
            "app.services.mailbox_sends.fetch_gmail_message",
            return_value=payload,
        ), patch(
            "app.services.mailbox_sends.fetch_gmail_attachment",
            return_value={"data": encoded_body},
        ) as fetch_attachment, patch(
            "app.services.mailbox_sends.upsert_gmail_messages"
        ) as upsert_messages, patch(
            "app.services.mailbox_sends.rebuild_touched_mail_groups"
        ), patch("app.services.mailbox_sends.enqueue_projection_refresh"):
            _import_sent_message(
                self.settings,
                user_id="user-1",
                message_id="sent-message-1",
            )

        fetch_attachment.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="sent-message-1",
            attachment_id="large-sent-body",
        )
        imported = upsert_messages.call_args.args[1][0]
        self.assertEqual(imported.text_body, "Complete large sent body")
        self.assertEqual(imported.body_fetch_status, "fetched")

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

    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send("sending"))
    @patch("app.services.mailbox_sends._perform_send", side_effect=TimeoutError("gmail timeout"))
    @patch("app.services.mailbox_sends.enqueue_job")
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_ambiguous_compose_failure_stays_sending_without_retry_job(
        self,
        _mock_can_send: Mock,
        _mock_upsert: Mock,
        mock_enqueue: Mock,
        _mock_perform: Mock,
        _mock_get: Mock,
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

        self.assertEqual(response.state, "sending")
        mock_enqueue.assert_not_called()

    @patch("app.services.mailbox_sends.mark_pending_send_failed")
    @patch("app.services.mailbox_sends._perform_send", side_effect=google_http_error(400))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_definite_provider_rejection_becomes_retryable_failed_send(
        self,
        _mock_can_send: Mock,
        _mock_upsert: Mock,
        _mock_perform: Mock,
        mock_failed: Mock,
    ) -> None:
        mock_failed.return_value = replace(pending_send("failed"), error="Google rejected mail delivery.")

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

        self.assertEqual(response.state, "failed")
        mock_failed.assert_called_once()

    @patch("app.services.mailbox_sends.enqueue_job")
    @patch("app.services.mailbox_sends.mark_pending_send_failed")
    @patch(
        "app.services.mailbox_sends._perform_send",
        side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
    )
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_expired_google_credentials_return_reauthentication_without_queueing(
        self,
        _mock_can_send: Mock,
        _mock_upsert: Mock,
        _mock_perform: Mock,
        mock_failed: Mock,
        mock_enqueue: Mock,
    ) -> None:
        mock_failed.return_value = replace(
            pending_send("failed"),
            error="Google authorization expired or was revoked. Please sign in again.",
        )

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
        self.assertEqual(response.server_send_id, "server-send-1")
        self.assertIn("sign in again", response.error or "")
        mock_enqueue.assert_not_called()

    @patch("app.services.mailbox_sends.mark_pending_send_failed")
    @patch(
        "app.services.mailbox_sends._perform_send",
        side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
    )
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send())
    def test_background_send_propagates_reauthentication_without_retry_wrapping(
        self,
        _mock_get: Mock,
        _mock_perform: Mock,
        mock_failed: Mock,
    ) -> None:
        with self.assertRaises(GoogleCredentialsUnavailable):
            run_pending_send(self.settings, user_id="user-1", server_send_id="server-send-1")

        mock_failed.assert_called_once()

    @patch("app.services.mailbox_sends.mark_pending_send_failed")
    @patch("app.services.mailbox_sends._perform_send", side_effect=TimeoutError("response lost"))
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send("sending"))
    def test_background_ambiguous_send_never_becomes_retryable_failed(
        self,
        _mock_get: Mock,
        _mock_perform: Mock,
        mock_failed: Mock,
    ) -> None:
        with self.assertRaises(MailSendConfirmationPending):
            run_pending_send(self.settings, user_id="user-1", server_send_id="server-send-1")

        mock_failed.assert_not_called()

    @patch("app.services.mailbox_sends.mark_pending_send_failed")
    @patch("app.services.mailbox_sends._perform_send", side_effect=google_http_error(401))
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send())
    def test_background_new_send_auth_rejection_is_conclusive_failed_state(
        self,
        _mock_get: Mock,
        _mock_perform: Mock,
        mock_failed: Mock,
    ) -> None:
        mock_failed.return_value = replace(
            pending_send("failed"),
            error="Google needs mail send permission.",
        )

        response = run_pending_send(self.settings, user_id="user-1", server_send_id="server-send-1")

        self.assertIsNotNone(response)
        self.assertEqual(response.state, "failed")
        mock_failed.assert_called_once()

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_all_resolves_headers_recipients_and_gmail_thread(
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
                mode="reply_all",
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
        self.assertIn("On 2026-05-21T09:00:00+00:00, Partner <partner@example.com> wrote:", mock_upsert.call_args.kwargs["body_text"])
        self.assertIn("> Reply test", mock_upsert.call_args.kwargs["body_text"])
        self.assertIn("<blockquote>", mock_upsert.call_args.kwargs["body_html"])

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_honors_changed_explicit_to_recipient(
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
                mode="reply",
                to=["Replacement <replacement@example.com>"],
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(mock_upsert.call_args.kwargs["to"], ["Replacement <replacement@example.com>"])
        self.assertNotIn("partner@example.com", " ".join(mock_upsert.call_args.kwargs["to"]).lower())

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_all_honors_removed_cc_and_filters_self_and_duplicates(
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
                mode="reply_all",
                to=["Me <me@example.com>", "Replacement <replacement@example.com>"],
                cc=[],
                bcc=["replacement@example.com", "Blind <blind@example.com>"],
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(mock_upsert.call_args.kwargs["to"], ["Replacement <replacement@example.com>"])
        self.assertEqual(mock_upsert.call_args.kwargs["cc"], [])
        self.assertEqual(mock_upsert.call_args.kwargs["bcc"], ["Blind <blind@example.com>"])
        recipients = " ".join(
            [
                *mock_upsert.call_args.kwargs["to"],
                *mock_upsert.call_args.kwargs["cc"],
                *mock_upsert.call_args.kwargs["bcc"],
            ]
        ).lower()
        self.assertNotIn("partner@example.com", recipients)
        self.assertNotIn("observer@example.com", recipients)
        self.assertNotIn("me@example.com", recipients)

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_selected_message_drives_reply_quote_headers_subject_and_recipient(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        _mock_user: Mock,
        mock_upsert: Mock,
        _mock_send_or_queue: Mock,
    ) -> None:
        older = replace(
            sample_message(),
            message_id="msg-old",
            internal_date="2026-05-20T09:00:00+00:00",
            subject="Older subject",
            sender="Older Sender <older@example.com>",
            headers={
                "message-id": "<msg-old@example.com>",
                "references": "<root@example.com>",
                "reply-to": "Reply Desk <reply@example.com>",
            },
            text_body="Older selected body",
            snippet="Older selected body",
        )
        latest = replace(
            sample_message(),
            message_id="msg-latest",
            internal_date="2026-05-22T09:00:00+00:00",
            subject="Latest subject",
            headers={"message-id": "<msg-latest@example.com>"},
            text_body="Latest body must not be quoted",
            snippet="Latest body must not be quoted",
        )
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[older, latest])

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                source_message_id="msg-old",
                mode="reply",
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(mock_upsert.call_args.kwargs["to"], ["Reply Desk <reply@example.com>"])
        self.assertEqual(mock_upsert.call_args.kwargs["subject"], "Re: Older subject")
        self.assertEqual(
            mock_upsert.call_args.kwargs["headers"],
            {"In-Reply-To": "<msg-old@example.com>", "References": "<root@example.com> <msg-old@example.com>"},
        )
        self.assertIn("Older selected body", mock_upsert.call_args.kwargs["body_text"])
        self.assertNotIn("Latest body must not be quoted", mock_upsert.call_args.kwargs["body_text"])

    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_all_to_sent_message_falls_back_to_original_to_and_cc(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        _mock_user: Mock,
        mock_upsert: Mock,
        _mock_send_or_queue: Mock,
    ) -> None:
        sent_message = replace(
            sample_message(),
            sender="Me <me@example.com>",
            recipients={
                "to": "First <first@example.com>, Second <second@example.com>",
                "cc": "Me <me@example.com>, Observer <observer@example.com>",
            },
            label_ids=["SENT"],
        )
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[sent_message])

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                mode="reply_all",
                body_text="Follow-up",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(
            mock_upsert.call_args.kwargs["to"],
            ["First <first@example.com>", "Second <second@example.com>"],
        )
        self.assertEqual(mock_upsert.call_args.kwargs["cc"], ["Observer <observer@example.com>"])

    @patch("app.services.mailbox_sends.upsert_pending_send")
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_reply_to_self_only_sent_message_requires_a_recipient(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        _mock_user: Mock,
        mock_upsert: Mock,
    ) -> None:
        self_only = replace(
            sample_message(),
            sender="Me <me@example.com>",
            recipients={"to": "Me <me@example.com>", "cc": ""},
            label_ids=["SENT"],
        )
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[self_only])

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                mode="reply",
                body_text="Follow-up",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "failed")
        self.assertEqual(response.error, "No reply recipient found.")
        mock_upsert.assert_not_called()

    @patch("app.services.mailbox_sends.upsert_pending_send")
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_selected_message_must_belong_to_requested_group(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        mock_upsert: Mock,
    ) -> None:
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[sample_message()])

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                source_message_id="message-from-another-thread",
                mode="reply",
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "failed")
        self.assertEqual(response.error, "Selected email is not part of this thread.")
        mock_detail.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            group_id="group-1",
        )
        mock_upsert.assert_not_called()

    @patch("app.services.mailbox_sends.upsert_pending_send")
    @patch("app.services.mailbox_sends.list_messages_for_gmail_thread", return_value=[sample_message()])
    @patch("app.services.mailbox_sends.get_mail_group_detail", return_value=None)
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_selected_message_lookup_is_scoped_to_authenticated_user(
        self,
        _mock_can_send: Mock,
        _mock_detail: Mock,
        mock_list_messages: Mock,
        mock_upsert: Mock,
    ) -> None:
        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="thread-a",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                source_message_id="other-users-message",
                mode="reply",
                body_text="Reply body",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "failed")
        mock_list_messages.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            gmail_thread_id="thread-a",
        )
        mock_upsert.assert_not_called()

    @patch("app.services.mailbox_sends.fetch_gmail_attachment", return_value={"data": "b3JpZ2luYWw="})
    @patch("app.services.mailbox_sends.gmail_attachments_for_message")
    @patch("app.services.mailbox_sends._send_or_queue", return_value=MailSendResponse(client_send_id="client-send-1", state="queued"))
    @patch("app.services.mailbox_sends.upsert_pending_send", return_value=pending_send())
    @patch("app.services.mailbox_sends.get_user", return_value=SimpleNamespace(email="me@example.com"))
    @patch("app.services.mailbox_sends.get_mail_group_detail")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    def test_forward_fetches_and_includes_original_gmail_attachments(
        self,
        _mock_can_send: Mock,
        mock_detail: Mock,
        _mock_user: Mock,
        mock_upsert: Mock,
        _mock_send_or_queue: Mock,
        mock_attachments: Mock,
        mock_fetch: Mock,
    ) -> None:
        selected = replace(
            sample_message(),
            message_id="msg-selected",
            internal_date="2026-05-20T09:00:00+00:00",
            subject="Selected attachment email",
            text_body="Selected forward body",
        )
        latest = replace(
            sample_message(),
            message_id="msg-latest",
            internal_date="2026-05-22T09:00:00+00:00",
            subject="Latest email",
            text_body="Wrong forward body",
        )
        mock_detail.return_value = MailGroupDetail(group=sample_group(), messages=[selected, latest])
        mock_attachments.return_value = [
            SimpleNamespace(filename="original.txt", mime_type="text/plain", attachment_id="att-1")
        ]

        response = send_reply(
            self.settings,
            user_id="user-1",
            mailbox_thread_id="group-1",
            request=MailReplyRequest(
                client_send_id="client-send-1",
                source_message_id="msg-selected",
                mode="forward",
                to=["recipient@example.com"],
                body_text="For your review",
                created_at="2026-05-21T09:05:00+00:00",
            ),
        )

        self.assertEqual(response.state, "queued")
        self.assertEqual(mock_upsert.call_args.kwargs["gmail_thread_id"], None)
        self.assertEqual(mock_upsert.call_args.kwargs["subject"], "Fwd: Selected attachment email")
        self.assertIn("---------- Forwarded message ---------", mock_upsert.call_args.kwargs["body_text"])
        self.assertIn("Selected forward body", mock_upsert.call_args.kwargs["body_text"])
        self.assertNotIn("Wrong forward body", mock_upsert.call_args.kwargs["body_text"])
        self.assertEqual(
            mock_upsert.call_args.kwargs["attachments"],
            [{"filename": "original.txt", "mime_type": "text/plain", "data_base64": "b3JpZ2luYWw="}],
        )
        mock_fetch.assert_called_once_with(
            self.settings,
            user_id="user-1",
            message_id="msg-selected",
            attachment_id="att-1",
        )

    def test_reader_message_exposes_reply_to_header(self) -> None:
        message = replace(
            sample_message(),
            headers={**sample_message().headers, "reply-to": "Reply Desk <reply@example.com>"},
        )

        reader_message = _thread_message_from_gmail(message)

        self.assertEqual(reader_message.reply_to, "Reply Desk <reply@example.com>")

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
        self.assertRegex(str(parsed["Message-ID"]), r"^<send\.[0-9a-f]{32}@electronic-mail\.local>$")

    @patch("app.services.mailbox_sends.send_gmail_raw_message")
    @patch("app.services.mailbox_sends.claim_pending_send")
    @patch(
        "app.services.mailbox_sends.find_sent_gmail_message_by_rfc822_message_id",
        side_effect=google_http_error(401),
    )
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send("sending"))
    def test_ambiguous_reconciliation_auth_error_never_authorizes_resend(
        self,
        _mock_get: Mock,
        _mock_find: Mock,
        mock_claim: Mock,
        mock_send: Mock,
    ) -> None:
        with self.assertRaises(MailSendConfirmationPending):
            _perform_send(self.settings, user_id="user-1", record=pending_send("sending"))

        mock_claim.assert_not_called()
        mock_send.assert_not_called()

    @patch("app.services.mailbox_sends.send_gmail_raw_message")
    @patch("app.services.mailbox_sends.claim_pending_send")
    @patch(
        "app.services.mailbox_sends.find_sent_gmail_message_by_rfc822_message_id",
        side_effect=GoogleCredentialsUnavailable("Google credentials are not connected"),
    )
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send("sending"))
    def test_ambiguous_reconciliation_credential_failure_never_authorizes_resend(
        self,
        _mock_get: Mock,
        _mock_find: Mock,
        mock_claim: Mock,
        mock_send: Mock,
    ) -> None:
        with self.assertRaises(MailSendConfirmationPending):
            _perform_send(self.settings, user_id="user-1", record=pending_send("sending"))

        mock_claim.assert_not_called()
        mock_send.assert_not_called()

    @patch("app.services.mailbox_sends.send_gmail_raw_message")
    @patch("app.services.mailbox_sends.claim_pending_send", return_value=None)
    @patch("app.services.mailbox_sends.find_sent_gmail_message_by_rfc822_message_id", return_value=None)
    @patch("app.services.mailbox_sends.get_pending_send")
    def test_concurrent_send_owner_prevents_second_gmail_send(
        self,
        mock_get: Mock,
        _mock_find: Mock,
        _mock_claim: Mock,
        mock_send: Mock,
    ) -> None:
        sending = pending_send("sending")
        mock_get.return_value = sending

        result = _perform_send(self.settings, user_id="user-1", record=pending_send())

        self.assertEqual(result.state, "sending")
        _mock_claim.assert_not_called()
        mock_send.assert_not_called()

    @patch("app.services.mailbox_sends.send_gmail_raw_message")
    @patch("app.services.mailbox_sends.claim_pending_send")
    @patch("app.services.mailbox_sends._finalize_sent_message")
    @patch("app.services.mailbox_sends.find_sent_gmail_message_by_rfc822_message_id")
    @patch("app.services.mailbox_sends.get_pending_send")
    def test_ambiguous_retry_recovers_existing_gmail_message_without_resending(
        self,
        mock_get: Mock,
        mock_find: Mock,
        mock_finalize: Mock,
        mock_claim: Mock,
        mock_send: Mock,
    ) -> None:
        retry = replace(pending_send(), error="TimeoutError: response lost")
        recovered = {"id": "gmail-message-1", "threadId": "gmail-thread-1"}
        sent = replace(retry, state="sent", gmail_message_id="gmail-message-1", gmail_thread_id="gmail-thread-1")
        mock_get.return_value = retry
        mock_find.return_value = recovered
        mock_finalize.return_value = sent

        result = _perform_send(self.settings, user_id="user-1", record=retry)

        self.assertEqual(result.state, "sent")
        mock_find.assert_called_once()
        mock_finalize.assert_called_once_with(self.settings, user_id="user-1", record=retry, result=recovered)
        mock_claim.assert_not_called()
        mock_send.assert_not_called()

    @patch("app.services.mailbox_sends._send_or_queue")
    @patch("app.services.mailbox_sends._can_send", return_value=True)
    @patch("app.services.mailbox_sends.get_pending_send", return_value=pending_send("sending"))
    def test_manual_retry_of_ambiguous_send_only_enters_reconciliation(
        self,
        _mock_get: Mock,
        _mock_can_send: Mock,
        mock_send_or_queue: Mock,
    ) -> None:
        mock_send_or_queue.return_value = MailSendResponse(
            client_send_id="client-send-1",
            server_send_id="server-send-1",
            state="sending",
        )

        response = retry_send(self.settings, user_id="user-1", server_send_id="server-send-1")

        self.assertIsNotNone(response)
        self.assertEqual(response.state, "sending")
        mock_send_or_queue.assert_called_once()


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

    @patch(
        "app.api.routes.mailbox.send_compose",
        side_effect=MailSendIdempotencyConflict("This send identity was already used for a different email."),
    )
    @patch("app.api.routes.mailbox.require_current_user", return_value=SimpleNamespace(id="user-1"))
    def test_compose_endpoint_rejects_reused_identity_with_different_payload(
        self,
        _mock_user: Mock,
        _mock_send: Mock,
    ) -> None:
        response = self.client.post(
            "/v1/mailbox/compose",
            json={
                "client_send_id": "client-send-1",
                "to": ["recipient@example.com"],
                "subject": "Changed payload",
                "body_text": "Body",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("already used", response.json()["detail"])

    @patch("app.api.routes.mailbox.list_outbox_statuses")
    @patch("app.api.routes.mailbox.require_current_user")
    def test_outbox_lists_only_the_authenticated_users_unresolved_sends(
        self,
        mock_user: Mock,
        mock_list: Mock,
    ) -> None:
        mock_user.return_value = SimpleNamespace(id="user-1")
        mock_list.return_value = [
            MailSendResponse(
                client_send_id="client-send-1",
                server_send_id="server-send-1",
                state="failed",
                error="Google mail delivery failed.",
            )
        ]

        response = self.client.get("/v1/mailbox/outbox", params={"limit": 25})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["server_send_id"], "server-send-1")
        self.assertEqual(mock_list.call_args.kwargs, {"user_id": "user-1", "limit": 25})

    @patch("app.api.routes.mailbox.get_send_status")
    @patch("app.api.routes.mailbox.require_current_user", return_value=SimpleNamespace(id="user-1"))
    def test_send_status_returns_not_found_for_another_users_send(
        self,
        _mock_user: Mock,
        mock_status: Mock,
    ) -> None:
        mock_status.return_value = None

        response = self.client.get("/v1/mailbox/sends/not-owned")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            mock_status.call_args.kwargs,
            {"user_id": "user-1", "server_send_id": "not-owned"},
        )

    @patch("app.api.routes.mailbox.retry_send")
    @patch("app.api.routes.mailbox.require_current_user", return_value=SimpleNamespace(id="user-1"))
    def test_retry_endpoint_returns_current_durable_send_state(
        self,
        _mock_user: Mock,
        mock_retry: Mock,
    ) -> None:
        mock_retry.return_value = MailSendResponse(
            client_send_id="client-send-1",
            server_send_id="server-send-1",
            state="queued",
        )

        response = self.client.post("/v1/mailbox/sends/server-send-1/retry")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["state"], "queued")
        self.assertEqual(
            mock_retry.call_args.kwargs,
            {"user_id": "user-1", "server_send_id": "server-send-1"},
        )

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
                "source_message_id": "msg-selected",
                "body_text": "Reply body",
                "created_at": "2026-05-21T09:00:00+00:00",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["mailbox_thread_id"], "group-1")
        self.assertEqual(mock_send.call_args.kwargs["mailbox_thread_id"], "group-1")
        self.assertEqual(mock_send.call_args.kwargs["request"].source_message_id, "msg-selected")


class MailboxSendRepositoryTests(unittest.TestCase):
    def test_claim_never_treats_stale_sending_as_resend_authority(self) -> None:
        connection = _SentRedactionConnection()
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            result = claim_pending_send(
                "postgresql://example/db",
                user_id="user-1",
                server_send_id="server-send-1",
            )

        self.assertIsNone(result)
        self.assertIn("state IN ('queued', 'failed')", connection.sql)
        self.assertNotIn("OR (state = 'sending'", connection.sql)
        self.assertNotIn("interval '2 minutes'", connection.sql)

    def test_sent_delivery_redacts_recipient_body_header_and_attachment_payloads(self) -> None:
        connection = _SentRedactionConnection()
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            result = mark_pending_send_sent(
                "postgresql://example/db",
                user_id="user-1",
                server_send_id="server-send-1",
                gmail_message_id="gmail-message-1",
                gmail_thread_id="gmail-thread-1",
            )

        self.assertIsNone(result)
        self.assertIn("to_json = '[]'", connection.sql)
        self.assertIn("subject = ''", connection.sql)
        self.assertIn("body_text = ''", connection.sql)
        self.assertIn("body_html = NULL", connection.sql)
        self.assertIn("headers_json = '{}'", connection.sql)
        self.assertIn("attachments_json = '[]'::jsonb", connection.sql)


class _NoRowResult:
    def mappings(self):
        return self

    def first(self):
        return None


class _SentRedactionConnection:
    def __init__(self) -> None:
        self.sql = ""

    def execute(self, statement, _params=None) -> _NoRowResult:
        self.sql = str(statement)
        return _NoRowResult()


if __name__ == "__main__":
    unittest.main()
