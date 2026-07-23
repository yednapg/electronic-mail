from __future__ import annotations

from contextlib import nullcontext
import json
import unittest
from unittest.mock import patch

from app.db import mail_groups


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        scalar: int | bool = 0,
        first_row: dict[str, object] | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._first_row = first_row

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self):
        return self._first_row

    def scalar_one(self) -> int:
        return self._scalar


class _Connection:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params: dict[str, object]) -> _Result:
        self.calls.append((str(statement), params))
        return self.result


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self):
        return nullcontext(self.connection)


class _QueuedConnection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params: dict[str, object]) -> _Result:
        self.calls.append((str(statement), params))
        return self.results.pop(0)


def _message_row(
    message_id: str,
    thread_id: str,
    *,
    labels: list[str],
    internal_date: str,
    mailbox_latest_at: str,
) -> dict[str, object]:
    return {
        "mailbox_thread_id": thread_id,
        "mailbox_latest_matching_at": mailbox_latest_at,
        "mailbox_order_position": None,
        "user_id": "user-1",
        "message_id": message_id,
        "gmail_thread_id": thread_id,
        "history_id": "10",
        "label_ids_json": json.dumps(labels),
        "internal_date": internal_date,
        "subject": f"Subject {message_id}",
        "sender": "sender@example.com",
        "recipients_json": json.dumps({"to": "recipient@example.com"}),
        "headers_json": "{}",
        "snippet": "body phrase",
        "raw_payload_json": "{}",
        "html_body_sanitized": None,
        "html_render_document": None,
        "text_body": "body phrase",
        "extracted_signals_json": "{}",
        "body_hash": f"hash-{message_id}",
        "created_at": internal_date,
        "updated_at": internal_date,
        "body_fetch_status": "fetched",
        "body_fetched_at": internal_date,
        "body_fetch_error": None,
        "render_doc_bytes": 0,
        "ai_title": None,
        "ai_title_generated_at": None,
    }


