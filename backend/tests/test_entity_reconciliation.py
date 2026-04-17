from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.repository import (
    attach_record_to_entity,
    attach_thread_to_entity,
    create_entity,
    initialize_database,
    list_all_loaded_entities,
    upsert_source_records,
)
from app.db.models import StoredSourceRecord
from app.schemas.ai import EntityGroupingResponse
from app.schemas.domain import SourceRecord
from app.services.entities.entity_reconciler import reconcile_entities


def build_record(
    *,
    record_id: str,
    thread_id: str,
    subject: str,
    sender: str,
    body: str,
    received_at: str,
) -> SourceRecord:
    return SourceRecord(
        id=record_id,
        user_id="local-user",
        source="gmail",
        thread_id=thread_id,
        raw_payload={
            "subject": subject,
            "from": sender,
            "sender": sender,
            "body": body,
        },
        received_at=received_at,
    )


class EntityReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "test.sqlite3")
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def seed_entity(self, record: SourceRecord):
        upsert_source_records(
            self.database_path,
            [
                StoredSourceRecord(
                    id=record.id,
                    source=record.source,
                    thread_id=record.thread_id,
                    subject=record.raw_payload["subject"],
                    sender=record.raw_payload["from"],
                    timestamp=record.received_at,
                    raw_payload=record.raw_payload,
                    created_at=record.received_at,
                )
            ],
        )
        entity = create_entity(self.database_path, f"gmail-thread:{record.thread_id}")
        attach_record_to_entity(self.database_path, entity.id, record.id)
        attach_thread_to_entity(self.database_path, entity.id, record.source, record.thread_id)
        return entity

    def test_reconcile_entities_uses_ai_to_merge_shared_complaint_reference(self) -> None:
        registered = build_record(
            record_id="registered",
            thread_id="thread-registered",
            subject="Acknowledgement: Registration of Complaint - N202526014012439",
            sender="RBI CMS Team <rbiteamcms@rbi.org.in>",
            body="RBI/CMS/N202526014012439/2025-26",
            received_at="2025-08-26T15:30:28+00:00",
        )
        compensation = build_record(
            record_id="compensation",
            thread_id="thread-compensation",
            subject="[Restricted] CEBO/RK/2250719966",
            sender="Nodal Officer INM <nodalofficerinm@hsbc.co.in>",
            body="This is in reference to RBI complaint N202526014012439 regarding your HSBC Live Credit Card application.",
            received_at="2025-09-16T03:58:07+00:00",
        )

        registered_entity = self.seed_entity(registered)
        self.seed_entity(compensation)

        with patch(
            "app.services.entities.entity_reconciler.resolve_entity_group",
            return_value=EntityGroupingResponse(entity_id=registered_entity.id, confidence=0.97),
        ):
            changed = reconcile_entities(self.database_path)

        loaded_entities = list_all_loaded_entities(self.database_path)
        self.assertEqual(len(loaded_entities), 1)
        self.assertEqual(changed, [registered_entity.id])
        self.assertEqual({member.id for member in loaded_entities[0].members}, {"registered", "compensation"})

    def test_reconcile_entities_can_use_ai_to_merge_generic_case_thread(self) -> None:
        registered = build_record(
            record_id="registered",
            thread_id="thread-registered",
            subject="Acknowledgement: Registration of Complaint - N202526014012439",
            sender="RBI CMS Team <rbiteamcms@rbi.org.in>",
            body="RBI/CMS/N202526014012439/2025-26",
            received_at="2025-08-26T15:30:28+00:00",
        )
        compensation = build_record(
            record_id="compensation",
            thread_id="thread-compensation",
            subject="[Restricted] CEBO/RK/2250719966",
            sender="Nodal Officer INM <nodalofficerinm@hsbc.co.in>",
            body="This is in reference to RBI complaint N202526014012439 regarding your HSBC Live Credit Card application.",
            received_at="2025-09-16T03:58:07+00:00",
        )
        case_thread = build_record(
            record_id="case-thread",
            thread_id="thread-case",
            subject="[Case ID : 0006750673]",
            sender="crpc <crpc@rbi.org.in>",
            body="Please refer to your complaint regarding rejection of his HSBC Live Credit Card application without written communication about the status or reason for rejection.",
            received_at="2025-09-17T07:56:03+00:00",
        )

        registered_entity = self.seed_entity(registered)
        self.seed_entity(compensation)
        case_entity = self.seed_entity(case_thread)

        with patch(
            "app.services.entities.entity_reconciler.resolve_entity_group",
            return_value=EntityGroupingResponse(entity_id=registered_entity.id, confidence=0.96),
        ):
            changed = reconcile_entities(self.database_path)

        loaded_entities = list_all_loaded_entities(self.database_path)
        self.assertEqual(len(loaded_entities), 1)
        self.assertEqual(set(changed), {registered_entity.id})
        self.assertNotEqual(registered_entity.id, case_entity.id)
        self.assertEqual(
            {member.id for member in loaded_entities[0].members},
            {"registered", "compensation", "case-thread"},
        )

    def test_reconcile_entities_does_not_merge_unrelated_regulatory_thread(self) -> None:
        hsbc_chain = build_record(
            record_id="hsbc-chain",
            thread_id="thread-hsbc",
            subject="Closure of your complaint against HSBC credit card rejection",
            sender="RBI CMS Team <rbiteamcms@rbi.org.in>",
            body="This is regarding your HSBC Live Credit Card application complaint and compensation.",
            received_at="2025-10-14T05:18:48+00:00",
        )
        cibil_case = build_record(
            record_id="cibil-case",
            thread_id="thread-cibil",
            subject="[Case ID : 0006751571]",
            sender="crpc <crpc@rbi.org.in>",
            body="Please refer to your complaint about persistent MyCIBIL login failures and your comments on the CIBIL response.",
            received_at="2025-09-02T05:25:51+00:00",
        )

        self.seed_entity(hsbc_chain)
        self.seed_entity(cibil_case)

        with patch(
            "app.services.entities.entity_reconciler.resolve_entity_group",
            return_value=EntityGroupingResponse(entity_id="should-not-merge", confidence=0.99),
        ) as mock_grouping:
            changed = reconcile_entities(self.database_path)

        loaded_entities = list_all_loaded_entities(self.database_path)
        self.assertEqual(len(loaded_entities), 2)
        self.assertEqual(changed, [])
        mock_grouping.assert_not_called()

    def test_reconcile_entities_does_not_merge_stale_provider_history_without_explicit_reference(self) -> None:
        groww_closure = build_record(
            record_id="groww-closure",
            thread_id="thread-groww-closure",
            subject="Demat Account successfully closed",
            sender="Groww <noreply@groww.in>",
            body="Your request for closure of your Demat Account has been successfully processed.",
            received_at="2025-02-07T04:34:00+00:00",
        )
        groww_issue = build_record(
            record_id="groww-issue",
            thread_id="thread-groww-issue",
            subject="Re: screenshot 25597155",
            sender="Groww <support@groww.in>",
            body="We have taken up your concern on high priority with the CDSL and will provide you with an update at the earliest.",
            received_at="2026-04-17T07:22:10+00:00",
        )

        self.seed_entity(groww_closure)
        self.seed_entity(groww_issue)

        with patch(
            "app.services.entities.entity_reconciler.resolve_entity_group",
            return_value=EntityGroupingResponse(entity_id="should-not-merge", confidence=0.99),
        ) as mock_grouping:
            changed = reconcile_entities(self.database_path)

        loaded_entities = list_all_loaded_entities(self.database_path)
        self.assertEqual(len(loaded_entities), 2)
        self.assertEqual(changed, [])
        mock_grouping.assert_not_called()

    def test_reconcile_entities_merges_recent_same_provider_support_threads_with_same_subject_root(self) -> None:
        screenshot_seed = build_record(
            record_id="groww-screenshot-seed",
            thread_id="thread-groww-screenshot-seed",
            subject="screenshot",
            sender="Groww <support@groww.in>",
            body="With reference to your concern, we would like a screen shot of the issue to check it better.",
            received_at="2026-04-09T05:14:42+00:00",
        )
        screenshot_followup = build_record(
            record_id="groww-screenshot-followup",
            thread_id="thread-groww-screenshot-followup",
            subject="Re: screenshot 25597155",
            sender="Groww <support@groww.in>",
            body=(
                "We have taken up your concern on high priority with the CDSL and will provide you with an update at the earliest.\n"
                "On Thu, Apr 9, Groww wrote:\n"
                "With reference to your concern, we would like a screen shot of the issue to check it better."
            ),
            received_at="2026-04-12T07:39:56+00:00",
        )

        seed_entity = self.seed_entity(screenshot_seed)
        self.seed_entity(screenshot_followup)

        with patch(
            "app.services.entities.entity_reconciler.resolve_entity_group",
            return_value=EntityGroupingResponse(entity_id=seed_entity.id, confidence=0.84),
        ):
            changed = reconcile_entities(self.database_path)

        loaded_entities = list_all_loaded_entities(self.database_path)
        self.assertEqual(len(loaded_entities), 1)
        self.assertEqual(changed, [seed_entity.id])
        self.assertEqual(
            {member.id for member in loaded_entities[0].members},
            {"groww-screenshot-seed", "groww-screenshot-followup"},
        )


if __name__ == "__main__":
    unittest.main()
