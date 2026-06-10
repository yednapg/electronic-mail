from __future__ import annotations

from dataclasses import replace
import unittest

from app.db.mail_groups import GmailMessageRecord, MailGroupRecord
from app.services.grouping_projection import build_inbox_projection, canonical_entity_for_message, evaluate_inbox_candidate


def message(
    message_id: str,
    *,
    thread_id: str,
    sender: str = "HDFC Bank <support@hdfcbank.com>",
    subject: str = "HDFC Bank service request update",
    snippet: str = "Service request 106400420 was updated.",
    signals: dict | None = None,
) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id=thread_id,
        history_id="1",
        label_ids=["INBOX"],
        internal_date=f"2026-06-0{message_id[-1]}T12:00:00+00:00",
        subject=subject,
        sender=sender,
        recipients={"to": "me@example.com"},
        headers={},
        snippet=snippet,
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals=signals or {"sender_domain": "hdfcbank.com", "ticket_id": "106400420"},
        body_hash=f"hash-{message_id}",
        created_at="",
        updated_at="",
    )


def group(
    group_id: str,
    *,
    group_key: str,
    membership_source: str = "deterministic_candidate",
    classification: dict | None = None,
) -> MailGroupRecord:
    return MailGroupRecord(
        id=group_id,
        user_id="user-1",
        group_key=group_key,
        group_type="support_case",
        status="active",
        enrichment_status="ready",
        membership_source=membership_source,
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="HDFC remittance support case",
        ai_summary="HDFC updated service request 106400420.",
        labels=["support_case"],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=True,
        latest_message_at="2026-06-02T12:00:00+00:00",
        latest_message_id="msg-2",
        generated_from_hash="hash",
        generated_at="2026-06-02T12:00:00+00:00",
        created_at="2026-06-02T12:00:00+00:00",
        updated_at="2026-06-02T12:00:00+00:00",
        classification_version="attention_classifier_v1",
        classification=classification
        or {
            "workflow_family": "support_case",
            "facts": {"provider": "hdfc", "reference_id": "ticket_id:106400420"},
        },
        classification_confidence=0.92,
    )


def ai_contract(*message_ids: str, shared_object: str = "HDFC wire transfer status case") -> dict:
    return {
        "workflow_family": "financial_transfer",
        "grouping_contract": {
            "group_kind": "lifecycle",
            "canonical_entity": "HDFC Bank",
            "shared_object": shared_object,
            "workflow_family": "financial_transfer",
            "member_ids": list(message_ids),
            "context_sent_ids": [],
            "strong_evidence": ["messages discuss the same outward remittance status lifecycle"],
            "weak_evidence": ["same bank and close dates"],
            "per_message_evidence": {message_id: "same outward remittance lifecycle" for message_id in message_ids},
            "excluded_ids": [],
            "risk_level": "low",
            "should_show_in_inbox": True,
            "should_show_in_dashboard": True,
            "confidence": 0.96,
        },
    }


