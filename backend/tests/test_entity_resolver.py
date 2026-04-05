from __future__ import annotations

"""Regression tests for cross-thread support email grouping."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import StoredSourceRecord
from app.db.repository import initialize_database, list_all_loaded_entities, upsert_source_records
from app.services.feed.memory_pipeline import hydrate_persistent_memory


class EntityResolverTest(unittest.TestCase):
    """Protect grouping for support lifecycles that shift subjects across follow-ups."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        initialize_database(str(self.database_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch.dict(os.environ, {}, clear=True)
    def test_vague_followup_groups_with_existing_support_request(self) -> None:
        upsert_source_records(
            str(self.database_path),
            [
                create_record(
                    record_id="northstar-1",
                    thread_id="thread-a",
                    subject="Request to increase credit limit and card upgrade assistance",
                    body="Please help with my credit limit increase and card upgrade assistance request.",
                    timestamp="2026-04-03T08:00:00+00:00",
                ),
                create_record(
                    record_id="northstar-2",
                    thread_id="thread-b",
                    subject="Acknowledgement of your credit limit increase request",
                    body="We have received your credit limit increase and card upgrade assistance request.",
                    timestamp="2026-04-03T08:10:00+00:00",
                ),
                create_record(
                    record_id="northstar-3",
                    thread_id="thread-c",
                    subject="Your service request has been registered",
                    body="Your query/concern for card upgrade assistance has been registered.",
                    timestamp="2026-04-03T08:20:00+00:00",
                ),
            ],
        )

        changed_ids = hydrate_persistent_memory(str(self.database_path), [])
        self.assertGreaterEqual(len(changed_ids), 1)

        loaded_entities = list_all_loaded_entities(str(self.database_path))

        self.assertEqual(len(loaded_entities), 1)
        self.assertEqual(len(loaded_entities[0].members), 3)


def create_record(
    *,
    record_id: str,
    thread_id: str,
    subject: str,
    body: str,
    timestamp: str,
) -> StoredSourceRecord:
    return StoredSourceRecord(
        id=record_id,
        source="gmail",
        thread_id=thread_id,
        subject=subject,
        sender="alerts@northstarbank.example",
        timestamp=timestamp,
        raw_payload={
            "subject": subject,
            "body": body,
            "from": "alerts@northstarbank.example",
        },
        created_at=timestamp,
    )


if __name__ == "__main__":
    unittest.main()
