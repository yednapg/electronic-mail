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

        self.assertTrue(
            {
                "idx_source_records_timestamp",
                "idx_source_records_user_timestamp",
                "idx_source_records_thread",
                "idx_entities_created",
                "idx_entities_user_created",
                "idx_entity_members_entity",
                "idx_trace_records_entity_created",
                "idx_trace_records_source_record",
            }.issubset(indexes)
        )


if __name__ == "__main__":
    unittest.main()
