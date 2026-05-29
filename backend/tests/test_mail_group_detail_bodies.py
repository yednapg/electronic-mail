from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db.mail_groups import GmailMessageRecord, MailGroupDetail, MailGroupRecord
from app.services.mail_groups import build_group_detail_response


def sample_message() -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id="msg-1",
        gmail_thread_id="thread-1",
        history_id="10",
        label_ids=["INBOX"],
        internal_date="2026-05-15T12:00:00+00:00",
        subject="California State University application started",
        sender="California State University <apply@example.com>",
        recipients={"to": "me@example.com"},
        headers={"subject": "California State University application started"},
        snippet="Gaurav, Thank you for choosing the California State University.",
        raw_payload={"payload": {"headers": []}},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="Gaurav, Thank you for choosing the California State University.",
        extracted_signals={"sender_domain": "example.com"},
        body_hash="hash-1",
        created_at="2026-05-15T12:00:00+00:00",
        updated_at="2026-05-15T12:00:00+00:00",
    )


RICH_APPLICATION_HTML = '<html><body><table style="width:100%"><tr><td><img src="https://example.com/logo.png">Full application email</td></tr></table></body></html>'


def sample_group() -> MailGroupRecord:
    return MailGroupRecord(
        id="group-1",
        user_id="user-1",
        group_key="gmail-thread:thread-1",
        group_type="conversation",
        status="active",
        enrichment_status="ready",
        membership_source="gmail_thread",
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="California State University application started",
        ai_summary="Cal State Apply started the application.",
        labels=["application"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=False,
        latest_message_at="2026-05-15T12:00:00+00:00",
        latest_message_id="msg-1",
        generated_from_hash="hash-1",
        generated_at="2026-05-15T12:00:00+00:00",
        created_at="2026-05-15T12:00:00+00:00",
        updated_at="2026-05-15T12:00:00+00:00",
    )


class MailGroupDetailBodyTests(unittest.TestCase):
    def test_reader_fetches_full_body_immediately_when_cached_body_is_only_metadata_snippet(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = sample_group()
        metadata_only = sample_message()
        hydrated = replace(metadata_only, html_body_sanitized=RICH_APPLICATION_HTML, html_render_document=RICH_APPLICATION_HTML, text_body="Full application email")

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            side_effect=[
                MailGroupDetail(group=group, messages=[metadata_only]),
                MailGroupDetail(group=group, messages=[hydrated]),
            ],
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            body_fetch.return_value = 1
            response = build_group_detail_response(settings, user_id="user-1", group_id="group-1")

        self.assertIsNotNone(response)
        body_fetch.assert_called_once_with(settings, user_id="user-1", group_id="group-1", gmail_thread_id="")
        enqueue_job.assert_not_called()
        self.assertEqual(response.messages[0].body, "Full application email")
        self.assertEqual(response.messages[0].html_body, RICH_APPLICATION_HTML)
        self.assertEqual(response.messages[0].html_render_document, RICH_APPLICATION_HTML)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertTrue(response.messages[0].reader.original_html_available)

    def test_reader_enqueues_full_body_fetch_when_immediate_fetch_fails(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = sample_group()
        metadata_only = sample_message()

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[metadata_only]),
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            body_fetch.side_effect = RuntimeError("gmail timeout")
            enqueue_job.return_value = SimpleNamespace(id="job-1")
            response = build_group_detail_response(settings, user_id="user-1", group_id="group-1")

        self.assertIsNotNone(response)
        body_fetch.assert_called_once_with(settings, user_id="user-1", group_id="group-1", gmail_thread_id="")
        enqueue_job.assert_called_once_with(
            "postgresql://example/db",
            kind="gmail_body_fetch",
            queue="reader",
            user_id="user-1",
            dedupe_key="gmail-body-fetch:user-1:group-1",
            priority=90,
            payload={"user_id": "user-1", "group_id": "group-1"},
        )
        self.assertEqual(response.messages[0].body, "Gaurav, Thank you for choosing the California State University.")
        self.assertIsNone(response.messages[0].html_body)
        self.assertIsNone(response.messages[0].html_render_document)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertFalse(response.messages[0].reader.original_html_available)

    def test_reader_returns_cached_html_without_enqueuing_body_fetch(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = sample_group()
        hydrated = replace(sample_message(), html_body_sanitized=RICH_APPLICATION_HTML, html_render_document=RICH_APPLICATION_HTML, text_body="Full application email")

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[hydrated]),
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch("app.services.mail_groups.enqueue_job") as enqueue_job, patch("app.services.gmail_importer.run_gmail_body_fetch") as body_fetch:
            response = build_group_detail_response(settings, user_id="user-1", group_id="group-1")

        self.assertIsNotNone(response)
        body_fetch.assert_not_called()
        enqueue_job.assert_not_called()
        self.assertEqual(response.messages[0].body, "Full application email")
        self.assertEqual(response.messages[0].html_body, RICH_APPLICATION_HTML)
        self.assertEqual(response.messages[0].html_render_document, RICH_APPLICATION_HTML)
        self.assertIsNotNone(response.messages[0].reader)
        self.assertEqual(response.messages[0].reader.primary_text, "Full application email")
        self.assertTrue(response.messages[0].reader.original_html_available)


if __name__ == "__main__":
    unittest.main()
