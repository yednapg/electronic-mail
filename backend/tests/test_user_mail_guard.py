from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, TimeoutError as SQLAlchemyTimeoutError

from app.db import mail_groups, repository
from app.db.jobs import cancel_user_jobs, complete_job, enqueue_job, fail_job, get_job
from app.db.user_mail_guard import (
    AdvisoryLockUnavailable,
    UserMailWorkBlocked,
    exclusive_google_subject_lock,
    exclusive_user_mail_lock,
    gmail_draft_lock_key,
    google_subject_lock_key,
    google_subject_tombstone_hash,
    guard_user_mail_write,
    shared_user_mail_lock,
    user_mail_lock_key,
    user_mail_provider_lock_key,
    user_mail_write_transaction,
)
from app.services.integrations import google as google_service
from app.services.integrations.google import persist_token_payload
from app.workers import gmail_poller


class UserMailGuardUnitTests(unittest.TestCase):
    def test_write_guard_locks_before_checking_durable_state(self) -> None:
        connection = _GuardConnection(allowed=True)

        guard_user_mail_write(connection, user_id="user-1")

        self.assertEqual(len(connection.calls), 4)
        self.assertIn("lock_timeout", connection.calls[0][0])
        self.assertEqual(
            connection.calls[0][1]["lock_timeout"],
            f"{repository.ADVISORY_LOCK_WAIT_TIMEOUT_SECONDS}s",
        )
        self.assertIn("pg_advisory_xact_lock_shared", connection.calls[1][0])
        self.assertIn("google_data_delete_requested_at IS NULL", connection.calls[2][0])
        self.assertIn("electronic_mail.user_mail_write_user_id", connection.calls[3][0])
        self.assertEqual(connection.calls[1][1]["lock_key"], user_mail_lock_key("user-1"))

    def test_write_guard_rejects_disconnected_or_deleted_user(self) -> None:
        connection = _GuardConnection(allowed=False)

        with self.assertRaises(UserMailWorkBlocked):
            guard_user_mail_write(connection, user_id="user-1")

    def test_transaction_lock_timeout_raises_safe_error_before_guard_check(self) -> None:
        connection = _FailingTransactionGuardConnection(allowed=True)

        with self.assertRaisesRegex(
            AdvisoryLockUnavailable,
            r"^Mail operation is temporarily busy\. Please retry shortly\.$",
        ) as raised:
            guard_user_mail_write(connection, user_id="user-1")

        self.assertNotIn("driver-sensitive-detail", str(raised.exception))
        self.assertEqual(len(connection.calls), 2)
        self.assertIn("lock_timeout", connection.calls[0][0])
        self.assertIn("pg_advisory_xact_lock_shared", connection.calls[1][0])
        self.assertFalse(any("SELECT EXISTS" in statement for statement, _params in connection.calls))

    def test_provider_lock_is_session_scoped_checks_after_lock_and_unlocks(self) -> None:
        connection = _SessionGuardConnection(allowed=True)
        engine = SimpleNamespace(connect=lambda: connection)

        with patch(
            "app.db.user_mail_guard.get_advisory_lock_engine",
            return_value=engine,
        ), patch("app.db.user_mail_guard.get_engine") as main_pool_engine:
            with shared_user_mail_lock("postgresql://example/db", user_id="user-1"):
                main_pool_engine.assert_not_called()
                self.assertEqual(len(connection.calls), 3)
                self.assertIn("lock_timeout", connection.calls[0][0])
                self.assertIn("pg_advisory_lock_shared", connection.calls[1][0])
                self.assertEqual(
                    connection.calls[1][1]["lock_key"],
                    user_mail_provider_lock_key("user-1"),
                )
                self.assertIn("SELECT EXISTS", connection.calls[2][0])

        self.assertEqual(len(connection.calls), 4)
        self.assertIn("pg_advisory_unlock_shared", connection.calls[3][0])
        self.assertEqual(connection.commits, 3)
        self.assertEqual(connection.rollbacks, 1)

    def test_provider_lock_fails_closed_and_unlocks_when_durable_guard_is_closed(self) -> None:
        connection = _SessionGuardConnection(allowed=False)
        engine = SimpleNamespace(connect=lambda: connection)
        provider_called = False

        with patch(
            "app.db.user_mail_guard.get_advisory_lock_engine",
            return_value=engine,
        ):
            with self.assertRaises(UserMailWorkBlocked):
                with shared_user_mail_lock("postgresql://example/db", user_id="user-1"):
                    provider_called = True

        self.assertFalse(provider_called)
        self.assertIn("pg_advisory_unlock_shared", connection.calls[-1][0])
        self.assertEqual(connection.commits, 2)
        self.assertEqual(connection.rollbacks, 1)

    def test_session_lock_timeout_releases_prior_lock_and_raises_safe_error(self) -> None:
        connection = _FailingSessionGuardConnection(allowed=True, fail_lock_number=2)
        engine = SimpleNamespace(connect=lambda: connection)

        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with self.assertRaisesRegex(
                AdvisoryLockUnavailable,
                r"^Mail operation is temporarily busy\. Please retry shortly\.$",
            ) as raised:
                with exclusive_user_mail_lock("postgresql://example/db", user_id="user-1"):
                    self.fail("lock timeout must fail before entering the protected scope")

        self.assertNotIn("driver-sensitive-detail", str(raised.exception))
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.invalidations, 0)
        unlocked_keys = [
            int(params["lock_key"])
            for statement, params in connection.calls
            if "pg_advisory_unlock" in statement
        ]
        self.assertEqual(unlocked_keys, [user_mail_provider_lock_key("user-1")])

    def test_pool_checkout_timeout_raises_safe_error(self) -> None:
        engine = SimpleNamespace(
            connect=lambda: (_ for _ in ()).throw(
                SQLAlchemyTimeoutError("pool-sensitive-detail")
            )
        )

        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with self.assertRaisesRegex(
                AdvisoryLockUnavailable,
                r"^Mail operation is temporarily busy\. Please retry shortly\.$",
            ) as raised:
                with shared_user_mail_lock("postgresql://example/db", user_id="user-1"):
                    self.fail("pool timeout must fail before entering the protected scope")

        self.assertNotIn("pool-sensitive-detail", str(raised.exception))

    def test_unlock_failure_invalidates_and_closes_session_instead_of_pooling_it(self) -> None:
        connection = _FailingSessionGuardConnection(allowed=True, fail_unlock=True)
        engine = SimpleNamespace(connect=lambda: connection)

        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with self.assertRaisesRegex(
                AdvisoryLockUnavailable,
                r"^Mail operation is temporarily busy\. Please retry shortly\.$",
            ) as raised:
                with exclusive_google_subject_lock(
                    "postgresql://example/db",
                    subject_hash=google_subject_tombstone_hash("google-principal-123"),
                ):
                    pass

        self.assertNotIn("cleanup-sensitive-detail", str(raised.exception))
        self.assertEqual(connection.invalidations, 1)
        self.assertEqual(connection.closes, 1)
        self.assertTrue(connection.closed)

    def test_lock_key_is_stable_signed_bigint(self) -> None:
        lock_key = user_mail_lock_key("user-1")
        provider_lock_key = user_mail_provider_lock_key("user-1")

        self.assertEqual(lock_key, user_mail_lock_key("user-1"))
        self.assertNotEqual(lock_key, user_mail_lock_key("user-2"))
        self.assertEqual(provider_lock_key, user_mail_provider_lock_key("user-1"))
        self.assertNotEqual(provider_lock_key, user_mail_provider_lock_key("user-2"))
        self.assertNotEqual(provider_lock_key, lock_key)
        self.assertGreaterEqual(lock_key, -(2**63))
        self.assertLess(lock_key, 2**63)

    def test_destructive_lock_uses_provider_then_transaction_namespace(self) -> None:
        events: list[tuple[str, int]] = []

        @contextmanager
        def recording_scope(_database_url: str, *, locks: list[tuple[str, int]]):
            for _mode, lock_key in locks:
                events.append(("enter", lock_key))
            try:
                yield SimpleNamespace()
            finally:
                for _mode, lock_key in reversed(locks):
                    events.append(("exit", lock_key))

        with patch(
            "app.db.user_mail_guard._advisory_lock_scope",
            side_effect=recording_scope,
        ):
            with exclusive_user_mail_lock("postgresql://example/db", user_id="user-1"):
                events.append(("body", 0))

        self.assertEqual(
            events,
            [
                ("enter", user_mail_provider_lock_key("user-1")),
                ("enter", user_mail_lock_key("user-1")),
                ("body", 0),
                ("exit", user_mail_lock_key("user-1")),
                ("exit", user_mail_provider_lock_key("user-1")),
            ],
        )

    def test_nested_subject_and_user_locks_share_one_bounded_pool_session(self) -> None:
        connection = _SessionGuardConnection(allowed=True)
        connect_calls = 0

        def connect():
            nonlocal connect_calls
            connect_calls += 1
            return connection

        engine = SimpleNamespace(connect=connect)
        subject_hash = google_subject_tombstone_hash("google-principal-123")
        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with exclusive_google_subject_lock("postgresql://example/db", subject_hash=subject_hash):
                with exclusive_user_mail_lock("postgresql://example/db", user_id="user-1"):
                    pass

        acquired_keys = [
            int(params["lock_key"])
            for statement, params in connection.calls
            if "pg_advisory_lock(" in statement and "unlock" not in statement
        ]
        self.assertEqual(connect_calls, 1)
        self.assertEqual(
            acquired_keys,
            [
                google_subject_lock_key(subject_hash),
                user_mail_provider_lock_key("user-1"),
                user_mail_lock_key("user-1"),
            ],
        )

    def test_nested_provider_and_draft_locks_share_one_bounded_pool_session(self) -> None:
        connection = _SessionGuardConnection(allowed=True)
        connect_calls = 0

        def connect():
            nonlocal connect_calls
            connect_calls += 1
            return connection

        engine = SimpleNamespace(connect=connect)
        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with shared_user_mail_lock("postgresql://example/db", user_id="user-1"):
                with mail_groups.client_draft_lock(
                    "postgresql://example/db",
                    user_id="user-1",
                    client_draft_id="draft-1",
                    gmail_draft_id="gmail-draft-1",
                ):
                    pass

        acquired_keys = [
            int(params["lock_key"])
            for statement, params in connection.calls
            if "pg_advisory_lock" in statement and "unlock" not in statement
        ]
        self.assertEqual(connect_calls, 1)
        self.assertEqual(
            acquired_keys,
            [
                user_mail_provider_lock_key("user-1"),
                gmail_draft_lock_key("user-1", "gmail-draft-1"),
                mail_groups.client_draft_lock_key("user-1", "draft-1"),
            ],
        )

    def test_new_draft_handoff_reacquires_provider_before_client_on_same_session(self) -> None:
        connection = _SessionGuardConnection(allowed=True)
        connect_calls = 0

        def connect():
            nonlocal connect_calls
            connect_calls += 1
            return connection

        engine = SimpleNamespace(connect=connect)
        with patch("app.db.user_mail_guard.get_advisory_lock_engine", return_value=engine):
            with shared_user_mail_lock("postgresql://example/db", user_id="user-1"):
                with mail_groups.client_draft_lock(
                    "postgresql://example/db",
                    user_id="user-1",
                    client_draft_id="client-draft-1",
                ) as draft_lock:
                    draft_lock.adopt_gmail_draft_id("gmail-draft-1")

        acquired_keys = [
            int(params["lock_key"])
            for statement, params in connection.calls
            if "pg_advisory_lock" in statement and "unlock" not in statement
        ]
        self.assertEqual(connect_calls, 1)
        self.assertEqual(
            acquired_keys,
            [
                user_mail_provider_lock_key("user-1"),
                mail_groups.client_draft_lock_key("user-1", "client-draft-1"),
                gmail_draft_lock_key("user-1", "gmail-draft-1"),
                mail_groups.client_draft_lock_key("user-1", "client-draft-1"),
            ],
        )

    def test_google_subject_tombstone_hash_is_stable_and_does_not_reveal_subject(self) -> None:
        google_sub = "google-principal-123"
        subject_hash = google_subject_tombstone_hash(google_sub)

        self.assertEqual(
            subject_hash,
            google_subject_tombstone_hash(google_sub),
        )
        self.assertNotEqual(
            subject_hash,
            google_subject_tombstone_hash("another-principal"),
        )
        self.assertNotIn(google_sub, subject_hash)
        self.assertTrue(subject_hash.startswith("sha256:v1:"))
        self.assertEqual(google_subject_lock_key(subject_hash), google_subject_lock_key(subject_hash))

    def test_poller_ignores_stale_connected_user_after_guard_closes(self) -> None:
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        with (
            patch.object(gmail_poller, "renew_heartbeat"),
            patch.object(gmail_poller, "list_connected_gmail_user_ids", return_value=["user-1"]),
            patch.object(
                gmail_poller,
                "check_user_google_credentials",
                return_value=SimpleNamespace(connected=True),
            ),
            patch.object(gmail_poller, "get_import_state", return_value=None),
            patch.object(
                gmail_poller,
                "enqueue_job",
                side_effect=UserMailWorkBlocked("disconnected"),
            ),
        ):
            queued = gmail_poller.poll_once(settings)

        self.assertEqual(queued, 0)

    def test_poller_does_not_enqueue_jobs_while_google_reauthentication_is_required(self) -> None:
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        gmail_poller._GOOGLE_REAUTH_RECHECK_AT.clear()
        with (
            patch.object(gmail_poller, "renew_heartbeat"),
            patch.object(gmail_poller, "list_connected_gmail_user_ids", return_value=["user-1"]),
            patch.object(
                gmail_poller,
                "check_user_google_credentials",
                return_value=SimpleNamespace(
                    connected=False,
                    reauth_required=True,
                    has_stored_tokens=True,
                ),
            ),
            patch.object(gmail_poller, "get_import_state") as get_state,
            patch.object(gmail_poller, "enqueue_job") as enqueue,
        ):
            queued = gmail_poller.poll_once(settings)

        self.assertEqual(queued, 0)
        get_state.assert_not_called()
        enqueue.assert_not_called()
        self.assertIn("user-1", gmail_poller._GOOGLE_REAUTH_RECHECK_AT)


