from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from base64 import urlsafe_b64encode
from dataclasses import replace
import json
from math import sqrt
import sys
import unittest
from unittest.mock import patch

from app.core.counterpart_identity import (
    aggregate_counterpart_entities,
    counterpart_aware_headline,
    mailbox_owner_identity,
)
from app.db.mail_groups import GmailMessageRecord
from app.schemas.ai_inbox import AIMatterDetailResponse, AIMatterRow, AIOrganizingRow
from app.services import ai_inbox


def _message(
    *,
    text: str,
    subject: str = "Service request update",
    sender: str = "Support <support@example.com>",
    recipients: dict[str, object] | None = None,
    label_ids: list[str] | None = None,
) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id="message-1",
        gmail_thread_id="thread-1",
        history_id="1",
        label_ids=label_ids or ["INBOX", "UNREAD"],
        internal_date="2026-08-27T10:00:00+00:00",
        subject=subject,
        sender=sender,
        recipients=recipients or {"to": ["me@example.com"]},
        headers={},
        snippet=text[:120],
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=text,
        extracted_signals={},
        body_hash="body-hash",
        created_at="2026-08-27T10:00:00+00:00",
        updated_at="2026-08-27T10:00:00+00:00",
    )


def _classification(
    *,
    candidate: str | None = "matter-1",
    status: str = "in_progress",
    risks: list[str] | None = None,
    verdict: str | None = None,
    role: str = "processing",
    subgoals: list[ai_inbox.MatterSubgoalState] | None = None,
) -> ai_inbox.MatterClassification:
    resolved_verdict = verdict or (
        "strong_continuation" if candidate is not None else "different_matter"
    )
    return ai_inbox.MatterClassification(
        candidate_matter_id=candidate,
        stable_goal="Move the customer's account servicing branch",
        dynamic_title="The branch transfer is being processed",
        summary="The customer requested a branch transfer and the bank is processing it.",
        status=status,
        event=ai_inbox.MessageEventV1(
            role=role,
            purpose="Move the customer's account servicing branch",
            organization="Example Bank",
            product_or_account="Bank account",
            state_change="The bank started processing the requested branch transfer.",
            subgoal_goals=["Move the account servicing branch"],
            evidence_message_ids=["message-1"],
        ),
        subgoals=subgoals
        or [
            ai_inbox.MatterSubgoalState(
                goal="Move the account servicing branch",
                status="in_progress",
                latest_development="The bank is processing the request.",
                evidence_message_ids=["message-1"],
            )
        ],
        decision_basis=ai_inbox.GroupingDecisionBasis(
            verdict=resolved_verdict,
            explanation=(
                "This event causally advances the same unresolved request."
                if candidate is not None
                else "No candidate has the same continuing purpose."
            ),
            supporting_factors=["same concrete purpose"],
        ),
        evidence_message_ids=["message-1"],
        risk_flags=risks or [],
    )


class AIInboxSanitizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(app_encryption_key="test-hmac-key")

    def test_rfc_ancestor_ids_use_exact_reply_headers(self) -> None:
        headers = {
            "in-reply-to": "<parent@example.com>",
            "references": "<root@example.com> <parent@example.com>",
        }

        self.assertEqual(
            ai_inbox._rfc_ancestor_message_ids(headers),
            ["<parent@example.com>", "<root@example.com>"],
        )

    def test_conversation_detail_recovers_cross_gmail_thread_origin_without_ai(self) -> None:
        root = replace(
            _message(text="Please resolve this request", label_ids=["SENT"]),
            message_id="root-message",
            gmail_thread_id="outbound-thread",
            headers={"message-id": "<root@example.com>"},
        )
        reply = replace(
            _message(text="We received your request"),
            message_id="reply-message",
            gmail_thread_id="reply-thread",
            headers={
                "message-id": "<reply@example.com>",
                "in-reply-to": "<root@example.com>",
            },
        )

        with patch.object(
            ai_inbox,
            "list_messages_by_rfc_message_ids",
            return_value=[root],
        ) as lookup:
            messages = ai_inbox._messages_with_rfc_ancestors(
                SimpleNamespace(database_path="postgresql:///unused"),
                user_id="user-1",
                messages=[reply],
            )

        self.assertEqual({message.message_id for message in messages}, {"root-message", "reply-message"})
        lookup.assert_called_once_with(
            "postgresql:///unused",
            user_id="user-1",
            rfc_message_ids=["<root@example.com>"],
        )

    def test_matter_detail_contract_does_not_serialize_outcomes(self) -> None:
        self.assertNotIn("subgoals", AIMatterDetailResponse.model_fields)
        self.assertNotIn("events", AIMatterDetailResponse.model_fields)
        self.assertIn("matter_message_ids", AIMatterDetailResponse.model_fields)

    def test_model_text_removes_credentials_numbers_auth_links_and_quoted_history(self) -> None:
        text = (
            "Case reference SR-2260890430 is now being processed.\n"
            "Account number 1234 5678 9012 and password: hunter2.\n"
            "Your OTP is 483920.\n"
            "Verify at https://bank.example/login?token=very-secret-token\n"
            "On Tue, Support wrote:\n"
            "> duplicated old content"
        )

        cleaned, references = ai_inbox._model_text(self.settings, _message(text=text))

        self.assertNotIn("hunter2", cleaned)
        self.assertNotIn("483920", cleaned)
        self.assertNotIn("1234 5678 9012", cleaned)
        self.assertNotIn("very-secret-token", cleaned)
        self.assertNotIn("duplicated old content", cleaned)
        self.assertIn("[CREDENTIAL_REMOVED]", cleaned)
        self.assertIn("[AUTHENTICATION_CODE_REMOVED]", cleaned)
        self.assertIn("[AUTHENTICATION_LINK_REMOVED]", cleaned)
        self.assertTrue(references)
        _, references_without_secrets = ai_inbox._model_text(
            self.settings,
            _message(text="Case reference SR-2260890430 is now being processed."),
        )
        self.assertEqual(references, references_without_secrets)
        self.assertTrue(all(value.startswith("ref:v1:") for value in references))
        self.assertTrue(all("2260890430" not in value for value in references))

    def test_reference_tokens_are_stable_but_secret(self) -> None:
        first = ai_inbox._reference_tokens(self.settings, "Ticket ABC-1234567")
        second = ai_inbox._reference_tokens(self.settings, "ticket abc-1234567")
        other_key = ai_inbox._reference_tokens(
            SimpleNamespace(app_encryption_key="another-key"), "Ticket ABC-1234567"
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, other_key)
        self.assertNotIn("abc1234567", json.dumps(first).lower())

    def test_quoted_numeric_reply_references_in_subject_are_recognized(self) -> None:
        first = _message(
            text="Please process the requested limit enhancement.",
            subject="Re: '1899633014' Re: '1853927768' Requesting to upgrade credit card",
        )
        second = _message(
            text="The limit enhancement has been processed.",
            subject="RE: ‘1899633014’ Re: ‘1853927768’ Limit enhancement processed",
        )

        _, first_references = ai_inbox._model_text(self.settings, first)
        _, second_references = ai_inbox._model_text(self.settings, second)

        self.assertEqual(len(first_references), 2)
        self.assertEqual(set(first_references), set(second_references))
        self.assertTrue(all("1899633014" not in item for item in first_references))

    def test_unlabelled_number_in_body_is_not_a_reference(self) -> None:
        _, references = ai_inbox._model_text(
            self.settings,
            _message(text="The account balance is 1234567890.", subject="Balance update"),
        )

        self.assertEqual(references, [])

    def test_unlabelled_mixed_identifier_is_subject_only(self) -> None:
        _, subject_references = ai_inbox._model_text(
            self.settings,
            _message(text="A support update.", subject="Re: HSBC///FB-2260890430"),
        )
        _, body_references = ai_inbox._model_text(
            self.settings,
            _message(text="Account alias ABC-1234567", subject="Balance update"),
        )

        self.assertEqual(len(subject_references), 1)
        self.assertEqual(body_references, [])

    def test_html_only_message_uses_visible_complete_body(self) -> None:
        message = replace(
            _message(text="fallback snippet"),
            text_body=None,
            snippet="fallback snippet",
            html_body_sanitized=(
                "<html><head><style>.hidden{display:none}</style></head>"
                "<body><p>The visible request &amp; its update.</p></body></html>"
            ),
        )

        cleaned, _ = ai_inbox._model_text(self.settings, message)

        self.assertIn("The visible request & its update", cleaned)
        self.assertNotIn("display:none", cleaned)
        self.assertNotIn("fallback snippet", cleaned)

    def test_first_forwarded_message_is_kept_but_nested_duplicate_is_removed(self) -> None:
        cleaned = ai_inbox._strip_quoted_and_boilerplate(
            "Please handle this.\n"
            "--- Forwarded message ---\n"
            "From: Partner\nThe actual request.\n"
            "--- Forwarded message ---\n"
            "Duplicated earlier chain."
        )

        self.assertIn("The actual request", cleaned)
        self.assertNotIn("Duplicated earlier chain", cleaned)

    def test_attachment_text_is_redacted_before_model_use_and_references_are_hashed(self) -> None:
        message = replace(
            _message(text="See the attached request."),
            attachment_descriptors=[
                {
                    "attachment_id": "attachment-1",
                    "filename": "request.txt",
                    "mime_type": "text/plain",
                }
            ],
        )
        settings = SimpleNamespace(
            app_encryption_key="test-hmac-key",
            database_path="postgresql:///unused",
        )
        raw = b"Ticket ABC-1234567 password: private-value account 123456789012"

        with (
            patch.object(
                ai_inbox,
                "fetch_gmail_attachment",
                return_value={"data": urlsafe_b64encode(raw).decode().rstrip("=")},
            ),
            patch.object(ai_inbox, "upsert_attachment_extraction") as upsert,
        ):
            model_text, references, entity_tokens = ai_inbox._extract_attachment_text(settings, message)

        self.assertNotIn("private-value", model_text)
        self.assertNotIn("123456789012", model_text)
        self.assertIn("[CREDENTIAL_REMOVED]", model_text)
        self.assertTrue(references)
        self.assertTrue(entity_tokens)
        # Local derived storage may retain extracted text; only the redacted
        # value above is eligible for provider transmission.
        self.assertIn("private-value", upsert.call_args.kwargs["extracted_text"])

    def test_attachment_text_removes_nul_characters_before_storage(self) -> None:
        message = replace(
            _message(text="See the attached invoice."),
            attachment_descriptors=[
                {
                    "attachment_id": "attachment-1",
                    "filename": "invoice.pdf",
                    "mime_type": "application/pdf",
                }
            ],
        )
        settings = SimpleNamespace(
            app_encryption_key="test-hmac-key",
            database_path="postgresql:///unused",
        )

        with (
            patch.object(
                ai_inbox,
                "fetch_gmail_attachment",
                return_value={"data": urlsafe_b64encode(b"pdf").decode().rstrip("=")},
            ),
            patch.object(
                ai_inbox,
                "_text_from_attachment",
                return_value="Invoice Z6Y9ONSQ\x000001\nOlivi\x00a Sauls",
            ),
            patch.object(ai_inbox, "upsert_attachment_extraction") as upsert,
        ):
            model_text, _, _ = ai_inbox._extract_attachment_text(settings, message)

        stored_text = upsert.call_args.kwargs["extracted_text"]
        self.assertNotIn("\x00", stored_text)
        self.assertNotIn("\x00", model_text)
        self.assertIn("Invoice Z6Y9ONSQ0001", stored_text)


