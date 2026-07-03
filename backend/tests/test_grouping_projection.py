from __future__ import annotations

from dataclasses import replace
import os
import random
import unittest

from app.db.mail_groups import GmailMessageRecord, MailGroupRecord
from app.services.grouping_projection import build_inbox_projection, canonical_entity_for_message, evaluate_inbox_candidate
from app.services.mail_groups import _candidate_groups


GENERATED_SEEDS = (13, 29, 47, 71, 101)
PROVIDER_ROOTS = (
    "auroraledger",
    "northmint",
    "brightcargo",
    "cedarcloud",
    "emberpay",
    "frostdesk",
    "greenvault",
    "harborloop",
    "indigobooks",
    "juniperrail",
    "keystonecare",
    "lumenmarket",
)
WORKFLOWS = ("support_case", "billing", "logistics", "account_security", "application", "financial_transfer")
REFERENCE_SIGNALS = ("ticket_id", "invoice_id", "tracking_id", "booking_id", "application_id", "order_id", "dispute_id", "repair_id")


def generated_seed_values() -> tuple[int, ...]:
    raw_seed = os.getenv("GROUPING_GENERATED_TEST_SEED", "").strip()
    if not raw_seed:
        return GENERATED_SEEDS
    try:
        extra_seed = int(raw_seed)
    except ValueError:
        extra_seed = sum((index + 1) * ord(character) for index, character in enumerate(raw_seed))
    return (*GENERATED_SEEDS, extra_seed)


def message(
    message_id: str,
    *,
    thread_id: str,
    sender: str = "Support <support@support.provider.example.com>",
    subject: str = "Service request update",
    snippet: str = "Service request CASE10001 was updated.",
    signals: dict | None = None,
    labels: list[str] | None = None,
    headers: dict | None = None,
) -> GmailMessageRecord:
    minute = sum(ord(char) for char in message_id) % 50
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id=thread_id,
        history_id="1",
        label_ids=labels or ["INBOX"],
        internal_date=f"2026-06-01T12:{minute:02d}:00+00:00",
        subject=subject,
        sender=sender,
        recipients={"to": "me@example.com"},
        headers=headers or {},
        snippet=snippet,
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals=signals or {"sender_domain": "provider.example.com", "ticket_id": "CASE10001"},
        body_hash=f"hash-{message_id}",
        created_at="",
        updated_at="",
    )


def group(
    group_id: str,
    *,
    group_key: str,
    membership_source: str = "deterministic_candidate",
    workflow_family: str = "support_case",
    classification: dict | None = None,
    confidence: float = 0.92,
) -> MailGroupRecord:
    return MailGroupRecord(
        id=group_id,
        user_id="user-1",
        group_key=group_key,
        group_type=workflow_family,
        status="active",
        enrichment_status="ready",
        membership_source=membership_source,
        ai_model=None,
        ai_error=None,
        ai_generated_at=None,
        ai_title="Provider support case",
        ai_summary="Provider updated the referenced case.",
        labels=[workflow_family],
        action_needed=False,
        action_type="open",
        priority=20,
        timing_band="later",
        dashboard_visible=True,
        latest_message_at="2026-06-01T12:00:00+00:00",
        latest_message_id="msg-2",
        generated_from_hash="hash",
        generated_at="2026-06-01T12:00:00+00:00",
        created_at="2026-06-01T12:00:00+00:00",
        updated_at="2026-06-01T12:00:00+00:00",
        classification_version="attention_classifier_v1",
        classification=classification
        or {
            "workflow_family": workflow_family,
            "facts": {"provider": "provider", "reference_id": group_key},
        },
        classification_confidence=confidence,
    )


def ai_contract(
    *message_ids: str,
    entity: str = "Provider",
    shared_object: str = "one customer case lifecycle",
    workflow_family: str = "support_case",
    confidence: float = 0.96,
) -> dict:
    return {
        "workflow_family": workflow_family,
        "grouping_contract": {
            "group_kind": "lifecycle",
            "canonical_entity": entity,
            "shared_object": shared_object,
            "workflow_family": workflow_family,
            "member_ids": list(message_ids),
            "context_sent_ids": [],
            "strong_evidence": [f"all messages discuss {shared_object}"],
            "weak_evidence": ["same sender organization and close dates"],
            "per_message_evidence": {message_id: f"{message_id} belongs to {shared_object}" for message_id in message_ids},
            "excluded_ids": [],
            "risk_level": "low",
            "should_show_in_inbox": True,
            "should_show_in_dashboard": True,
            "confidence": confidence,
        },
    }


