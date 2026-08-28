from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from app.db import mail_groups


EXPECTED_MAIL_DATA_DELETE_ORDER = (
    "google_contact_avatar_cache",
    "ai_usage_events",
    "matter_decisions",
    "matter_members",
    "matter_subgoals",
    "matters",
    "attachment_text_extractions",
    "message_semantics",
    "matter_profiles",
    "matter_generations",
    "app_session_snapshots",
    "gmail_client_drafts",
    "gmail_pending_sends",
    "gmail_pending_thread_actions",
    "mailbox_events",
    "gmail_thread_order_entries",
    "gmail_thread_order_state",
    "gmail_reconcile_seen",
    "gmail_initial_window_entries",
    "entity_outcomes",
    "grouping_decision_audit",
    "visible_mail_group_members",
    "visible_mail_groups",
    "mail_group_members",
    "mail_groups",
    "gmail_messages",
    "gmail_import_state",
    "background_jobs",
)


class MailDataDeletionRepositoryTests(unittest.TestCase):
    def test_transaction_purges_every_mail_table_for_only_requested_user(self) -> None:
        self.assertEqual(mail_groups.USER_MAIL_DATA_DELETE_ORDER, EXPECTED_MAIL_DATA_DELETE_ORDER)
        connection = StatefulDeletionConnection()
        engine = StatefulDeletionEngine(connection)

        with patch.object(mail_groups, "get_engine", return_value=engine):
            mail_groups.delete_user_mail_data("postgresql://example/db", user_id="user-1")

        self.assertEqual(engine.begin_calls, 1)
        for table in EXPECTED_MAIL_DATA_DELETE_ORDER:
            if table == "entity_outcomes":
                continue
            self.assertFalse(
                any(row.get("user_id") == "user-1" for row in connection.tables[table]),
                f"{table} retained user-1 mail data",
            )
            self.assertTrue(
                any(row.get("user_id") == "user-2" for row in connection.tables[table]),
                f"{table} removed user-2 data",
            )

        self.assertEqual(
            [row["entity_id"] for row in connection.tables["entity_outcomes"] if row["user_id"] == "user-1"],
            ["manual-task:user-1"],
        )
        self.assertEqual(
            {row["entity_id"] for row in connection.tables["entity_outcomes"] if row["user_id"] == "user-2"},
            {"gmail-thread-user-2", "manual-task:user-2"},
        )
        self.assertNotIn("job-user-1", {row["job_id"] for row in connection.tables["background_job_events"]})
        self.assertIn("job-user-2", {row["job_id"] for row in connection.tables["background_job_events"]})

        self.assertEqual({row["id"] for row in connection.tables["users"]}, {"user-1", "user-2"})
        target_user = next(row for row in connection.tables["users"] if row["id"] == "user-1")
        self.assertEqual(target_user["google_data_delete_requested_at"], "now")
        self.assertEqual(target_user["google_data_deleted_at"], "now")
        for preserved_table in ("app_sessions", "google_oauth_tokens", "manual_tasks"):
            self.assertTrue(
                any(row.get("user_id") == "user-1" for row in connection.tables[preserved_table]),
                f"{preserved_table} should survive Gmail-data deletion",
            )


class StatefulDeletionConnection:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, str | None]]] = {
            table: [
                {"id": f"{table}-user-1", "user_id": "user-1"},
                {"id": f"{table}-user-2", "user_id": "user-2"},
            ]
            for table in EXPECTED_MAIL_DATA_DELETE_ORDER
            if table != "entity_outcomes"
        }
        self.tables["users"] = [
            {
                "id": "user-1",
                "google_data_delete_requested_at": None,
                "google_data_deleted_at": None,
            },
            {
                "id": "user-2",
                "google_data_delete_requested_at": None,
                "google_data_deleted_at": None,
            },
        ]
        self.tables["app_sessions"] = [
            {"id": "session-user-1", "user_id": "user-1"},
            {"id": "session-user-2", "user_id": "user-2"},
        ]
        self.tables["google_oauth_tokens"] = [
            {"id": "token-user-1", "user_id": "user-1"},
            {"id": "token-user-2", "user_id": "user-2"},
        ]
        self.tables["manual_tasks"] = [
            {"id": "task-user-1", "user_id": "user-1", "entity_id": "manual-task:user-1"},
            {"id": "task-user-2", "user_id": "user-2", "entity_id": "manual-task:user-2"},
        ]
        self.tables["entity_outcomes"] = [
            {"id": "mail-outcome-user-1", "user_id": "user-1", "entity_id": "gmail-thread-user-1"},
            {"id": "manual-outcome-user-1", "user_id": "user-1", "entity_id": "manual-task:user-1"},
            {"id": "mail-outcome-user-2", "user_id": "user-2", "entity_id": "gmail-thread-user-2"},
            {"id": "manual-outcome-user-2", "user_id": "user-2", "entity_id": "manual-task:user-2"},
        ]
        self.tables["background_jobs"] = [
            {"id": "job-user-1", "user_id": "user-1"},
            {"id": "job-user-2", "user_id": "user-2"},
        ]
        self.tables["background_job_events"] = [
            {"id": "event-user-1", "job_id": "job-user-1"},
            {"id": "event-user-2", "job_id": "job-user-2"},
        ]

    def __enter__(self) -> StatefulDeletionConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, params: dict[str, str] | None = None) -> object:
        sql = " ".join(str(statement).split())
        user_id = str((params or {})["user_id"])
        if sql.startswith("UPDATE users SET google_data_delete_requested_at"):
            self._user(user_id)["google_data_delete_requested_at"] = "now"
            return object()
        if sql.startswith("UPDATE users SET google_data_deleted_at"):
            self._user(user_id)["google_data_deleted_at"] = "now"
            return object()
        if sql.startswith("DELETE FROM entity_outcomes AS outcome"):
            manual_entity_ids = {
                str(row["entity_id"])
                for row in self.tables["manual_tasks"]
                if row["user_id"] == user_id
            }
            self.tables["entity_outcomes"] = [
                row
                for row in self.tables["entity_outcomes"]
                if row["user_id"] != user_id or row["entity_id"] in manual_entity_ids
            ]
            return object()
        match = re.match(r"DELETE FROM ([a-z_]+) WHERE user_id = :user_id", sql)
        if match is None:
            raise AssertionError(f"Unexpected deletion statement: {sql}")
        table = match.group(1)
        deleted_job_ids = {
            str(row["id"])
            for row in self.tables[table]
            if table == "background_jobs" and row["user_id"] == user_id
        }
        self.tables[table] = [row for row in self.tables[table] if row["user_id"] != user_id]
        if deleted_job_ids:
            self.tables["background_job_events"] = [
                row for row in self.tables["background_job_events"] if row["job_id"] not in deleted_job_ids
            ]
        return object()

    def _user(self, user_id: str) -> dict[str, str | None]:
        return next(row for row in self.tables["users"] if row["id"] == user_id)


class StatefulDeletionEngine:
    def __init__(self, connection: StatefulDeletionConnection) -> None:
        self.connection = connection
        self.begin_calls = 0

    def begin(self) -> StatefulDeletionConnection:
        self.begin_calls += 1
        return self.connection


if __name__ == "__main__":
    unittest.main()
