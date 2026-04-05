from __future__ import annotations

"""Integration-style tests for the persisted memory pipeline."""

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import StoredSourceRecord
from app.db.repository import (
    get_loaded_entity,
    initialize_database,
    list_trace_records_for_entity,
    upsert_ai_suggestion,
    upsert_source_records,
)
from app.services.feed.memory_pipeline import (
    build_feed_from_entities,
    hydrate_persistent_memory,
    refresh_ai_suggestions_for_entities,
)


class MemoryPipelineTest(unittest.TestCase):
    """Validate feed suppression and due-date safety against a temporary SQLite DB."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        initialize_database(str(self.database_path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolved_entity_stays_hidden(self) -> None:
        persist_records(
            self.database_path,
            [
                create_record(
                    record_id="resolved-1",
                    thread_id="thread-resolved",
                    sender="billing@example.com",
                    subject="Invoice paid",
                    body="Your invoice has been paid and completed.",
                    timestamp="2026-04-03T08:00:00+00:00",
                )
            ],
        )

        changed_ids = hydrate_persistent_memory(str(self.database_path), [])
        self.assertEqual(len(changed_ids), 1)

        refresh_ai_suggestions_for_entities(str(self.database_path), [])
        feed = build_feed_from_entities(str(self.database_path), "2026-04-05T10:00:00+00:00")
        self.assertEqual(len(feed.now) + len(feed.today) + len(feed.worth_knowing), 0)

    def test_no_deadline_phrase_does_not_fabricate_due_date(self) -> None:
        persist_records(
            self.database_path,
            [
                create_record(
                    record_id="deadline-1",
                    thread_id="thread-no-deadline",
                    sender="support@github.com",
                    subject="Your GitHub Pro discount ends April 6",
                    body="This is a product update with no deadline request.",
                    timestamp="2026-04-03T08:00:00+00:00",
                )
            ],
        )

        changed_ids = hydrate_persistent_memory(str(self.database_path), [])
        self.assertEqual(len(changed_ids), 1)
        loaded_entity = get_loaded_entity(str(self.database_path), changed_ids[0])
        self.assertIsNotNone(loaded_entity)
        self.assertIsNotNone(loaded_entity.state)
        self.assertIsNone(loaded_entity.state.due_at)

    def test_pipeline_persists_trace_records_for_replay(self) -> None:
        persist_records(
            self.database_path,
            [
                create_record(
                    record_id="reply-1",
                    thread_id="thread-reply",
                    sender="founder@example.com",
                    subject="Please reply about the contract",
                    body="Please reply before tomorrow.",
                    timestamp="2026-04-03T08:00:00+00:00",
                )
            ],
        )

        changed_ids = hydrate_persistent_memory(str(self.database_path), [])
        entity_id = changed_ids[0]
        refresh_ai_suggestions_for_entities(str(self.database_path), changed_ids)
        build_feed_from_entities(str(self.database_path), "2026-04-03T10:00:00+00:00")

        traces = list_trace_records_for_entity(str(self.database_path), entity_id)
        stages = {trace.stage for trace in traces}

        self.assertIn("grouping", stages)
        self.assertIn("state_derivation", stages)
        self.assertIn("action_selection", stages)
        self.assertIn("timing", stages)
        self.assertIn("ranking", stages)
        self.assertIn("output", stages)

    def test_hidden_ai_judgment_is_downgraded_into_worth_knowing(self) -> None:
        persist_records(
            self.database_path,
            [
                create_record(
                    record_id="hidden-1",
                    thread_id="thread-hidden",
                    sender="ops@example.com",
                    subject="Maintenance window notice",
                    body="Heads up: maintenance is scheduled for tomorrow morning.",
                    timestamp="2026-04-03T08:00:00+00:00",
                )
            ],
        )

        changed_ids = hydrate_persistent_memory(str(self.database_path), [])
        self.assertEqual(len(changed_ids), 1)

        entity_id = changed_ids[0]
        loaded_entity = get_loaded_entity(str(self.database_path), entity_id)
        self.assertIsNotNone(loaded_entity)
        self.assertIsNotNone(loaded_entity.state)

        upsert_ai_suggestion(
            str(self.database_path),
            entity_id=entity_id,
            title="Deployment completed",
            explanation="This is useful context, but it should stay off the main action surface.",
            action="none",
            suggested_timing="hidden",
            suggested_priority=10,
            suggested_visibility=False,
            model="test",
            generated_from_updated_at=loaded_entity.state.updated_at,
        )

        feed = build_feed_from_entities(str(self.database_path), "2026-04-05T10:00:00+00:00")

        self.assertEqual(len(feed.now), 0)
        self.assertEqual(len(feed.today), 0)
        self.assertEqual(len(feed.worth_knowing), 1)
        self.assertEqual(feed.worth_knowing[0].entity_id, entity_id)
        self.assertEqual(feed.worth_knowing[0].timing_band, "later")


def create_record(
    *,
    record_id: str,
    thread_id: str,
    sender: str,
    subject: str,
    body: str,
    timestamp: str,
) -> StoredSourceRecord:
    return StoredSourceRecord(
        id=record_id,
        source="gmail",
        thread_id=thread_id,
        subject=subject,
        sender=sender,
        timestamp=timestamp,
        raw_payload={
            "subject": subject,
            "body": body,
            "from": sender,
        },
        created_at=timestamp,
    )


def persist_records(database_path: Path, records: list[StoredSourceRecord]) -> None:
    upsert_source_records(str(database_path), records)


if __name__ == "__main__":
    unittest.main()
