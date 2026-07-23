from __future__ import annotations

"""Postgres runtime repository for auth, sessions, OAuth, and engine access."""

import atexit
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import os
from threading import Lock
from typing import Any, Iterable, Iterator
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.db.models import (
    StoredAppSession,
    StoredGoogleOAuthToken,
    StoredMobileLoginCode,
    StoredMobileOAuthHandoff,
    StoredOAuthLoginSession,
    StoredUser,
)

DEFAULT_USER_ID = os.getenv("APP_USER_ID", "local-user").strip() or "local-user"
ALEMBIC_HEAD_REVISION = "20260723_0025"
ALEMBIC_BASELINE_REVISION = ALEMBIC_HEAD_REVISION
POSTGRES_URL_PREFIXES = ("postgres://", "postgresql://")
_ENGINES: dict[str, Engine] = {}
_ENGINES_LOCK = Lock()
logger = logging.getLogger(__name__)


class IdentityConflictError(RuntimeError):
    """A verified Google principal conflicts with another stored account."""


class RowAdapter:
    """Small row object that supports sqlite-style index, key, and keys() access."""

    def __init__(self, mapping: dict[str, Any], values: tuple[Any, ...]):
        self._mapping = mapping
        self._values = values

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def keys(self) -> list[str]:
        return list(self._mapping.keys())


class ResultAdapter:
    """sqlite-like result wrapper around a SQLAlchemy cursor result."""

    def __init__(self, result):
        self._result = result
        self.rowcount = int(getattr(result, "rowcount", -1))

    def fetchone(self) -> RowAdapter | None:
        if not self._result.returns_rows:
            return None
        row = self._result.fetchone()
        return _adapt_row(row) if row is not None else None

    def fetchall(self) -> list[RowAdapter]:
        if not self._result.returns_rows:
            return []
        return [_adapt_row(row) for row in self._result.fetchall()]


