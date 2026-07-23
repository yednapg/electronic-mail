from __future__ import annotations

from contextlib import nullcontext
import json
import os
import unittest
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import text

from app.db import repository
from app.db.mail_groups import (
    GmailMessageRecord,
    force_update_gmail_message_labels,
    list_messages_by_ids,
    upsert_gmail_messages,
)


def _message(
    *,
    user_id: str = "user-1",
    history_id: str = "200",
    labels: list[str] | None = None,
) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id=user_id,
        message_id="message-1",
        gmail_thread_id="thread-1",
        history_id=history_id,
        label_ids=list(labels or ["INBOX"]),
        internal_date="2026-07-23T00:00:00+00:00",
        subject="Subject",
        sender="sender@example.test",
        recipients={"to": "recipient@example.test"},
        headers={"subject": "Subject"},
        snippet="Snippet",
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals={},
        body_hash="metadata",
        created_at="",
        updated_at="",
    )


class GmailMessageMetadataOrderingUnitTests(unittest.TestCase):
    def test_provider_upsert_only_replaces_labels_at_a_strictly_newer_numeric_history(self) -> None:
        connection = _RecordingConnection()
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            upsert_gmail_messages("postgresql://example/db", [_message()])

        sql = connection.calls[0][0]
        self.assertEqual(sql.count("excluded.history_id::NUMERIC > gmail_messages.history_id::NUMERIC"), 2)
        self.assertIn("excluded.history_id !~ '^[0-9]+$'", sql)
        self.assertIn("ELSE gmail_messages.label_ids_json", sql)

    def test_forced_local_label_write_cannot_change_provider_history(self) -> None:
        connection = _RecordingConnection()
        with (
            patch("app.db.mail_groups.get_engine", return_value=object()),
            patch(
                "app.db.mail_groups.user_mail_write_transaction",
                return_value=nullcontext(connection),
            ),
        ):
            updated = force_update_gmail_message_labels(
                "postgresql://example/db",
                user_id="user-1",
                labels_by_message_id={"message-1": ["INBOX", "STARRED", "STARRED"]},
            )

        self.assertEqual(updated, 1)
        sql, params = connection.calls[0]
        self.assertIn("UPDATE gmail_messages", sql)
        self.assertNotIn("history_id", sql)
        self.assertEqual(json.loads(str(params["label_ids_json"])), ["INBOX", "STARRED"])


def _postgres_integration_enabled() -> bool:
    database_url = os.getenv("DATABASE_URL", "")
    parsed = urlparse(database_url)
    return os.getenv("APP_ENV") == "staging" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


@unittest.skipUnless(
    _postgres_integration_enabled(),
    "requires the migrated loopback Postgres used by staging CI",
)
class GmailMessageMetadataOrderingPostgresTests(unittest.TestCase):
    database_url = os.getenv("DATABASE_URL", "")

    def setUp(self) -> None:
        self.user_id = f"message-order-test-{uuid4()}"
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, google_sub, access_enabled, created_at, updated_at)
                    VALUES (:user_id, :email, :google_sub, TRUE, now(), now())
                    """
                ),
                {
                    "user_id": self.user_id,
                    "email": f"{self.user_id}@example.test",
                    "google_sub": f"sub-{self.user_id}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO google_oauth_tokens (user_id, token_json_encrypted, updated_at)
                    VALUES (:user_id, 'test-token', now())
                    """
                ),
                {"user_id": self.user_id},
            )

    def tearDown(self) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": self.user_id})

    def test_stale_provider_rows_cannot_overwrite_local_labels_or_regress_history(self) -> None:
        upsert_gmail_messages(
            self.database_url,
            [_message(user_id=self.user_id, history_id="200", labels=["INBOX", "STARRED"])],
        )
        force_update_gmail_message_labels(
            self.database_url,
            user_id=self.user_id,
            labels_by_message_id={"message-1": ["STARRED"]},
        )

        upsert_gmail_messages(
            self.database_url,
            [_message(user_id=self.user_id, history_id="200", labels=["INBOX", "STARRED"])],
        )
        upsert_gmail_messages(
            self.database_url,
            [_message(user_id=self.user_id, history_id="199", labels=["INBOX"])],
        )

        persisted = list_messages_by_ids(
            self.database_url,
            user_id=self.user_id,
            message_ids=["message-1"],
        )[0]
        self.assertEqual(persisted.history_id, "200")
        self.assertEqual(persisted.label_ids, ["STARRED"])

        upsert_gmail_messages(
            self.database_url,
            [_message(user_id=self.user_id, history_id="201", labels=["INBOX", "UNREAD"])],
        )
        persisted = list_messages_by_ids(
            self.database_url,
            user_id=self.user_id,
            message_ids=["message-1"],
        )[0]
        self.assertEqual(persisted.history_id, "201")
        self.assertEqual(persisted.label_ids, ["INBOX", "UNREAD"])


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return object()


if __name__ == "__main__":
    unittest.main()
