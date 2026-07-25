from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.db.mail_groups import (
    GmailInitialWindowEntry,
    activate_existing_gmail_progressive_sync,
)
from app.services.gmail_importer import (
    _decode_full_thread_mailbox_cursor,
    _ensure_gmail_reconciliation_started,
)
from app.services.mail_groups import ensure_background_import_work


class _Result:
    def __init__(self, *, scalar=None, row=None) -> None:
        self.scalar = scalar
        self.row = row

    def scalar_one_or_none(self):
        return self.scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def one(self):
        return self.row


class _UpgradeConnection:
    def __init__(self, *, already_active: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.already_active = already_active

    def execute(self, statement, params=None):
        sql = str(statement)
        values = dict(params or {})
        self.calls.append((sql, values))
        if "SELECT *" in sql and "FOR UPDATE" in sql:
            return _Result(
                row={
                    "sync_generation": "existing-generation" if self.already_active else None,
                    "first_batch_imported_at": "2026-07-20T00:00:00+00:00",
                }
            )
        if "UPDATE gmail_import_state" in sql and "SET sync_generation" in sql:
            return _Result(scalar="user-1")
        if "AS initial_count" in sql:
            return _Result(
                row={
                    "initial_count": 100,
                    "initial_body_ready_count": 7,
                    "history_count": 358,
                    "history_body_ready_count": 40,
                    "all_bodies_ready": False,
                }
            )
        if "UPDATE gmail_import_state" in sql and "initial_target_count" in sql:
            return _Result(row={"user_id": "user-1"})
        return _Result()


@contextmanager
def _upgrade_transaction(connection):
    yield connection


def _legacy_state(*, completed: bool, active_reconciliation: bool = False):
    return SimpleNamespace(
        user_id="user-1",
        first_batch_imported_at="2026-07-20T00:00:00+00:00",
        first_groups_ready_at=None,
        full_backfill_cursor=(None if completed else "legacy-page-4"),
        full_backfill_started_at=(None if completed else "2026-07-20T00:01:00+00:00"),
        full_backfill_completed_at=("2026-07-20T01:00:00+00:00" if completed else None),
        last_history_id="1234",
        history_cursor_authoritative=completed,
        reconcile_generation=("reconcile-existing" if active_reconciliation else None),
        sync_generation=None,
    )


def _progressive_state(*, completed: bool, active_reconciliation: bool = False):
    return SimpleNamespace(
        **{
            **_legacy_state(
                completed=completed,
                active_reconciliation=active_reconciliation,
            ).__dict__,
            "sync_generation": (
                "reconcile-existing" if active_reconciliation else "upgrade-generation"
            ),
            "initial_target_count": 100,
            "initial_body_target_count": 25,
            "initial_body_ready_count": 0,
            "initial_window_complete": True,
            "history_metadata_complete": completed,
            "history_body_complete": False,
        }
    )


class ExistingAccountProgressiveUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")

    def test_activation_derives_progress_without_mutating_legacy_cursors(self) -> None:
        connection = _UpgradeConnection()
        projected = SimpleNamespace(sync_generation="upgrade-generation")
        with patch(
            "app.db.mail_groups.get_engine",
            return_value=object(),
        ), patch(
            "app.db.mail_groups.user_mail_write_transaction",
            side_effect=lambda *_args, **_kwargs: _upgrade_transaction(connection),
        ), patch(
            "app.db.mail_groups._state_from_row",
            return_value=projected,
        ):
            state = activate_existing_gmail_progressive_sync(
                "postgresql://example/db",
                user_id="user-1",
                generation_id="upgrade-generation",
                history_metadata_complete=True,
            )

        self.assertIs(state, projected)
        sql = "\n".join(statement for statement, _params in connection.calls)
        self.assertIn("ROW_NUMBER() OVER", sql)
        self.assertIn("ON CONFLICT (user_id, generation_id, gmail_thread_id) DO NOTHING", sql)
        self.assertIn("initial_window_complete = TRUE", sql)
        mutating_sql = "\n".join(
            statement
            for statement, _params in connection.calls
            if statement.lstrip().startswith(("UPDATE", "INSERT", "DELETE"))
        )
        self.assertNotIn("last_history_id =", mutating_sql)
        self.assertNotIn("full_backfill_cursor =", mutating_sql)
        self.assertNotIn("full_backfill_completed_at =", mutating_sql)
        self.assertNotIn("reconcile_cursor =", mutating_sql)
        final_params = next(
            params
            for statement, params in connection.calls
            if "UPDATE gmail_import_state" in statement and "initial_target_count" in statement
        )
        self.assertEqual(final_params["initial_count"], 100)
        self.assertEqual(final_params["history_count"], 358)
        self.assertTrue(final_params["history_metadata_complete"])
        self.assertFalse(final_params["history_body_complete"])

    def test_activation_is_idempotent_after_another_worker_wins(self) -> None:
        connection = _UpgradeConnection(already_active=True)
        projected = SimpleNamespace(sync_generation="existing-generation")
        with patch(
            "app.db.mail_groups.get_engine",
            return_value=object(),
        ), patch(
            "app.db.mail_groups.user_mail_write_transaction",
            side_effect=lambda *_args, **_kwargs: _upgrade_transaction(connection),
        ), patch(
            "app.db.mail_groups._state_from_row",
            return_value=projected,
        ):
            state = activate_existing_gmail_progressive_sync(
                "postgresql://example/db",
                user_id="user-1",
                generation_id="losing-generation",
                history_metadata_complete=True,
            )

        self.assertIs(state, projected)
        self.assertEqual(len(connection.calls), 1)

    def test_completed_account_seeds_priority_bodies_and_history_body_backfill(self) -> None:
        legacy = _legacy_state(completed=True)
        progressive = _progressive_state(completed=True)
        entries = [
            GmailInitialWindowEntry(
                user_id="user-1",
                generation_id="upgrade-generation",
                gmail_thread_id="thread-newest",
                position=0,
                message_count=1,
                metadata_ready_at="2026-07-20T00:00:00+00:00",
                body_ready_at=None,
            ),
            GmailInitialWindowEntry(
                user_id="user-1",
                generation_id="upgrade-generation",
                gmail_thread_id="thread-older",
                position=30,
                message_count=1,
                metadata_ready_at="2026-07-20T00:00:00+00:00",
                body_ready_at=None,
            ),
        ]
        enqueue = Mock()
        with patch(
            "app.services.mail_groups.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.mail_groups.get_import_state",
            return_value=legacy,
        ), patch(
            "app.services.mail_groups.activate_existing_gmail_progressive_sync",
            return_value=progressive,
        ) as activate, patch(
            "app.services.mail_groups.list_gmail_initial_window_entries_needing_body_fetch",
            return_value=entries,
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.enqueue_job",
            enqueue,
        ), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status",
            return_value={"pending": 0},
        ):
            ensure_background_import_work(self.settings, user_id="user-1")

        activate.assert_called_once()
        self.assertTrue(activate.call_args.kwargs["history_metadata_complete"])
        kinds = [call.kwargs["kind"] for call in enqueue.call_args_list]
        self.assertEqual(kinds.count("gmail_body_fetch"), 2)
        self.assertEqual(kinds.count("gmail_body_backfill"), 1)
        self.assertNotIn("gmail_full_reconcile", kinds)
        self.assertNotIn("gmail_backfill", kinds)
        priorities = {
            call.kwargs["payload"].get("gmail_thread_id"): call.kwargs["priority"]
            for call in enqueue.call_args_list
            if call.kwargs["kind"] == "gmail_body_fetch"
        }
        self.assertEqual(priorities, {"thread-newest": 90, "thread-older": 60})

    def test_active_legacy_account_keeps_backfill_and_starts_canonical_reconciliation(self) -> None:
        legacy = _legacy_state(completed=False, active_reconciliation=False)
        progressive = _progressive_state(completed=False, active_reconciliation=False)
        progressive.initial_body_target_count = 0
        enqueue = Mock()
        with patch(
            "app.services.mail_groups.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.mail_groups.get_import_state",
            return_value=legacy,
        ), patch(
            "app.services.mail_groups.activate_existing_gmail_progressive_sync",
            return_value=progressive,
        ) as activate, patch(
            "app.services.mail_groups.list_gmail_initial_window_entries_needing_body_fetch",
            return_value=[],
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            return_value=0,
        ), patch(
            "app.services.mail_groups.enqueue_job",
            enqueue,
        ), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status",
            return_value={"pending": 0},
        ):
            ensure_background_import_work(self.settings, user_id="user-1")

        self.assertFalse(activate.call_args.kwargs["history_metadata_complete"])
        kinds = [call.kwargs["kind"] for call in enqueue.call_args_list]
        self.assertEqual(kinds.count("gmail_full_reconcile"), 1)
        self.assertEqual(kinds.count("gmail_backfill"), 1)
        self.assertNotIn("gmail_body_backfill", kinds)

    def test_active_reconciliation_generation_is_adopted_without_restarting_it(self) -> None:
        legacy = _legacy_state(completed=False, active_reconciliation=True)
        progressive = _progressive_state(completed=False, active_reconciliation=True)
        progressive.initial_body_target_count = 0
        enqueue = Mock()

        def active_jobs(_database_url: str, **kwargs) -> int:
            return 1 if kwargs.get("kinds") == ["gmail_full_reconcile"] else 0

        with patch(
            "app.services.mail_groups.user_can_write_gmail",
            return_value=True,
        ), patch(
            "app.services.mail_groups.get_import_state",
            return_value=legacy,
        ), patch(
            "app.services.mail_groups.activate_existing_gmail_progressive_sync",
            return_value=progressive,
        ) as activate, patch(
            "app.services.mail_groups.list_gmail_initial_window_entries_needing_body_fetch",
            return_value=[],
        ), patch(
            "app.services.mail_groups.count_active_jobs",
            side_effect=active_jobs,
        ), patch(
            "app.services.mail_groups.enqueue_job",
            enqueue,
        ), patch(
            "app.services.mail_groups.count_mail_groups_by_enrichment_status",
            return_value={"pending": 0},
        ):
            ensure_background_import_work(self.settings, user_id="user-1")

        self.assertEqual(activate.call_args.kwargs["generation_id"], "reconcile-existing")
        kinds = [call.kwargs["kind"] for call in enqueue.call_args_list]
        self.assertNotIn("gmail_full_reconcile", kinds)
        self.assertEqual(kinds.count("gmail_backfill"), 1)

    def test_canonical_reconciliation_reuses_upgrade_generation_and_thread_pages(self) -> None:
        state = SimpleNamespace(
            sync_generation="upgrade-generation",
            history_metadata_complete=False,
            reconcile_generation=None,
        )
        started = SimpleNamespace(reconcile_generation="upgrade-generation")
        with patch(
            "app.services.gmail_importer.get_import_state",
            return_value=state,
        ), patch(
            "app.services.gmail_importer._current_gmail_history_id",
            return_value="5678",
        ), patch(
            "app.services.gmail_importer.start_gmail_reconciliation",
            return_value=started,
        ) as start:
            result = _ensure_gmail_reconciliation_started(
                self.settings,
                user_id="user-1",
            )

        self.assertIs(result, started)
        self.assertEqual(start.call_args.kwargs["generation_id"], "upgrade-generation")
        self.assertIsNotNone(
            _decode_full_thread_mailbox_cursor(start.call_args.kwargs["initial_cursor"])
        )


if __name__ == "__main__":
    unittest.main()
