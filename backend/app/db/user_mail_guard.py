from __future__ import annotations

"""Cross-process serialization for Gmail writes and destructive user actions."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.db.repository import (
    ADVISORY_LOCK_WAIT_TIMEOUT_SECONDS,
    get_advisory_lock_engine,
    get_engine,
)


class UserMailWorkBlocked(RuntimeError):
    """Raised when Gmail-derived work is no longer allowed for a user."""


class AdvisoryLockUnavailable(RuntimeError):
    """Raised safely when serialization cannot be obtained or released."""


_LOCK_UNAVAILABLE_MESSAGE = "Mail operation is temporarily busy. Please retry shortly."


@dataclass
class _AdvisoryLockSession:
    database_url: str
    connection: Connection
    invalidated: bool = False


_ACTIVE_ADVISORY_LOCK_SESSION: ContextVar[_AdvisoryLockSession | None] = ContextVar(
    "active_user_mail_advisory_lock_session",
    default=None,
)


def _advisory_lock_key(namespace: str, identity: str) -> int:
    digest = sha256(f"electronic-mail:{namespace}:{identity}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def user_mail_lock_key(user_id: str) -> int:
    """Return a stable signed bigint key in this application's advisory-lock namespace."""
    return _advisory_lock_key("user-mail", user_id)


def user_mail_provider_lock_key(user_id: str) -> int:
    """Return the session-lock key that drains external Gmail operations."""
    return _advisory_lock_key("user-mail-provider", user_id)


def client_draft_lock_key(user_id: str, client_draft_id: str) -> int:
    """Return the session-lock key that serializes one app draft identity."""
    return _advisory_lock_key("gmail-client-draft", f"{user_id}:{client_draft_id}")


def gmail_draft_lock_key(user_id: str, gmail_draft_id: str) -> int:
    """Return the session-lock key that serializes one Gmail draft identity."""
    return _advisory_lock_key("gmail-provider-draft", f"{user_id}:{gmail_draft_id}")


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
    _configure_advisory_lock_timeout(connection)
    try:
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(:lock_key)"),
            {"lock_key": user_mail_lock_key(user_id)},
        )
    except Exception as exc:
        if _is_advisory_lock_timeout(exc):
            raise AdvisoryLockUnavailable(_LOCK_UNAVAILABLE_MESSAGE) from exc
        raise
    _require_user_mail_work_allowed(connection, user_id=user_id)
    # The token-write trigger uses this transaction-local proof to distinguish
    # current guarded refreshes from writes issued by a drained older replica.
    connection.execute(
        text("SELECT set_config('electronic_mail.user_mail_write_user_id', :user_id, TRUE)"),
        {"user_id": user_id},
    )


def _require_user_mail_work_allowed(connection: Connection, *, user_id: str) -> None:
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
def _advisory_lock_connection(database_url: str) -> Iterator[Connection]:
    """Reuse one bounded-pool session across nested ordered lock scopes."""
    active = _ACTIVE_ADVISORY_LOCK_SESSION.get()
    if active is not None and active.database_url == database_url:
        if active.invalidated or bool(getattr(active.connection, "closed", False)):
            raise AdvisoryLockUnavailable(_LOCK_UNAVAILABLE_MESSAGE)
        yield active.connection
        return

    try:
        connection_context = get_advisory_lock_engine(database_url).connect()
    except SQLAlchemyTimeoutError as exc:
        raise AdvisoryLockUnavailable(_LOCK_UNAVAILABLE_MESSAGE) from exc

    with connection_context as connection:
        token = _ACTIVE_ADVISORY_LOCK_SESSION.set(
            _AdvisoryLockSession(database_url=database_url, connection=connection)
        )
        try:
            yield connection
        finally:
            _ACTIVE_ADVISORY_LOCK_SESSION.reset(token)


def _configure_advisory_lock_timeout(connection: Connection) -> None:
    """Bound the next transaction's PostgreSQL lock waits server-side."""
    connection.execute(
        text("SELECT set_config('lock_timeout', :lock_timeout, TRUE)"),
        {"lock_timeout": f"{ADVISORY_LOCK_WAIT_TIMEOUT_SECONDS}s"},
    )


def _is_advisory_lock_timeout(exc: BaseException) -> bool:
    if isinstance(exc, SQLAlchemyTimeoutError):
        return True
    original = getattr(exc, "orig", None)
    for candidate in (exc, original):
        if candidate is None:
            continue
        sqlstate = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if sqlstate == "55P03":
            return True
    return False


def _discard_advisory_lock_connection(connection: Connection) -> None:
    """Poison and close a session whose lock ownership is no longer trustworthy."""
    active = _ACTIVE_ADVISORY_LOCK_SESSION.get()
    if active is not None and active.connection is connection:
        active.invalidated = True
    try:
        connection.invalidate()
    except Exception:
        pass
    try:
        connection.close()
    except Exception:
        pass


