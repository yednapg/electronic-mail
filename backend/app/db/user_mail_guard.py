from __future__ import annotations

"""Cross-process serialization for Gmail writes and destructive user actions."""

from contextlib import contextmanager
from hashlib import sha256
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.db.repository import get_engine


class UserMailWorkBlocked(RuntimeError):
    """Raised when Gmail-derived work is no longer allowed for a user."""


def _advisory_lock_key(namespace: str, identity: str) -> int:
    digest = sha256(f"electronic-mail:{namespace}:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def user_mail_lock_key(user_id: str) -> int:
    """Return a stable signed bigint key in this application's advisory-lock namespace."""
    return _advisory_lock_key("user-mail", user_id)


def google_subject_tombstone_hash(google_sub: str) -> str:
    """Return the stable, non-reversible key used for subject revocation ordering."""
    subject = google_sub.strip()
    if not subject:
        raise ValueError("Google subject is required for account deletion")
    digest = sha256(
        b"electronic-mail:google-subject:v1:" + subject.encode("utf-8")
    ).hexdigest()
    return f"sha256:v1:{digest}"


def google_subject_lock_key(subject_hash: str) -> int:
    """Return the advisory-lock key that serializes one verified Google principal."""
    return _advisory_lock_key("google-subject", subject_hash)


def guard_user_mail_write(connection: Connection, *, user_id: str) -> None:
    """Serialize one transaction with destructive work, then re-check its durable guard."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock_shared(:lock_key)"),
        {"lock_key": user_mail_lock_key(user_id)},
    )
    allowed = connection.execute(
        text(
            """
            SELECT EXISTS (
              SELECT 1
              FROM users
              JOIN google_oauth_tokens tokens ON tokens.user_id = users.id
              WHERE users.id = :user_id
                AND users.access_enabled = TRUE
                AND users.google_disconnected_at IS NULL
                AND users.google_data_delete_requested_at IS NULL
                AND users.google_data_deleted_at IS NULL
            )
            """
        ),
        {"user_id": user_id},
    ).scalar_one()
    if not bool(allowed):
        raise UserMailWorkBlocked("Gmail-derived work is no longer allowed for this user")
    # The token-write trigger uses this transaction-local proof to distinguish
    # current guarded refreshes from writes issued by a drained older replica.
    connection.execute(
        text("SELECT set_config('electronic_mail.user_mail_write_user_id', :user_id, TRUE)"),
        {"user_id": user_id},
    )


@contextmanager
def user_mail_write_transaction(engine: Engine, *, user_id: str) -> Iterator[Connection]:
    """Open a transaction that cannot commit across a disconnect or destructive purge."""
    with engine.begin() as connection:
        guard_user_mail_write(connection, user_id=user_id)
        yield connection


def update_connected_google_oauth_token(
    database_url: str,
    *,
    user_id: str,
    token_json_encrypted: str,
) -> None:
    """Update an existing connected token without allowing refresh to recreate it."""
    engine = get_engine(database_url)
    with user_mail_write_transaction(engine, user_id=user_id) as connection:
        updated_user_id = connection.execute(
            text(
                """
                UPDATE google_oauth_tokens
                SET token_json_encrypted = :token_json_encrypted,
                    updated_at = now()
                WHERE user_id = :user_id
                RETURNING user_id
                """
            ),
            {
                "user_id": user_id,
                "token_json_encrypted": token_json_encrypted,
            },
        ).scalar_one_or_none()
        if updated_user_id is None:
            raise UserMailWorkBlocked("Google credentials are no longer connected for this user")


@contextmanager
def _exclusive_advisory_lock(database_url: str, *, lock_key: int) -> Iterator[None]:
    with get_engine(database_url).connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        connection.commit()
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})
            connection.commit()


@contextmanager
def exclusive_user_mail_lock(database_url: str, *, user_id: str) -> Iterator[None]:
    """Hold the per-user destructive lock across multiple repository transactions."""
    with _exclusive_advisory_lock(database_url, lock_key=user_mail_lock_key(user_id)):
        yield


@contextmanager
def exclusive_google_subject_lock(database_url: str, *, subject_hash: str) -> Iterator[None]:
    """Serialize account deletion and OAuth completion for one Google principal."""
    with _exclusive_advisory_lock(database_url, lock_key=google_subject_lock_key(subject_hash)):
        yield