class PostgresConnectionAdapter:
    """Minimal sqlite-compatible adapter for auth repository functions."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> ResultAdapter:
        statement, values = _sqlalchemy_text_statement(sql, tuple(params or ()))
        return ResultAdapter(self._connection.execute(statement, values))

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> ResultAdapter:
        rows = [tuple(item) for item in params]
        if not rows:
            statement, values = _sqlalchemy_text_statement(sql, ())
            return ResultAdapter(self._connection.execute(statement, values))
        statement, _values = _sqlalchemy_text_statement(sql, rows[0])
        return ResultAdapter(self._connection.execute(statement, [_sqlalchemy_text_params(row) for row in rows]))


def _adapt_row(row) -> RowAdapter:
    mapping = dict(row._mapping)
    return RowAdapter(mapping, tuple(row))


def is_postgres_database(database_path: str) -> bool:
    """Return true when a repository database identifier is a Postgres URL."""
    return database_path.strip().startswith(POSTGRES_URL_PREFIXES)


def database_backend(database_path: str) -> str:
    """Return the configured repository backend name."""
    return "postgres" if is_postgres_database(database_path) else "unsupported"


def _sqlalchemy_text_statement(sql: str, params: tuple[Any, ...]):
    """Translate sqlite qmark placeholders into SQLAlchemy Core text parameters."""
    parts = sql.split("?")
    if len(parts) == 1:
        return text(sql), {}
    names = [f"p{index}" for index in range(len(parts) - 1)]
    statement_sql = "".join(part + (f":{names[index]}" if index < len(names) else "") for index, part in enumerate(parts))
    return text(statement_sql), {name: params[index] for index, name in enumerate(names)}


def _sqlalchemy_text_params(params: tuple[Any, ...]) -> dict[str, Any]:
    """Return named SQLAlchemy parameters for an executemany qmark row."""
    return {f"p{index}": value for index, value in enumerate(params)}


def _sqlalchemy_url(database_path: str) -> str:
    normalized = database_path.strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized.removeprefix("postgres://")
    if normalized.startswith("postgresql://"):
        return "postgresql+psycopg://" + normalized.removeprefix("postgresql://")
    raise RuntimeError("DATABASE_URL must be Postgres. SQLite is no longer supported.")


def get_engine(database_path: str) -> Engine:
    """Return a cached SQLAlchemy engine for Postgres runtime access."""
    url = _sqlalchemy_url(database_path)
    with _ENGINES_LOCK:
        engine = _ENGINES.get(url)
        if engine is None:
            engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
            )
            _ENGINES[url] = engine
        return engine


def dispose_cached_engines() -> None:
    """Dispose every process-cached DB pool once and clear the cache safely."""

    with _ENGINES_LOCK:
        cached = list(_ENGINES.values())
        _ENGINES.clear()
    seen: set[int] = set()
    for engine in cached:
        identity = id(engine)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            engine.dispose()
        except Exception as exc:
            # Continue draining other pools during shutdown; a single broken
            # driver/pool must not prevent the rest from releasing resources.
            logger.warning(
                "database.engine_dispose_failed",
                extra={"event_fields": {"exception_type": type(exc).__name__}},
            )


atexit.register(dispose_cached_engines)


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def initialize_database(database_path: str) -> None:
    """Validate runtime DB choice; schema is managed by Alembic."""
    if not is_postgres_database(database_path):
        raise RuntimeError("DATABASE_URL must be Postgres. SQLite bootstrap was removed.")


@contextmanager
def connect(database_path: str) -> Iterator[PostgresConnectionAdapter]:
    """Open a transaction-scoped Postgres repository connection."""
    with get_engine(database_path).begin() as connection:
        yield PostgresConnectionAdapter(connection)


def upsert_user(
    database_path: str,
    *,
    email: str,
    google_sub: str,
    display_name: str | None = None,
    access_enabled: bool = True,
    oauth_started_epoch: int | None = None,
) -> StoredUser:
    """Create or update an app user from Google identity."""
    normalized_email = email.strip().lower()
    now = utc_now_iso()
    with connect(database_path) as connection:
        if oauth_started_epoch is not None:
            connection.execute(
                "SELECT set_config('electronic_mail.oauth_started_epoch', ?, TRUE)",
                (str(oauth_started_epoch),),
            )
        existing_by_sub = connection.execute(
            "SELECT * FROM users WHERE google_sub = ? LIMIT 1 FOR UPDATE",
            (google_sub,),
        ).fetchone()
        existing_by_email = connection.execute(
            "SELECT * FROM users WHERE email = ? LIMIT 1 FOR UPDATE",
            (normalized_email,),
        ).fetchone()
        if existing_by_email is not None and (
            existing_by_sub is None or str(existing_by_email["id"]) != str(existing_by_sub["id"])
        ):
            # Email is mutable and can be reassigned by a Workspace admin. Never
            # attach a new immutable Google subject to an existing mailbox merely
            # because the address matches. Legacy email-keyed rows must be
            # migrated or re-linked explicitly after ownership is verified.
            raise IdentityConflictError("Google identity conflicts with an existing account")
        existing = existing_by_sub
        user_id = str(existing["id"]) if existing is not None else str(uuid4())
        created_at = str(existing["created_at"]) if existing is not None else now
        enabled = bool(existing["access_enabled"]) if existing is not None else access_enabled
        connection.execute(
            """
            INSERT INTO users (id, email, display_name, google_sub, access_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              email = excluded.email,
              display_name = excluded.display_name,
              google_sub = excluded.google_sub,
              access_enabled = excluded.access_enabled,
              updated_at = excluded.updated_at
            """,
            (user_id, normalized_email, display_name, google_sub, enabled, created_at, now),
        )
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _to_user(row)


def get_user(database_path: str, user_id: str) -> StoredUser | None:
    """Load one app user."""
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,)).fetchone()
    return _to_user(row) if row is not None else None


def get_user_by_email(database_path: str, email: str) -> StoredUser | None:
    """Load one app user by email."""
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ? LIMIT 1", (email.strip().lower(),)).fetchone()
    return _to_user(row) if row is not None else None


def is_allowed_email(database_path: str, email: str) -> bool | None:
    """Return table allowlist state, or None when the table has no opinion."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT enabled FROM allowed_emails WHERE email = ? LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
    if row is None:
        return None
    return bool(row["enabled"])


def create_app_session(
    database_path: str,
    *,
    user_id: str,
    token_hash: str,
    platform: str,
    expires_at: str,
) -> StoredAppSession:
    """Persist one app session for a web cookie or iOS bearer token."""
    now = utc_now_iso()
    session_id = str(uuid4())
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO app_sessions (id, user_id, token_hash, platform, expires_at, revoked_at, last_seen_at, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (session_id, user_id, token_hash, platform, expires_at, now, now),
        )
        row = connection.execute("SELECT * FROM app_sessions WHERE id = ?", (session_id,)).fetchone()
    return _to_app_session(row)


def get_active_app_session(database_path: str, *, token_hash: str, now: str) -> StoredAppSession | None:
    """Load an unexpired, unrevoked app session by token hash."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM app_sessions
            WHERE token_hash = ?
              AND revoked_at IS NULL
              AND expires_at > ?
            LIMIT 1
            """,
            (token_hash, now),
        ).fetchone()
        if row is not None:
            connection.execute("UPDATE app_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
    return _to_app_session(row) if row is not None else None


def revoke_app_session(database_path: str, *, token_hash: str) -> bool:
    """Revoke one app session by token hash."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        cursor = connection.execute(
            "UPDATE app_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, token_hash),
        )
    return cursor.rowcount > 0


def revoke_user_app_sessions(database_path: str, *, user_id: str) -> int:
    """Revoke all active app sessions for one user."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE app_sessions
            SET revoked_at = ?
            WHERE user_id = ?
              AND revoked_at IS NULL
            """,
            (now, user_id),
        )
    return int(cursor.rowcount)


def save_oauth_login_session(
    database_path: str,
    *,
    state: str,
    code_verifier: str,
    redirect_to: str | None,
    expires_at: str,
) -> None:
    """Persist an OAuth PKCE login session keyed by state."""
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO oauth_login_sessions (
              state, code_verifier, redirect_to, expires_at, created_at, started_epoch
            )
            VALUES (
              ?, ?, ?, ?, clock_timestamp(), nextval('google_identity_event_epoch_seq')
            )
            ON CONFLICT(state) DO UPDATE SET
              code_verifier = excluded.code_verifier,
              redirect_to = excluded.redirect_to,
              expires_at = excluded.expires_at,
              created_at = excluded.created_at,
              started_epoch = excluded.started_epoch
            """,
            (state, code_verifier, redirect_to, expires_at),
        )