def _release_advisory_locks(
    connection: Connection,
    *,
    acquired: list[tuple[str, int]],
) -> None:
    """Release session locks or discard the entire session on any cleanup fault."""
    try:
        connection.rollback()
        for mode, lock_key in reversed(acquired):
            function = "pg_advisory_unlock_shared" if mode == "shared" else "pg_advisory_unlock"
            released = connection.execute(
                text(f"SELECT {function}(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
            if not bool(released):
                raise RuntimeError("PostgreSQL advisory lock ownership was lost")
        if acquired:
            connection.commit()
    except Exception as exc:
        _discard_advisory_lock_connection(connection)
        raise AdvisoryLockUnavailable(_LOCK_UNAVAILABLE_MESSAGE) from exc


@contextmanager
def _advisory_lock_scope(
    database_url: str,
    *,
    locks: list[tuple[str, int]],
) -> Iterator[Connection]:
    """Acquire ordered session locks and release them in reverse order."""
    acquired: list[tuple[str, int]] = []
    with _advisory_lock_connection(database_url) as connection:
        try:
            _configure_advisory_lock_timeout(connection)
            for mode, lock_key in locks:
                if mode not in {"shared", "exclusive"}:
                    raise ValueError(f"Unsupported advisory lock mode: {mode}")
                function = "pg_advisory_lock_shared" if mode == "shared" else "pg_advisory_lock"
                connection.execute(text(f"SELECT {function}(:lock_key)"), {"lock_key": lock_key})
                acquired.append((mode, lock_key))
            # Session locks survive this commit; provider I/O must not retain a
            # database snapshot or transaction while the scope is active.
            connection.commit()
        except Exception as exc:
            _release_advisory_locks(connection, acquired=acquired)
            if _is_advisory_lock_timeout(exc):
                raise AdvisoryLockUnavailable(_LOCK_UNAVAILABLE_MESSAGE) from exc
            raise
        try:
            yield connection
        finally:
            _release_advisory_locks(connection, acquired=acquired)


@contextmanager
def _exclusive_advisory_lock(database_url: str, *, lock_key: int) -> Iterator[None]:
    with _advisory_lock_scope(database_url, locks=[("exclusive", lock_key)]):
        yield


@contextmanager
def advisory_session_lock(
    database_url: str,
    *,
    lock_key: int,
    shared: bool = False,
) -> Iterator[None]:
    """Hold a re-entrant bounded-pool session lock for a nested operation."""
    mode = "shared" if shared else "exclusive"
    with _advisory_lock_scope(database_url, locks=[(mode, lock_key)]):
        yield


@contextmanager
def advisory_session_locks(
    database_url: str,
    *,
    lock_keys: list[int],
) -> Iterator[None]:
    """Hold distinct exclusive locks in the caller's deterministic order."""
    ordered_keys = list(dict.fromkeys(lock_keys))
    with _advisory_lock_scope(
        database_url,
        locks=[("exclusive", lock_key) for lock_key in ordered_keys],
    ):
        yield


@contextmanager
def shared_user_mail_lock(database_url: str, *, user_id: str) -> Iterator[None]:
    """Hold a fail-closed shared user lock across one external Gmail operation.

    This is deliberately a session advisory lock rather than a transaction
    lock: provider calls cannot run inside a database transaction, but account
    deletion still has to drain them before it can return. The durable guard is
    checked only after the lock is held, so a waiter that loses to deletion
    cannot use credentials observed before the deletion.
    """
    with _advisory_lock_scope(
        database_url,
        locks=[("shared", user_mail_provider_lock_key(user_id))],
    ) as connection:
        _require_user_mail_work_allowed(connection, user_id=user_id)
        # End the guard-check transaction while retaining the session lock.
        connection.commit()
        yield


@contextmanager
def exclusive_user_mail_lock(database_url: str, *, user_id: str) -> Iterator[None]:
    """Drain provider work, then exclude guarded writes for destructive work.

    Provider scopes can open ordinary guarded transactions while their session
    lock is held. Separate namespaces and the provider -> transaction lock
    order keep an exclusive waiter from sitting between those two compatible
    shared acquisitions and forming a cross-connection wait cycle.
    """
    with _advisory_lock_scope(
        database_url,
        locks=[
            ("exclusive", user_mail_provider_lock_key(user_id)),
            ("exclusive", user_mail_lock_key(user_id)),
        ],
    ):
        yield


@contextmanager
def exclusive_google_subject_lock(database_url: str, *, subject_hash: str) -> Iterator[None]:
    """Serialize account deletion and OAuth completion for one Google principal."""
    with _exclusive_advisory_lock(database_url, lock_key=google_subject_lock_key(subject_hash)):
        yield
