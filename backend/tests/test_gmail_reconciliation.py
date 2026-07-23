from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import text

from app.db import repository
from app.db.mail_groups import (
    GmailMessageRecord,
    GmailReconcileFinalizeResult,
    MailSendIdempotencyConflict,
    finalize_gmail_reconciliation,
    list_messages_by_ids,
    record_gmail_reconciliation_page,
    start_gmail_reconciliation,
    upsert_gmail_messages,
    upsert_pending_send,
)
from app.services.gmail_importer import (
    _encode_full_mailbox_cursor,
    _finalize_gmail_full_reconciliation,
    run_gmail_full_reconciliation,
)


def _message(message_id: str = "msg-1") -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id="thread-1",
        history_id="101",
        label_ids=["INBOX"],
        internal_date="2026-07-23T00:00:00+00:00",
        subject="Subject",
        sender="sender@example.com",
        recipients={"to": "me@example.com"},
        headers={},
        snippet="Snippet",
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals={},
        body_hash="hash-1",
        created_at="",
        updated_at="",
    )


class GmailFullReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(database_path="postgresql://example/db")
        self.initial_cursor = _encode_full_mailbox_cursor()
        self.next_cursor = _encode_full_mailbox_cursor(page_token="next-page")
        self.state = SimpleNamespace(
            reconcile_generation="generation-1",
            reconcile_baseline_history_id="100",
            reconcile_cursor=self.initial_cursor,
        )

    @patch("app.services.gmail_importer.enqueue_gmail_full_reconciliation", return_value="job-2")
    @patch("app.services.gmail_importer.record_gmail_reconciliation_page", return_value=True)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_reconciliation_cursor_page")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_page_budget_checkpoints_and_queues_durable_successor(
        self,
        _can_write: Mock,
        get_state: Mock,
        hydrate_page: Mock,
        upsert: Mock,
        checkpoint: Mock,
        enqueue_successor: Mock,
    ) -> None:
        get_state.return_value = self.state
        hydrate_page.return_value = ([_message()], self.next_cursor)

        touched = run_gmail_full_reconciliation(
            self.settings,
            user_id="user-1",
            batch_size=250,
            max_pages=1,
        )

        self.assertEqual(touched, 1)
        upsert.assert_called_once()
        checkpoint.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            generation_id="generation-1",
            expected_cursor=self.initial_cursor,
            next_cursor=self.next_cursor,
            message_ids=["msg-1"],
        )
        enqueue_successor.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.services.gmail_importer._finalize_gmail_full_reconciliation", return_value=3)
    @patch("app.services.gmail_importer.record_gmail_reconciliation_page", return_value=True)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_reconciliation_cursor_page")
    @patch("app.services.gmail_importer.get_import_state")
    @patch("app.services.gmail_importer.user_can_write_gmail", return_value=True)
    def test_last_page_runs_final_delta_and_absent_row_sweep(
        self,
        _can_write: Mock,
        get_state: Mock,
        hydrate_page: Mock,
        _upsert: Mock,
        _checkpoint: Mock,
        finalize: Mock,
    ) -> None:
        get_state.return_value = self.state
        hydrate_page.return_value = ([_message()], None)

        touched = run_gmail_full_reconciliation(
            self.settings,
            user_id="user-1",
            max_pages=1,
        )

        self.assertEqual(touched, 4)
        finalize.assert_called_once_with(
            self.settings,
            user_id="user-1",
            generation_id="generation-1",
            baseline_history_id="100",
        )

    @patch("app.services.gmail_importer.emit_mailbox_event")
    @patch("app.services.gmail_importer._refresh_gmail_thread_order_best_effort")
    @patch("app.services.gmail_importer.prune_empty_mail_groups")
    @patch("app.services.gmail_importer.finalize_gmail_reconciliation")
    @patch("app.services.gmail_importer.record_gmail_reconciliation_seen", return_value=True)
    @patch("app.services.gmail_importer.upsert_gmail_messages")
    @patch("app.services.gmail_importer._hydrate_message_ids")
    @patch("app.services.gmail_importer.delete_gmail_messages", return_value=["group-delta"])
    @patch("app.services.gmail_importer._list_history_delta")
    def test_final_delta_protects_new_messages_then_prunes_authoritatively_absent_rows(
        self,
        list_delta: Mock,
        _delete_delta: Mock,
        hydrate: Mock,
        upsert: Mock,
        record_seen: Mock,
        finalize: Mock,
        prune: Mock,
        order: Mock,
        event: Mock,
    ) -> None:
        list_delta.return_value = {
            "message_ids": ["msg-new"],
            "deleted_message_ids": ["msg-deleted"],
            "latest_history_id": "105",
        }
        hydrate.return_value = ([_message("msg-new")], "105")
        finalize.return_value = GmailReconcileFinalizeResult(
            deleted_message_count=2,
            affected_group_ids=["group-sweep"],
        )

        touched = _finalize_gmail_full_reconciliation(
            self.settings,
            user_id="user-1",
            generation_id="generation-1",
            baseline_history_id="100",
        )

        self.assertEqual(touched, 4)
        upsert.assert_called_once()
        record_seen.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            generation_id="generation-1",
            message_ids=["msg-new"],
        )
        finalize.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            generation_id="generation-1",
            final_history_id="105",
        )
        prune.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            group_ids=["group-delta", "group-sweep"],
        )
        order.assert_called_once_with(
            self.settings,
            user_id="user-1",
            target_history_id="105",
        )
        event.assert_called_once()


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
class GmailReconciliationPostgresTests(unittest.TestCase):
    database_url = os.getenv("DATABASE_URL", "")

    def setUp(self) -> None:
        self.user_id = f"reconcile-test-{uuid4()}"
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                      id, email, google_sub, access_enabled, created_at, updated_at
                    ) VALUES (
                      :user_id, :email, :google_sub, TRUE, now(), now()
                    )
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
                    INSERT INTO google_oauth_tokens (
                      user_id, token_json_encrypted, updated_at
                    ) VALUES (:user_id, 'test-token', now())
                    """
                ),
                {"user_id": self.user_id},
            )

    def tearDown(self) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": self.user_id},
            )

    def test_authoritative_sweep_preserves_seen_and_newer_history_rows(self) -> None:
        upsert_gmail_messages(
            self.database_url,
            [
                self._message("keep", "90"),
                self._message("stale", "91"),
                self._message("newer-than-final-delta", "999"),
            ],
        )
        state = start_gmail_reconciliation(
            self.database_url,
            user_id=self.user_id,
            generation_id="generation-1",
            baseline_history_id="100",
            initial_cursor="page-1",
        )
        self.assertEqual(state.reconcile_generation, "generation-1")
        self.assertTrue(
            record_gmail_reconciliation_page(
                self.database_url,
                user_id=self.user_id,
                generation_id="generation-1",
                expected_cursor="page-1",
                next_cursor=None,
                message_ids=["keep"],
            )
        )

        result = finalize_gmail_reconciliation(
            self.database_url,
            user_id=self.user_id,
            generation_id="generation-1",
            final_history_id="105",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.deleted_message_count, 1)
        remaining = list_messages_by_ids(
            self.database_url,
            user_id=self.user_id,
            message_ids=["keep", "stale", "newer-than-final-delta"],
        )
        self.assertEqual(
            {message.message_id for message in remaining},
            {"keep", "newer-than-final-delta"},
        )
        with repository.get_engine(self.database_url).connect() as connection:
            persisted = connection.execute(
                text(
                    """
                    SELECT last_history_id, reconcile_generation
                    FROM gmail_import_state
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": self.user_id},
            ).mappings().one()
        self.assertEqual(str(persisted["last_history_id"]), "105")
        self.assertIsNone(persisted["reconcile_generation"])

    def test_pending_send_identity_accepts_exact_replay_and_rejects_mutation(self) -> None:
        kwargs = {
            "user_id": self.user_id,
            "client_send_id": "client-send-1",
            "send_type": "compose",
            "mailbox_thread_id": None,
            "gmail_thread_id": None,
            "to": ["recipient@example.test"],
            "cc": [],
            "bcc": [],
            "subject": "Original subject",
            "body_text": "Original body",
            "body_html": None,
            "headers": {},
            "attachments": [],
            "created_at": "2026-07-23T00:00:00+00:00",
        }
        first = upsert_pending_send(self.database_url, **kwargs)
        replay = upsert_pending_send(self.database_url, **kwargs)

        self.assertEqual(replay.server_send_id, first.server_send_id)
        self.assertTrue(first.request_hash)
        with self.assertRaises(MailSendIdempotencyConflict):
            upsert_pending_send(
                self.database_url,
                **{**kwargs, "subject": "Mutated subject"},
            )

    def _message(self, message_id: str, history_id: str) -> GmailMessageRecord:
        return GmailMessageRecord(
            **{
                **_message(message_id).__dict__,
                "user_id": self.user_id,
                "gmail_thread_id": message_id,
                "history_id": history_id,
            }
        )


if __name__ == "__main__":
    unittest.main()
