from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import mail_groups, repository
from app.db.jobs import cancel_user_jobs, complete_job, enqueue_job, fail_job, get_job
from app.db.user_mail_guard import (
    UserMailWorkBlocked,
    exclusive_google_subject_lock,
    exclusive_user_mail_lock,
    google_subject_lock_key,
    google_subject_tombstone_hash,
    guard_user_mail_write,
    user_mail_lock_key,
    user_mail_write_transaction,
)
from app.services.integrations.google import persist_token_payload
from app.workers import gmail_poller


class UserMailGuardUnitTests(unittest.TestCase):
    def test_write_guard_locks_before_checking_durable_state(self) -> None:
        connection = _GuardConnection(allowed=True)

        guard_user_mail_write(connection, user_id="user-1")

        self.assertEqual(len(connection.calls), 3)
        self.assertIn("pg_advisory_xact_lock_shared", connection.calls[0][0])
        self.assertIn("google_data_delete_requested_at IS NULL", connection.calls[1][0])
        self.assertIn("electronic_mail.user_mail_write_user_id", connection.calls[2][0])
        self.assertEqual(connection.calls[0][1]["lock_key"], user_mail_lock_key("user-1"))

    def test_write_guard_rejects_disconnected_or_deleted_user(self) -> None:
        connection = _GuardConnection(allowed=False)

        with self.assertRaises(UserMailWorkBlocked):
            guard_user_mail_write(connection, user_id="user-1")

    def test_lock_key_is_stable_signed_bigint(self) -> None:
        lock_key = user_mail_lock_key("user-1")

        self.assertEqual(lock_key, user_mail_lock_key("user-1"))
        self.assertNotEqual(lock_key, user_mail_lock_key("user-2"))
        self.assertGreaterEqual(lock_key, -(2**63))
        self.assertLess(lock_key, 2**63)

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
            last_import_completed_at=(
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

    def test_refresh_completion_after_disconnect_cannot_recreate_token(self) -> None:
        user_id = f"guard-test-{uuid4()}"
        settings = SimpleNamespace(
            database_path=self.database_url,
            app_encryption_key="refresh-race-encryption-key",
        )
        refresh_started = threading.Event()
        disconnect_done = threading.Event()
        stale_refresh_rejected = threading.Event()
        errors: list[BaseException] = []

        self._create_connected_user(user_id)
        self.addCleanup(self._delete_test_user, user_id)

        def finish_external_refresh() -> None:
            try:
                observed = repository.get_google_oauth_token(self.database_url, user_id=user_id)
                if observed is None:
                    raise AssertionError("refresh did not observe the connected token")
                refresh_started.set()
                if not disconnect_done.wait(5):
                    raise TimeoutError("disconnect did not complete")
                try:
                    persist_token_payload(
                        settings,
                        {"token": "stale-refreshed-token", "refresh_token": "stale-refresh-token"},
                        user_id=user_id,
                    )
                except UserMailWorkBlocked:
                    stale_refresh_rejected.set()
            except BaseException as exc:
                errors.append(exc)

        refresh = threading.Thread(target=finish_external_refresh, daemon=True)
        refresh.start()
        self.assertTrue(refresh_started.wait(5), "refresh did not complete its external-read phase")
        with exclusive_user_mail_lock(self.database_url, user_id=user_id):
            mail_groups.mark_google_disconnected(self.database_url, user_id=user_id)
            repository.delete_google_oauth_token(self.database_url, user_id=user_id)
        disconnect_done.set()
        refresh.join(5)

        self.assertFalse(refresh.is_alive(), "stale refresh race deadlocked")
        self.assertEqual(errors, [])
        self.assertTrue(stale_refresh_rejected.is_set())
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
        return _GuardResult(self.allowed if "SELECT EXISTS" in sql else None)


if __name__ == "__main__":
    unittest.main()