def get_oauth_login_session(database_path: str, *, state: str, now: str) -> StoredOAuthLoginSession | None:
    """Load an unexpired OAuth login session."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM oauth_login_sessions
            WHERE state = ? AND expires_at > ?
            LIMIT 1
            """,
            (state, now),
        ).fetchone()
    return _to_oauth_login_session(row) if row is not None else None


def delete_oauth_login_session(database_path: str, *, state: str) -> None:
    """Delete one OAuth login session."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM oauth_login_sessions WHERE state = ?", (state,))


def oauth_session_is_after_google_subject_deletion(
    database_path: str,
    *,
    subject_hash: str,
    oauth_started_epoch: int,
) -> bool:
    """Return whether this OAuth start event is newer than the subject's last deletion."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT NOT EXISTS (
              SELECT 1
              FROM google_subject_deletion_tombstones
              WHERE subject_hash = ?
                AND deleted_epoch >= ?
            ) AS allowed
            """,
            (subject_hash, oauth_started_epoch),
        ).fetchone()
    return bool(row["allowed"]) if row is not None else False


def create_mobile_login_code(
    database_path: str,
    *,
    code_hash: str,
    user_id: str,
    expires_at: str,
) -> StoredMobileLoginCode:
    """Persist one mobile login code."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO mobile_login_codes (code_hash, user_id, expires_at, consumed_at, created_at)
            VALUES (?, ?, ?, NULL, ?)
            """,
            (code_hash, user_id, expires_at, now),
        )
        row = connection.execute("SELECT * FROM mobile_login_codes WHERE code_hash = ?", (code_hash,)).fetchone()
    return _to_mobile_login_code(row)


def consume_mobile_login_code(database_path: str, *, code_hash: str, now: str) -> StoredMobileLoginCode | None:
    """Consume one unexpired mobile login code exactly once."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            UPDATE mobile_login_codes
            SET consumed_at = ?
            WHERE code_hash = ?
              AND consumed_at IS NULL
              AND expires_at > ?
            RETURNING *
            """,
            (now, code_hash, now),
        ).fetchone()
    return _to_mobile_login_code(row) if row is not None else None


def create_mobile_oauth_handoff(
    database_path: str,
    *,
    handoff_id: str,
    login_code_encrypted: str | None,
    expires_at: str,
    login_code_hash: str | None = None,
    exchange_code_challenge: str | None = None,
    status: str = "ready",
    error: str | None = None,
) -> StoredMobileOAuthHandoff:
    """Persist an encrypted browser-to-native handoff across API replicas."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO mobile_oauth_handoffs (
              handoff_id, login_code_encrypted, login_code_hash, exchange_code_challenge,
              status, error, expires_at, consumed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(handoff_id) DO UPDATE SET
              login_code_encrypted = excluded.login_code_encrypted,
              login_code_hash = excluded.login_code_hash,
              exchange_code_challenge = excluded.exchange_code_challenge,
              status = excluded.status,
              error = excluded.error,
              expires_at = excluded.expires_at,
              consumed_at = NULL,
              created_at = excluded.created_at
            """,
            (
                handoff_id,
                login_code_encrypted,
                login_code_hash,
                exchange_code_challenge,
                status,
                error,
                expires_at,
                now,
            ),
        )
        row = connection.execute("SELECT * FROM mobile_oauth_handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
    return _to_mobile_oauth_handoff(row)


def consume_mobile_oauth_handoff(
    database_path: str,
    *,
    handoff_id: str,
    now: str,
) -> StoredMobileOAuthHandoff | None:
    """Atomically consume one unexpired handoff exactly once."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            UPDATE mobile_oauth_handoffs
            SET consumed_at = ?
            WHERE handoff_id = ?
              AND consumed_at IS NULL
              AND expires_at > ?
            RETURNING *
            """,
            (now, handoff_id, now),
        ).fetchone()
    return _to_mobile_oauth_handoff(row) if row is not None else None


def consume_bound_mobile_login_code(
    database_path: str,
    *,
    handoff_id: str,
    exchange_code_challenge: str,
    code_hash: str,
    now: str,
) -> StoredMobileLoginCode | None:
    """Atomically consume a login code and its verifier-bound handoff once."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            WITH eligible_handoff AS (
              SELECT handoff_id
              FROM mobile_oauth_handoffs
              WHERE handoff_id = ?
                AND status = 'ready'
                AND consumed_at IS NULL
                AND expires_at > ?
                AND exchange_code_challenge = ?
                AND login_code_hash = ?
              FOR UPDATE
            ), consumed_code AS (
              UPDATE mobile_login_codes
              SET consumed_at = ?
              WHERE code_hash = ?
                AND consumed_at IS NULL
                AND expires_at > ?
                AND EXISTS (SELECT 1 FROM eligible_handoff)
              RETURNING *
            ), consumed_handoff AS (
              UPDATE mobile_oauth_handoffs
              SET consumed_at = ?
              WHERE handoff_id = ?
                AND EXISTS (SELECT 1 FROM consumed_code)
              RETURNING handoff_id
            )
            SELECT consumed_code.*
            FROM consumed_code
            JOIN consumed_handoff ON TRUE
            """,
            (
                handoff_id,
                now,
                exchange_code_challenge,
                code_hash,
                now,
                code_hash,
                now,
                now,
                handoff_id,
            ),
        ).fetchone()
    return _to_mobile_login_code(row) if row is not None else None


def get_mobile_oauth_handoff(
    database_path: str,
    *,
    handoff_id: str,
    now: str,
) -> StoredMobileOAuthHandoff | None:
    """Read an unexpired handoff without racing browser completion and polling."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM mobile_oauth_handoffs
            WHERE handoff_id = ?
              AND consumed_at IS NULL
              AND expires_at > ?
            LIMIT 1
            """,
            (handoff_id, now),
        ).fetchone()
    return _to_mobile_oauth_handoff(row) if row is not None else None


def upsert_google_oauth_token(
    database_path: str,
    *,
    user_id: str,
    token_json_encrypted: str,
    oauth_started_epoch: int | None = None,
) -> StoredGoogleOAuthToken:
    """Persist encrypted Google OAuth credentials for a user."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        if oauth_started_epoch is not None:
            connection.execute(
                "SELECT set_config('electronic_mail.oauth_started_epoch', ?, TRUE)",
                (str(oauth_started_epoch),),
            )
        connection.execute(
            """
            INSERT INTO google_oauth_tokens (user_id, token_json_encrypted, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              token_json_encrypted = excluded.token_json_encrypted,
              updated_at = excluded.updated_at
            """,
            (user_id, token_json_encrypted, now),
        )
        row = connection.execute("SELECT * FROM google_oauth_tokens WHERE user_id = ?", (user_id,)).fetchone()
    return _to_google_oauth_token(row)


