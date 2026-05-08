from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.models import StoredSourceRecord
from app.db.repository import initialize_database, list_loaded_entities, upsert_source_records
from app.schemas.domain import SourceRecord
from app.services.feed.memory_pipeline import hydrate_persistent_memory


def build_source_record(record_id: str) -> SourceRecord:
    return SourceRecord(
        id=record_id,
        user_id="google-dev-user",
        source="gmail",
        thread_id=f"thread-{record_id}",
        raw_payload={
            "subject": "Please review this",
            "body": "Can you review this before launch?",
            "from": "Sender <sender@example.com>",
        },
        received_at="2026-04-17T10:00:00+00:00",
    )


def stored_record_from_source(record: SourceRecord) -> StoredSourceRecord:
    return StoredSourceRecord(
        id=record.id,
        source=record.source,
        thread_id=record.thread_id,
        subject=str(record.raw_payload["subject"]),
        sender=str(record.raw_payload["from"]),
        timestamp=record.received_at,
        raw_payload=record.raw_payload,
        created_at=record.received_at,
    )


class MemoryHydrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.tempdir.name) / "memory.sqlite3")
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_explicit_records_skip_global_repair_scans(self) -> None:
        record = build_source_record("message-1")
        upsert_source_records(self.database_path, [stored_record_from_source(record)])

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False),
            patch(
                "app.services.feed.memory_pipeline.list_unlinked_source_records",
                side_effect=AssertionError("normal import should not scan all unlinked records"),
            ) as mock_unlinked,
            patch(
                "app.services.feed.memory_pipeline.list_entities_missing_state_ids",
                side_effect=AssertionError("normal import should not scan all missing states"),
            ) as mock_missing_state,
            patch(
                "app.services.feed.memory_pipeline.reconcile_entities",
                side_effect=AssertionError("normal import should not run global reconciliation"),
            ) as mock_reconcile,
        ):
            changed_entity_ids = hydrate_persistent_memory(self.database_path, [record])

        mock_unlinked.assert_not_called()
        mock_missing_state.assert_not_called()
        mock_reconcile.assert_not_called()
        self.assertEqual(len(changed_entity_ids), 1)
        [loaded] = list_loaded_entities(self.database_path, changed_entity_ids)
        self.assertEqual([member.id for member in loaded.members], ["message-1"])
        self.assertIsNotNone(loaded.state)


if __name__ == "__main__":
    unittest.main()
