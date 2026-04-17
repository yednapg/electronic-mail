from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.repository import (
    attach_record_to_entity,
    create_entity,
    find_entity_by_thread_id,
    get_loaded_entity,
    initialize_database,
    list_loaded_entities,
    upsert_source_records,
)
from app.db.models import StoredSourceRecord
from app.schemas.ai import EntityGroupingResponse
from app.schemas.domain import SourceRecord
from app.services.entities.entity_resolver import (
    extract_message_reference_ids,
    find_entity_candidates,
    get_subject_confidence,
    grouping_body,
    resolve_entity_for_record,
)


def build_record(
    *,
    record_id: str,
    source: str,
    thread_id: str,
    subject: str = "Subject",
    sender: str = "Sender <sender@example.com>",
    body: str = "Body text",
    received_at: str = "2024-04-05T10:00:00+00:00",
) -> SourceRecord:
    return SourceRecord(
        id=record_id,
        user_id="local-user",
        source=source,
        thread_id=thread_id,
        raw_payload={
            "subject": subject,
            "from": sender,
            "sender": sender,
            "body": body,
        },
        received_at=received_at,
    )


def candidate_for_entity(entity, *, confidence: float = 0.9) -> dict[str, object]:
    return {
        "entity": entity,
        "confidence": confidence,
        "latest_subject": "Latest subject",
        "latest_sender": "Sender <sender@example.com>",
        "current_state": "received",
        "summary": "Summary",
        "shared_reference_ids": [],
    }


def stored_record_from_source(record: SourceRecord) -> StoredSourceRecord:
    return StoredSourceRecord(
        id=record.id,
        source=record.source,
        thread_id=record.thread_id,
        subject=record.raw_payload["subject"],
        sender=record.raw_payload["from"],
        timestamp=record.received_at,
        raw_payload=record.raw_payload,
        created_at=record.received_at,
    )


class EntityResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "test.sqlite3")
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_gmail_threads_seed_entities_deterministically(self) -> None:
        record = build_record(record_id="gmail-1", source="gmail", thread_id="gmail-thread-1")
        upsert_source_records(self.database_path, [stored_record_from_source(record)])

        entity, confidence = resolve_entity_for_record(self.database_path, record)

        self.assertEqual(confidence, 0.0)
        self.assertEqual(entity.canonical_key, "gmail-thread:gmail-thread-1")
        self.assertEqual(find_entity_by_thread_id(self.database_path, "gmail", "gmail-thread-1").id, entity.id)

        loaded = get_loaded_entity(self.database_path, entity.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([(membership.source, membership.thread_id) for membership in loaded.thread_memberships], [
            ("gmail", "gmail-thread-1"),
        ])

    def test_ai_can_override_a_thread_seed_with_high_confidence(self) -> None:
        seed_record = build_record(record_id="gmail-1", source="gmail", thread_id="gmail-thread-1")
        upsert_source_records(self.database_path, [stored_record_from_source(seed_record)])
        resolve_entity_for_record(self.database_path, seed_record)

        candidate_entity = create_entity(self.database_path, "gmail-thread:gmail-thread-2")

        incoming = build_record(record_id="gmail-3", source="gmail", thread_id="gmail-thread-1", subject="Merge me")
        upsert_source_records(self.database_path, [stored_record_from_source(incoming)])

        with patch("app.services.entities.entity_resolver.find_entity_candidates", return_value=[candidate_for_entity(candidate_entity)]), patch(
            "app.services.entities.entity_resolver.resolve_entity_with_ai",
            return_value=EntityGroupingResponse(entity_id=candidate_entity.id, confidence=0.95),
        ):
            entity, confidence = resolve_entity_for_record(self.database_path, incoming)

        self.assertEqual(entity.id, candidate_entity.id)
        self.assertEqual(confidence, 0.95)
        self.assertEqual(find_entity_by_thread_id(self.database_path, "gmail", "gmail-thread-1").id, candidate_entity.id)

        loaded = get_loaded_entity(self.database_path, candidate_entity.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual([(membership.source, membership.thread_id) for membership in loaded.thread_memberships], [
            ("gmail", "gmail-thread-1"),
        ])

    def test_calendar_records_keep_the_legacy_canonical_seed(self) -> None:
        record = build_record(record_id="calendar-1", source="calendar", thread_id="calendar-event-1")
        upsert_source_records(
            self.database_path,
            [
                stored_record_from_source(record)
            ],
        )

        entity, confidence = resolve_entity_for_record(self.database_path, record)

        self.assertEqual(confidence, 0.0)
        self.assertEqual(entity.canonical_key, "fallback:calendar-1")
        self.assertEqual(find_entity_by_thread_id(self.database_path, "calendar", "calendar-event-1").id, entity.id)

        loaded = list_loaded_entities(self.database_path, [entity.id])[0]
        self.assertEqual([(membership.source, membership.thread_id) for membership in loaded.thread_memberships], [
            ("calendar", "calendar-event-1"),
        ])

    def test_legacy_thread_lookup_still_falls_back_to_message_membership(self) -> None:
        record = build_record(record_id="gmail-legacy-1", source="gmail", thread_id="gmail-legacy-thread")
        upsert_source_records(
            self.database_path,
            [
                stored_record_from_source(record)
            ],
        )
        entity = create_entity(self.database_path, "legacy-threadless-entity")
        attach_record_to_entity(self.database_path, entity.id, record.id)

        self.assertEqual(find_entity_by_thread_id(self.database_path, "gmail", "gmail-legacy-thread").id, entity.id)

    def test_cross_thread_heuristics_do_not_auto_merge_without_shared_references(self) -> None:
        ipo_record = build_record(
            record_id="gmail-ipo",
            source="gmail",
            thread_id="gmail-thread-ipo",
            subject="Your IPO application for National Securities Depository Limited is submitted",
            sender="Zerodha <noreply-ipo@qmailer.zerodha.net>",
            body="Your IPO application for NSDL has been submitted.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(ipo_record)])
        ipo_entity, _ = resolve_entity_for_record(self.database_path, ipo_record)

        email_update_record = build_record(
            record_id="gmail-email-update",
            source="gmail",
            thread_id="gmail-thread-email-update",
            subject="Rectification in the email update request.",
            sender="Zerodha Account <no-reply@mailer.zerodha.net>",
            body="This is about your request to update the email address.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(email_update_record)])

        with patch(
            "app.services.entities.entity_resolver.resolve_entity_with_ai",
            return_value=EntityGroupingResponse(entity_id=None, confidence=0.42),
        ):
            entity, confidence = resolve_entity_for_record(self.database_path, email_update_record)

        self.assertNotEqual(entity.id, ipo_entity.id)
        self.assertEqual(confidence, 0.0)
        self.assertEqual(entity.canonical_key, "gmail-thread:gmail-thread-email-update")

    def test_exact_reference_overlap_is_passed_to_ai_for_cross_thread_merge(self) -> None:
        reference_record = build_record(
            record_id="gmail-ref-seed",
            source="gmail",
            thread_id="gmail-thread-ref-seed",
            subject="Complaint N202526014012439 acknowledgement",
            body="Ref No. N202526014012439 was registered.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(reference_record)])
        reference_entity, _ = resolve_entity_for_record(self.database_path, reference_record)

        followup_record = build_record(
            record_id="gmail-ref-followup",
            source="gmail",
            thread_id="gmail-thread-ref-followup",
            subject="Closure of complaint N202526014012439",
            body="Ref No. N202526014012439 is now closed.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(followup_record)])

        with patch(
            "app.services.entities.entity_resolver.find_entity_candidates",
            return_value=[
                {
                    **candidate_for_entity(reference_entity, confidence=0.94),
                    "shared_reference_ids": ["N202526014012439"],
                }
            ],
        ), patch(
            "app.services.entities.entity_resolver.resolve_entity_with_ai",
            return_value=EntityGroupingResponse(entity_id=reference_entity.id, confidence=0.96),
        ):
            entity, confidence = resolve_entity_for_record(self.database_path, followup_record)

        self.assertEqual(entity.id, reference_entity.id)
        self.assertEqual(confidence, 0.96)

    def test_grouping_body_ignores_quoted_history_and_html(self) -> None:
        body = """
        <div>Hello there</div>
        <div>Please review the latest response.</div>
        <div>From: older@example.com</div>
        <div>Complaint No. N202526014012439</div>
        """

        self.assertEqual(grouping_body(body), "Hello there\nPlease review the latest response.")

    def test_reference_extraction_ignores_quoted_history(self) -> None:
        refs = extract_message_reference_ids(
            "American Express Complaint Reference Number GC-IN3T8I/CL-INFVC68",
            "Thanks for the update.\nFrom: old@example.com\nComplaint No. N202526014012439\nCase ID 0006750673",
        )

        self.assertEqual(refs, {"GC-IN3T8I/CL-INFVC68"})

    def test_different_case_id_subjects_are_not_considered_similar(self) -> None:
        confidence = get_subject_confidence("case id 0006750673", "case id 0006751571")
        self.assertEqual(confidence, 0.0)

    def test_related_threads_can_be_considered_across_sender_subdomains(self) -> None:
        rectification_record = build_record(
            record_id="gmail-rectification",
            source="gmail",
            thread_id="gmail-thread-rectification",
            subject="Rectification in the email update request.",
            sender="Zerodha Account <no-reply@mailer.zerodha.net>",
            body="This is about your request to update the email address.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(rectification_record)])
        rectification_entity, _ = resolve_entity_for_record(self.database_path, rectification_record)

        callback_record = build_record(
            record_id="gmail-callback",
            source="gmail",
            thread_id="gmail-thread-callback",
            subject="Request for Clarification on Email ID Modification Rejection [Ticket #20250805900531]- Zerodha Helpdesk",
            sender="Zerodha Support <support@mailer.zerodha.com>",
            body="Kindly let us know a convenient time for a callback about the email ID modification rejection.",
        )

        candidates = find_entity_candidates(self.database_path, callback_record)

        self.assertTrue(any(candidate["entity"].id == rectification_entity.id for candidate in candidates))

    def test_low_signal_rbi_case_without_shared_named_markers_is_not_a_candidate(self) -> None:
        hsbc_registered = build_record(
            record_id="gmail-hsbc-registered",
            source="gmail",
            thread_id="gmail-thread-hsbc-registered",
            subject="Acknowledgement: Registration of Complaint - N202526014012439 against HONGKONG AND SHANGHAI BANKING CORPN.LTD. regarding Credit Card",
            sender="RBI CMS Team <rbi-team@example.gov>",
            body="This complaint concerns your HSBC credit card application rejection.",
        )
        upsert_source_records(self.database_path, [stored_record_from_source(hsbc_registered)])
        hsbc_entity, _ = resolve_entity_for_record(self.database_path, hsbc_registered)

        cibil_case = build_record(
            record_id="gmail-cibil-case",
            source="gmail",
            thread_id="gmail-thread-cibil-case",
            subject="[Case ID : 0006751571]",
            sender="crpc <rbi-crpc@example.gov>",
            body="Please refer to the CIBIL response regarding persistent MyCIBIL login failures.",
        )

        candidates = find_entity_candidates(self.database_path, cibil_case)

        self.assertFalse(any(candidate["entity"].id == hsbc_entity.id for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
