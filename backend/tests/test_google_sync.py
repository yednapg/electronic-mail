from __future__ import annotations

from base64 import urlsafe_b64encode
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.schemas.domain import SourceRecord
from app.services.integrations.google import (
    GMAIL_PAGE_SIZE,
    extract_participants,
    fetch_google_source_records,
    gmail_message_snapshot_from_payload,
    normalize_gmail_messages,
    resolve_gmail_sync_scope,
    to_gmail_source_record,
)


def build_message(
    *,
    message_id: str,
    thread_id: str,
    subject: str = "Subject",
    sender: str = "Sender <sender@example.com>",
    to: str = "alpha@example.com, Beta <beta@example.com>",
    cc: str = "",
    label_ids: list[str] | None = None,
    internal_date_ms: str = "1712304000000",
    snippet: str = "Snippet body",
    date_header: str | None = "Fri, 05 Apr 2024 10:00:00 +0000",
) -> dict[str, object]:
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
    ]

    if date_header is not None:
        headers.append({"name": "Date", "value": date_header})

    if cc:
        headers.append({"name": "Cc", "value": cc})

    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": internal_date_ms,
        "snippet": snippet,
        "labelIds": label_ids or ["INBOX"],
        "historyId": "12345",
        "payload": {
            "headers": headers,
            "mimeType": "text/plain",
            "body": {},
        },
    }


def encode_body(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("utf-8").rstrip("=")


class GmailSyncNormalizationTests(unittest.TestCase):
    def test_page_size_uses_gmail_maximum_for_faster_listing(self) -> None:
        self.assertEqual(GMAIL_PAGE_SIZE, 500)

    def test_full_scope_keeps_archived_messages(self) -> None:
        records = normalize_gmail_messages(
            [
                build_message(
                    message_id="message-1",
                    thread_id="thread-1",
                    label_ids=["CATEGORY_UPDATES"],
                )
            ],
            scope="full",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "message-1")

    def test_recent_scope_filters_by_time_but_not_by_label(self) -> None:
        records = normalize_gmail_messages(
            [
                build_message(
                    message_id="recent-message",
                    thread_id="thread-1",
                    internal_date_ms="4102444800000",
                    label_ids=["ARCHIVED_ONLY"],
                    date_header=None,
                ),
                build_message(
                    message_id="old-message",
                    thread_id="thread-2",
                    internal_date_ms="946684800000",
                    label_ids=["ARCHIVED_ONLY"],
                    date_header=None,
                ),
            ],
            scope="recent",
            recent_days=30,
        )

        self.assertEqual([record.id for record in records], ["recent-message"])

    def test_gmail_payload_includes_participants(self) -> None:
        [record] = normalize_gmail_messages(
            [
                build_message(
                    message_id="message-1",
                    thread_id="thread-1",
                    cc="gamma@example.com, Sender <sender@example.com>",
                )
            ],
            scope="full",
        )

        self.assertEqual(
            record.raw_payload["participants"],
            ["sender@example.com", "alpha@example.com", "beta@example.com", "gamma@example.com"],
        )

    def test_plain_text_body_is_decoded_and_capped_without_blob_lines(self) -> None:
        blob = "A" * 120
        message = build_message(message_id="message-plain", thread_id="thread-plain")
        message["payload"]["body"] = {"data": encode_body(f"Please review this.\n{blob}\nThanks.")}

        record = to_gmail_source_record(message)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.raw_payload["body"], "Please review this.\nThanks.")

    def test_html_body_is_converted_to_readable_text(self) -> None:
        message = build_message(message_id="message-html", thread_id="thread-html")
        message["payload"] = {
            "headers": message["payload"]["headers"],
            "mimeType": "text/html",
            "body": {"data": encode_body("<div>Hello</div><div>Please reply today.</div>")},
        }

        record = to_gmail_source_record(message)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertIn("Hello Please reply today.", record.raw_payload["body"])

    def test_attachments_are_metadata_only_and_not_body_text(self) -> None:
        message = build_message(message_id="message-attachment", thread_id="thread-attachment")
        message["payload"] = {
            "headers": message["payload"]["headers"],
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": encode_body("Readable mail body.")},
                },
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "statement.pdf",
                    "body": {"data": encode_body("RAWPDFDATA" * 100), "size": 1200, "attachmentId": "att-1"},
                    "headers": [{"name": "Content-Disposition", "value": "attachment; filename=statement.pdf"}],
                },
            ],
        }

        record = to_gmail_source_record(message)
        snapshot = gmail_message_snapshot_from_payload(message)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.raw_payload["body"], "Readable mail body.")
        self.assertNotIn("RAWPDFDATA", record.raw_payload["body"])
        self.assertEqual(record.raw_payload["attachments"][0]["filename"], "statement.pdf")
        self.assertNotIn("payload", snapshot.raw_payload)
        self.assertEqual(snapshot.raw_payload["attachments"][0]["attachment_id"], "att-1")

    def test_extract_participants_deduplicates_addresses(self) -> None:
        self.assertEqual(
            extract_participants(
                "Sender <sender@example.com>",
                "sender@example.com, second@example.com",
            ),
            ["sender@example.com", "second@example.com"],
        )

    def test_invalid_scope_defaults_to_full(self) -> None:
        self.assertEqual(resolve_gmail_sync_scope("unknown"), "full")

    @patch("app.services.integrations.google.fetch_upcoming_calendar_records")
    @patch("app.services.integrations.google.sync_gmail_source_records")
    @patch("app.services.integrations.google.build")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_collect_records_false_still_returns_changed_gmail_records(
        self,
        mock_credentials: Mock,
        mock_build: Mock,
        mock_sync: Mock,
        mock_calendar: Mock,
    ) -> None:
        with TemporaryDirectory() as tmp_dir:
            settings = SimpleNamespace(database_path=Path(tmp_dir) / "gmail.db")
            gmail_record = SourceRecord(
                id="gmail-1",
                user_id="local-user",
                source="gmail",
                thread_id="thread-1",
                raw_payload={"subject": "Reply needed"},
                received_at="2026-05-06T10:00:00+00:00",
            )
            calendar_record = SourceRecord(
                id="calendar-1",
                user_id="local-user",
                source="calendar",
                thread_id="calendar-1",
                raw_payload={"summary": "Meeting"},
                received_at="2026-05-06T09:00:00+00:00",
            )
            mock_credentials.return_value = object()
            mock_build.side_effect = [object(), object()]
            mock_sync.return_value = [gmail_record]
            mock_calendar.return_value = [calendar_record]

            records = fetch_google_source_records(settings, collect_records=False)

            self.assertEqual([record.id for record in records], ["gmail-1", "calendar-1"])
            self.assertFalse(mock_sync.call_args.kwargs["collect_records"])


if __name__ == "__main__":
    unittest.main()
