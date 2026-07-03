from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.schemas.domain import GmailThreadRow, GmailThreadSection, MailboxResponse, ThreadReaderResponse
from audit_smart_inbox import build_contract_audit_payload


class SmartInboxAuditScriptTests(unittest.TestCase):
    def test_contract_audit_passes_ready_inbox_without_default_reader_summaries(self) -> None:
        mailbox = _mailbox(_row("thread-1", "Apple order W123456789 delivered", message_count=2))
        reader = _thread("thread-1", summary=None)

        with patch("audit_smart_inbox.build_group_detail_response", return_value=reader) as mock_detail:
            payload = build_contract_audit_payload(
                SimpleNamespace(database_path="postgresql://example/db"),
                user_id="user-1",
                mailbox=mailbox,
                quality_blocking_issue_count=0,
                state=_ready_state(),
                reader_sample_limit=4,
            )

        self.assertTrue(payload["passed"])
        self.assertEqual(payload["blocking_issue_count"], 0)
        self.assertEqual(payload["reader_default_summary"]["sampled_rows"], 1)
        self.assertEqual(payload["reader_default_summary"]["unexpected_summary_count"], 0)
        mock_detail.assert_called_once()

    def test_contract_audit_fails_missing_first_run_state_and_quality_blockers(self) -> None:
        payload = build_contract_audit_payload(
            SimpleNamespace(database_path="postgresql://example/db"),
            user_id="user-1",
            mailbox=_mailbox(),
            quality_blocking_issue_count=2,
            state=None,
            reader_sample_limit=4,
        )

        self.assertFalse(payload["passed"])
        codes = {issue["code"] for issue in payload["issues"]}
        self.assertIn("first_run_state_missing", codes)
        self.assertIn("empty_inbox", codes)
        self.assertIn("quality_blockers", codes)

    def test_contract_audit_fails_when_reader_returns_summary_without_request(self) -> None:
        mailbox = _mailbox(_row("thread-1", "HDFC wire transfer processed"))
        reader = _thread("thread-1", summary="This should only appear after explicit summary request.")

        with patch("audit_smart_inbox.build_group_detail_response", return_value=reader):
            payload = build_contract_audit_payload(
                SimpleNamespace(database_path="postgresql://example/db"),
                user_id="user-1",
                mailbox=mailbox,
                quality_blocking_issue_count=0,
                state=_ready_state(),
                reader_sample_limit=4,
            )

        self.assertFalse(payload["passed"])
        self.assertIn("reader_default_summary_present", {issue["code"] for issue in payload["issues"]})
        self.assertEqual(payload["reader_default_summary"]["unexpected_summary_count"], 1)

    def test_contract_audit_can_skip_reader_sampling(self) -> None:
        detail = Mock()
        with patch("audit_smart_inbox.build_group_detail_response", detail):
            payload = build_contract_audit_payload(
                SimpleNamespace(database_path="postgresql://example/db"),
                user_id="user-1",
                mailbox=_mailbox(_row("thread-1", "Apple order W123456789 delivered")),
                quality_blocking_issue_count=0,
                state=_ready_state(),
                sample_reader_defaults=False,
            )

        self.assertTrue(payload["passed"])
        self.assertTrue(payload["reader_default_summary"]["skipped"])
        detail.assert_not_called()


def _ready_state() -> SimpleNamespace:
    return SimpleNamespace(
        first_batch_imported_at="2026-06-20T10:00:00+00:00",
        first_groups_ready_at="2026-06-20T10:00:30+00:00",
        hot_window_completed_at="2026-06-20T10:00:45+00:00",
        full_backfill_completed_at=None,
        last_sync_error=None,
    )


def _mailbox(*rows: GmailThreadRow) -> MailboxResponse:
    return MailboxResponse(
        label="inbox",
        total_threads=len(rows),
        sections=[GmailThreadSection(id="today", title="Today", rows=list(rows))] if rows else [],
    )


def _row(thread_id: str, title: str, *, message_count: int = 1) -> GmailThreadRow:
    return GmailThreadRow(
        thread_id=thread_id,
        entity_id=thread_id,
        title=title,
        latest_source_record_id=f"{thread_id}:message",
        latest_received_at="2026-06-20T10:00:00+00:00",
        latest_subject=title,
        latest_sender="Sender <sender@example.com>",
        sender="Sender",
        message_count=message_count,
        presentation_status="ai_ready",
        grouping_metadata={"reference": {"value": "W123456789"}} if message_count > 1 else {},
    )


def _thread(thread_id: str, *, summary: str | None) -> ThreadReaderResponse:
    return ThreadReaderResponse(
        entity_id=thread_id,
        user_id="user-1",
        source="gmail",
        gmail_thread_id=thread_id,
        subject="Subject",
        title="Subject",
        summary=summary,
        total_messages=1,
        limit=50,
        offset=0,
        has_more=False,
        messages=[],
    )


if __name__ == "__main__":
    unittest.main()
