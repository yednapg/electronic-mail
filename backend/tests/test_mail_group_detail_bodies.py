from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db.mail_groups import GmailMessageRecord, MailGroupDetail, MailGroupRecord, SmartInboxRowRecord
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
        snippet="TestUser, Thank you for choosing the California State University.",
        raw_payload={"payload": {"headers": []}},
        html_body_sanitized=None,
        html_render_document=None,
        text_body="TestUser, Thank you for choosing the California State University.",
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
    def test_synthetic_group_detail_expands_member_gmail_threads(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = replace(sample_group(), id="group-ai", group_key="ai:psu", membership_source="ai_batch", ai_title="State University application updates")
        sent = replace(
            sample_message(),
            message_id="sent-1",
            gmail_thread_id="thread-psu",
            label_ids=["SENT"],
            internal_date="2026-05-15T12:00:00+00:00",
            sender="TestUser <hi@example.com>",
            subject="Question About Reconsideration Request",
            text_body="Can I request reconsideration?",
        )
        reply = replace(
            sample_message(),
            message_id="reply-1",
            gmail_thread_id="thread-psu",
            label_ids=["INBOX"],
            internal_date="2026-05-15T13:00:00+00:00",
            sender="State University <university-admissions@example.edu>",
            subject="RE: Question About Reconsideration Request",
            text_body="Yes, you may request reconsideration.",
        )

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[reply]),
        ), patch(
            "app.services.mail_groups.list_messages_for_gmail_thread",
            side_effect=[[], [sent, reply]],
        ), patch("app.services.mail_groups.enqueue_job"):
            response = build_group_detail_response(settings, user_id="user-1", group_id="group-ai")

        self.assertIsNotNone(response)
        self.assertEqual(response.total_messages, 2)
        self.assertEqual([message.id for message in response.messages], ["sent-1", "reply-1"])

    def test_smart_row_detail_returns_messages_from_group_reader_target(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        smart_row = SmartInboxRowRecord(
            id="user-1:smart-row-northstar-wire",
            public_id="smart-row-northstar-wire",
            user_id="user-1",
            row_key="mail-object:northstar-wire",
            row_type="verified_group",
            title="Wire transfer status with Northstar Bank",
            summary="Two Northstar emails track the same wire transfer.",
            source_thread_ids=["thread-wire", "thread-reply"],
            source_message_ids=["msg-wire", "msg-reply"],
            latest_message_at="2026-06-07T11:00:00+00:00",
            latest_message_id="msg-reply",
            action_type="review",
        )
        wire = replace(
            sample_message(),
            message_id="msg-wire",
            gmail_thread_id="thread-wire",
            internal_date="2026-06-07T10:00:00+00:00",
            subject="Northstar international wire registered",
        )
        reply = replace(
            sample_message(),
            message_id="msg-reply",
            gmail_thread_id="thread-reply",
            internal_date="2026-06-07T11:00:00+00:00",
            subject="Northstar response on wire request",
        )

        def thread_messages(_database_url: str, *, user_id: str, gmail_thread_id: str):
            self.assertEqual(user_id, "user-1")
            return {"thread-wire": [wire], "thread-reply": [reply]}.get(gmail_thread_id, [])

        with patch("app.services.mail_groups.get_smart_inbox_row", return_value=smart_row) as get_row, patch(
            "app.services.mail_groups.list_messages_by_ids",
            return_value=[wire, reply],
        ) as list_by_ids, patch(
            "app.services.mail_groups.list_messages_for_gmail_thread",
            side_effect=thread_messages,
        ), patch("app.services.mail_groups.enqueue_job"):
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="smart-row:smart-row-northstar-wire",
                include_summary=False,
            )

        self.assertIsNotNone(response)
        get_row.assert_called_once_with("postgresql://example/db", user_id="user-1", row_id="smart-row-northstar-wire")
        list_by_ids.assert_called_once_with("postgresql://example/db", user_id="user-1", message_ids=["msg-wire", "msg-reply"])
        self.assertEqual(response.gmail_thread_id, "smart-row:smart-row-northstar-wire")
        self.assertEqual(response.title, "Wire transfer status with Northstar Bank")
        self.assertIsNone(response.summary)
        self.assertEqual(response.total_messages, 2)
        self.assertEqual([message.id for message in response.messages], ["msg-wire", "msg-reply"])

    def test_smart_row_detail_generates_summary_on_request_instead_of_using_row_summary(self) -> None:
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            openai_configured=True,
            openai_api_key="test-key",
            openai_model="gpt-test",
        )
        smart_row = SmartInboxRowRecord(
            id="user-1:smart-row-northstar-wire",
            public_id="smart-row-northstar-wire",
            user_id="user-1",
            row_key="mail-object:northstar-wire",
            row_type="verified_group",
            title="Wire transfer status with Northstar Bank",
            summary="Two Northstar emails track the same wire transfer.",
            source_thread_ids=["thread-wire"],
            source_message_ids=["msg-wire"],
            latest_message_at="2026-06-07T10:00:00+00:00",
            latest_message_id="msg-wire",
            action_type="review",
        )
        message = replace(
            sample_message(),
            message_id="msg-wire",
            gmail_thread_id="thread-wire",
            internal_date="2026-06-07T10:00:00+00:00",
            subject="Northstar international wire registered",
        )

        with patch("app.services.mail_groups.get_smart_inbox_row", return_value=smart_row), patch(
            "app.services.mail_groups.list_messages_by_ids",
            return_value=[message],
        ), patch(
            "app.services.mail_groups.list_messages_for_gmail_thread",
            return_value=[message],
        ), patch("app.services.mail_groups.enqueue_job"), patch(
            "app.services.mail_groups._generate_requested_reader_summary",
            return_value="Northstar registered the wire transfer and the bank response is pending.",
        ) as mock_generate:
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="smart-row:smart-row-northstar-wire",
                include_summary=True,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.summary, "Northstar registered the wire transfer and the bank response is pending.")
        mock_generate.assert_called_once()

    def test_group_detail_omits_summary_until_requested(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = sample_group()
        message = sample_message()

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[message]),
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch("app.services.mail_groups.enqueue_job"):
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="group-1",
            )

        self.assertIsNotNone(response)
        self.assertIsNone(response.summary)

    def test_group_detail_includes_summary_when_requested(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        group = sample_group()
        message = sample_message()

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[message]),
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch("app.services.mail_groups.enqueue_job"):
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="group-1",
                include_summary=True,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.summary, "Cal State Apply started the application.")

    def test_group_detail_generates_missing_summary_only_when_requested(self) -> None:
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            openai_configured=True,
            openai_api_key="test-key",
            openai_model="gpt-test",
        )
        group = replace(sample_group(), ai_summary="")
        message = sample_message()

        with patch(
            "app.services.mail_groups.get_mail_group_detail",
            return_value=MailGroupDetail(group=group, messages=[message]),
        ), patch("app.services.mail_groups.list_messages_for_gmail_thread", return_value=[]), patch(
            "app.services.mail_groups.enqueue_job"
        ), patch(
            "app.services.mail_groups._generate_requested_reader_summary",
            return_value="Cal State Apply started the application and no action is needed yet.",
        ) as mock_generate, patch("app.services.mail_groups.update_mail_group_ai_summary") as mock_update:
            response = build_group_detail_response(
                settings,
                user_id="user-1",
                group_id="group-1",
                include_summary=True,
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.summary, "Cal State Apply started the application and no action is needed yet.")
        mock_generate.assert_called_once()
        mock_update.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            group_id="group-1",
            ai_summary="Cal State Apply started the application and no action is needed yet.",
        )

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
        self.assertEqual(response.messages[0].body, "TestUser, Thank you for choosing the California State University.")
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