class GmailRecoveryPollerTests(unittest.TestCase):
    def _state(self, *, completed_seconds_ago: int, watch_error: str | None = None):
        return SimpleNamespace(
            last_delta_sync_at=(
                datetime.now(timezone.utc) - timedelta(seconds=completed_seconds_ago)
            ).isoformat(),
            gmail_watch_error=watch_error,
            gmail_watch_expiration_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )

    def test_healthy_pubsub_still_gets_bounded_recovery_delta(self) -> None:
        settings = SimpleNamespace(gmail_pubsub_topic="projects/example/topics/gmail")

        self.assertFalse(
            gmail_poller._should_poll(settings, self._state(completed_seconds_ago=4 * 60))
        )
        self.assertTrue(
            gmail_poller._should_poll(settings, self._state(completed_seconds_ago=6 * 60))
        )

    def test_broken_watch_uses_fast_polling_fallback(self) -> None:
        settings = SimpleNamespace(gmail_pubsub_topic="projects/example/topics/gmail")

        self.assertFalse(
            gmail_poller._should_poll(
                settings,
                self._state(completed_seconds_ago=10, watch_error="watch failed"),
            )
        )
        self.assertTrue(
            gmail_poller._should_poll(
                settings,
                self._state(completed_seconds_ago=40, watch_error="watch failed"),
            )
        )

    def test_recent_backfill_completion_does_not_suppress_delta_recovery(self) -> None:
        settings = SimpleNamespace(gmail_pubsub_topic="")
        state = SimpleNamespace(
            last_import_completed_at=datetime.now(timezone.utc).isoformat(),
            last_delta_sync_at=None,
            gmail_watch_error=None,
            gmail_watch_expiration_at=None,
        )

        self.assertTrue(gmail_poller._should_poll(settings, state))

    def test_poller_does_not_run_delta_from_untrusted_legacy_cursor(self) -> None:
        settings = SimpleNamespace(
            database_path="postgresql://example/db",
            release_sha="release-1",
        )
        state = SimpleNamespace(
            last_history_id="102",
            history_cursor_authoritative=False,
            reconcile_generation=None,
        )
        with (
            patch.object(gmail_poller, "renew_heartbeat"),
            patch.object(
                gmail_poller,
                "list_connected_gmail_user_ids",
                return_value=["user-1"],
            ),
            patch.object(gmail_poller, "_credentials_available", return_value=True),
            patch.object(gmail_poller, "get_import_state", return_value=state),
            patch.object(gmail_poller, "enqueue_job") as enqueue,
        ):
            queued = gmail_poller.poll_once(settings)

        self.assertEqual(queued, 1)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["kind"], "gmail_import_batch")
        self.assertFalse(enqueue.call_args.kwargs["payload"]["first_run"])

    def test_retention_cleanup_is_a_global_deduplicated_slow_job(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        with patch.object(gmail_poller, "enqueue_job") as enqueue:
            gmail_poller._enqueue_retention_cleanup(settings)

        enqueue.assert_called_once_with(
            "postgresql://example/db",
            kind="job_retention_cleanup",
            queue="slow",
            dedupe_key="job-retention-cleanup",
            priority=-10,
            payload={},
        )


def _postgres_integration_enabled() -> bool:
    database_url = os.getenv("DATABASE_URL", "")
    parsed = urlparse(database_url)
    return os.getenv("APP_ENV") == "staging" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


@unittest.skipUnless(
    _postgres_integration_enabled(),
    "requires the migrated loopback Postgres used by staging CI",
)
class UserMailGuardPostgresTests(unittest.TestCase):
    database_url = os.getenv("DATABASE_URL", "")

    def test_oauth_reconnect_rolls_back_guard_clear_when_token_write_fails(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)

        with self.assertRaises(IntegrityError):
            repository.reconnect_google_oauth_token(
                self.database_url,
                user_id=user_id,
                token_json_encrypted=None,  # type: ignore[arg-type]
                oauth_started_epoch=2**62,
            )

        with repository.get_engine(self.database_url).connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT users.google_disconnected_at, tokens.token_json_encrypted
                    FROM users
                    JOIN google_oauth_tokens AS tokens ON tokens.user_id = users.id
                    WHERE users.id = :user_id
                    """
                ),
                {"user_id": user_id},
            ).mappings().one()
        self.assertIsNotNone(row["google_disconnected_at"])
        self.assertEqual(row["token_json_encrypted"], "test-token")

    def test_fetched_attachment_only_message_replaces_metadata_payload(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        message_id = f"attachment-only-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)

        base = mail_groups.GmailMessageRecord(
            user_id=user_id,
            message_id=message_id,
            gmail_thread_id=message_id,
            history_id="1",
            label_ids=["INBOX"],
            internal_date=None,
            subject="Attachment only",
            sender="sender@example.test",
            recipients={},
            headers={},
            snippet="Attached statement",
            raw_payload={"payload": {"headers": []}},
            html_body_sanitized=None,
            html_render_document=None,
            text_body=None,
            extracted_signals={},
            body_hash="metadata",
            body_fetch_status="missing",
            created_at="",
            updated_at="",
        )
        mail_groups.upsert_gmail_messages(self.database_url, [base])
        mail_groups.upsert_gmail_messages(
            self.database_url,
            [
                replace(
                    base,
                    raw_payload={
                        "payload": {
                            "mimeType": "multipart/mixed",
                            "parts": [
                                {
                                    "mimeType": "application/pdf",
                                    "filename": "statement.pdf",
                                    "body": {
                                        "attachmentId": "attachment-1",
                                        "size": 1024,
                                    },
                                }
                            ],
                        }
                    },
                    body_hash="full",
                    body_fetch_status="fetched",
                )
            ],
        )

        messages = mail_groups.list_messages_for_gmail_thread(
            self.database_url,
            user_id=user_id,
            gmail_thread_id=message_id,
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_fetch_status, "fetched")
        parts = messages[0].raw_payload["payload"]["parts"]
        self.assertEqual(parts[0]["body"]["attachmentId"], "attachment-1")

    def test_body_fetch_bookkeeping_does_not_advance_mailbox_revision(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        message_id = f"body-state-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        self._insert_message(user_id, message_id)
        revision_before = mail_groups.latest_gmail_mailbox_revision(
            self.database_url,
            user_id=user_id,
        )

        mail_groups.mark_gmail_messages_body_fetch_state(
            self.database_url,
            user_id=user_id,
            message_ids=[message_id],
            status="pending",
        )
        mail_groups.mark_gmail_messages_body_fetch_state(
            self.database_url,
            user_id=user_id,
            message_ids=[message_id],
            status="failed",
            error="temporary attachment failure",
        )

        self.assertEqual(
            mail_groups.latest_gmail_mailbox_revision(
                self.database_url,
                user_id=user_id,
            ),
            revision_before,
        )
        messages = mail_groups.list_messages_for_gmail_thread(
            self.database_url,
            user_id=user_id,
            gmail_thread_id=message_id,
        )
        self.assertEqual(messages[0].body_fetch_status, "failed")
        self.assertEqual(messages[0].body_fetch_error, "temporary attachment failure")

    def test_stale_body_worker_cannot_downgrade_terminal_fetched_message(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        message_id = f"body-race-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        fetched = mail_groups.GmailMessageRecord(
            user_id=user_id,
            message_id=message_id,
            gmail_thread_id=message_id,
            history_id="1",
            label_ids=["INBOX"],
            internal_date=None,
            subject="Attachment-only body race",
            sender="sender@example.test",
            recipients={},
            headers={},
            snippet="Attached statement",
            raw_payload={
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "statement.pdf",
                            "body": {"attachmentId": "attachment-1", "size": 1024},
                        }
                    ],
                }
            },
            html_body_sanitized=None,
            html_render_document=None,
            text_body=None,
            extracted_signals={},
            body_hash="fetched-attachment",
            body_fetch_status="fetched",
            created_at="",
            updated_at="",
        )
        mail_groups.upsert_gmail_messages(self.database_url, [fetched])

        self.assertEqual(
            mail_groups.mark_gmail_messages_body_fetch_state(
                self.database_url,
                user_id=user_id,
                message_ids=[message_id],
                status="pending",
            ),
            0,
        )
        self.assertEqual(
            mail_groups.mark_gmail_messages_body_fetch_state(
                self.database_url,
                user_id=user_id,
                message_ids=[message_id],
                status="failed",
                error="stale worker failure",
            ),
            0,
        )
        messages = mail_groups.list_messages_for_gmail_thread(
            self.database_url,
            user_id=user_id,
            gmail_thread_id=message_id,
        )
        self.assertEqual(messages[0].body_fetch_status, "fetched")
        self.assertIsNone(messages[0].body_fetch_error)

    def test_stale_body_hydration_preserves_newer_labels_and_history(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        message_id = f"body-metadata-race-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        metadata = mail_groups.GmailMessageRecord(
            user_id=user_id,
            message_id=message_id,
            gmail_thread_id=message_id,
            history_id="200",
            label_ids=["INBOX", "STARRED"],
            internal_date=None,
            subject="Newer metadata subject",
            sender="sender@example.test",
            recipients={"to": "recipient@example.test"},
            headers={"subject": "Newer metadata subject"},
            snippet="Metadata preview",
            raw_payload={"payload": {"headers": []}},
            html_body_sanitized=None,
            html_render_document=None,
            text_body=None,
            extracted_signals={},
            body_hash="metadata",
            body_fetch_status="missing",
            created_at="",
            updated_at="",
        )
        mail_groups.upsert_gmail_messages(self.database_url, [metadata])
        revision_before = mail_groups.latest_gmail_mailbox_revision(
            self.database_url,
            user_id=user_id,
        )

        mail_groups.update_gmail_message_bodies(
            self.database_url,
            [
                replace(
                    metadata,
                    history_id="100",
                    label_ids=["INBOX"],
                    subject="Stale body-fetch subject",
                    headers={"subject": "Stale body-fetch subject"},
                    raw_payload={
                        "payload": {
                            "mimeType": "text/plain",
                            "body": {"data": "RnVsbCBib2R5"},
                        }
                    },
                    text_body="Full body",
                    body_hash="full-body",
                    body_fetch_status="fetched",
                )
            ],
        )

        messages = mail_groups.list_messages_for_gmail_thread(
            self.database_url,
            user_id=user_id,
            gmail_thread_id=message_id,
        )
        self.assertEqual(messages[0].history_id, "200")
        self.assertEqual(messages[0].label_ids, ["INBOX", "STARRED"])
        self.assertEqual(messages[0].subject, "Newer metadata subject")
        self.assertEqual(messages[0].text_body, "Full body")
        self.assertEqual(messages[0].body_fetch_status, "fetched")
        self.assertEqual(
            mail_groups.latest_gmail_mailbox_revision(
                self.database_url,
                user_id=user_id,
            ),
            revision_before,
        )

    def test_stale_body_hydration_cannot_replace_a_concurrently_fetched_body(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        message_id = f"body-content-race-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        current = mail_groups.GmailMessageRecord(
            user_id=user_id,
            message_id=message_id,
            gmail_thread_id=message_id,
            history_id="200",
            label_ids=["INBOX"],
            internal_date=None,
            subject="Concurrent body",
            sender="sender@example.test",
            recipients={},
            headers={},
            snippet="Preview",
            raw_payload={
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": "Q3VycmVudCBmdWxsIGJvZHk"},
                }
            },
            html_body_sanitized=None,
            html_render_document=None,
            text_body="Current full body",
            extracted_signals={},
            body_hash="current-body",
            body_fetch_status="fetched",
            created_at="",
            updated_at="",
        )
        mail_groups.upsert_gmail_messages(self.database_url, [current])
        stale_attachment_only = replace(
            current,
            raw_payload={
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "application/pdf",
                            "filename": "stale.pdf",
                            "body": {"attachmentId": "stale-attachment", "size": 1024},
                        }
                    ],
                }
            },
            text_body=None,
            body_hash="stale-body",
        )

        self.assertEqual(
            mail_groups.update_gmail_message_bodies(
                self.database_url,
                [stale_attachment_only],
            ),
            [],
        )
        messages = mail_groups.list_messages_for_gmail_thread(
            self.database_url,
            user_id=user_id,
            gmail_thread_id=message_id,
        )
        self.assertEqual(messages[0].text_body, "Current full body")
        self.assertEqual(messages[0].body_hash, "current-body")
        self.assertEqual(messages[0].body_fetch_status, "fetched")

    def test_data_delete_blocks_stale_post_fetch_write(self) -> None:
        self._assert_destructive_race("data_delete")

    def test_disconnect_blocks_stale_post_fetch_write(self) -> None:
        self._assert_destructive_race("disconnect")

    def test_account_delete_blocks_stale_post_fetch_write(self) -> None:
        self._assert_destructive_race("account_delete")

    def test_cancelled_job_cannot_be_completed_or_requeued(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        job_id = f"job-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        self.addCleanup(self._delete_test_job, job_id)
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO background_jobs (
                      id, kind, queue, status, user_id, payload_json, attempt_count,
                      max_attempts, run_after, lease_owner, lease_expires_at, created_at, updated_at
                    ) VALUES (
                      :job_id, 'gmail_delta_sync', 'critical', 'running', :user_id, '{}', 1,
                      5, now(), 'worker-1', now() + interval '5 minutes', now(), now()
                    )
                    """
                ),
                {"job_id": job_id, "user_id": user_id},
            )
        claimed = get_job(self.database_url, job_id)
        assert claimed is not None

        self.assertEqual(cancel_user_jobs(self.database_url, user_id=user_id), 1)
        self.assertFalse(complete_job(self.database_url, job_id, worker_id="worker-1"))
        self.assertFalse(fail_job(self.database_url, claimed, "late failure", worker_id="worker-1"))

        current = get_job(self.database_url, job_id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.status, "cancelled")
        self.assertIsNone(current.lease_owner)

    def test_exclusive_lock_releases_after_exception(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        engine = repository.get_engine(self.database_url)
        lock_key = user_mail_lock_key(user_id)

        with engine.connect() as independent_probe:
            with self.assertRaisesRegex(RuntimeError, "forced failure"):
                with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                    raise RuntimeError("forced failure")
            acquired = independent_probe.execute(
                text("SELECT pg_try_advisory_lock_shared(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
            self.assertTrue(acquired)
            independent_probe.execute(
                text("SELECT pg_advisory_unlock_shared(:lock_key)"),
                {"lock_key": lock_key},
            )

    def test_provider_shared_lock_drains_before_deletion_and_rejects_late_work(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        lock_key = user_mail_provider_lock_key(user_id)
        provider_entered = threading.Event()
        deletion_attempted = threading.Event()
        release_provider = threading.Event()
        errors: list[BaseException] = []
        order: list[str] = []

        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)

        def provider_mutation() -> None:
            try:
                with shared_user_mail_lock(self.database_url, user_id=user_id):
                    order.append("provider_entered")
                    provider_entered.set()
                    if not deletion_attempted.wait(5):
                        raise TimeoutError("deletion did not attempt the exclusive lock")
                    if not release_provider.wait(5):
                        raise TimeoutError("test did not release provider mutation")
                    # A real repository write acquires the transaction-key
                    # shared lock on another connection. It must finish even
                    # while deletion is queued on the provider key.
                    mail_groups.mark_gmail_watch_error(
                        self.database_url,
                        user_id=user_id,
                        error="provider completion",
                    )
                    order.append("provider_local_completion")
                    # This stands for the last externally visible provider
                    # effect. Deletion must not enter, much less return, first.
                    order.append("provider_effect_complete")
            except BaseException as exc:
                errors.append(exc)

        def delete_account_data() -> None:
            try:
                deletion_attempted.set()
                with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                    order.append("deletion_entered")
                    mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)
                    repository.delete_google_oauth_token(self.database_url, user_id=user_id)
                order.append("deletion_returned")
            except BaseException as exc:
                errors.append(exc)

        provider = threading.Thread(target=provider_mutation, daemon=True)
        deletion = threading.Thread(target=delete_account_data, daemon=True)
        provider.start()
        self.assertTrue(provider_entered.wait(5), "provider did not acquire its shared lock")
        deletion.start()
        self.assertTrue(deletion_attempted.wait(5), "deletion did not attempt its exclusive lock")

        unsigned_lock_key = lock_key % (2**64)
        class_id = unsigned_lock_key >> 32
        object_id = unsigned_lock_key & 0xFFFFFFFF
        deadline = monotonic() + 5
        deletion_is_waiting = False
        with repository.get_engine(self.database_url).connect() as probe:
            while monotonic() < deadline:
                deletion_is_waiting = bool(
                    probe.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1
                              FROM pg_locks
                              WHERE locktype = 'advisory'
                                AND mode = 'ExclusiveLock'
                                AND granted = FALSE
                                AND classid::bigint = :class_id
                                AND objid::bigint = :object_id
                                AND objsubid = 1
                            )
                            """
                        ),
                        {"class_id": class_id, "object_id": object_id},
                    ).scalar_one()
                )
                if deletion_is_waiting:
                    break
                deletion_attempted.wait(0.01)
        self.assertTrue(deletion_is_waiting, "deletion did not queue on the provider lock")

        # A non-blocking independent database probe proves the shared lock is
        # excluding destructive work without relying on scheduler sleeps.
        with repository.get_engine(self.database_url).connect() as probe:
            acquired = probe.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
            self.assertFalse(acquired)

        release_provider.set()
        provider.join(5)
        deletion.join(5)

        self.assertFalse(provider.is_alive() or deletion.is_alive(), "provider/deletion race deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(
            order,
            [
                "provider_entered",
                "provider_local_completion",
                "provider_effect_complete",
                "deletion_entered",
                "deletion_returned",
            ],
        )
        with self.assertRaises(UserMailWorkBlocked):
            with shared_user_mail_lock(self.database_url, user_id=user_id):
                self.fail("post-deletion provider work passed the durable guard")

    def test_database_subject_key_matches_runtime_and_rolling_guard_triggers_exist(self) -> None:
        google_sub = f"parity-sub-{uuid4()}"
        with repository.get_engine(self.database_url).connect() as connection:
            database_hash = connection.execute(
                text("SELECT electronic_mail_google_subject_key(:google_sub)"),
                {"google_sub": google_sub},
            ).scalar_one()
            trigger_names = set(
                connection.execute(
                    text(
                        """
                        SELECT tgname
                        FROM pg_trigger
                        WHERE tgname IN (
                          'trg_users_tombstone_google_subject',
                          'trg_users_tombstone_google_guard',
                          'trg_users_require_fresh_oauth',
                          'trg_users_require_fresh_guard_clear',
                          'trg_google_tokens_require_guard'
                        )
                        """
                    )
                ).scalars()
            )

        self.assertEqual(database_hash, google_subject_tombstone_hash(google_sub))
        self.assertEqual(
            trigger_names,
            {
                "trg_users_tombstone_google_subject",
                "trg_users_tombstone_google_guard",
                "trg_users_require_fresh_oauth",
                "trg_users_require_fresh_guard_clear",
                "trg_google_tokens_require_guard",
            },
        )

    def test_old_replica_disconnect_and_data_delete_paths_advance_revocation_epoch(self) -> None:
        for operation in ("disconnect", "data_delete"):
            with self.subTest(operation=operation):
                user_id = f"guard-test-{uuid4()}"
                google_sub = f"google-sub-{uuid4()}"
                email = f"{user_id}@example.test"
                subject_hash = google_subject_tombstone_hash(google_sub)
                old_state = f"old-state-{uuid4()}"
                fresh_state = f"fresh-state-{uuid4()}"
                self._create_connected_user(user_id, google_sub=google_sub, email=email)
                self.addCleanup(self._delete_test_user, user_id)
                self.addCleanup(self._delete_tombstone, subject_hash)
                self.addCleanup(self._delete_oauth_session, old_state)
                self.addCleanup(self._delete_oauth_session, fresh_state)
                old_session = self._save_oauth_session(old_state)

                # These are intentionally the old-replica paths: no subject
                # lock and no explicit record_google_subject_revocation call.
                if operation == "disconnect":
                    mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)
                else:
                    mail_groups.delete_user_mail_data(self.database_url, user_id=user_id)

                tombstone = self._tombstone(subject_hash)
                self.assertIsNotNone(tombstone)
                assert tombstone is not None
                self.assertGreater(int(tombstone["deleted_epoch"]), old_session.started_epoch)

                with self.assertRaises(DBAPIError):
                    repository.upsert_user(
                        self.database_url,
                        email=email,
                        google_sub=google_sub,
                        display_name="Old replica callback",
                    )
                with self.assertRaises(DBAPIError):
                    mail_groups.clear_google_guard_state(
                        self.database_url,
                        user_id=user_id,
                        oauth_started_epoch=old_session.started_epoch,
                    )

                fresh_session = self._save_oauth_session(fresh_state)
                repository.upsert_user(
                    self.database_url,
                    email=email,
                    google_sub=google_sub,
                    display_name="Fresh callback",
                    oauth_started_epoch=fresh_session.started_epoch,
                )
                mail_groups.clear_google_guard_state(
                    self.database_url,
                    user_id=user_id,
                    oauth_started_epoch=fresh_session.started_epoch,
                )
                repository.upsert_google_oauth_token(
                    self.database_url,
                    user_id=user_id,
                    token_json_encrypted="fresh-token",
                    oauth_started_epoch=fresh_session.started_epoch,
                )
                self.assertGreater(fresh_session.started_epoch, int(tombstone["deleted_epoch"]))

    def test_deletion_waits_for_inflight_credential_refresh_and_then_removes_token(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        settings = SimpleNamespace(
            database_path=self.database_url,
            app_encryption_key="refresh-race-encryption-key",
            google_configured=True,
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        deletion_attempted = threading.Event()
        deletion_done = threading.Event()
        errors: list[BaseException] = []
        order: list[str] = []

        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)

        credentials = SimpleNamespace(
            expired=True,
            token="expired-access-token",
            refresh_token="refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=[google_service.GMAIL_FULL_SCOPE],
            expiry=None,
        )

        def block_refresh(_request) -> None:
            refresh_started.set()
            if not release_refresh.wait(5):
                raise TimeoutError("test did not release credential refresh")
            credentials.expired = False
            credentials.token = "refreshed-access-token"

        credentials.refresh = block_refresh

        def finish_external_refresh() -> None:
            try:
                loaded = google_service.create_authorized_credentials(settings, user_id=user_id)
                if loaded is not credentials:
                    raise AssertionError("credential refresh did not return the refreshed credentials")
                order.append("refresh_returned")
            except BaseException as exc:
                errors.append(exc)

        def disconnect() -> None:
            try:
                deletion_attempted.set()
                with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                    order.append("deletion_entered")
                    mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)
                    repository.delete_google_oauth_token(self.database_url, user_id=user_id)
                order.append("deletion_returned")
                deletion_done.set()
            except BaseException as exc:
                errors.append(exc)

        with (
            patch.object(
                google_service,
                "decrypt_json",
                return_value={
                    "token": "expired-access-token",
                    "refresh_token": "refresh-token",
                    "scopes": [google_service.GMAIL_FULL_SCOPE],
                },
            ),
            patch.object(
                google_service.Credentials,
                "from_authorized_user_info",
                return_value=credentials,
            ),
        ):
            refresh = threading.Thread(target=finish_external_refresh, daemon=True)
            deletion = threading.Thread(target=disconnect, daemon=True)
            refresh.start()
            self.assertTrue(refresh_started.wait(5), "credential refresh did not start")
            deletion.start()
            self.assertTrue(deletion_attempted.wait(5), "deletion did not attempt its lock")

            lock_key = user_mail_provider_lock_key(user_id)
            unsigned_lock_key = lock_key % (2**64)
            deadline = monotonic() + 5
            deletion_is_waiting = False
            with repository.get_engine(self.database_url).connect() as probe:
                while monotonic() < deadline:
                    deletion_is_waiting = bool(
                        probe.execute(
                            text(
                                """
                                SELECT EXISTS (
                                  SELECT 1 FROM pg_locks
                                  WHERE locktype = 'advisory'
                                    AND mode = 'ExclusiveLock'
                                    AND granted = FALSE
                                    AND classid::bigint = :class_id
                                    AND objid::bigint = :object_id
                                    AND objsubid = 1
                                )
                                """
                            ),
                            {
                                "class_id": unsigned_lock_key >> 32,
                                "object_id": unsigned_lock_key & 0xFFFFFFFF,
                            },
                        ).scalar_one()
                    )
                    if deletion_is_waiting:
                        break
                    deletion_attempted.wait(0.01)
            self.assertTrue(deletion_is_waiting, "deletion did not wait for credential refresh")
            self.assertFalse(deletion_done.is_set())
            release_refresh.set()
            refresh.join(5)
            deletion.join(5)

        self.assertFalse(refresh.is_alive() or deletion.is_alive(), "credential refresh race deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(order, ["refresh_returned", "deletion_entered", "deletion_returned"])
        self.assertEqual(self._token_count(user_id), 0)

    def test_deletion_tombstone_rejects_started_callback_and_allows_new_oauth_start(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        google_sub = f"google-sub-{uuid4()}"
        email = f"{user_id}@example.test"
        subject_hash = google_subject_tombstone_hash(google_sub)
        old_state = f"old-state-{uuid4()}"
        new_state = f"new-state-{uuid4()}"
        callback_started = threading.Event()
        deletion_done = threading.Event()
        stale_callback_rejected = threading.Event()
        errors: list[BaseException] = []

        self._create_connected_user(user_id, google_sub=google_sub, email=email)
        self.addCleanup(self._delete_tombstone, subject_hash)
        self.addCleanup(self._delete_oauth_session, old_state)
        self.addCleanup(self._delete_oauth_session, new_state)
        self.addCleanup(self._delete_test_user, user_id)
        old_session = self._save_oauth_session(old_state)

        def finish_started_callback() -> None:
            try:
                callback_started.set()
                if not deletion_done.wait(5):
                    raise TimeoutError("account deletion did not finish")
                with exclusive_google_subject_lock(self.database_url, subject_hash=subject_hash):
                    if repository.oauth_session_is_after_google_subject_deletion(
                        self.database_url,
                        subject_hash=subject_hash,
                        oauth_started_epoch=old_session.started_epoch,
                    ):
                        repository.upsert_user(
                            self.database_url,
                            email=email,
                            google_sub=google_sub,
                            display_name="Stale callback",
                        )
                    else:
                        stale_callback_rejected.set()
            except BaseException as exc:
                errors.append(exc)

        callback = threading.Thread(target=finish_started_callback, daemon=True)
        callback.start()
        self.assertTrue(callback_started.wait(5), "callback did not reach its external exchange phase")
        with exclusive_google_subject_lock(self.database_url, subject_hash=subject_hash):
            with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                deleted = repository.delete_user_account_with_google_subject_tombstone(
                    self.database_url,
                    user_id=user_id,
                )
        self.assertTrue(deleted)
        deletion_done.set()
        callback.join(5)

        self.assertFalse(callback.is_alive(), "stale callback race deadlocked")
        self.assertEqual(errors, [])
        self.assertTrue(stale_callback_rejected.is_set())
        self.assertEqual(self._user_count(user_id), 0)
        tombstone = self._tombstone(subject_hash)
        self.assertIsNotNone(tombstone)
        assert tombstone is not None
        self.assertEqual(tombstone["subject_hash"], subject_hash)
        self.assertNotIn(google_sub, tombstone["subject_hash"])
        self.assertGreater(int(tombstone["deleted_epoch"]), old_session.started_epoch)
        with self.assertRaises(DBAPIError):
            repository.upsert_user(
                self.database_url,
                email=email,
                google_sub=google_sub,
                display_name="Old replica without epoch proof",
            )

        new_session = self._save_oauth_session(new_state)
        with exclusive_google_subject_lock(self.database_url, subject_hash=subject_hash):
            self.assertTrue(
                repository.oauth_session_is_after_google_subject_deletion(
                    self.database_url,
                    subject_hash=subject_hash,
                    oauth_started_epoch=new_session.started_epoch,
                )
            )
            recreated = repository.upsert_user(
                self.database_url,
                email=email,
                google_sub=google_sub,
                display_name="Fresh callback",
                oauth_started_epoch=new_session.started_epoch,
            )
            with exclusive_user_mail_lock(self.database_url, user_id=recreated.id):
                repository.upsert_google_oauth_token(
                    self.database_url,
                    user_id=recreated.id,
                    token_json_encrypted="fresh-token",
                    oauth_started_epoch=new_session.started_epoch,
                )
        self.addCleanup(self._delete_test_user, recreated.id)
        self.assertGreater(new_session.started_epoch, int(tombstone["deleted_epoch"]))
        self.assertEqual(self._user_count(recreated.id), 1)
        self.assertEqual(self._token_count(recreated.id), 1)

    def test_account_deletion_waits_for_callback_that_won_subject_lock(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        google_sub = f"google-sub-{uuid4()}"
        subject_hash = google_subject_tombstone_hash(google_sub)
        callback_holds_subject = threading.Event()
        release_callback = threading.Event()
        deletion_entered = threading.Event()
        errors: list[BaseException] = []

        self._create_connected_user(user_id, google_sub=google_sub)
        self.addCleanup(self._delete_tombstone, subject_hash)
        self.addCleanup(self._delete_test_user, user_id)

        def callback_wins() -> None:
            try:
                with exclusive_google_subject_lock(self.database_url, subject_hash=subject_hash):
                    with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                        repository.upsert_google_oauth_token(
                            self.database_url,
                            user_id=user_id,
                            token_json_encrypted="callback-token",
                        )
                    callback_holds_subject.set()
                    if not release_callback.wait(5):
                        raise TimeoutError("test did not release callback")
            except BaseException as exc:
                errors.append(exc)

        def delete_after_callback() -> None:
            try:
                with exclusive_google_subject_lock(self.database_url, subject_hash=subject_hash):
                    deletion_entered.set()
                    with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                        repository.delete_user_account_with_google_subject_tombstone(
                            self.database_url,
                            user_id=user_id,
                        )
            except BaseException as exc:
                errors.append(exc)

        callback = threading.Thread(target=callback_wins, daemon=True)
        deletion = threading.Thread(target=delete_after_callback, daemon=True)
        callback.start()
        self.assertTrue(callback_holds_subject.wait(5), "callback did not acquire subject lock")
        deletion.start()
        self.assertFalse(deletion_entered.wait(0.2), "deletion bypassed callback's subject lock")
        release_callback.set()
        callback.join(5)
        deletion.join(5)

        self.assertFalse(callback.is_alive() or deletion.is_alive(), "subject ordering race deadlocked")
        self.assertEqual(errors, [])
        self.assertTrue(deletion_entered.is_set())
        self.assertEqual(self._user_count(user_id), 0)
        self.assertIsNotNone(self._tombstone(subject_hash))

    def _assert_destructive_race(self, operation: str) -> None:
        user_id = f"guard-test-{uuid4()}"
        subject_hash = google_subject_tombstone_hash(f"sub-{user_id}")
        engine = repository.get_engine(self.database_url)
        errors: list[BaseException] = []
        holder_entered = threading.Event()
        release_holder = threading.Event()
        deletion_entered = threading.Event()
        deletion_done = threading.Event()
        stale_fetch_done = threading.Event()
        stale_rejected = threading.Event()

        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)
        if operation == "account_delete":
            self.addCleanup(self._delete_tombstone, subject_hash)
        self._insert_message(user_id, "seed")

        def hold_inflight_write() -> None:
            try:
                with user_mail_write_transaction(engine, user_id=user_id) as connection:
                    connection.execute(
                        text(
                            """
                            INSERT INTO gmail_messages (
                              user_id, message_id, raw_payload_json, body_hash, created_at, updated_at
                            ) VALUES (:user_id, 'inflight', '{}', 'inflight', now(), now())
                            """
                        ),
                        {"user_id": user_id},
                    )
                    holder_entered.set()
                    if not release_holder.wait(5):
                        raise TimeoutError("test did not release in-flight writer")
            except BaseException as exc:
                errors.append(exc)

        def delete_or_disconnect() -> None:
            try:
                with exclusive_user_mail_lock(self.database_url, user_id=user_id):
                    deletion_entered.set()
                    cancel_user_jobs(self.database_url, user_id=user_id)
                    if operation == "data_delete":
                        mail_groups.delete_user_mail_data(self.database_url, user_id=user_id)
                    elif operation == "disconnect":
                        mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)
                        repository.delete_google_oauth_token(self.database_url, user_id=user_id)
                    else:
                        mail_groups.delete_user_mail_data(self.database_url, user_id=user_id)
                        repository.delete_user_account_with_google_subject_tombstone(
                            self.database_url,
                            user_id=user_id,
                        )
            except BaseException as exc:
                errors.append(exc)
            finally:
                deletion_done.set()

        def persist_after_external_fetch() -> None:
            stale_fetch_done.set()
            if not deletion_done.wait(5):
                errors.append(TimeoutError("destructive operation did not finish"))
                return
            try:
                self._insert_message(user_id, "stale-after-fetch")
            except UserMailWorkBlocked:
                stale_rejected.set()
            except BaseException as exc:
                errors.append(exc)

        holder = threading.Thread(target=hold_inflight_write, daemon=True)
        deletion = threading.Thread(target=delete_or_disconnect, daemon=True)
        stale = threading.Thread(target=persist_after_external_fetch, daemon=True)
        holder.start()
        self.assertTrue(holder_entered.wait(5), "in-flight writer did not acquire shared lock")
        deletion.start()
        stale.start()
        self.assertTrue(stale_fetch_done.wait(5), "stale worker did not finish its external-fetch phase")
        self.assertFalse(deletion_entered.wait(0.2), "exclusive deletion bypassed an active shared writer")

        release_holder.set()
        holder.join(5)
        deletion.join(5)
        stale.join(5)

        self.assertFalse(holder.is_alive() or deletion.is_alive() or stale.is_alive(), "guard race test deadlocked")
        self.assertEqual(errors, [])
        self.assertTrue(deletion_entered.is_set())
        self.assertTrue(stale_rejected.is_set())
        self.assertEqual(self._message_count(user_id, "stale-after-fetch"), 0)
        with self.assertRaises(UserMailWorkBlocked):
            enqueue_job(
                self.database_url,
                kind="gmail_delta_sync",
                user_id=user_id,
                payload={"user_id": user_id},
            )
        if operation == "disconnect":
            self.assertEqual(self._message_count(user_id), 2)
            self.assertEqual(self._token_count(user_id), 0)
        elif operation == "data_delete":
            self.assertEqual(self._message_count(user_id), 0)
            self.assertEqual(self._user_count(user_id), 1)
        else:
            self.assertEqual(self._message_count(user_id), 0)
            self.assertEqual(self._user_count(user_id), 0)

        self._assert_rejected_guard_releases_lock(user_id)

    def _create_connected_user(
        self,
        user_id: str,
        *,
        google_sub: str | None = None,
        email: str | None = None,
    ) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, google_sub, access_enabled, created_at, updated_at)
                    VALUES (:user_id, :email, :google_sub, TRUE, now(), now())
                    """
                ),
                {
                    "user_id": user_id,
                    "email": email or f"{user_id}@example.test",
                    "google_sub": google_sub or f"sub-{user_id}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO google_oauth_tokens (user_id, token_json_encrypted, updated_at)
                    VALUES (:user_id, 'test-token', now())
                    """
                ),
                {"user_id": user_id},
            )

    def _save_oauth_session(self, state: str):
        repository.save_oauth_login_session(
            self.database_url,
            state=state,
            code_verifier="v" * 43,
            redirect_to=None,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        )
        session = repository.get_oauth_login_session(
            self.database_url,
            state=state,
            now=datetime.now(timezone.utc).isoformat(),
        )
        if session is None:
            raise AssertionError("OAuth session was not persisted")
        return session

    def _tombstone(self, subject_hash: str) -> dict[str, object] | None:
        with repository.get_engine(self.database_url).connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT subject_hash, deleted_epoch, deleted_at
                    FROM google_subject_deletion_tombstones
                    WHERE subject_hash = :subject_hash
                    """
                ),
                {"subject_hash": subject_hash},
            ).mappings().first()
        return dict(row) if row is not None else None

    def _insert_message(self, user_id: str, message_id: str) -> None:
        mail_groups.upsert_gmail_messages(
            self.database_url,
            [
                mail_groups.GmailMessageRecord(
                    user_id=user_id,
                    message_id=message_id,
                    gmail_thread_id=message_id,
                    history_id=None,
                    label_ids=["INBOX"],
                    internal_date=None,
                    subject=message_id,
                    sender="sender@example.test",
                    recipients={},
                    headers={},
                    snippet=None,
                    raw_payload={},
                    html_body_sanitized=None,
                    html_render_document=None,
                    text_body=None,
                    extracted_signals={},
                    body_hash=message_id,
                    created_at="",
                    updated_at="",
                )
            ],
        )

    def _assert_rejected_guard_releases_lock(self, user_id: str) -> None:
        engine = repository.get_engine(self.database_url)
        with self.assertRaises(UserMailWorkBlocked):
            with user_mail_write_transaction(engine, user_id=user_id):
                pass
        lock_key = user_mail_lock_key(user_id)
        with engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
            self.assertTrue(acquired)
            connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})

    def _message_count(self, user_id: str, message_id: str | None = None) -> int:
        message_filter = " AND message_id = :message_id" if message_id is not None else ""
        with repository.get_engine(self.database_url).connect() as connection:
            value = connection.execute(
                text(f"SELECT COUNT(*) FROM gmail_messages WHERE user_id = :user_id{message_filter}"),
                {"user_id": user_id, "message_id": message_id},
            ).scalar_one()
        return int(value)

    def _user_count(self, user_id: str) -> int:
        return self._count("users", user_id)

    def _token_count(self, user_id: str) -> int:
        return self._count("google_oauth_tokens", user_id)

    def _count(self, table: str, user_id: str) -> int:
        with repository.get_engine(self.database_url).connect() as connection:
            value = connection.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {'id' if table == 'users' else 'user_id'} = :user_id"),
                {"user_id": user_id},
            ).scalar_one()
        return int(value)

    def _delete_test_user(self, user_id: str) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            google_sub = connection.execute(
                text("SELECT google_sub FROM users WHERE id = :user_id"),
                {"user_id": user_id},
            ).scalar_one_or_none()
            connection.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id})
            if google_sub is not None:
                connection.execute(
                    text(
                        "DELETE FROM google_subject_deletion_tombstones "
                        "WHERE subject_hash = :subject_hash"
                    ),
                    {"subject_hash": google_subject_tombstone_hash(str(google_sub))},
                )

    def _delete_tombstone(self, subject_hash: str) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM google_subject_deletion_tombstones WHERE subject_hash = :subject_hash"
                ),
                {"subject_hash": subject_hash},
            )

    def _delete_oauth_session(self, state: str) -> None:
        repository.delete_oauth_login_session(self.database_url, state=state)

    def _delete_test_job(self, job_id: str) -> None:
        with repository.get_engine(self.database_url).begin() as connection:
            connection.execute(text("DELETE FROM background_jobs WHERE id = :job_id"), {"job_id": job_id})


