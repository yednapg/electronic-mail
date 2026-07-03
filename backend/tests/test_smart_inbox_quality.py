from __future__ import annotations

import unittest

from app.schemas.domain import GmailThreadChildRow, GmailThreadRow, GmailThreadSection, MailboxResponse, SmartInboxResponse, SmartInboxRow, SmartInboxSection
from app.services.smart_inbox_quality import audit_smart_inbox_quality, audit_smart_inbox_response_quality, smart_inbox_response_is_product_ready


class SmartInboxQualityTests(unittest.TestCase):
    def test_reference_group_with_stateful_title_passes(self) -> None:
        report = audit_smart_inbox_quality(
            _mailbox(
                _row(
                    thread_id="mailbox-cluster:service-request",
                    title="Northstar Bank service request 900000002 updated",
                    sender="Northstar Bank",
                    message_count=2,
                    grouping_metadata={
                        "source": "mailbox_display_cluster",
                        "reference": {"signal_name": "ticket_id", "value": "900000002"},
                    },
                    children=[
                        _child("registered service request 900000002"),
                        _child("updated service request 900000002"),
                    ],
                )
            )
        )

        self.assertEqual(report.total_rows, 1)
        self.assertEqual(report.grouped_rows, 1)
        self.assertEqual(report.blocking_issue_count, 0)
        self.assertEqual(report.issues, [])

    def test_sender_stream_group_is_blocking(self) -> None:
        report = audit_smart_inbox_quality(
            _mailbox(
                _row(
                    thread_id="mailbox-cluster:sender-stream",
                    title="Provider updates",
                    sender="Provider",
                    message_count=3,
                    grouping_metadata={"source": "provider_stream", "cluster_key": "newsletter:provider-stream:provider"},
                    children=[
                        _child("invoice available"),
                        _child("security alert"),
                        _child("new webinar"),
                    ],
                )
            )
        )

        codes = {issue.code for issue in report.issues}
        self.assertIn("sender_stream_group", codes)
        self.assertIn("vague_title", codes)
        self.assertIn("group_without_task_evidence", codes)
        self.assertGreaterEqual(report.blocking_issue_count, 3)

    def test_fallback_or_vague_single_row_title_is_blocking(self) -> None:
        report = audit_smart_inbox_quality(
            _mailbox(
                _row(
                    thread_id="thread-1",
                    title="Account update",
                    sender="Bank",
                    message_count=1,
                    presentation_status="fallback",
                )
            )
        )

        self.assertEqual({issue.code for issue in report.issues}, {"title_not_ai_ready", "vague_title"})
        self.assertEqual(report.blocking_issue_count, 2)

    def test_mixed_child_tasks_are_blocking_even_with_reference_metadata(self) -> None:
        report = audit_smart_inbox_quality(
            _mailbox(
                _row(
                    thread_id="mailbox-cluster:mixed",
                    title="Apple order W123456789 delivered",
                    sender="Apple",
                    message_count=2,
                    grouping_metadata={"reference": {"signal_name": "order_id", "value": "W123456789"}},
                    children=[
                        _child("Apple order W123456789 shipped"),
                        _child("Developer account renewal failed"),
                    ],
                )
            )
        )

        self.assertIn("mixed_child_tasks", {issue.code for issue in report.issues})
        self.assertEqual(report.blocking_issue_count, 1)
        self.assertEqual(report.warning_count, 0)

    def test_grouping_contract_counts_as_task_evidence(self) -> None:
        report = audit_smart_inbox_quality(
            _mailbox(
                _row(
                    thread_id="ai-group:apple-order",
                    title="Apple order W123456789 ready for pickup",
                    sender="Apple",
                    message_count=2,
                    grouping_metadata={
                        "grouping_contract": {
                            "group_kind": "lifecycle",
                            "workflow_type": "order_tracking",
                            "shared_object": "W123456789",
                        }
                    },
                    children=[
                        _child("Apple order W123456789 processing"),
                        _child("Apple order W123456789 ready"),
                    ],
                )
            )
        )

        self.assertEqual(report.blocking_issue_count, 0)
        self.assertNotIn("group_without_task_evidence", {issue.code for issue in report.issues})

    def test_smart_inbox_response_requires_all_visible_rows_ready(self) -> None:
        smart = _smart_inbox(
            _smart_row("smart-row:ready", "Apple order W123456789 out for delivery", readiness="ready"),
            _smart_row("smart-row:partial", "Northstar transfer pending review", readiness="partial"),
            ready_count=1,
            partial_count=1,
        )

        report = audit_smart_inbox_response_quality(smart)

        self.assertFalse(smart_inbox_response_is_product_ready(smart))
        self.assertIn("row_not_ready", {issue.code for issue in report.issues})
        self.assertEqual(report.blocking_issue_count, 1)

    def test_smart_cross_thread_mail_object_evidence_passes(self) -> None:
        smart = _smart_inbox(
            _smart_row(
                "smart-object:ticket-1",
                "Northstar Bank service request 900000003 resolved",
                source_thread_ids=["thread-1", "thread-2"],
                source_message_ids=["msg-1", "msg-2"],
                grouping_reason={
                    "source": "mail_object",
                    "canonical_key": "ticket_id:northstar:900000003",
                    "evidence": {"signal_name": "ticket_id", "reference_value": "900000003"},
                },
            )
        )

        report = audit_smart_inbox_response_quality(smart)

        self.assertTrue(smart_inbox_response_is_product_ready(smart))
        self.assertEqual(report.blocking_issue_count, 0)

    def test_smart_cross_thread_without_task_evidence_is_blocking(self) -> None:
        smart = _smart_inbox(
            _smart_row(
                "smart-row:provider-updates",
                "Provider updates",
                source_thread_ids=["thread-1", "thread-2"],
                source_message_ids=["msg-1", "msg-2"],
                grouping_reason={"source": "provider_stream", "cluster_key": "sender:provider"},
            )
        )

        report = audit_smart_inbox_response_quality(smart)

        codes = {issue.code for issue in report.issues}
        self.assertFalse(smart_inbox_response_is_product_ready(smart))
        self.assertIn("group_without_task_evidence", codes)
        self.assertIn("sender_stream_group", codes)
        self.assertIn("vague_title", codes)

    def test_smart_inbox_blocks_stacked_state_titles(self) -> None:
        smart = _smart_inbox(
            _smart_row("smart-row:pending-resolved", "Northstar grievance 900000001 pending response resolved"),
            _smart_row("smart-row:application-arriving", "State University I-20 request needed arriving"),
            _smart_row("smart-row:ready-confirmed", "IBKR daily activity statement ready confirmed"),
        )

        report = audit_smart_inbox_response_quality(smart)

        self.assertFalse(smart_inbox_response_is_product_ready(smart))
        self.assertEqual(
            [issue.code for issue in report.issues],
            ["stacked_state_title", "stacked_state_title", "stacked_state_title"],
        )


