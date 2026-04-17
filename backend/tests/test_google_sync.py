from __future__ import annotations

import unittest

from app.services.integrations.google import (
    extract_participants,
    normalize_gmail_messages,
    resolve_gmail_sync_scope,
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


class GmailSyncNormalizationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
