from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.db import mail_groups
from app.services.mail_groups import _gmail_row_from_canonical_thread


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

    def scalar_one_or_none(self):
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


class _DescriptorConnection:
    def __init__(self, *, row_count: int, has_more: bool) -> None:
        self.rows = [
            {"message_id": f"message-{index:03d}", "raw_payload_json": "{}"}
            for index in range(1, row_count + 1)
        ]
        self.has_more = has_more
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params: dict[str, object]) -> _Result:
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT message_id, raw_payload_json" in sql:
            return _Result(rows=self.rows)
        if "SELECT message_id" in sql and "raw_payload_json" not in sql:
            return _Result(scalar="message-026" if self.has_more else None)
        return _Result()


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
        "content_revision": 1,
        "attachment_descriptors_json": "[]",
        "mailbox_message_count": 1,
        "mailbox_body_ready": True,
        "mailbox_content_revision": f"revision-{thread_id}",
        "mailbox_attachment_count": 0,
    }


class MailboxQueryContractTests(unittest.TestCase):
    def test_mailbox_list_transfers_one_aggregate_projection_for_ten_thousand_message_thread(self) -> None:
        row = _message_row(
            "newest-message",
            "thread-with-ten-thousand-messages",
            labels=["INBOX", "UNREAD"],
            internal_date="2026-07-13T12:00:00+00:00",
            mailbox_latest_at="2026-07-13T12:00:00+00:00",
        )
        row.update(
            {
                "mailbox_message_count": 10_000,
                "mailbox_body_ready": False,
                "mailbox_content_revision": "stable-thread-revision-12345678",
                "mailbox_attachment_count": 237,
            }
        )
        connection = _Connection(_Result(rows=[row]))

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=10,
            )

        self.assertEqual(len(page.threads), 1)
        thread_id, messages = page.threads[0]
        self.assertEqual(thread_id, "thread-with-ten-thousand-messages")
        # The DB must transfer one bounded projection row, not 10,000 member
        # rows for Python to regroup.
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "newest-message")
        self.assertEqual(messages[0].raw_payload, {})
        self.assertIsNone(messages[0].html_body_sanitized)
        self.assertIsNone(messages[0].html_render_document)
        self.assertIsNone(messages[0].text_body)
        self.assertEqual(messages[0].mailbox_message_count, 10_000)
        self.assertFalse(messages[0].mailbox_body_ready)
        self.assertEqual(messages[0].mailbox_content_revision, "stable-thread-revision-12345678")
        self.assertEqual(messages[0].mailbox_attachment_count, 237)

        mailbox_row = _gmail_row_from_canonical_thread(thread_id, messages, "inbox", None)
        self.assertEqual(mailbox_row.message_count, 10_000)
        self.assertFalse(mailbox_row.body_ready)
        self.assertEqual(mailbox_row.latest_subject, "Subject newest-message")
        self.assertEqual(mailbox_row.latest_sender, "sender@example.com")
        self.assertEqual(mailbox_row.sender, "sender@example.com")
        self.assertEqual(mailbox_row.snippet, "body phrase")
        self.assertEqual(mailbox_row.latest_message_at, "2026-07-13T12:00:00+00:00")
        self.assertEqual(mailbox_row.label_ids, ["INBOX", "UNREAD"])
        self.assertTrue(mailbox_row.unread)
        self.assertTrue(mailbox_row.has_attachments)
        self.assertEqual(mailbox_row.attachment_count, 237)
        self.assertEqual(mailbox_row.content_revision, "stable-thread-revision-12345678")

        sql, _params = connection.calls[-1]
        self.assertNotIn("messages.*", sql)
        for body_column in (
            "messages.raw_payload_json",
            "messages.html_body_sanitized",
            "messages.html_render_document",
            "messages.text_body",
            "messages.body_fetch_error",
        ):
            self.assertNotIn(body_column, sql)
        self.assertNotIn("raw_payload_json", sql)
        self.assertNotIn("headers_json", sql)
        self.assertNotIn("extracted_signals_json", sql)
        self.assertIn("JOIN LATERAL", sql)
        self.assertIn("COUNT(*)::INTEGER", sql)
        self.assertIn("BOOL_AND", sql)
        self.assertIn("STRING_AGG", sql)
        self.assertIn("JSONB_ARRAY_LENGTH", sql.upper())

    def test_attachment_descriptor_migration_is_schema_only_and_constant_default(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "20260725_0031_mailbox_attachment_descriptors.py"
        )
        migration_source = migration_path.read_text(encoding="utf-8")
        upgrade_source = migration_source.split("def upgrade() -> None:", maxsplit=1)[1].split(
            "def downgrade() -> None:", maxsplit=1
        )[0]
        normalized_upgrade = " ".join(upgrade_source.upper().split())

        self.assertEqual(upgrade_source.count("op.execute("), 3)
        self.assertIn("ADD COLUMN IF NOT EXISTS ATTACHMENT_DESCRIPTORS_JSON", normalized_upgrade)
        self.assertIn("DEFAULT '[]'", normalized_upgrade)
        self.assertIn("ADD COLUMN IF NOT EXISTS ATTACHMENT_DESCRIPTORS_READY", normalized_upgrade)
        self.assertIn("DEFAULT FALSE", normalized_upgrade)
        self.assertIn("ADD COLUMN IF NOT EXISTS ATTACHMENT_DESCRIPTORS_COMPLETE", normalized_upgrade)
        self.assertNotIn("WITH RECURSIVE", normalized_upgrade)
        self.assertNotIn("UPDATE GMAIL_MESSAGES", normalized_upgrade)
        self.assertNotIn("SELECT ", normalized_upgrade)

    def test_legacy_attachment_descriptor_convergence_is_cursor_bounded_to_25(self) -> None:
        connection = _DescriptorConnection(row_count=25, has_more=True)
        with patch.object(mail_groups, "get_engine", return_value=object()), patch.object(
            mail_groups,
            "user_mail_write_transaction",
            return_value=nullcontext(connection),
        ):
            processed, has_more, cursor = (
                mail_groups.backfill_gmail_attachment_descriptors_page(
                    "postgresql://example/db",
                    user_id="user-1",
                    limit=10_000,
                    after_message_id="message-000",
                )
            )

        self.assertEqual(processed, 25)
        self.assertTrue(has_more)
        self.assertEqual(cursor, "message-025")
        selection_sql, selection_params = connection.calls[0]
        self.assertIn("LIMIT :limit", selection_sql)
        self.assertIn("message_id > :after_message_id", selection_sql)
        self.assertEqual(selection_params["limit"], 25)
        self.assertEqual(selection_params["after_message_id"], "message-000")
        self.assertEqual(
            sum("UPDATE gmail_messages" in sql for sql, _params in connection.calls),
            25,
        )
        self.assertFalse(
            any("UPDATE gmail_import_state" in sql for sql, _params in connection.calls)
        )

    def test_ten_thousand_message_reader_thread_transfers_only_requested_page(self) -> None:
        row = _message_row(
            "message-9901",
            "thread-huge",
            labels=["INBOX"],
            internal_date="2026-07-13T12:00:00+00:00",
            mailbox_latest_at="2026-07-13T12:00:00+00:00",
        )
        row.update(
            {
                "thread_total_messages": 10_000,
                "thread_incomplete_body_count": 0,
                "thread_content_revision": "stable-revision-12345678",
                "thread_latest_subject": "Newest subject",
                "content_revision": 4,
            }
        )
        connection = _Connection(_Result(rows=[row]))

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            page = mail_groups.get_gmail_thread_message_page(
                "postgresql://example/db",
                user_id="user-1",
                gmail_thread_id="thread-huge",
                limit=100,
                offset=9_900,
            )

        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(page.total_messages, 10_000)
        self.assertEqual([message.message_id for message in page.messages], ["message-9901"])
        self.assertEqual(page.latest_subject, "Newest subject")
        self.assertEqual(page.content_revision, "stable-revision-12345678")
        self.assertTrue(page.body_ready)
        self.assertEqual(len(connection.calls), 1)
        sql, params = connection.calls[0]
        self.assertIn("LEFT JOIN LATERAL", sql)
        self.assertIn("LIMIT :page_limit", sql)
        self.assertIn("OFFSET :page_offset", sql)
        self.assertIn("COUNT(*)::INTEGER AS thread_total_messages", sql)
        self.assertIn("STRING_AGG", sql)
        self.assertEqual(params["page_limit"], 100)
        self.assertEqual(params["page_offset"], 9_900)

    def test_folder_page_uses_label_for_eligibility_but_whole_thread_for_order_and_render(self) -> None:
        rows = [
            _message_row(
                "thread-a-sent",
                "thread-a",
                labels=["INBOX", "SENT", "UNREAD"],
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
        rows[0]["mailbox_message_count"] = 2
        connection = _Connection(_Result(rows=rows))

        with patch.object(mail_groups, "get_engine", return_value=_Engine(connection)):
            page = mail_groups.list_mailbox_thread_page(
                "postgresql://example/db",
                user_id="user-1",
                label="inbox",
                limit=2,
            )

        self.assertEqual([thread_id for thread_id, _messages in page.threads], ["thread-a", "thread-b"])
        self.assertEqual([message.message_id for message in page.threads[0][1]], ["thread-a-sent"])
        self.assertEqual(page.threads[0][1][0].mailbox_message_count, 2)
        self.assertEqual(page.threads[0][1][0].label_ids, ["INBOX", "SENT", "UNREAD"])
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
        self.assertIn("JOIN LATERAL", sql)
        self.assertIn("LIMIT 1", sql)
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