def _mailbox(*rows: GmailThreadRow) -> MailboxResponse:
    return MailboxResponse(
        label="inbox",
        total_threads=len(rows),
        sections=[GmailThreadSection(id="today", title="Today", rows=list(rows))],
    )


def _row(
    *,
    thread_id: str,
    title: str,
    sender: str,
    message_count: int,
    presentation_status: str = "ai_ready",
    grouping_metadata: dict | None = None,
    children: list[GmailThreadChildRow] | None = None,
) -> GmailThreadRow:
    return GmailThreadRow(
        thread_id=thread_id,
        entity_id=thread_id,
        title=title,
        latest_source_record_id=f"{thread_id}:message",
        latest_received_at="2026-06-20T10:00:00+00:00",
        latest_subject=title,
        latest_sender=f"{sender} <sender@example.com>",
        sender=sender,
        message_count=message_count,
        presentation_status=presentation_status,  # type: ignore[arg-type]
        grouping_metadata=grouping_metadata or {},
        children=children or [],
    )


def _child(title: str) -> GmailThreadChildRow:
    return GmailThreadChildRow(
        message_id=title.lower().replace(" ", "-"),
        sender="Sender <sender@example.com>",
        subject=title,
        ai_title=title,
        snippet=title,
        received_at="2026-06-20T10:00:00+00:00",
    )


def _smart_inbox(
    *rows: SmartInboxRow,
    ready_count: int | None = None,
    partial_count: int = 0,
    failed_count: int = 0,
) -> SmartInboxResponse:
    ready = len(rows) if ready_count is None else ready_count
    return SmartInboxResponse(
        total_rows=len(rows),
        sections=[SmartInboxSection(id="today", title="Today", rows=list(rows))],
        ready_count=ready,
        partial_count=partial_count,
        failed_count=failed_count,
        generated_at="2026-06-20T10:00:00+00:00",
    )


def _smart_row(
    row_id: str,
    title: str,
    *,
    readiness: str = "ready",
    source_thread_ids: list[str] | None = None,
    source_message_ids: list[str] | None = None,
    grouping_reason: dict | None = None,
) -> SmartInboxRow:
    return SmartInboxRow(
        id=row_id,
        row_key=row_id,
        row_type="verified_group" if source_thread_ids and len(source_thread_ids) > 1 else "summarized_thread",
        title=title,
        primary_sender="Sender <sender@example.com>",
        latest_message_at="2026-06-20T10:00:00+00:00",
        latest_message_id=(source_message_ids or ["msg-1"])[0],
        source_thread_ids=source_thread_ids or ["thread-1"],
        source_message_ids=source_message_ids or ["msg-1"],
        confidence_tier="strong",
        confidence=0.95,
        grouping_reason=grouping_reason or {"source": "test"},
        offline_status="partial",
        readiness=readiness,  # type: ignore[arg-type]
        action_type="open",
        priority=50,
    )


if __name__ == "__main__":
    unittest.main()
