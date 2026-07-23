from __future__ import annotations

from contextlib import nullcontext
import unittest
from unittest.mock import patch

from app.db.mail_groups import mark_import_completed


class GmailHistoryCursorPersistenceTests(unittest.TestCase):
    def test_completion_uses_numeric_monotonic_max_for_history_cursor(self) -> None:
        connection = _RecordingConnection()
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            mark_import_completed(
                "postgresql://example/db",
                user_id="user-1",
                last_history_id="90071992547409931234",
            )

        self.assertIn("excluded.last_history_id::NUMERIC", connection.sql)
        self.assertIn("gmail_import_state.last_history_id::NUMERIC", connection.sql)
        self.assertIn("ELSE gmail_import_state.last_history_id", connection.sql)
        self.assertEqual(connection.params["last_history_id"], "90071992547409931234")

    def test_completion_rejects_invalid_history_cursor(self) -> None:
        with self.assertRaises(ValueError):
            mark_import_completed(
                "postgresql://example/db",
                user_id="user-1",
                last_history_id="history-123",
            )


class _RecordingConnection:
    def __init__(self) -> None:
        self.sql = ""
        self.params: dict[str, object] = {}

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = dict(params or {})
        return object()


if __name__ == "__main__":
    unittest.main()