class GroupingProjectionTests(unittest.TestCase):
    def test_exact_reference_cross_thread_group_passes(self) -> None:
        messages = [message("msg-1", thread_id="thread-1"), message("msg-2", thread_id="thread-2")]
        decision = evaluate_inbox_candidate(group("group-1", group_key="ticket_id:hdfcbank.com:106400420"), messages)

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.group_kind, "lifecycle")
        self.assertEqual(decision.canonical_entity, "HDFC Bank")
        self.assertIn("ticket_id:hdfcbank.com:106400420", decision.evidence["strong_evidence"])

    def test_provider_topic_only_cross_thread_group_is_rejected(self) -> None:
        messages = [
            message("msg-1", thread_id="thread-1", signals={"sender_domain": "hdfcbank.com"}),
            message("msg-2", thread_id="thread-2", signals={"sender_domain": "hdfcbank.com"}),
        ]
        candidate = group(
            "group-1",
            group_key="entity-lifecycle:hdfcbank.com:hdfc-bank:transfer",
            classification={"workflow_family": "support_case", "facts": {"provider": "hdfc"}},
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertFalse(decision.accepted)
        self.assertIn("no strong shared reference", decision.reason)

    def test_high_confidence_ai_lifecycle_group_passes_without_extracted_ids(self) -> None:
        messages = [
            message(
                "msg-1",
                thread_id="thread-1",
                subject="Request for Status of International Wire Sent",
                snippet="Please confirm whether the outward remittance was processed.",
                signals={"sender_domain": "hdfcbank.com"},
            ),
            message(
                "msg-2",
                thread_id="thread-2",
                subject="Outward remittance processed",
                snippet="Your outward remittance was processed by the bank.",
                signals={"sender_domain": "hdfcbank.com"},
            ),
        ]
        candidate = group(
            "group-ai",
            group_key="ai-lifecycle:hdfc-wire",
            membership_source="ai_lifecycle",
            classification=ai_contract("msg-1", "msg-2"),
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.workflow_type, "financial_transfer")
        self.assertIn("ai:messages discuss the same outward remittance status lifecycle", decision.evidence["strong_evidence"])

    def test_high_confidence_ai_lifecycle_rejects_conflicting_references(self) -> None:
        messages = [
            message("msg-1", thread_id="thread-1", signals={"sender_domain": "hdfcbank.com", "ticket_id": "106400420"}),
            message("msg-2", thread_id="thread-2", signals={"sender_domain": "hdfcbank.com", "ticket_id": "106756996"}),
        ]
        candidate = group(
            "group-ai",
            group_key="ai-lifecycle:hdfc-conflict",
            membership_source="ai_lifecycle",
            classification=ai_contract("msg-1", "msg-2"),
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertFalse(decision.accepted)
        self.assertIn("conflicting extracted references", decision.reason)

    def test_ai_lifecycle_sent_context_does_not_create_entity_conflict(self) -> None:
        inbound = message("msg-1", thread_id="thread-1", signals={"sender_domain": "hdfcbank.com"})
        reply = message("msg-2", thread_id="thread-2", signals={"sender_domain": "hdfcbank.com"})
        sent = replace(
            message("msg-3", thread_id="thread-3", sender="Gaurav Pandey <me@example.com>", signals={"sender_domain": "gmail.com"}),
            label_ids=["SENT"],
        )
        classification = ai_contract("msg-1", "msg-2")
        classification["grouping_contract"]["context_sent_ids"] = ["msg-3"]
        candidate = group(
            "group-ai",
            group_key="ai-lifecycle:hdfc-sent-context",
            membership_source="ai_lifecycle",
            classification=classification,
        )

        decision = evaluate_inbox_candidate(candidate, [inbound, reply, sent])

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.canonical_entity, "HDFC Bank")

    def test_conflict_resolver_gives_message_one_visible_home(self) -> None:
        messages = [message("msg-1", thread_id="thread-1"), message("msg-2", thread_id="thread-2")]
        strong = group("group-strong", group_key="ticket_id:hdfcbank.com:106400420")
        weaker = replace(strong, id="group-weaker", group_key="ticket_id:hdfcbank.com:106400420:duplicate", classification_confidence=0.81)

        result = build_inbox_projection(groups=[weaker, strong], group_messages={"group-strong": messages, "group-weaker": messages})

        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].source_group_id, "group-strong")
        self.assertTrue(any(event["decision"] == "superseded" for event in result.audit_events))

    def test_conflict_resolver_gives_projection_key_one_visible_group(self) -> None:
        first_pair = [message("msg-1", thread_id="thread-1"), message("msg-2", thread_id="thread-2")]
        second_pair = [message("msg-3", thread_id="thread-3"), message("msg-4", thread_id="thread-4")]
        strong = group("group-strong", group_key="ticket_id:hdfcbank.com:106400420")
        duplicate_key = replace(strong, id="group-duplicate", classification_confidence=0.88)

        result = build_inbox_projection(
            groups=[duplicate_key, strong],
            group_messages={"group-strong": first_pair, "group-duplicate": second_pair},
        )

        self.assertEqual(len(result.groups), 1)
        self.assertTrue(result.groups[0].projection_key.startswith("inbox:"))
        self.assertTrue(any(event["reason"] == "another stronger visible group already claimed this projection key" for event in result.audit_events))

    def test_cross_entity_group_is_rejected_even_with_financial_language(self) -> None:
        messages = [
            message("msg-1", thread_id="thread-1", sender="HDFC Bank <support@hdfcbank.com>"),
            message(
                "msg-2",
                thread_id="thread-2",
                sender="Interactive Brokers <service@interactivebrokers.com>",
                signals={"sender_domain": "interactivebrokers.com", "ticket_id": "106400420"},
            ),
        ]

        decision = evaluate_inbox_candidate(group("group-1", group_key="ticket_id:hdfcbank.com:106400420"), messages)

        self.assertFalse(decision.accepted)
        self.assertIn("unrelated canonical entities", decision.reason)

    def test_entity_normalization_avoids_generic_sender_names(self) -> None:
        entity, channel = canonical_entity_for_message(
            message(
                "msg-1",
                thread_id="thread-1",
                sender="Default User <support@hdfcbank.com>",
                signals={"sender_domain": "hdfcbank.com"},
            )
        )

        self.assertEqual(entity, "HDFC Bank")
        self.assertIsNone(channel)


if __name__ == "__main__":
    unittest.main()
