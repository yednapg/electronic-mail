from __future__ import annotations

from contextlib import nullcontext
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.db.mail_groups import (
    finalize_gmail_reconciliation,
    gmail_history_cursor_is_authoritative,
    mark_history_delta_completed,
    mark_import_completed,
)


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
            mark_history_delta_completed(
                "postgresql://example/db",
                user_id="user-1",
                last_history_id="90071992547409931234",
            )

        self.assertIn("excluded.last_history_id::NUMERIC", connection.sql)
        self.assertIn("gmail_import_state.last_history_id::NUMERIC", connection.sql)
        self.assertIn("ELSE gmail_import_state.last_history_id", connection.sql)
        self.assertIn("gmail_import_state.history_cursor_authoritative IS NOT TRUE", connection.sql)
        self.assertIn("history_cursor_authoritative = TRUE", connection.sql)
        self.assertIn("last_delta_sync_at = now()", connection.sql)
        self.assertLess(
            connection.sql.index("gmail_import_state.history_cursor_authoritative IS NOT TRUE"),
            connection.sql.index("excluded.last_history_id::NUMERIC"),
        )
        self.assertEqual(connection.params["last_history_id"], "90071992547409931234")

    def test_completion_rejects_invalid_history_cursor(self) -> None:
        with self.assertRaises(ValueError):
            mark_history_delta_completed(
                "postgresql://example/db",
                user_id="user-1",
                last_history_id="history-123",
            )

    def test_generic_import_completion_cannot_publish_history_cursor(self) -> None:
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
                full_backfill_cursor="page-2",
            )

        self.assertNotIn("last_history_id", connection.sql)
        self.assertNotIn("last_delta_sync_at", connection.sql)
        self.assertNotIn("history_cursor_authoritative", connection.sql)

    def test_cursor_requires_explicit_durable_provenance(self) -> None:
        self.assertFalse(
            gmail_history_cursor_is_authoritative(
                SimpleNamespace(last_history_id="102")
            )
        )
        self.assertFalse(
            gmail_history_cursor_is_authoritative(
                SimpleNamespace(
                    last_history_id="not-numeric",
                    history_cursor_authoritative=True,
                )
            )
        )
        self.assertTrue(
            gmail_history_cursor_is_authoritative(
                SimpleNamespace(
                    last_history_id="101",
                    history_cursor_authoritative=True,
                )
            )
        )

    def test_provenance_migration_defaults_old_rows_to_untrusted_and_revokes_old_worker_writes(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "versions"
            / "20260724_0027_gmail_cursor_rebaseline.py"
        ).read_text(encoding="utf-8")

        self.assertIn("BOOLEAN NOT NULL DEFAULT FALSE", migration)
        self.assertIn("enforce_gmail_history_cursor_provenance", migration)
        self.assertIn(
            "NEW.last_history_id IS DISTINCT FROM OLD.last_history_id",
            migration,
        )
        self.assertIn(
            "NEW.last_delta_sync_at IS NOT DISTINCT FROM OLD.last_delta_sync_at",
            migration,
        )
        self.assertIn("NEW.history_cursor_authoritative := FALSE", migration)
        downgrade = migration.split("def downgrade() -> None:", maxsplit=1)[1]
        unsafe_cursor_clear = downgrade.index("UPDATE gmail_import_state")
        trigger_drop = downgrade.index(
            "DROP TRIGGER IF EXISTS trg_gmail_history_cursor_provenance"
        )
        provenance_column_drop = downgrade.index(
            "DROP COLUMN IF EXISTS history_cursor_authoritative"
        )
        self.assertLess(unsafe_cursor_clear, trigger_drop)
        self.assertLess(unsafe_cursor_clear, provenance_column_drop)
        self.assertIn("SET last_history_id = NULL", downgrade)
        self.assertIn("last_delta_sync_at = NULL", downgrade)
        self.assertIn(
            "WHERE history_cursor_authoritative IS NOT TRUE",
            downgrade,
        )

    def test_reconciliation_publishes_lower_trusted_cursor_over_higher_untrusted_value(self) -> None:
        source = inspect.getsource(finalize_gmail_reconciliation)

        untrusted_branch = source.index(
            "WHEN history_cursor_authoritative IS NOT TRUE"
        )
        monotonic_branch = source.index(
            "WHEN CAST(:final_history_id AS NUMERIC) >= last_history_id::NUMERIC"
        )
        self.assertLess(untrusted_branch, monotonic_branch)
        self.assertIn("last_delta_sync_at = now()", source)
        self.assertIn("history_cursor_authoritative = TRUE", source)


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