def provider_case(rng: random.Random, index: int) -> dict[str, str]:
    root = f"{rng.choice(PROVIDER_ROOTS)}{index}"
    return {
        "root": root,
        "domain": f"{root}.example.com",
        "entity": root.title(),
    }


def provider_sender(provider: dict[str, str], role: str = "support") -> str:
    return f"{role.title()} <{role}@{role}.{provider['domain']}>"


def reference_value(rng: random.Random, index: int) -> str:
    return f"REF{index}{rng.randint(100000, 999999)}"


def reference_key(signal_name: str, provider: dict[str, str], value: str) -> str:
    return f"{signal_name}:{provider['root']}:{value}"


class GroupingProjectionTests(unittest.TestCase):
    def test_exact_reference_cross_thread_group_passes(self) -> None:
        provider = provider_case(random.Random(1), 1)
        reference = "CASE900000002"
        messages = [
            message(
                "msg-1",
                thread_id="thread-1",
                sender=provider_sender(provider, "support"),
                signals={"sender_domain": f"support.{provider['domain']}", "ticket_id": reference},
            ),
            message(
                "msg-2",
                thread_id="thread-2",
                sender=provider_sender(provider, "care"),
                signals={"sender_domain": f"mail.{provider['domain']}", "ticket_id": reference},
            ),
        ]
        decision = evaluate_inbox_candidate(group("group-1", group_key=reference_key("ticket_id", provider, reference)), messages)

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.group_kind, "lifecycle")
        self.assertEqual(decision.canonical_entity, provider["entity"])
        self.assertIn(reference_key("ticket_id", provider, reference), decision.evidence["strong_evidence"])

    def test_dispute_and_repair_reference_cross_thread_groups_pass(self) -> None:
        provider = provider_case(random.Random(11), 11)
        cases = [
            ("dispute_id", "P20052026334071", "support_case"),
            ("repair_id", "SR99887766", "support_case"),
        ]
        for signal_name, reference, workflow in cases:
            messages = [
                message(
                    f"{signal_name}-1",
                    thread_id=f"thread-{signal_name}-1",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": f"support.{provider['domain']}", signal_name: reference},
                ),
                message(
                    f"{signal_name}-2",
                    thread_id=f"thread-{signal_name}-2",
                    sender=provider_sender(provider, "care"),
                    signals={"sender_domain": f"mail.{provider['domain']}", signal_name: reference},
                ),
            ]

            with self.subTest(signal_name=signal_name):
                decision = evaluate_inbox_candidate(
                    group("group-1", group_key=reference_key(signal_name, provider, reference), workflow_family=workflow),
                    messages,
                )

                self.assertTrue(decision.accepted)
                self.assertIn(reference_key(signal_name, provider, reference), decision.evidence["strong_evidence"])

    def test_dispute_and_repair_reference_conflicts_reject(self) -> None:
        provider = provider_case(random.Random(12), 12)
        for signal_name in ("dispute_id", "repair_id"):
            messages = [
                message(
                    f"{signal_name}-conflict-1",
                    thread_id=f"thread-{signal_name}-conflict-1",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": provider["domain"], signal_name: "CASE10001"},
                ),
                message(
                    f"{signal_name}-conflict-2",
                    thread_id=f"thread-{signal_name}-conflict-2",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": provider["domain"], signal_name: "CASE20002"},
                ),
            ]

            with self.subTest(signal_name=signal_name):
                decision = evaluate_inbox_candidate(
                    group("group-conflict", group_key=reference_key(signal_name, provider, "CASE10001")),
                    messages,
                )

                self.assertFalse(decision.accepted)
                self.assertIn("conflicting extracted references", decision.reason)

    def test_provider_topic_only_cross_thread_group_is_rejected(self) -> None:
        provider = provider_case(random.Random(2), 2)
        messages = [
            message("msg-1", thread_id="thread-1", sender=provider_sender(provider), signals={"sender_domain": f"support.{provider['domain']}"}),
            message("msg-2", thread_id="thread-2", sender=provider_sender(provider, "care"), signals={"sender_domain": f"mail.{provider['domain']}"}),
        ]
        candidate = group(
            "group-1",
            group_key=f"entity-lifecycle:{provider['domain']}:{provider['root']}:status",
            classification={"workflow_family": "support_case", "facts": {"provider": provider["root"]}},
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertFalse(decision.accepted)
        self.assertIn("no strong shared reference", decision.reason)

    def test_high_confidence_ai_lifecycle_group_passes_without_extracted_ids(self) -> None:
        provider = provider_case(random.Random(3), 3)
        messages = [
            message(
                "msg-1",
                thread_id="thread-1",
                sender=provider_sender(provider),
                subject="Status request",
                snippet="Please confirm the case status.",
                signals={"sender_domain": f"support.{provider['domain']}"},
            ),
            message(
                "msg-2",
                thread_id="thread-2",
                sender=provider_sender(provider, "care"),
                subject="Status processed",
                snippet="The case status is now processed.",
                signals={"sender_domain": f"mail.{provider['domain']}"},
            ),
        ]
        candidate = group(
            "group-ai",
            group_key=f"ai-lifecycle:{provider['root']}",
            membership_source="ai_lifecycle",
            classification=ai_contract("msg-1", "msg-2", entity=provider["entity"]),
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.workflow_type, "support_case")
        self.assertTrue(any(item.startswith("ai:") for item in decision.evidence["strong_evidence"]))

    def test_high_confidence_ai_lifecycle_rejects_conflicting_references(self) -> None:
        provider = provider_case(random.Random(4), 4)
        messages = [
            message("msg-1", thread_id="thread-1", sender=provider_sender(provider), signals={"sender_domain": provider["domain"], "ticket_id": "CASE10001"}),
            message("msg-2", thread_id="thread-2", sender=provider_sender(provider, "care"), signals={"sender_domain": provider["domain"], "ticket_id": "CASE20002"}),
        ]
        candidate = group(
            "group-ai",
            group_key=f"ai-lifecycle:{provider['root']}-conflict",
            membership_source="ai_lifecycle",
            classification=ai_contract("msg-1", "msg-2", entity=provider["entity"]),
        )

        decision = evaluate_inbox_candidate(candidate, messages)

        self.assertFalse(decision.accepted)
        self.assertIn("conflicting extracted references", decision.reason)

    def test_ai_lifecycle_sent_context_does_not_create_entity_conflict(self) -> None:
        provider = provider_case(random.Random(5), 5)
        inbound = message("msg-1", thread_id="thread-1", sender=provider_sender(provider), signals={"sender_domain": provider["domain"]})
        reply = message("msg-2", thread_id="thread-2", sender=provider_sender(provider, "care"), signals={"sender_domain": provider["domain"]})
        sent = replace(
            message("msg-3", thread_id="thread-3", sender="Me <me@example.com>", signals={"sender_domain": "example.com"}),
            label_ids=["SENT"],
        )
        classification = ai_contract("msg-1", "msg-2", entity=provider["entity"])
        classification["grouping_contract"]["context_sent_ids"] = ["msg-3"]
        candidate = group(
            "group-ai",
            group_key=f"ai-lifecycle:{provider['root']}-sent-context",
            membership_source="ai_lifecycle",
            classification=classification,
        )

        decision = evaluate_inbox_candidate(candidate, [inbound, reply, sent])

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.canonical_entity, provider["entity"])

    def test_conflict_resolver_gives_message_one_visible_home(self) -> None:
        provider = provider_case(random.Random(6), 6)
        reference = "CASE30003"
        messages = [
            message("msg-1", thread_id="thread-1", sender=provider_sender(provider), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
            message("msg-2", thread_id="thread-2", sender=provider_sender(provider, "care"), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
        ]
        strong = group("group-strong", group_key=reference_key("ticket_id", provider, reference))
        weaker = replace(strong, id="group-weaker", group_key=f"{reference_key('ticket_id', provider, reference)}:duplicate", classification_confidence=0.81)

        result = build_inbox_projection(groups=[weaker, strong], group_messages={"group-strong": messages, "group-weaker": messages})

        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].source_group_id, "group-strong")
        self.assertTrue(any(event["decision"] == "superseded" for event in result.audit_events))

    def test_conflict_resolver_gives_projection_key_one_visible_group(self) -> None:
        provider = provider_case(random.Random(7), 7)
        reference = "CASE40004"
        first_pair = [
            message("msg-1", thread_id="thread-1", sender=provider_sender(provider), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
            message("msg-2", thread_id="thread-2", sender=provider_sender(provider, "care"), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
        ]
        second_pair = [
            message("msg-3", thread_id="thread-3", sender=provider_sender(provider), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
            message("msg-4", thread_id="thread-4", sender=provider_sender(provider, "care"), signals={"sender_domain": provider["domain"], "ticket_id": reference}),
        ]
        strong = group("group-strong", group_key=reference_key("ticket_id", provider, reference))
        duplicate_key = replace(strong, id="group-duplicate", classification_confidence=0.88)

        result = build_inbox_projection(
            groups=[duplicate_key, strong],
            group_messages={"group-strong": first_pair, "group-duplicate": second_pair},
        )

        self.assertEqual(len(result.groups), 1)
        self.assertTrue(result.groups[0].projection_key.startswith("inbox:"))
        self.assertTrue(any(event["reason"] == "another stronger visible group already claimed this projection key" for event in result.audit_events))

    def test_cross_entity_group_is_rejected_even_with_same_reference(self) -> None:
        provider_a = provider_case(random.Random(8), 8)
        provider_b = provider_case(random.Random(9), 9)
        reference = "CASE50005"
        messages = [
            message("msg-1", thread_id="thread-1", sender=provider_sender(provider_a), signals={"sender_domain": provider_a["domain"], "ticket_id": reference}),
            message("msg-2", thread_id="thread-2", sender=provider_sender(provider_b), signals={"sender_domain": provider_b["domain"], "ticket_id": reference}),
        ]

        decision = evaluate_inbox_candidate(group("group-1", group_key=reference_key("ticket_id", provider_a, reference)), messages)

        self.assertFalse(decision.accepted)
        self.assertIn("unrelated canonical entities", decision.reason)

    def test_entity_normalization_avoids_generic_sender_names(self) -> None:
        provider = provider_case(random.Random(10), 10)
        entity, channel = canonical_entity_for_message(
            message(
                "msg-1",
                thread_id="thread-1",
                sender=f"Default User <support@support.{provider['domain']}>",
                signals={"sender_domain": f"support.{provider['domain']}"},
            )
        )

        self.assertEqual(entity, provider["entity"])
        self.assertIsNone(channel)

    def test_generated_exact_reference_groups_accept_across_many_provider_shapes(self) -> None:
        for seed in generated_seed_values():
            rng = random.Random(seed)
            for index in range(12):
                provider = provider_case(rng, seed * 100 + index)
                signal_name = rng.choice(REFERENCE_SIGNALS)
                workflow = rng.choice(WORKFLOWS)
                reference = reference_value(rng, index)
                messages = [
                    message(
                        f"generated-{seed}-{index}-a",
                        thread_id=f"thread-{seed}-{index}-a",
                        sender=provider_sender(provider, "support"),
                        subject=f"{workflow.replace('_', ' ')} update {reference}",
                        signals={"sender_domain": f"support.{provider['domain']}", signal_name: reference},
                    ),
                    message(
                        f"generated-{seed}-{index}-b",
                        thread_id=f"thread-{seed}-{index}-b",
                        sender=provider_sender(provider, "alerts"),
                        subject=f"{workflow.replace('_', ' ')} confirmation {reference}",
                        signals={"sender_domain": f"mail.{provider['domain']}", signal_name: reference},
                    ),
                ]

                with self.subTest(seed=seed, index=index, workflow=workflow, signal_name=signal_name):
                    decision = evaluate_inbox_candidate(
                        group(
                            f"group-{seed}-{index}",
                            group_key=reference_key(signal_name, provider, reference),
                            workflow_family=workflow,
                        ),
                        messages,
                    )
                    self.assertTrue(decision.accepted)
                    self.assertEqual(decision.canonical_entity, provider["entity"])
                    self.assertIn(reference_key(signal_name, provider, reference), decision.evidence["strong_evidence"])

    def test_generated_candidate_groups_keep_distinct_references_and_normal_threads(self) -> None:
        rng = random.Random(20260611)
        generated_messages: list[GmailMessageRecord] = []
        expected_exact_keys: dict[str, set[str]] = {}
        expected_thread_keys: set[str] = set()
        for index in range(30):
            provider = provider_case(rng, index)
            signal_name = rng.choice(REFERENCE_SIGNALS)
            shared_reference = reference_value(rng, index)
            distinct_reference = reference_value(rng, index + 100)
            first = message(
                f"candidate-{index}-a",
                thread_id=f"candidate-thread-{index}-a",
                sender=provider_sender(provider, "support"),
                signals={"sender_domain": f"support.{provider['domain']}", signal_name: shared_reference},
            )
            second = message(
                f"candidate-{index}-b",
                thread_id=f"candidate-thread-{index}-b",
                sender=provider_sender(provider, "mail"),
                signals={"sender_domain": f"mail.{provider['domain']}", signal_name: shared_reference},
            )
            distinct = message(
                f"candidate-{index}-c",
                thread_id=f"candidate-thread-{index}-c",
                sender=provider_sender(provider, "support"),
                signals={"sender_domain": f"support.{provider['domain']}", signal_name: distinct_reference},
            )
            normal = message(
                f"candidate-{index}-d",
                thread_id=f"candidate-thread-{index}-d",
                sender=provider_sender(provider, "support"),
                signals={"sender_domain": provider["domain"], "normalized_subject": f"plain update {index}"},
            )
            generated_messages.extend([first, second, distinct, normal])
            expected_exact_keys[reference_key(signal_name, provider, shared_reference)] = {first.message_id, second.message_id}
            expected_exact_keys[reference_key(signal_name, provider, distinct_reference)] = {distinct.message_id}
            expected_thread_keys.add(f"gmail-thread:{normal.gmail_thread_id}")

        candidates = _candidate_groups(generated_messages)

        for key, expected_ids in expected_exact_keys.items():
            with self.subTest(key=key):
                self.assertEqual({message.message_id for message in candidates[key]}, expected_ids)
        for key in expected_thread_keys:
            with self.subTest(key=key):
                self.assertEqual(len(candidates[key]), 1)

    def test_generated_conflicting_references_reject_for_same_provider(self) -> None:
        rng = random.Random(303)
        for index in range(40):
            provider = provider_case(rng, index)
            signal_name = rng.choice(REFERENCE_SIGNALS)
            first_reference = reference_value(rng, index)
            second_reference = reference_value(rng, index + 200)
            messages = [
                message(
                    f"conflict-{index}-a",
                    thread_id=f"conflict-thread-{index}-a",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": provider["domain"], signal_name: first_reference},
                ),
                message(
                    f"conflict-{index}-b",
                    thread_id=f"conflict-thread-{index}-b",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": provider["domain"], signal_name: second_reference},
                ),
            ]

            with self.subTest(index=index, signal_name=signal_name):
                decision = evaluate_inbox_candidate(
                    group(
                        f"conflict-group-{index}",
                        group_key=reference_key(signal_name, provider, first_reference),
                        workflow_family=rng.choice(WORKFLOWS),
                    ),
                    messages,
                )
                self.assertFalse(decision.accepted)
                self.assertIn("conflicting extracted references", decision.reason)

    def test_generated_cross_entity_same_reference_rejects(self) -> None:
        rng = random.Random(404)
        for index in range(40):
            provider_a = provider_case(rng, index)
            provider_b = provider_case(rng, index + 100)
            signal_name = rng.choice(REFERENCE_SIGNALS)
            reference = reference_value(rng, index)
            messages = [
                message(
                    f"cross-entity-{index}-a",
                    thread_id=f"cross-entity-thread-{index}-a",
                    sender=provider_sender(provider_a, "support"),
                    signals={"sender_domain": provider_a["domain"], signal_name: reference},
                ),
                message(
                    f"cross-entity-{index}-b",
                    thread_id=f"cross-entity-thread-{index}-b",
                    sender=provider_sender(provider_b, "support"),
                    signals={"sender_domain": provider_b["domain"], signal_name: reference},
                ),
            ]

            with self.subTest(index=index, signal_name=signal_name):
                decision = evaluate_inbox_candidate(
                    group(
                        f"cross-entity-group-{index}",
                        group_key=reference_key(signal_name, provider_a, reference),
                        workflow_family=rng.choice(WORKFLOWS),
                    ),
                    messages,
                )
                self.assertFalse(decision.accepted)
                self.assertIn("unrelated canonical entities", decision.reason)

    def test_generated_ai_lifecycle_requires_complete_per_message_evidence(self) -> None:
        rng = random.Random(505)
        for index in range(25):
            provider = provider_case(rng, index)
            messages = [
                message(
                    f"ai-evidence-{index}-a",
                    thread_id=f"ai-evidence-thread-{index}-a",
                    sender=provider_sender(provider, "support"),
                    signals={"sender_domain": provider["domain"]},
                ),
                message(
                    f"ai-evidence-{index}-b",
                    thread_id=f"ai-evidence-thread-{index}-b",
                    sender=provider_sender(provider, "care"),
                    signals={"sender_domain": provider["domain"]},
                ),
            ]
            incomplete = ai_contract(messages[0].message_id, messages[1].message_id, entity=provider["entity"])
            incomplete["grouping_contract"]["per_message_evidence"].pop(messages[1].message_id)

            with self.subTest(index=index):
                decision = evaluate_inbox_candidate(
                    group(
                        f"ai-evidence-group-{index}",
                        group_key=f"ai-lifecycle:{provider['root']}-{index}",
                        membership_source="ai_lifecycle",
                        classification=incomplete,
                    ),
                    messages,
                )
                self.assertFalse(decision.accepted)
                self.assertIn("AI output did not explicitly justify", decision.reason)


if __name__ == "__main__":
    unittest.main()