class AIInboxCounterpartIdentityTests(unittest.TestCase):
    def test_verified_organization_aliases_replace_departments_and_domains(self) -> None:
        fixtures = [
            ('"Domain Harbor Test Support." <test@domain-harbor.example>', "Domain Harbor"),
            ('"Screener Test" <test@screener.in>', "Screener"),
            ("test@northstarbank.example", "Northstar Bank"),
            ("Test Office <test@bank.example>", "HSBC"),
            ("<test@bank.example>", "SBI"),
            ('"Test Contact" <test@university.example>', "State University"),
            ("Test Foundation Update <test@linuxfoundation.org>", "Linux Foundation"),
            ('"Test Financial Service" <test@schwab.com>', "Charles Schwab"),
            ('"Test Grants Contact" <test@aigrants.in>', "AI Grants India"),
        ]

        for sender, expected in fixtures:
            with self.subTest(sender=sender):
                message = _message(text="A factual update.", sender=sender)
                self.assertEqual(
                    ai_inbox.resolve_counterpart_entities(message),
                    [expected],
                )

    def test_sent_message_uses_external_recipients_and_excludes_the_owner(self) -> None:
        message = _message(
            text="Please increase the card limit.",
            sender="Mailbox Owner <owner@example.com>",
            recipients={
                "to": ["Priority Redressal Credit Cards <priority@northstar.example>"],
                "cc": ["Mailbox Owner <owner@example.com>"],
            },
            label_ids=["SENT"],
        )

        self.assertEqual(ai_inbox.resolve_counterpart_entities(message), ["Northstar Bank"])

    def test_relay_resolves_represented_organization_instead_of_delivery_service(self) -> None:
        fixtures = [
            ("OpenAI Build Week <event@calendar.luma-mail.com>", "OpenAI"),
            ("Northstar via Adobe Sign <agreement@adobesign.com>", "Northstar Bank"),
            ("State University via Calendly <invite@calendly.com>", "State University"),
            ("Charles Schwab via Stripe <payment-receipt@example.compe.com>", "Charles Schwab"),
        ]
        for sender, expected in fixtures:
            with self.subTest(sender=sender):
                message = _message(
                    text=f"{expected} sent a factual update.",
                    sender=sender,
                )
                self.assertEqual(ai_inbox.resolve_counterpart_entities(message), [expected])

        message = _message(
            text="OpenAI Build Week registration is approved.",
            sender=fixtures[0][0],
        )
        candidates = ai_inbox.redacted_identity_candidates(message)
        self.assertEqual(candidates[0]["domain"], "calendar.luma-mail.com")
        self.assertNotIn("event@", json.dumps(candidates))

    def test_personal_conversation_preserves_the_person_name(self) -> None:
        message = _message(
            text="Are we still meeting tomorrow?",
            sender="Test Contact <test@example.test>",
        )

        self.assertEqual(ai_inbox.resolve_counterpart_entities(message), ["Test Contact"])

    def test_model_entity_must_be_supported_by_redacted_identity_or_content(self) -> None:
        message = _message(
            text="The fellowship application was received by General Intelligence Company.",
            sender="Andrew Pignanelli <andrew@generalintelligencecompany.com>",
        )

        self.assertEqual(
            ai_inbox.resolve_counterpart_entities(
                message,
                suggested_entities=["Invented Holdings", "General Intelligence Company"],
                context_text=message.text_body or "",
            ),
            ["General Intelligence Company"],
        )

    def test_weak_unknown_sender_uses_conservative_domain_fallback(self) -> None:
        message = _message(
            text="A routine service notification.",
            sender="Support <help@mailer.rocketlane.com>",
        )

        self.assertEqual(
            ai_inbox.resolve_counterpart_entities(
                message,
                suggested_entities=["Invented Holdings"],
            ),
            ["Rocketlane"],
        )

    def test_matter_aggregation_deduplicates_departments_and_ranks_distinct_entities(self) -> None:
        records = [
            {
                "sender": "Northstar Bank Care <care@northstar.example>",
                "recipients": {"to": ["owner@example.com"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Northstar"],
                "latest_message_at": "2026-08-20T10:00:00+00:00",
            },
            {
                "sender": "Priority Redressal Credit Cards <priority@northstar.example>",
                "recipients": {"to": ["owner@example.com"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Northstar"],
                "latest_message_at": "2026-08-21T10:00:00+00:00",
            },
            {
                "sender": "Mailbox Owner <owner@example.com>",
                "recipients": {
                    "to": ["Credit Card Operations <cards@northstarbank.example>"],
                },
                "label_ids": ["SENT"],
                "counterpart_entities": ["Northstar"],
                "latest_message_at": "2026-08-21T11:00:00+00:00",
            },
            {
                "sender": "FxNoReply <notice@ccilindia.co.in>",
                "recipients": {"to": ["owner@example.com"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["CCIL"],
                "latest_message_at": "2026-08-22T10:00:00+00:00",
            },
        ]

        self.assertEqual(aggregate_counterpart_entities(records), ["Northstar Bank", "CCIL"])

    def test_group_wide_participants_collapse_departments_and_use_owner_when_needed(self) -> None:
        owner = mailbox_owner_identity(
            [
                "TestUser <owner@example.test>",
                "TestUser <demo@example.test>",
            ],
            fallback_display_name="TestUser",
            fallback_address="demo@example.test",
        )
        northstar_records = [
            {
                "sender": "Priority Redressal Credit Cards <priority@northstar.example>",
                "recipients": {"to": ["TestUser <owner@example.test>"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Priority Redressal Credit Cards"],
                "latest_message_at": "2026-08-20T10:00:00+00:00",
            },
            {
                "sender": "TestUser <owner@example.test>",
                "recipients": {"to": ["Support Department <cards@northstarbank.example>"]},
                "label_ids": ["SENT"],
                "counterpart_entities": ["Support Department"],
                "latest_message_at": "2026-08-21T10:00:00+00:00",
            },
        ]

        self.assertEqual(
            aggregate_counterpart_entities(northstar_records, owner_identity=owner),
            ["Northstar Bank", "TestUser"],
        )

        sbi_record = {
            "sender": "<test@bank.example>",
            "recipients": {"to": ["<mailbox-test@example.test>"]},
            "label_ids": ["INBOX"],
            "counterpart_entities": ["SBI", "Example Merchant"],
            "latest_message_at": "2026-08-20T10:00:00+00:00",
        }
        self.assertEqual(
            aggregate_counterpart_entities(
                [sbi_record],
                context_text="A payment was sent to Example Merchant",
                owner_identity=owner,
            ),
            ["SBI"],
        )

    def test_received_mass_mail_ignores_other_recipients(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <demo@example.test>"],
        )
        records = [
            {
                "sender": "Community Network <community@example.test>",
                "recipients": {
                    "to": [
                        "TestUser <demo@example.test>",
                        "Contact One <contact-one@example.test>",
                        "Contact Two <contact-two@example.test>",
                    ],
                },
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Community Network", "Contact One"],
                "latest_message_at": "2023-09-27T11:01:00+00:00",
            },
            {
                "sender": "Contact Three <contact-four@example.test>",
                "recipients": {
                    "to": ["Community Network <community@example.test>"],
                    "cc": [
                        "TestUser <demo@example.test>",
                        "Contact Four <contact-three@example.test>",
                    ],
                },
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Contact Three", "Contact Four"],
                "latest_message_at": "2023-09-28T06:11:00+00:00",
            },
        ]

        self.assertEqual(
            aggregate_counterpart_entities(records, owner_identity=owner),
            ["Contact Three", "Community Network"],
        )

    def test_body_only_entities_do_not_appear_as_row_participants(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <demo@example.test>"],
        )
        record = {
            "sender": "INVOICES Hack Club <hcb@hackclub.com>",
            "recipients": {"to": ["TestUser <demo@example.test>"]},
            "label_ids": ["INBOX"],
            "counterpart_entities": ["INVOICES Hack Club", "The Hack Foundation"],
            "latest_message_at": "2024-04-15T13:25:45+00:00",
        }

        self.assertEqual(
            aggregate_counterpart_entities([record], owner_identity=owner),
            ["INVOICES Hack Club"],
        )

    def test_bare_domain_uses_exact_identity_backed_human_presentation(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <owner@example.test>"],
        )
        records = [
            {
                "sender": "support@example-retailer.example",
                "recipients": {"to": ["TestUser <owner@example.test>"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["Rbiretaildirect"],
                "latest_message_at": "2026-08-27T10:00:00+00:00",
            },
            {
                "sender": "support@example-retailer.example",
                "recipients": {"to": ["TestUser <owner@example.test>"]},
                "label_ids": ["INBOX"],
                "counterpart_entities": ["RBI Retail Direct"],
                "latest_message_at": "2026-08-28T10:00:00+00:00",
            },
        ]

        self.assertEqual(
            aggregate_counterpart_entities(records, owner_identity=owner),
            ["RBI Retail Direct"],
        )

    def test_received_mail_does_not_render_the_owner_as_a_counterpart(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <owner@example.test>"],
        )
        received_record = {
            "sender": "Domain Harbor Support <test@domain-harbor.example>",
            "recipients": {"to": ["TestUser <owner@example.test>"]},
            "label_ids": ["INBOX"],
            "counterpart_entities": ["Domain Harbor"],
            "latest_message_at": "2026-08-28T04:57:00+00:00",
        }

        self.assertEqual(
            aggregate_counterpart_entities([received_record], owner_identity=owner),
            ["Domain Harbor"],
        )

    def test_sent_only_mail_does_not_render_the_owner_as_a_counterpart(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <demo@example.test>"],
        )
        sent_record = {
            "sender": "TestUser <demo@example.test>",
            "recipients": {"to": ["printandgo@fedex.com"]},
            "label_ids": ["SENT", "INBOX"],
            "counterpart_entities": ["Fedex"],
            "latest_message_at": "2023-04-20T17:53:56+00:00",
        }

        self.assertEqual(
            aggregate_counterpart_entities([sent_record], owner_identity=owner),
            ["Fedex"],
        )

    def test_identity_presentation_uses_proper_names_and_preserves_acronyms(self) -> None:
        message = _message(
            text="A payment was sent to example merchant.",
            sender="<test@bank.example>",
        )

        self.assertEqual(
            ai_inbox.resolve_counterpart_entities(
                message,
                suggested_entities=["sbi", "example merchant"],
                context_text=message.text_body or "",
            ),
            ["SBI", "Sarthak Sewa Private"],
        )

    def test_group_wide_aliases_collapse_psu_income_tax_and_cams(self) -> None:
        owner = mailbox_owner_identity(
            ["TestUser <owner@example.test>"],
        )
        fixtures = [
            (
                [
                    {
                        "sender": "PSU ENROLLMENT FEE <noreply@elavon.com>",
                        "recipients": {"to": ["owner@example.test"]},
                        "label_ids": ["INBOX"],
                        "counterpart_entities": ["PSU ENROLLMENT FEE", "State University"],
                    }
                ],
                ["State University"],
            ),
            (
                [
                    {
                        "sender": "tax-communication@example.gov",
                        "recipients": {"to": ["owner@example.test"]},
                        "label_ids": ["INBOX"],
                        "counterpart_entities": ["Tax Service"],
                    },
                    {
                        "sender": "Your tax notice <tax-intimation@example.gov>",
                        "recipients": {"to": ["owner@example.test"]},
                        "label_ids": ["INBOX"],
                        "counterpart_entities": ["Your tax notice"],
                    },
                ],
                ["Tax Service"],
            ),
            (
                [
                    {
                        "sender": "donotreply@camsonline.com",
                        "recipients": {"to": ["demo@example.test"]},
                        "label_ids": ["INBOX"],
                        "counterpart_entities": ["CAMS"],
                    },
                    {
                        "sender": "CVLKRA <alerts@cvlkra.com>",
                        "recipients": {"to": ["demo@example.test"]},
                        "label_ids": ["INBOX"],
                        "counterpart_entities": ["CDSL Ventures Limited", "CAMS"],
                    },
                ],
                ["CAMS", "CVL KRA"],
            ),
        ]

        for records, expected in fixtures:
            with self.subTest(expected=expected):
                self.assertEqual(
                    aggregate_counterpart_entities(records, owner_identity=owner),
                    expected,
                )

    def test_headline_omits_redundant_single_counterpart(self) -> None:
        self.assertEqual(
            counterpart_aware_headline(
                "Northstar confirms ₹75,000 credit-limit enhancement was processed",
                ["Northstar Bank"],
            ),
            "₹75,000 credit-limit enhancement was processed",
        )
        self.assertEqual(
            counterpart_aware_headline(
                "HSBC Bank closes feedback pending branch-change documents",
                ["HSBC"],
            ),
            "Closes feedback pending branch-change documents",
        )

    def test_headline_preserves_names_for_multi_party_outcome(self) -> None:
        title = "HSBC sent the settlement instruction and CCIL confirmed funding"

        self.assertEqual(
            counterpart_aware_headline(title, ["HSBC", "CCIL"]),
            title,
        )

    def test_classifier_receives_only_redacted_counterpart_candidates(self) -> None:
        settings = SimpleNamespace(
            ai_inbox_classifier_model="gpt-5.6-sol",
            app_encryption_key="test-hmac-key",
        )
        message = _message(
            text="The application was received.",
            sender="Admissions Team <private-local-part@university.example>",
        )
        parsed = _classification(candidate=None)
        with patch.object(ai_inbox, "_structured_response", return_value=parsed) as response:
            result = ai_inbox._classify(
                settings,
                client=object(),
                user_id="user-1",
                generation_id="generation-1",
                message=message,
                sanitized_text="The application was received.",
                candidates=[],
                grouping_style="focused",
            )

        self.assertIs(result, parsed)
        payload = response.call_args.kwargs["payload"]
        self.assertEqual(
            payload["counterpart_candidates"],
            [{"display_name": "Admissions Team", "domain": "university.example", "role": "sender"}],
        )
        self.assertNotIn("private-local-part", json.dumps(payload["counterpart_candidates"]))

    def test_classifier_bounds_accumulated_candidate_subgoal_evidence(self) -> None:
        settings = SimpleNamespace(
            ai_inbox_classifier_model="gpt-5.6-sol",
            app_encryption_key="test-hmac-key",
        )
        parsed = _classification(candidate="matter-1")
        evidence = [f"message-{index}" for index in range(25)]
        candidates = [
            {
                "id": "matter-1",
                "stable_goal": "Move the account servicing branch",
                "dynamic_title": "The branch transfer is being processed",
                "summary": "The bank is processing the requested branch transfer.",
                "status": "in_progress",
                "confidence_state": "automatic",
                "subgoals": [
                    {
                        "id": "subgoal-1",
                        "goal": "Move the account servicing branch",
                        "status": "in_progress",
                        "latest_development": "The bank is processing the request.",
                        "evidence_message_ids": evidence,
                    }
                ],
                "event_history": [],
            }
        ]

        with patch.object(ai_inbox, "_structured_response", return_value=parsed) as response:
            result = ai_inbox._classify(
                settings,
                client=object(),
                user_id="user-1",
                generation_id="generation-1",
                message=_message(text="The application was received."),
                sanitized_text="The application was received.",
                candidates=candidates,
                grouping_style="focused",
            )

        self.assertIs(result, parsed)
        bounded = response.call_args.kwargs["payload"]["candidate_matters"][0]["subgoals"][0]
        self.assertEqual(bounded["evidence_message_ids"], evidence[-20:])

    def test_public_rows_add_entities_without_replacing_raw_identity_fields(self) -> None:
        matter = AIMatterRow(
            id="matter-1",
            title="Credit-limit enhancement was processed",
            summary="The request was completed.",
            status="completed",
            confidence_state="automatic",
            confidence=0.98,
            latest_message_at="2026-08-21T10:00:00+00:00",
            message_count=2,
            participants=["test@northstarbank.example"],
            counterpart_entities=["Northstar"],
            revision=1,
        )
        organizing = AIOrganizingRow(
            id="organizing:thread-1",
            gmail_thread_id="thread-1",
            title="Domain expiration notice",
            sender='"Domain Harbor Test Support." <test@domain-harbor.example>',
            counterpart_entities=["Domain Harbor"],
            latest_message_at="2026-08-21T10:00:00+00:00",
        )

        self.assertEqual(matter.counterpart_entities, ["Northstar"])
        self.assertEqual(matter.participants, ["test@northstarbank.example"])
        self.assertEqual(organizing.counterpart_entities, ["Domain Harbor"])
        self.assertIn("Test Support", organizing.sender or "")


class AIInboxDecisionGateTests(unittest.TestCase):
    def test_safe_exact_same_thread_merge_does_not_require_risk_review(self) -> None:
        candidate = {
            "same_gmail_thread": True,
            "exact_reference": True,
            "status": "in_progress",
        }
        self.assertFalse(ai_inbox._requires_review(_classification(), candidate))

    def test_cross_thread_merge_requires_risk_review(self) -> None:
        candidate = {
            "same_gmail_thread": False,
            "exact_reference": True,
            "status": "in_progress",
        }
        self.assertTrue(ai_inbox._requires_review(_classification(), candidate))

    def test_completed_claim_and_completed_reopen_require_risk_review(self) -> None:
        current = {"same_gmail_thread": True, "exact_reference": True, "status": "in_progress"}
        completed = {"same_gmail_thread": True, "exact_reference": True, "status": "completed"}
        self.assertTrue(ai_inbox._requires_review(_classification(status="completed"), current))
        self.assertTrue(ai_inbox._requires_review(_classification(), completed))

    def test_new_matter_without_continuation_evidence_stays_separate_without_review(self) -> None:
        self.assertFalse(
            ai_inbox._requires_review(
                _classification(candidate=None),
                None,
            )
        )

    def test_split_inside_an_existing_gmail_thread_requires_risk_review(self) -> None:
        same_thread = {
            "same_gmail_thread": True,
            "exact_reference": False,
            "status": "in_progress",
            "vector_similarity": 0.72,
        }
        self.assertTrue(
            ai_inbox._requires_review(
                _classification(candidate=None),
                None,
                [same_thread],
            )
        )

    def test_cross_thread_new_matter_with_similar_candidate_requires_review(self) -> None:
        related = {
            "same_gmail_thread": False,
            "exact_reference": False,
            "status": "in_progress",
            "vector_similarity": 0.78,
        }

        self.assertTrue(
            ai_inbox._requires_review(
                _classification(candidate=None),
                None,
                [related],
            )
        )

    def test_review_recommendation_must_be_supported_and_allowlisted(self) -> None:
        candidates = [{"id": "matter-1"}, {"id": "matter-2"}]
        accepted = ai_inbox.MatterReview(
            recommended_matter_id="matter-2",
            verdict="strong_continuation",
            explanation="The message advances the same unresolved request.",
        )
        invented = ai_inbox.MatterReview(
            recommended_matter_id="invented",
            verdict="strong_continuation",
            explanation="The message advances the same unresolved request.",
        )
        conflicting = ai_inbox.MatterReview(
            recommended_matter_id="matter-2",
            verdict="strong_continuation",
            explanation="The evidence is internally contradictory.",
            risk_flags=["insufficient_evidence"],
        )

        self.assertEqual(ai_inbox._recommended_candidate(accepted, candidates), candidates[1])
        self.assertIsNone(ai_inbox._recommended_candidate(invented, candidates))
        self.assertIsNone(ai_inbox._recommended_candidate(conflicting, candidates))

    def test_review_receives_alternative_matters_for_new_matter_proposal(self) -> None:
        settings = SimpleNamespace(ai_inbox_review_model="gpt-5.6-sol")
        candidates = [
            {
                "id": "matter-1",
                "stable_goal": "Increase the card limit",
                "dynamic_title": "The bank is processing the limit increase",
                "summary": "The customer requested a credit-limit increase.",
                "status": "in_progress",
                "exact_reference": True,
                "same_gmail_thread": False,
                "exact_entity": True,
                "vector_similarity": 0.81,
                "lexical_score": 0.4,
                "subgoals": [],
                "event_history": [],
            }
        ]
        parsed = ai_inbox.MatterReview(
            recommended_matter_id="matter-1",
            verdict="strong_continuation",
            explanation="The completion event resolves the same limit-enhancement request.",
            supporting_factors=["same purpose", "compatible account"],
        )
        with patch.object(ai_inbox, "_structured_response", return_value=parsed) as response:
            result = ai_inbox._review(
                settings,
                client=object(),
                user_id="user-1",
                generation_id="generation-1",
                message_id="message-1",
                sanitized_text="The limit increase has been processed.",
                classification=_classification(candidate=None, status="completed"),
                candidates=candidates,
            )

        self.assertIs(result, parsed)
        payload = response.call_args.kwargs["payload"]
        self.assertEqual(payload["candidate_matters"][0]["id"], "matter-1")

    def test_close_candidate_scores_require_risk_review(self) -> None:
        selected = {
            "same_gmail_thread": True,
            "exact_reference": True,
            "status": "in_progress",
            "vector_similarity": 0.91,
        }
        runner_up = {
            "same_gmail_thread": False,
            "exact_reference": False,
            "status": "in_progress",
            "vector_similarity": 0.89,
        }
        self.assertTrue(
            ai_inbox._requires_review(
                _classification(),
                selected,
                [selected, runner_up],
            )
        )

    def test_only_actual_message_ids_can_become_evidence(self) -> None:
        self.assertEqual(
            ai_inbox._valid_evidence_ids(
                ["invented", "message-1", "message-1"],
                {"message-1"},
                current_message_id="message-1",
            ),
            ["message-1"],
        )
        self.assertEqual(
            ai_inbox._valid_evidence_ids(
                ["invented"],
                {"message-1"},
                current_message_id="message-1",
            ),
            ["message-1"],
        )

    def test_northstar_compound_request_remains_open_after_partial_success(self) -> None:
        classification = _classification(
            status="waiting_on_others",
            role="completed",
            subgoals=[
                ai_inbox.MatterSubgoalState(
                    existing_subgoal_id="limit-enhancement",
                    goal="Increase the Northstar credit limit",
                    status="completed",
                    latest_development="Northstar increased the credit limit by ₹75,000.",
                    evidence_message_ids=["message-1"],
                ),
                ai_inbox.MatterSubgoalState(
                    existing_subgoal_id="card-upgrade",
                    goal="Upgrade the card to Regalia Gold",
                    status="waiting_on_others",
                    latest_development="Northstar has not resolved the requested upgrade.",
                    evidence_message_ids=["message-1"],
                ),
            ],
        )
        candidate = {
            "same_gmail_thread": False,
            "exact_reference": False,
            "exact_entity": True,
            "status": "waiting_on_others",
        }
        review = ai_inbox.MatterReview(
            recommended_matter_id="matter-1",
            verdict="strong_continuation",
            explanation="The completion resolves one subgoal of the same compound card request.",
            supporting_factors=["same card", "same unresolved upgrade", "causal next event"],
        )

        self.assertTrue(ai_inbox._requires_review(classification, candidate))
        self.assertEqual(ai_inbox._recommended_candidate(review, [candidate | {"id": "matter-1"}])["id"], "matter-1")
        self.assertEqual(classification.subgoals[0].status, "completed")
        self.assertEqual(classification.subgoals[1].status, "waiting_on_others")
        self.assertEqual(classification.status, "waiting_on_others")

    def test_shadow_promotion_requires_every_quality_cost_and_latency_gate(self) -> None:
        passing = {
            "reviewed_messages": 100,
            "explicit_reference_candidate_recall": 1.0,
            "automatic_merge_precision": 0.997,
            "false_automatic_merges": 0,
            "unsupported_outcome_headlines": 0,
            "confirmed_membership_moves": 0,
            "unsolicited_gmail_mutations": 0,
            "normal_inbox_regressions": 0,
            "provider_errors": 0,
            "classifier_p95_seconds": 7.8,
            "review_p95_seconds": 19.5,
            "cost_per_1000_messages": 19.9,
        }
        self.assertEqual(ai_inbox.shadow_promotion_gate_failures(passing), [])

        failing = {**passing, "false_automatic_merges": 1, "cost_per_1000_messages": 25.1}
        failures = ai_inbox.shadow_promotion_gate_failures(failing)
        self.assertTrue(any("False automatic merges" in item for item in failures))
        self.assertTrue(any("$20" in item for item in failures))


class AIInboxOpenAIContractTests(unittest.TestCase):
    def test_structured_response_is_not_stored_and_is_bounded(self) -> None:
        parsed = _classification(candidate=None)
        usage = SimpleNamespace(
            input_tokens=800,
            output_tokens=100,
            input_tokens_details=SimpleNamespace(cached_tokens=400),
        )
        parse = unittest.mock.Mock(return_value=SimpleNamespace(output_parsed=parsed, usage=usage))
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        settings = SimpleNamespace(
            database_path="postgresql:///unused",
            ai_inbox_prompt_version="matter-v1",
            ai_inbox_classifier_input_usd_per_million=1.0,
            ai_inbox_classifier_output_usd_per_million=4.0,
            ai_inbox_review_input_usd_per_million=2.0,
            ai_inbox_review_output_usd_per_million=8.0,
            ai_inbox_job_cost_limit_usd=1.0,
        )

        with patch.object(ai_inbox, "record_usage") as record_usage:
            result = ai_inbox._structured_response(
                settings,
                client=client,
                user_id="user-1",
                generation_id="generation-1",
                message_id="message-1",
                operation="classification",
                model="gpt-5.6-luna",
                reasoning_effort="low",
                instructions="Classify conservatively.",
                payload={"content": "sanitized"},
                output_type=ai_inbox.MatterClassification,
                max_output_tokens=1000,
            )

        self.assertIs(result, parsed)
        kwargs = parse.call_args.kwargs
        self.assertIs(kwargs["store"], False)
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertEqual(kwargs["max_output_tokens"], 1000)
        self.assertEqual(kwargs["text_format"], ai_inbox.MatterClassification)
        self.assertEqual(len(kwargs["safety_identifier"]), 64)
        self.assertNotIn("user-1", kwargs["safety_identifier"])
        self.assertEqual(kwargs["prompt_cache_key"], "ai-inbox:matter-v1:classification")
        record_usage.assert_called_once()
        self.assertAlmostEqual(record_usage.call_args.kwargs["estimated_cost_usd"], 0.00084)
        self.assertNotIn("sanitized", json.dumps(record_usage.call_args.kwargs))

    def test_provider_failure_records_only_safe_error_metadata(self) -> None:
        client = SimpleNamespace(
            responses=SimpleNamespace(parse=unittest.mock.Mock(side_effect=TimeoutError("secret body")))
        )
        settings = SimpleNamespace(
            database_path="postgresql:///unused",
            ai_inbox_prompt_version="matter-v1",
        )

        with patch.object(ai_inbox, "record_usage") as record_usage:
            with self.assertRaises(TimeoutError):
                ai_inbox._structured_response(
                    settings,
                    client=client,
                    user_id="user-1",
                    generation_id="generation-1",
                    message_id="message-1",
                    operation="review",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    instructions="Review.",
                    payload={"content": "private mail body"},
                    output_type=ai_inbox.MatterReview,
                    max_output_tokens=400,
                )

        kwargs = record_usage.call_args.kwargs
        self.assertFalse(kwargs["success"])
        self.assertEqual(kwargs["error_code"], "TimeoutError")
        self.assertNotIn("private mail body", json.dumps(kwargs))
        self.assertNotIn("secret body", json.dumps(kwargs))


class AIInboxLocalCodexContractTests(unittest.TestCase):
    def test_local_embedding_is_deterministic_normalized_and_content_sensitive(self) -> None:
        first = ai_inbox._local_embedding("HSBC branch transfer request", dimensions=1024)
        repeated = ai_inbox._local_embedding("HSBC branch transfer request", dimensions=1024)
        unrelated = ai_inbox._local_embedding("Founder fellowship survey", dimensions=1024)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, unrelated)
        self.assertEqual(len(first), 1024)
        self.assertAlmostEqual(sqrt(sum(value * value for value in first)), 1.0)

    def test_codex_provider_uses_ephemeral_sol_fast_schema_turn_without_api_client(self) -> None:
        parsed = _classification(candidate=None)
        result = SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            final_response=parsed.model_dump_json(),
            usage=SimpleNamespace(
                last=SimpleNamespace(
                    input_tokens=100,
                    cached_input_tokens=20,
                    output_tokens=30,
                )
            ),
        )
        turn = SimpleNamespace(
            run=unittest.mock.Mock(return_value=result),
            interrupt=unittest.mock.Mock(),
        )
        thread = SimpleNamespace(turn=unittest.mock.Mock(return_value=turn))
        runtime = SimpleNamespace(thread_start=unittest.mock.Mock(return_value=thread))
        fake_sdk = SimpleNamespace(
            ApprovalMode=SimpleNamespace(deny_all="deny_all"),
            Sandbox=SimpleNamespace(read_only="read_only"),
        )
        settings = SimpleNamespace(
            app_env="local",
            database_path="postgresql:///unused",
            ai_inbox_text_provider="codex",
            ai_inbox_codex_service_tier="fast",
            ai_inbox_codex_timeout_seconds=5.0,
        )

        with (
            patch.dict(sys.modules, {"openai_codex": fake_sdk}),
            patch.object(ai_inbox, "_get_codex_runtime", return_value=runtime),
            patch.object(ai_inbox, "record_usage") as record_usage,
        ):
            response = ai_inbox._structured_response(
                settings,
                client=SimpleNamespace(responses=SimpleNamespace(parse=unittest.mock.Mock(side_effect=AssertionError))),
                user_id="user-1",
                generation_id="generation-1",
                message_id="message-1",
                operation="classification",
                model="gpt-5.6-sol",
                reasoning_effort="low",
                instructions="Classify conservatively.",
                payload={"content": "untrusted email data"},
                output_type=ai_inbox.MatterClassification,
                max_output_tokens=1000,
            )

        self.assertEqual(response, parsed)
        thread_kwargs = runtime.thread_start.call_args.kwargs
        self.assertEqual(thread_kwargs["model"], "gpt-5.6-sol")
        self.assertEqual(thread_kwargs["service_tier"], "fast")
        self.assertTrue(thread_kwargs["ephemeral"])
        self.assertEqual(thread_kwargs["sandbox"], "read_only")
        turn_kwargs = thread.turn.call_args.kwargs
        self.assertEqual(turn_kwargs["service_tier"], "fast")
        self.assertEqual(
            turn_kwargs["output_schema"],
            ai_inbox._strict_output_schema(ai_inbox.MatterClassification),
        )
        usage_kwargs = record_usage.call_args.kwargs
        self.assertEqual(usage_kwargs["model"], "codex:gpt-5.6-sol:fast")
        self.assertEqual(usage_kwargs["estimated_cost_usd"], 0)


class AIInboxQueueTests(unittest.TestCase):
    def test_missing_ai_settings_make_sync_hook_a_noop(self) -> None:
        self.assertIsNone(
            ai_inbox.enqueue_message_organization(
                SimpleNamespace(),
                user_id="user-1",
                message_id="message-1",
                content_revision=1,
            )
        )

    def test_new_mail_is_enqueued_for_active_and_visible_shadow_generations(self) -> None:
        settings = SimpleNamespace(
            ai_inbox_enabled=True,
            ai_inbox_configured=True,
            ai_inbox_prompt_version="matter-v6-event-chain-counterpart-identity",
            database_path="postgresql:///unused",
        )
        with (
            patch.object(ai_inbox, "active_generation", return_value={"id": "active-1"}),
            patch.object(
                ai_inbox,
                "get_profile",
                return_value={"rollout_mode": "shadow"},
            ),
            patch.object(
                ai_inbox,
                "latest_shadow_generation",
                return_value={"id": "shadow-1", "status": "shadow"},
            ),
            patch.object(
                ai_inbox,
                "enqueue_job",
                side_effect=[SimpleNamespace(id="active-job"), SimpleNamespace(id="shadow-job")],
            ) as enqueue,
        ):
            result = ai_inbox.enqueue_message_organization(
                settings,
                user_id="user-1",
                message_id="message-1",
                content_revision=3,
            )

        self.assertEqual(result, "active-job")
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(
            [call.kwargs["payload"]["generation_id"] for call in enqueue.call_args_list],
            ["active-1", "shadow-1"],
        )

    def test_explicit_generation_enqueue_remains_isolated(self) -> None:
        settings = SimpleNamespace(
            ai_inbox_enabled=True,
            ai_inbox_configured=True,
            ai_inbox_prompt_version="matter-v6-event-chain-counterpart-identity",
            database_path="postgresql:///unused",
        )
        with (
            patch.object(ai_inbox, "active_generation") as active,
            patch.object(ai_inbox, "latest_shadow_generation") as shadow,
            patch.object(
                ai_inbox,
                "enqueue_job",
                return_value=SimpleNamespace(id="explicit-job"),
            ) as enqueue,
        ):
            result = ai_inbox.enqueue_message_organization(
                settings,
                user_id="user-1",
                generation_id="explicit-generation",
                message_id="message-1",
                content_revision=3,
            )

        self.assertEqual(result, "explicit-job")
        active.assert_not_called()
        shadow.assert_not_called()
        self.assertEqual(
            enqueue.call_args.kwargs["payload"]["generation_id"],
            "explicit-generation",
        )

    def test_bootstrap_pages_outrank_their_message_jobs(self) -> None:
        with patch.object(
            ai_inbox,
            "enqueue_job",
            return_value=SimpleNamespace(id="job-1"),
        ) as enqueue:
            ai_inbox.enqueue_generation_bootstrap(
                SimpleNamespace(database_path="postgresql:///unused"),
                user_id="user-1",
                generation_id="generation-1",
                recent=True,
                offset=250,
            )

        self.assertEqual(enqueue.call_args.kwargs["queue"], "ai")
        self.assertEqual(enqueue.call_args.kwargs["priority"], 60)

    def test_new_generation_can_request_one_chronological_full_history_stream(self) -> None:
        with patch.object(
            ai_inbox,
            "enqueue_job",
            return_value=SimpleNamespace(id="job-1"),
        ) as enqueue:
            ai_inbox.enqueue_generation_bootstrap(
                SimpleNamespace(database_path="postgresql:///unused"),
                user_id="user-1",
                generation_id="generation-1",
                chronological_all=True,
            )

        payload = enqueue.call_args.kwargs["payload"]
        self.assertTrue(payload["chronological_all"])
        self.assertIn(":all:", enqueue.call_args.kwargs["dedupe_key"])

    def test_chronological_bootstrap_queries_all_history_as_one_scope(self) -> None:
        settings = SimpleNamespace(database_path="postgresql:///unused")
        with (
            patch.object(ai_inbox, "get_generation", return_value={"status": "shadow"}),
            patch.object(ai_inbox, "list_generation_message_ids", return_value=[]) as list_messages,
            patch.object(ai_inbox, "enqueue_job"),
            patch.object(ai_inbox, "emit_mailbox_event"),
        ):
            ai_inbox.run_generation_bootstrap(
                settings,
                user_id="user-1",
                generation_id="generation-1",
                recent=True,
                offset=0,
                chronological_all=True,
            )

        self.assertTrue(list_messages.call_args.kwargs["all_history"])

    def test_superseded_generation_cannot_process_a_message(self) -> None:
        settings = SimpleNamespace(
            ai_inbox_enabled=True,
            ai_inbox_configured=True,
            database_path="postgresql:///unused",
        )
        with (
            patch.object(ai_inbox, "get_profile", return_value={"enabled": True}),
            patch.object(ai_inbox, "get_generation", return_value={"status": "superseded"}),
            patch.object(ai_inbox, "list_messages_by_ids") as list_messages,
        ):
            result = ai_inbox.organize_message(
                settings,
                user_id="user-1",
                generation_id="old-generation",
                message_id="message-1",
                content_revision=1,
            )

        self.assertIsNone(result)
        list_messages.assert_not_called()

    def test_superseded_generation_bootstrap_does_not_enqueue_work(self) -> None:
        settings = SimpleNamespace(database_path="postgresql:///unused")
        with (
            patch.object(ai_inbox, "get_generation", return_value={"status": "superseded"}),
            patch.object(ai_inbox, "list_generation_message_ids") as list_messages,
            patch.object(ai_inbox, "enqueue_job") as enqueue,
        ):
            ai_inbox.run_generation_bootstrap(
                settings,
                user_id="user-1",
                generation_id="old-generation",
                recent=True,
                offset=0,
            )

        list_messages.assert_not_called()
        enqueue.assert_not_called()


class AIInboxMigrationContractTests(unittest.TestCase):
    def test_migration_is_fresh_generation_scoped_pgvector_storage(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "20260827_0033_ai_inbox_matters.py"
        ).read_text()

        for table in (
            "matter_profiles",
            "matter_generations",
            "message_semantics",
            "attachment_text_extractions",
            "matters",
            "matter_members",
            "matter_decisions",
            "ai_usage_events",
        ):
            self.assertIn(f"CREATE TABLE {table}", migration)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", migration)
        self.assertIn("embedding vector(1024)", migration)
        self.assertNotIn("using hnsw", migration.lower())

    def test_event_chain_migration_adds_subgoals_and_partial_status(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "20260828_0034_ai_inbox_event_chain.py"
        ).read_text()

        self.assertIn("CREATE TABLE matter_subgoals", migration)
        self.assertIn("partially_completed", migration)
        self.assertIn("evidence_message_ids", migration)
        self.assertIn("revision", migration)


if __name__ == "__main__":
    unittest.main()
