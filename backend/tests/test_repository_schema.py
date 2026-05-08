from __future__ import annotations

import sqlite3
import tempfile
import unittest

from app.db.repository import initialize_database


class RepositorySchemaTests(unittest.TestCase):
    def test_initialize_database_creates_mailbox_scale_indexes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as db_file:
            initialize_database(db_file.name)

            with sqlite3.connect(db_file.name) as connection:
                indexes = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                source_record_columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(source_records)").fetchall()
                }

        self.assertTrue(
            {
                "idx_source_records_timestamp",
                "idx_source_records_user_timestamp",
                "idx_source_records_user_active_timestamp",
                "idx_source_records_thread",
                "idx_source_record_summaries_user_generated",
                "idx_entities_created",
                "idx_entities_user_created",
                "idx_entity_members_entity",
                "idx_trace_records_entity_created",
                "idx_trace_records_source_record",
                "idx_feed_projections_user_updated",
            }.issubset(indexes)
        )
        self.assertIn("deleted_at", source_record_columns)


if __name__ == "__main__":
    unittest.main()