class _GuardResult:
    def __init__(self, value: bool | None = None) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return bool(self.value)


class _GuardConnection:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, dict(params or {})))
        if "pg_advisory_unlock" in sql:
            return _GuardResult(True)
        return _GuardResult(self.allowed if "SELECT EXISTS" in sql else None)


class _SessionGuardConnection(_GuardConnection):
    def __init__(self, *, allowed: bool) -> None:
        super().__init__(allowed=allowed)
        self.commits = 0
        self.rollbacks = 0
        self.invalidations = 0
        self.closes = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def invalidate(self) -> None:
        self.invalidations += 1

    def close(self) -> None:
        self.closes += 1
        self.closed = True


class _PostgresLockTimeout(RuntimeError):
    sqlstate = "55P03"


class _FailingTransactionGuardConnection(_GuardConnection):
    def execute(self, statement, params=None):
        sql = str(statement)
        if "pg_advisory_xact_lock_shared" in sql:
            self.calls.append((sql, dict(params or {})))
            raise DBAPIError(
                sql,
                params,
                _PostgresLockTimeout("driver-sensitive-detail"),
                connection_invalidated=False,
            )
        return super().execute(statement, params)


class _FailingSessionGuardConnection(_SessionGuardConnection):
    def __init__(
        self,
        *,
        allowed: bool,
        fail_lock_number: int | None = None,
        fail_unlock: bool = False,
    ) -> None:
        super().__init__(allowed=allowed)
        self.fail_lock_number = fail_lock_number
        self.fail_unlock = fail_unlock
        self.lock_calls = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        is_lock = "pg_advisory_lock" in sql and "pg_advisory_unlock" not in sql
        if is_lock:
            self.lock_calls += 1
            if self.lock_calls == self.fail_lock_number:
                self.calls.append((sql, dict(params or {})))
                raise DBAPIError(
                    sql,
                    params,
                    _PostgresLockTimeout("driver-sensitive-detail"),
                    connection_invalidated=False,
                )
        if self.fail_unlock and "pg_advisory_unlock" in sql:
            self.calls.append((sql, dict(params or {})))
            raise RuntimeError("cleanup-sensitive-detail")
        return super().execute(statement, params)


if __name__ == "__main__":
    unittest.main()