def get_google_oauth_token(database_path: str, *, user_id: str) -> StoredGoogleOAuthToken | None:
    """Load encrypted Google OAuth credentials for a user."""
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM google_oauth_tokens WHERE user_id = ?", (user_id,)).fetchone()
    return _to_google_oauth_token(row) if row is not None else None


def delete_google_oauth_token(database_path: str, *, user_id: str) -> None:
    """Remove stored Google OAuth credentials for one user."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM google_oauth_tokens WHERE user_id = ?", (user_id,))


def record_google_subject_revocation(
    database_path: str,
    *,
    subject_hash: str,
) -> int:
    """Advance the durable revocation epoch for one stable Google subject key."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            INSERT INTO google_subject_deletion_tombstones (
              subject_hash, deleted_epoch, deleted_at
            )
            VALUES (
              ?, nextval('google_identity_event_epoch_seq'), clock_timestamp()
            )
            ON CONFLICT(subject_hash) DO UPDATE SET
              deleted_epoch = excluded.deleted_epoch,
              deleted_at = excluded.deleted_at
            RETURNING deleted_epoch
            """,
            (subject_hash,),
        ).fetchone()
    if row is None:
        raise RuntimeError("Google subject revocation epoch was not persisted")
    return int(row["deleted_epoch"])


def delete_user_account_with_google_subject_tombstone(
    database_path: str,
    *,
    user_id: str,
) -> bool:
    """Delete an account; the database trigger tombstones its Google subject atomically."""
    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id FROM users WHERE id = ? LIMIT 1 FOR UPDATE",
            (user_id,),
        ).fetchone()
        if existing is None:
            return False
        cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cursor.rowcount > 0


def _to_user(row: RowAdapter) -> StoredUser:
    return StoredUser(
        id=str(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]) if row["display_name"] is not None else None,
        google_sub=str(row["google_sub"]),
        access_enabled=bool(row["access_enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_app_session(row: RowAdapter) -> StoredAppSession:
    return StoredAppSession(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        token_hash=str(row["token_hash"]),
        platform=str(row["platform"]),
        expires_at=str(row["expires_at"]),
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] is not None else None,
        last_seen_at=str(row["last_seen_at"]) if row["last_seen_at"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _to_oauth_login_session(row: RowAdapter) -> StoredOAuthLoginSession:
    return StoredOAuthLoginSession(
        state=str(row["state"]),
        code_verifier=str(row["code_verifier"]),
        redirect_to=str(row["redirect_to"]) if row["redirect_to"] is not None else None,
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        started_epoch=int(row["started_epoch"]),
    )


def _to_mobile_login_code(row: RowAdapter) -> StoredMobileLoginCode:
    return StoredMobileLoginCode(
        code_hash=str(row["code_hash"]),
        user_id=str(row["user_id"]),
        expires_at=str(row["expires_at"]),
        consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _to_mobile_oauth_handoff(row: RowAdapter) -> StoredMobileOAuthHandoff:
    return StoredMobileOAuthHandoff(
        handoff_id=str(row["handoff_id"]),
        login_code_encrypted=str(row["login_code_encrypted"]) if row["login_code_encrypted"] is not None else None,
        login_code_hash=str(row["login_code_hash"]) if row["login_code_hash"] is not None else None,
        exchange_code_challenge=(
            str(row["exchange_code_challenge"]) if row["exchange_code_challenge"] is not None else None
        ),
        status=str(row["status"]),
        error=str(row["error"]) if row["error"] is not None else None,
        expires_at=str(row["expires_at"]),
        consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _to_google_oauth_token(row: RowAdapter) -> StoredGoogleOAuthToken:
    return StoredGoogleOAuthToken(
        user_id=str(row["user_id"]),
        token_json_encrypted=str(row["token_json_encrypted"]),
        updated_at=str(row["updated_at"]),
    )
