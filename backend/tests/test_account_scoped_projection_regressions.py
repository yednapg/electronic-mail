from __future__ import annotations

from contextlib import nullcontext
import json
import unittest
from unittest.mock import ANY, Mock, patch

from app.db.account_scope import active_gmail_account_id
from app.db.mail_groups import (
    get_app_session_snapshot,
    list_mailbox_events_after,
    upsert_app_session_snapshot,
)


class _Result:
    def __init__(self, *, row=None, scalar=None, rows=None) -> None:
        self._row = row
        self._scalar = scalar
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict, str | None]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {}), active_gmail_account_id()))
        return self.results.pop(0)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self):
        return nullcontext(self.connection)


class AccountScopedProjectionRegressionTests(unittest.TestCase):
    def test_snapshot_returns_primary_account_identity(self) -> None:
        row = {
            "user_id": "user-1",
            "gmail_account_id": "account-1",
            "dashboard_json": json.dumps({"feed": {}}),
            "mailbox_json": json.dumps({"total_threads": 4}),
            "sync_json": json.dumps({"projection_version": "v1"}),
            "updated_at": "2026-08-31T00:00:00+00:00",
        }
        connection = _Connection(
            [_Result(scalar="account-1"), _Result(row=row)]
        )
        with patch("app.db.mail_groups.get_engine", return_value=_Engine(connection)):
            snapshot = get_app_session_snapshot("postgresql://example/db", user_id="user-1")

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.gmail_account_id, "account-1")
        self.assertEqual(connection.calls[1][1]["gmail_account_id"], "account-1")
        self.assertEqual(connection.calls[1][2], "account-1")

    def test_snapshot_upsert_includes_account_key_and_transaction_scope(self) -> None:
        connection = _Connection([_Result()])
        with (
            patch("app.db.mail_groups._primary_gmail_account_id", return_value="account-1"),
            patch("app.db.mail_groups.get_engine", return_value=Mock()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ) as transaction,
        ):
            upsert_app_session_snapshot(
                "postgresql://example/db",
                user_id="user-1",
                dashboard={},
                mailbox={},
                sync={},
            )

        transaction.assert_called_once_with(
            ANY,
            user_id="user-1",
            gmail_account_id="account-1",
        )
        statement, params, scope = connection.calls[0]
        self.assertIn("gmail_account_id", statement)
        self.assertEqual(params["gmail_account_id"], "account-1")
        self.assertEqual(scope, "account-1")

    def test_primary_event_query_omits_untyped_nullable_account_parameter(self) -> None:
        connection = _Connection([_Result(rows=[])])
        with patch("app.db.mail_groups.get_engine", return_value=_Engine(connection)):
            events = list_mailbox_events_after(
                "postgresql://example/db",
                user_id="user-1",
                after_id=9,
            )

        self.assertEqual(events, [])
        statement, params, scope = connection.calls[0]
        self.assertNotIn(":gmail_account_id IS NULL", statement)
        self.assertNotIn("gmail_account_id", params)
        self.assertIsNone(scope)

    def test_secondary_event_query_sets_database_account_scope(self) -> None:
        connection = _Connection([_Result(rows=[])])
        with patch("app.db.mail_groups.get_engine", return_value=_Engine(connection)):
            list_mailbox_events_after(
                "postgresql://example/db",
                user_id="user-1",
                gmail_account_id="account-2",
                after_id=9,
            )

        statement, params, scope = connection.calls[0]
        self.assertIn("gmail_account_id = :gmail_account_id", statement)
        self.assertEqual(params["gmail_account_id"], "account-2")
        self.assertEqual(scope, "account-2")


if __name__ == "__main__":
    unittest.main()