class MailboxQueryContractTests(unittest.TestCase):
    def test_folder_page_uses_label_for_eligibility_but_whole_thread_for_order_and_render(self) -> None:
        rows = [
            _message_row(
                "thread-a-inbox",
                "thread-a",
                labels=["INBOX", "UNREAD"],
                internal_date="2026-07-13T09:00:00+00:00",
                mailbox_latest_at="2026-07-13T12:00:00+00:00",
            ),
            _message_row(
                "thread-a-sent",
                "thread-a",
                labels=["SENT"],
                internal_date="2026-07-13T12:00:00+00:00",
                mailbox_latest_at="2026-07-13T12:00:00+00:00",
            ),
            _message_row(
                "thread-b-inbox",
                "thread-b",
                labels=["INBOX"],
                internal_date="2026-07-13T11:00:00+00:00",
                mailbox_latest_at="2026-07-13T11:00:00+00:00",
            ),
            _message_row(
                "thread-c-inbox",
                "thread-c",
                labels=["INBOX"],
                internal_date="2026-07-13T10:00:00+00:00",
                mailbox_latest_at="2026-07-13T10:00:00+00:00",
            ),
        ]
        connection = _Connection(_Result(rows=rows))

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=2,
            )

        self.assertEqual([thread_id for thread_id, _messages in page.threads], ["thread-a", "thread-b"])
        self.assertEqual([message.message_id for message in page.threads[0][1]], ["thread-a-inbox", "thread-a-sent"])
        self.assertEqual(page.loaded_threads, 2)
        self.assertIsNotNone(page.next_cursor)
        self.assertEqual(
            mail_groups.decode_mailbox_cursor(page.next_cursor or ""),
            ("2026-07-13T11:00:00+00:00", "thread-b"),
        )
        sql, params = connection.calls[-1]
        self.assertIn("WITH eligible_threads AS", sql)
        self.assertIn("messages.label_ids_json::jsonb ? 'INBOX'", sql)
        self.assertIn("JOIN gmail_messages AS all_messages", sql)
        self.assertIn("MAX(COALESCE(all_messages.internal_date, all_messages.updated_at))", sql)
        self.assertIn("JOIN gmail_messages AS messages", sql)
        self.assertEqual(params["limit"], 3)

    def test_complete_gmail_snapshot_controls_order_and_uses_stable_generation_cursor(self) -> None:
        rows = [
            _message_row(
                "thread-b-message",
                "thread-b",
                labels=["INBOX"],
                internal_date="2026-07-13T10:00:00+00:00",
                mailbox_latest_at="2026-07-13T10:00:00+00:00",
            ),
            _message_row(
                "thread-a-message",
                "thread-a",
                labels=["INBOX"],
                internal_date="2026-07-13T12:00:00+00:00",
                mailbox_latest_at="2026-07-13T12:00:00+00:00",
            ),
            _message_row(
                "thread-c-message",
                "thread-c",
                labels=["INBOX"],
                internal_date="2026-07-13T09:00:00+00:00",
                mailbox_latest_at="2026-07-13T09:00:00+00:00",
            ),
        ]
        for position, row in enumerate(rows):
            row["mailbox_order_position"] = position
        first_connection = _QueuedConnection(
            [
                _Result(first_row={"active_generation_id": "generation-1", "previous_generation_id": None}),
                _Result(scalar=False),
                _Result(rows=rows),
            ]
        )

        with patch.object(mail_groups, "get_engine", return_value=_Engine(first_connection)):
            first_page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=2,
            )

        self.assertEqual([thread_id for thread_id, _messages in first_page.threads], ["thread-b", "thread-a"])
        self.assertEqual(first_page.order_source, "gmail")
        self.assertIsNotNone(first_page.next_cursor)
        cursor = mail_groups._decode_mailbox_cursor_state(first_page.next_cursor or "")
        self.assertEqual(cursor.generation_id, "generation-1")
        self.assertEqual(cursor.position, 1)
        self.assertEqual(cursor.thread_key, "thread-a")
        ranked_sql, ranked_params = first_connection.calls[-1]
        self.assertIn("JOIN gmail_thread_order_entries AS thread_order", ranked_sql)
        self.assertIn("ORDER BY thread_order.position ASC", ranked_sql)
        self.assertEqual(ranked_params["user_id"], "user-1")
        self.assertEqual(ranked_params["mailbox_label"], "inbox")
        self.assertEqual(ranked_params["order_generation_id"], "generation-1")

        second_row = rows[-1]
        second_connection = _QueuedConnection(
            [
                _Result(
                    first_row={
                        "active_generation_id": "generation-2",
                        "previous_generation_id": "generation-1",
                    }
                ),
                _Result(rows=[second_row]),
            ]
        )
        with patch.object(mail_groups, "get_engine", return_value=_Engine(second_connection)):
            second_page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=2,
                cursor=first_page.next_cursor,
            )

        self.assertEqual([thread_id for thread_id, _messages in second_page.threads], ["thread-c"])
        self.assertEqual(second_page.order_source, "gmail")
        second_sql, second_params = second_connection.calls[-1]
        self.assertIn("> (:cursor_position, :cursor_thread_key)", second_sql)
        self.assertEqual(second_params["order_generation_id"], "generation-1")
        self.assertEqual(second_params["cursor_position"], 1)

    def test_incomplete_gmail_snapshot_falls_back_to_existing_date_order(self) -> None:
        row = _message_row(
            "thread-a-message",
            "thread-a",
            labels=["INBOX"],
            internal_date="2026-07-13T12:00:00+00:00",
            mailbox_latest_at="2026-07-13T12:00:00+00:00",
        )
        connection = _QueuedConnection(
            [
                _Result(first_row={"active_generation_id": "generation-1", "previous_generation_id": None}),
                _Result(scalar=True),
                _Result(rows=[row]),
            ]
        )

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=10,
            )

        self.assertEqual(page.order_source, "date")
        sql, _params = connection.calls[-1]
        self.assertNotIn("JOIN gmail_thread_order_entries AS thread_order", sql)
        self.assertIn("ORDER BY latest_matching_at DESC", sql)

    def test_gmail_order_cursor_is_label_scoped_and_expires_after_two_generations(self) -> None:
        cursor = mail_groups.encode_gmail_order_cursor(
            label="inbox",
            generation_id="generation-1",
            position=4,
            thread_key="thread-a",
        )
        wrong_label_connection = _QueuedConnection([])
        with patch.object(mail_groups, "get_engine", return_value=_Engine(wrong_label_connection)):
            with self.assertRaises(mail_groups.MailboxCursorError):
                mail_groups.list_mailbox_thread_page(
                    "postgresql://example/db",
                    user_id="user-1",
                    label="sent",
                    cursor=cursor,
                )

        expired_connection = _QueuedConnection(
            [_Result(first_row={"active_generation_id": "generation-3", "previous_generation_id": "generation-2"})]
        )
        with patch.object(mail_groups, "get_engine", return_value=_Engine(expired_connection)):
            with self.assertRaises(mail_groups.MailboxCursorError):
                mail_groups.list_mailbox_thread_page(
                    "postgresql://example/db",
                    user_id="user-1",
                    label="inbox",
                    cursor=cursor,
                )

    def test_counts_are_thread_wide_and_simple_search_covers_sender_recipient_subject_and_body(self) -> None:
        connection = _Connection(_Result(scalar=7))

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            count = mail_groups.count_mailbox_threads(
                "postgresql://example/db",
                user_id="user-1",
                label="starred",
                search_query="contract phrase",
                unread_only=True,
            )

        self.assertEqual(count, 7)
        sql, params = connection.calls[0]
        self.assertIn("COUNT(*)", sql)
        self.assertIn("GROUP BY thread_key", sql)
        self.assertIn("label_ids_json::jsonb ? 'STARRED'", sql)
        self.assertIn("label_ids_json::jsonb ? 'UNREAD'", sql)
        for field in ("messages.subject", "messages.sender", "messages.recipients_json", "messages.snippet", "messages.text_body"):
            self.assertIn(field, sql)
        self.assertEqual(params["search_query"], "contract phrase")

    def test_all_mail_excludes_spam_and_trash_while_archive_excludes_system_folders(self) -> None:
        all_clause = mail_groups._mailbox_label_clause("messages", "all")
        archive_clause = mail_groups._mailbox_label_clause("messages", "archive")

        self.assertIn("NOT (messages.label_ids_json::jsonb ? 'SPAM')", all_clause)
        self.assertIn("NOT (messages.label_ids_json::jsonb ? 'TRASH')", all_clause)
        for label in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH"):
            self.assertIn(f"NOT (messages.label_ids_json::jsonb ? '{label}')", archive_clause)


if __name__ == "__main__":
    unittest.main()
