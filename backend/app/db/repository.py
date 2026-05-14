from __future__ import annotations

"""Persistence helpers for source records, entities, state, auth, and AI cache."""

from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import getaddresses
import json
import os
import sqlite3
from typing import Any, Iterator, Iterable
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.db.models import (
    StoredAppSession,
    StoredDashboardBriefing,
    StoredDashboardImportJob,
    StoredEntityOutcome,
    LoadedEntity,
    StoredGoogleOAuthToken,
    StoredMobileLoginCode,
    StoredOAuthLoginSession,
    StoredGmailDraft,
    StoredGmailHistoryEvent,
    StoredGmailMessageSnapshot,
    StoredGmailThreadProjection,
    StoredHistorySourceRecord,
    StoredFirstRunImportJob,
    StoredSourceRecordSummary,
    StoredEntity,
    StoredEntityAiSuggestion,
    StoredEntityState,
    StoredGmailSyncState,
    StoredManualTask,
    StoredSourceRecord,
    StoredEntityThreadMembership,
    StoredTraceRecord,
    StoredUser,
)


DEFAULT_USER_ID = os.getenv("APP_USER_ID", "local-user").strip() or "local-user"
ALEMBIC_BASELINE_REVISION = "20260515_0004"
POSTGRES_URL_PREFIXES = ("postgres://", "postgresql://")
_ENGINES: dict[str, Engine] = {}


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
    """Minimal sqlite-compatible adapter for the existing repository functions."""

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
        return ResultAdapter(
            self._connection.execute(
                statement,
                [_sqlalchemy_text_params(row) for row in rows],
            )
        )


def _adapt_row(row) -> RowAdapter:
    mapping = dict(row._mapping)
    return RowAdapter(mapping, tuple(row))


def is_postgres_database(database_path: str) -> bool:
    """Return true when a repository database identifier is a Postgres URL."""
    return database_path.strip().startswith(POSTGRES_URL_PREFIXES)


def database_backend(database_path: str) -> str:
    """Return the configured repository backend name."""
    return "postgres" if is_postgres_database(database_path) else "sqlite"


def _postgres_sql(sql: str) -> str:
    """Translate the repository's sqlite qmark placeholders to psycopg placeholders."""
    return sql.replace("?", "%s")


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
    return normalized


def get_engine(database_path: str) -> Engine:
    """Return a cached SQLAlchemy engine for Postgres runtime access."""
    url = _sqlalchemy_url(database_path)
    engine = _ENGINES.get(url)
    if engine is None:
        engine = create_engine(url, pool_pre_ping=True, poolclass=NullPool)
        _ENGINES[url] = engine
    return engine


def _sql_string_literal(value: str) -> str:
    """Return a SQLite string literal for schema defaults."""
    return "'" + value.replace("'", "''") + "'"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def initialize_database(database_path: str) -> None:
    """Create the SQLite schema if it does not already exist."""
    if is_postgres_database(database_path):
        return

    default_user_sql = _sql_string_literal(DEFAULT_USER_ID)
    with connect(database_path) as connection:
        connection.executescript(
            f"""
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              email TEXT NOT NULL UNIQUE,
              display_name TEXT,
              google_sub TEXT NOT NULL UNIQUE,
              access_enabled INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              platform TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              revoked_at TEXT,
              last_seen_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_app_sessions_user_expires
              ON app_sessions(user_id, expires_at DESC);

            CREATE TABLE IF NOT EXISTS oauth_login_sessions (
              state TEXT PRIMARY KEY,
              code_verifier TEXT NOT NULL,
              redirect_to TEXT,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mobile_login_codes (
              code_hash TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              consumed_at TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS google_oauth_tokens (
              user_id TEXT PRIMARY KEY,
              token_json_encrypted TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS allowed_emails (
              email TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 1,
              invited_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_records (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              source TEXT NOT NULL,
              thread_id TEXT,
              subject TEXT,
              sender TEXT,
              timestamp TEXT NOT NULL,
              raw_payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              deleted_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_source_records_timestamp
              ON source_records(timestamp DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_source_records_user_timestamp
              ON source_records(user_id, timestamp DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_source_records_user_active_timestamp
              ON source_records(user_id, deleted_at, timestamp DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_source_records_thread
              ON source_records(source, thread_id);

            CREATE TABLE IF NOT EXISTS source_record_summaries (
              source_record_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              summary TEXT NOT NULL,
              model TEXT NOT NULL,
              generated_from_hash TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              FOREIGN KEY(source_record_id) REFERENCES source_records(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_source_record_summaries_user_generated
              ON source_record_summaries(user_id, generated_at DESC, source_record_id);

            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              canonical_key TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(user_id, canonical_key)
            );

            CREATE INDEX IF NOT EXISTS idx_entities_created
              ON entities(created_at ASC, id ASC);

            CREATE INDEX IF NOT EXISTS idx_entities_user_created
              ON entities(user_id, created_at ASC, id ASC);

            CREATE TABLE IF NOT EXISTS entity_members (
              id TEXT PRIMARY KEY,
              entity_id TEXT NOT NULL,
              source_record_id TEXT NOT NULL UNIQUE,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
              FOREIGN KEY(source_record_id) REFERENCES source_records(id) ON DELETE CASCADE,
              UNIQUE(entity_id, source_record_id)
            );

            CREATE INDEX IF NOT EXISTS idx_entity_members_entity
              ON entity_members(entity_id);

            CREATE TABLE IF NOT EXISTS entity_thread_memberships (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              source TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
              UNIQUE(user_id, source, thread_id),
              UNIQUE(entity_id, source, thread_id)
            );

            CREATE TABLE IF NOT EXISTS entity_states (
              id TEXT PRIMARY KEY,
              entity_id TEXT NOT NULL UNIQUE,
              current_state TEXT NOT NULL,
              due_at TEXT,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS entity_ai_suggestions (
              id TEXT PRIMARY KEY,
              entity_id TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              explanation TEXT NOT NULL,
              action TEXT NOT NULL,
              suggested_timing TEXT NOT NULL,
              suggested_priority INTEGER NOT NULL,
              suggested_visibility INTEGER NOT NULL,
              model TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              generated_from_updated_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS trace_records (
              id TEXT PRIMARY KEY,
              trace_id TEXT NOT NULL,
              entity_id TEXT,
              source_record_id TEXT,
              user_id TEXT NOT NULL,
              stage TEXT NOT NULL,
              input TEXT NOT NULL,
              output TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
              FOREIGN KEY(source_record_id) REFERENCES source_records(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_trace_records_entity_created
              ON trace_records(entity_id, created_at ASC, id ASC);

            CREATE INDEX IF NOT EXISTS idx_trace_records_source_record
              ON trace_records(source_record_id);

            CREATE TABLE IF NOT EXISTS feed_projections (
              entity_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              pipeline_output TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_feed_projections_user_updated
              ON feed_projections(user_id, updated_at DESC, entity_id);

            CREATE TABLE IF NOT EXISTS gmail_sync_state (
              user_id TEXT PRIMARY KEY,
              last_history_id TEXT,
              last_full_sync_at TEXT,
              watch_expiration_at TEXT,
              last_sync_started_at TEXT,
              last_sync_completed_at TEXT,
              last_sync_error TEXT
            );

            CREATE TABLE IF NOT EXISTS gmail_message_snapshots (
              user_id TEXT NOT NULL,
              message_id TEXT NOT NULL,
              thread_id TEXT,
              history_id TEXT,
              internal_date TEXT,
              label_ids TEXT NOT NULL,
              raw_payload TEXT NOT NULL,
              fetch_status TEXT NOT NULL,
              tombstoned INTEGER NOT NULL DEFAULT 0,
              tombstoned_at TEXT,
              last_fetched_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_thread
              ON gmail_message_snapshots(user_id, thread_id);

            CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_history
              ON gmail_message_snapshots(user_id, history_id);

            CREATE TABLE IF NOT EXISTS gmail_history_events (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              history_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              message_id TEXT NOT NULL,
              thread_id TEXT,
              label_ids TEXT NOT NULL,
              message_payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_gmail_history_events_message
              ON gmail_history_events(user_id, message_id, history_id);

            CREATE TABLE IF NOT EXISTS gmail_thread_projections (
              user_id TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              latest_message_id TEXT NOT NULL,
              latest_received_at TEXT NOT NULL,
              latest_subject TEXT,
              latest_sender TEXT,
              snippet TEXT,
              participants TEXT NOT NULL,
              label_ids TEXT NOT NULL,
              message_count INTEGER NOT NULL,
              unread INTEGER NOT NULL DEFAULT 0,
              tombstoned INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id, thread_id)
            );

            CREATE INDEX IF NOT EXISTS idx_gmail_thread_projections_latest
              ON gmail_thread_projections(user_id, latest_received_at DESC, thread_id);

            CREATE TABLE IF NOT EXISTS dashboard_import_jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              status TEXT NOT NULL,
              stage TEXT NOT NULL DEFAULT 'queued',
              imported_count INTEGER NOT NULL DEFAULT 0,
              total_count INTEGER,
              source_records INTEGER NOT NULL DEFAULT 0,
              changed_entities INTEGER NOT NULL DEFAULT 0,
              refreshed_entities INTEGER NOT NULL DEFAULT 0,
              result_status TEXT,
              error_message TEXT,
              created_at TEXT NOT NULL,
              started_at TEXT,
              stage_started_at TEXT,
              stage_durations TEXT NOT NULL DEFAULT '{{}}',
              completed_at TEXT,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dashboard_import_jobs_latest
              ON dashboard_import_jobs(user_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_dashboard_import_jobs_active
              ON dashboard_import_jobs(user_id, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS first_run_import_jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              status TEXT NOT NULL,
              stage TEXT NOT NULL DEFAULT 'queued',
              fetched_count INTEGER NOT NULL DEFAULT 0,
              total_count INTEGER,
              thread_count INTEGER NOT NULL DEFAULT 0,
              dashboard_item_count INTEGER NOT NULL DEFAULT 0,
              inbox_ready_at TEXT,
              dashboard_ready_at TEXT,
              full_import_started_at TEXT,
              full_import_completed_at TEXT,
              error_message TEXT,
              created_at TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_first_run_import_jobs_latest
              ON first_run_import_jobs(user_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_first_run_import_jobs_active
              ON first_run_import_jobs(user_id, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS dashboard_briefings (
              user_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manual_tasks (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              entity_id TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              notes TEXT,
              section TEXT NOT NULL,
              due_at TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS entity_outcomes (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              outcome_type TEXT NOT NULL,
              snooze_until TEXT,
              note TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_entity_outcomes_latest
              ON entity_outcomes(user_id, entity_id, created_at);

            CREATE TABLE IF NOT EXISTS gmail_drafts (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              entity_id TEXT,
              gmail_draft_id TEXT NOT NULL,
              gmail_message_id TEXT,
              thread_id TEXT,
              to_recipients TEXT NOT NULL,
              cc_recipients TEXT,
              bcc_recipients TEXT,
              subject TEXT NOT NULL,
              body TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE SET NULL,
              UNIQUE(user_id, gmail_draft_id)
            );
            """
        )
        _ensure_column(connection, "source_records", "user_id", f"TEXT NOT NULL DEFAULT {default_user_sql}")
        _ensure_column(connection, "source_records", "deleted_at", "TEXT")
        _ensure_column(connection, "entities", "user_id", f"TEXT NOT NULL DEFAULT {default_user_sql}")
        _ensure_column(connection, "entity_thread_memberships", "user_id", f"TEXT NOT NULL DEFAULT {default_user_sql}")
        _ensure_column(connection, "gmail_message_snapshots", "internal_date", "TEXT")
        _ensure_column(connection, "gmail_sync_state", "watch_expiration_at", "TEXT")
        _ensure_column(connection, "gmail_sync_state", "last_sync_started_at", "TEXT")
        _ensure_column(connection, "gmail_sync_state", "last_sync_completed_at", "TEXT")
        _ensure_column(connection, "gmail_sync_state", "last_sync_error", "TEXT")
        _ensure_column(connection, "dashboard_import_jobs", "stage", "TEXT NOT NULL DEFAULT 'queued'")
        _ensure_column(connection, "dashboard_import_jobs", "imported_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "dashboard_import_jobs", "total_count", "INTEGER")
        _ensure_column(connection, "dashboard_import_jobs", "stage_started_at", "TEXT")
        _ensure_column(connection, "dashboard_import_jobs", "stage_durations", "TEXT NOT NULL DEFAULT '{}'")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_internal_date
              ON gmail_message_snapshots(user_id, internal_date)
            """
        )
    backfill_gmail_thread_projections(database_path)


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column to older local SQLite databases when needed."""
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if any(str(row["name"]) == column for row in rows):
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def backfill_gmail_thread_projections(database_path: str) -> None:
    """Materialize thread rows for snapshots created before projections existed."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT snapshots.user_id
            FROM gmail_message_snapshots snapshots
            LEFT JOIN gmail_thread_projections projections
              ON projections.user_id = snapshots.user_id
             AND projections.thread_id = snapshots.thread_id
            WHERE snapshots.thread_id IS NOT NULL
              AND snapshots.thread_id != ''
              AND projections.thread_id IS NULL
            """
        ).fetchall()

    for row in rows:
        refresh_gmail_thread_projections_for_threads(database_path, user_id=str(row["user_id"]))


@contextmanager
def connect(database_path: str) -> Iterator[sqlite3.Connection | PostgresConnectionAdapter]:
    """Open a transaction-scoped repository connection with row access by column name."""
    if is_postgres_database(database_path):
        with get_engine(database_path).begin() as connection:
            yield PostgresConnectionAdapter(connection)
        return

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def upsert_user(
    database_path: str,
    *,
    email: str,
    google_sub: str,
    display_name: str | None = None,
    access_enabled: bool = True,
) -> StoredUser:
    """Create or update an app user from Google identity."""
    normalized_email = email.strip().lower()
    now = utc_now_iso()
    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT * FROM users WHERE email = ? OR google_sub = ? LIMIT 1",
            (normalized_email, google_sub),
        ).fetchone()
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
        row = connection.execute(
            "SELECT * FROM users WHERE email = ? LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()
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
            connection.execute(
                "UPDATE app_sessions SET last_seen_at = ? WHERE id = ?",
                (now, row["id"]),
            )
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


def save_oauth_login_session(
    database_path: str,
    *,
    state: str,
    code_verifier: str,
    redirect_to: str | None,
    expires_at: str,
) -> None:
    """Persist an OAuth PKCE login session keyed by state."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO oauth_login_sessions (state, code_verifier, redirect_to, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(state) DO UPDATE SET
              code_verifier = excluded.code_verifier,
              redirect_to = excluded.redirect_to,
              expires_at = excluded.expires_at
            """,
            (state, code_verifier, redirect_to, expires_at, now),
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
            SELECT *
            FROM mobile_login_codes
            WHERE code_hash = ?
              AND consumed_at IS NULL
              AND expires_at > ?
            LIMIT 1
            """,
            (code_hash, now),
        ).fetchone()
        if row is not None:
            connection.execute(
                "UPDATE mobile_login_codes SET consumed_at = ? WHERE code_hash = ?",
                (now, code_hash),
            )
    return _to_mobile_login_code(row) if row is not None else None


def upsert_google_oauth_token(database_path: str, *, user_id: str, token_json_encrypted: str) -> StoredGoogleOAuthToken:
    """Persist encrypted Google OAuth credentials for a user."""
    now = utc_now_iso()
    with connect(database_path) as connection:
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


def list_google_oauth_token_user_ids(database_path: str) -> list[str]:
    """Return user ids that currently have stored Google credentials."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT user_id
            FROM google_oauth_tokens
            ORDER BY updated_at ASC, user_id ASC
            """
        ).fetchall()
    return [str(row["user_id"]) for row in rows]


def delete_google_oauth_token(database_path: str, *, user_id: str) -> None:
    """Remove stored Google OAuth credentials for one user."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM google_oauth_tokens WHERE user_id = ?", (user_id,))


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


def get_dashboard_briefing(database_path: str, *, user_id: str) -> StoredDashboardBriefing | None:
    """Load the cached dashboard briefing for one user."""
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM dashboard_briefings WHERE user_id = ?", (user_id,)).fetchone()
    return _to_dashboard_briefing(row) if row is not None else None


def upsert_dashboard_briefing(database_path: str, *, user_id: str, payload: dict[str, Any]) -> StoredDashboardBriefing:
    """Persist the cached dashboard briefing for one user."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO dashboard_briefings (user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(payload), now),
        )
    return StoredDashboardBriefing(user_id=user_id, payload=payload, updated_at=now)


def delete_user_google_data(database_path: str, *, user_id: str) -> None:
    """Delete Gmail/Calendar-derived data for one user while preserving the user and sessions."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM gmail_drafts WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM gmail_history_events WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM gmail_message_snapshots WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM gmail_thread_projections WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM gmail_sync_state WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM dashboard_import_jobs WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM dashboard_briefings WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM trace_records WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM source_record_summaries WHERE user_id = ?", (user_id,))
        connection.execute(
            """
            DELETE FROM entity_members
            WHERE source_record_id IN (
              SELECT id FROM source_records WHERE user_id = ? AND source != 'manual'
            )
            """,
            (user_id,),
        )
        connection.execute("DELETE FROM source_records WHERE user_id = ? AND source != 'manual'", (user_id,))
        connection.execute(
            """
            DELETE FROM feed_projections
            WHERE user_id = ?
              AND entity_id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_outcomes
            WHERE user_id = ?
              AND entity_id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_ai_suggestions
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_thread_memberships
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_states
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entities
            WHERE user_id = ?
              AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            """,
            (user_id, user_id),
        )


def clear_all_data(database_path: str) -> None:
    """Remove all persisted runtime/auth data while preserving schema and allowlist."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM gmail_history_events")
        connection.execute("DELETE FROM gmail_message_snapshots")
        connection.execute("DELETE FROM gmail_thread_projections")
        connection.execute("DELETE FROM gmail_drafts")
        connection.execute("DELETE FROM entity_outcomes")
        connection.execute("DELETE FROM manual_tasks")
        connection.execute("DELETE FROM feed_projections")
        connection.execute("DELETE FROM trace_records")
        connection.execute("DELETE FROM entity_ai_suggestions")
        connection.execute("DELETE FROM entity_members")
        connection.execute("DELETE FROM entity_thread_memberships")
        connection.execute("DELETE FROM entity_states")
        connection.execute("DELETE FROM entities")
        connection.execute("DELETE FROM source_record_summaries")
        connection.execute("DELETE FROM source_records")
        connection.execute("DELETE FROM gmail_sync_state")
        connection.execute("DELETE FROM dashboard_import_jobs")
        connection.execute("DELETE FROM first_run_import_jobs")
        connection.execute("DELETE FROM dashboard_briefings")
        connection.execute("DELETE FROM google_oauth_tokens")
        connection.execute("DELETE FROM mobile_login_codes")
        connection.execute("DELETE FROM oauth_login_sessions")
        connection.execute("DELETE FROM app_sessions")
        connection.execute("DELETE FROM users")


def clear_derived_memory(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> None:
    """Remove derived source memory while preserving synced records and manual tasks."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM trace_records WHERE user_id = ?", (user_id,))
        connection.execute(
            """
            DELETE FROM feed_projections
            WHERE user_id = ?
              AND entity_id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_ai_suggestions
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_members
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_thread_memberships
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entity_states
            WHERE entity_id IN (
              SELECT id FROM entities
              WHERE user_id = ?
                AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            )
            """,
            (user_id, user_id),
        )
        connection.execute(
            """
            DELETE FROM entities
            WHERE user_id = ?
              AND id NOT IN (SELECT entity_id FROM manual_tasks WHERE user_id = ?)
            """,
            (user_id, user_id),
        )


def upsert_source_records(database_path: str, records: Iterable[StoredSourceRecord]) -> None:
    """Persist normalized source records, updating existing rows by record id."""
    with connect(database_path) as connection:
        for record in records:
            user_id = record.raw_payload.get("user_id") if isinstance(record.raw_payload.get("user_id"), str) else DEFAULT_USER_ID
            record_id = _canonical_source_record_id(record.source, record.id, user_id=user_id, raw_payload=record.raw_payload)
            connection.execute(
                """
                INSERT INTO source_records (
                  id, user_id, source, thread_id, subject, sender, timestamp, raw_payload, created_at, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  source = excluded.source,
                  thread_id = excluded.thread_id,
                  subject = excluded.subject,
                  sender = excluded.sender,
                  timestamp = excluded.timestamp,
                  raw_payload = excluded.raw_payload,
                  deleted_at = excluded.deleted_at
                """,
                (
                    record_id,
                    user_id,
                    record.source,
                    record.thread_id,
                    record.subject,
                    record.sender,
                    record.timestamp,
                    json.dumps({**record.raw_payload, "user_id": user_id}, ensure_ascii=True),
                    record.created_at,
                    record.deleted_at,
                ),
            )


def _canonical_source_record_id(source: str, record_id: str, *, user_id: str, raw_payload: dict[str, Any]) -> str:
    """Scope provider-local source ids by user while preserving legacy local ids."""
    if source not in {"gmail", "calendar"} or user_id == DEFAULT_USER_ID:
        return record_id
    prefix = f"{user_id}:{source}:"
    if record_id.startswith(prefix):
        return record_id
    provider_id = raw_payload.get("message_id") if source == "gmail" else raw_payload.get("event_id")
    return f"{prefix}{provider_id if isinstance(provider_id, str) and provider_id else record_id}"


def mark_source_records_deleted(
    database_path: str,
    source_record_ids: Iterable[str],
    *,
    user_id: str | None = None,
    deleted_at: str | None = None,
) -> list[str]:
    """Mark source records as externally deleted and remove their active work projections."""
    unique_ids = sorted({source_record_id for source_record_id in source_record_ids if source_record_id})

    if not unique_ids:
        return []

    tombstoned_at = deleted_at or utc_now_iso()
    placeholders = ", ".join("?" for _ in unique_ids)
    user_clause = " AND source_records.user_id = ?" if user_id is not None else ""
    member_user_clause = (
        " AND source_record_id IN (SELECT id FROM source_records WHERE user_id = ?)"
        if user_id is not None
        else ""
    )

    with connect(database_path) as connection:
        entity_rows = connection.execute(
            f"""
            SELECT DISTINCT entity_id
            FROM entity_members
            WHERE source_record_id IN ({placeholders})
              {member_user_clause}
            """,
            [*unique_ids, *([user_id] if user_id is not None else [])],
        ).fetchall()
        entity_ids = [str(row["entity_id"]) for row in entity_rows]

        connection.execute(
            f"""
            UPDATE source_records
            SET deleted_at = ?
            WHERE id IN ({placeholders})
              {user_clause}
            """,
            [tombstoned_at, *unique_ids, *([user_id] if user_id is not None else [])],
        )
        connection.execute(
            f"""
            DELETE FROM entity_members
            WHERE source_record_id IN ({placeholders})
              {member_user_clause}
            """,
            [*unique_ids, *([user_id] if user_id is not None else [])],
        )

        empty_entity_ids: list[str] = []
        for entity_id in entity_ids:
            active_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM entity_members
                JOIN source_records ON source_records.id = entity_members.source_record_id
                WHERE entity_members.entity_id = ?
                  AND source_records.deleted_at IS NULL
                """,
                (entity_id,),
            ).fetchone()[0]
            if int(active_count) == 0:
                empty_entity_ids.append(entity_id)

        if empty_entity_ids:
            entity_placeholders = ", ".join("?" for _ in empty_entity_ids)
            connection.execute(
                f"DELETE FROM feed_projections WHERE entity_id IN ({entity_placeholders})",
                empty_entity_ids,
            )

    return entity_ids


def upsert_source_record_summary(
    database_path: str,
    *,
    source_record_id: str,
    user_id: str,
    summary: str,
    model: str,
    generated_from_hash: str,
) -> None:
    """Persist a compact generated summary for one source record."""
    generated_at = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO source_record_summaries (
              source_record_id, user_id, summary, model, generated_from_hash, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_record_id) DO UPDATE SET
              user_id = excluded.user_id,
              summary = excluded.summary,
              model = excluded.model,
              generated_from_hash = excluded.generated_from_hash,
              generated_at = excluded.generated_at
            """,
            (source_record_id, user_id, summary, model, generated_from_hash, generated_at),
        )


def list_source_record_summary_hashes(
    database_path: str,
    source_record_ids: Iterable[str],
    *,
    user_id: str | None = None,
) -> dict[str, str]:
    """Return existing summary input hashes keyed by source record id."""
    unique_ids = sorted({source_record_id for source_record_id in source_record_ids if source_record_id})

    if not unique_ids:
        return {}

    placeholders = ", ".join("?" for _ in unique_ids)
    user_clause = " AND user_id = ?" if user_id is not None else ""

    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT source_record_id, generated_from_hash
            FROM source_record_summaries
            WHERE source_record_id IN ({placeholders})
              {user_clause}
            """,
            [*unique_ids, *([user_id] if user_id is not None else [])],
        ).fetchall()

    return {str(row["source_record_id"]): str(row["generated_from_hash"]) for row in rows}


def list_existing_source_record_ids(database_path: str, ids: Iterable[str], *, user_id: str | None = None) -> set[str]:
    """Return the subset of source-record ids that already exist."""
    unique_ids = sorted({record_id for record_id in ids if record_id})

    if not unique_ids:
        return set()

    placeholders = ", ".join("?" for _ in unique_ids)
    user_clause = " AND user_id = ?" if user_id is not None else ""

    with connect(database_path) as connection:
        rows = connection.execute(
            f"SELECT id FROM source_records WHERE id IN ({placeholders}){user_clause}",
            [*unique_ids, *([user_id] if user_id is not None else [])],
        ).fetchall()

    return {str(row["id"]) for row in rows}


def list_source_records_by_ids(
    database_path: str,
    ids: Iterable[str],
    *,
    user_id: str | None = None,
) -> list[StoredSourceRecord]:
    """Return a bounded set of source records by primary key."""
    unique_ids = sorted({record_id for record_id in ids if record_id})

    if not unique_ids:
        return []

    placeholders = ", ".join("?" for _ in unique_ids)
    user_clause = " AND user_id = ?" if user_id is not None else ""

    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM source_records
            WHERE id IN ({placeholders})
              {user_clause}
              AND deleted_at IS NULL
            ORDER BY timestamp DESC, id DESC
            """,
            [*unique_ids, *([user_id] if user_id is not None else [])],
        ).fetchall()

    return [_to_source_record(row) for row in rows]


def get_gmail_sync_state(database_path: str, user_id: str) -> StoredGmailSyncState | None:
    """Load the persisted Gmail sync cursor for one user, if present."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM gmail_sync_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    return _to_gmail_sync_state(row) if row is not None else None


def upsert_gmail_sync_state(
    database_path: str,
    *,
    user_id: str,
    last_history_id: str | None,
    last_full_sync_at: str | None,
    watch_expiration_at: str | None = None,
    last_sync_started_at: str | None = None,
    last_sync_completed_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    """Insert or update the resumable Gmail sync cursor for one user."""
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO gmail_sync_state (
              user_id, last_history_id, last_full_sync_at, watch_expiration_at,
              last_sync_started_at, last_sync_completed_at, last_sync_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              last_history_id = excluded.last_history_id,
              last_full_sync_at = excluded.last_full_sync_at,
              watch_expiration_at = COALESCE(excluded.watch_expiration_at, gmail_sync_state.watch_expiration_at),
              last_sync_started_at = COALESCE(excluded.last_sync_started_at, gmail_sync_state.last_sync_started_at),
              last_sync_completed_at = COALESCE(excluded.last_sync_completed_at, gmail_sync_state.last_sync_completed_at),
              last_sync_error = excluded.last_sync_error
            """,
            (
                user_id,
                last_history_id,
                last_full_sync_at,
                watch_expiration_at,
                last_sync_started_at,
                last_sync_completed_at,
                last_sync_error,
            ),
        )


def update_gmail_sync_run_state(
    database_path: str,
    *,
    user_id: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    """Patch non-cursor sync status fields without changing the Gmail history cursor."""
    existing = get_gmail_sync_state(database_path, user_id)
    upsert_gmail_sync_state(
        database_path,
        user_id=user_id,
        last_history_id=existing.last_history_id if existing is not None else None,
        last_full_sync_at=existing.last_full_sync_at if existing is not None else None,
        watch_expiration_at=existing.watch_expiration_at if existing is not None else None,
        last_sync_started_at=started_at or (existing.last_sync_started_at if existing is not None else None),
        last_sync_completed_at=completed_at or (existing.last_sync_completed_at if existing is not None else None),
        last_sync_error=error_message,
    )


def upsert_gmail_message_snapshots(
    database_path: str,
    snapshots: Iterable[StoredGmailMessageSnapshot],
) -> None:
    """Persist Gmail message snapshots without mutating Gmail itself."""
    touched_threads_by_user: dict[str, set[str]] = {}
    with connect(database_path) as connection:
        for snapshot in snapshots:
            if snapshot.thread_id:
                touched_threads_by_user.setdefault(snapshot.user_id, set()).add(snapshot.thread_id)
            connection.execute(
                """
                INSERT INTO gmail_message_snapshots (
                  user_id, message_id, thread_id, history_id, internal_date, label_ids, raw_payload, fetch_status,
                  tombstoned, tombstoned_at, last_fetched_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, message_id) DO UPDATE SET
                  thread_id = excluded.thread_id,
                  history_id = excluded.history_id,
                  internal_date = excluded.internal_date,
                  label_ids = excluded.label_ids,
                  raw_payload = excluded.raw_payload,
                  fetch_status = excluded.fetch_status,
                  tombstoned = excluded.tombstoned,
                  tombstoned_at = excluded.tombstoned_at,
                  last_fetched_at = excluded.last_fetched_at,
                  updated_at = excluded.updated_at
                """,
                (
                    snapshot.user_id,
                    snapshot.message_id,
                    snapshot.thread_id,
                    snapshot.history_id,
                    snapshot.internal_date,
                    json.dumps(snapshot.label_ids, ensure_ascii=True),
                    json.dumps(snapshot.raw_payload, ensure_ascii=True),
                    snapshot.fetch_status,
                    1 if snapshot.tombstoned else 0,
                    snapshot.tombstoned_at,
                    snapshot.last_fetched_at,
                    snapshot.created_at,
                    snapshot.updated_at,
                ),
            )
    for user_id, thread_ids in touched_threads_by_user.items():
        refresh_gmail_thread_projections_for_threads(database_path, user_id=user_id, thread_ids=thread_ids)


def upsert_gmail_history_events(
    database_path: str,
    events: Iterable[StoredGmailHistoryEvent],
) -> None:
    """Persist Gmail History API events and apply tombstones to local snapshots."""
    touched_threads_by_user: dict[str, set[str]] = {}
    with connect(database_path) as connection:
        for event in events:
            if event.thread_id:
                touched_threads_by_user.setdefault(event.user_id, set()).add(event.thread_id)
            connection.execute(
                """
                INSERT INTO gmail_history_events (
                  id, user_id, history_id, event_type, message_id, thread_id, label_ids,
                  message_payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    event.id,
                    event.user_id,
                    event.history_id,
                    event.event_type,
                    event.message_id,
                    event.thread_id,
                    json.dumps(event.label_ids, ensure_ascii=True),
                    json.dumps(event.message_payload, ensure_ascii=True),
                    event.created_at,
                ),
            )

            if event.event_type == "messagesDeleted":
                connection.execute(
                    """
                    INSERT INTO gmail_message_snapshots (
                      user_id, message_id, thread_id, history_id, internal_date, label_ids, raw_payload,
                      fetch_status, tombstoned, tombstoned_at, last_fetched_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 1, ?, NULL, ?, ?)
                    ON CONFLICT(user_id, message_id) DO UPDATE SET
                      history_id = excluded.history_id,
                      internal_date = excluded.internal_date,
                      fetch_status = excluded.fetch_status,
                      tombstoned = 1,
                      tombstoned_at = excluded.tombstoned_at,
                      updated_at = excluded.updated_at
                    """,
                    (
                        event.user_id,
                        event.message_id,
                        event.thread_id,
                        event.history_id,
                        json.dumps(event.label_ids, ensure_ascii=True),
                        json.dumps(event.message_payload, ensure_ascii=True),
                        "deleted",
                        event.created_at,
                        event.created_at,
                        event.created_at,
                    ),
                )
    for user_id, thread_ids in touched_threads_by_user.items():
        refresh_gmail_thread_projections_for_threads(database_path, user_id=user_id, thread_ids=thread_ids)


def refresh_gmail_thread_projections_for_threads(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    thread_ids: Iterable[str] | None = None,
) -> None:
    """Rebuild fast Gmail thread rows from canonical Gmail message snapshots."""
    requested_thread_ids = {thread_id for thread_id in (thread_ids or []) if thread_id}
    with connect(database_path) as connection:
        if thread_ids is None:
            requested_thread_ids = {
                str(row["thread_id"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT thread_id
                    FROM gmail_message_snapshots
                    WHERE user_id = ? AND thread_id IS NOT NULL AND thread_id != ''
                    """,
                    (user_id,),
                ).fetchall()
            }

        for thread_id in requested_thread_ids:
            rows = connection.execute(
                """
                SELECT *
                FROM gmail_message_snapshots
                WHERE user_id = ? AND thread_id = ?
                ORDER BY COALESCE(internal_date, updated_at, created_at) DESC, message_id DESC
                """,
                (user_id, thread_id),
            ).fetchall()
            if not rows:
                connection.execute(
                    "DELETE FROM gmail_thread_projections WHERE user_id = ? AND thread_id = ?",
                    (user_id, thread_id),
                )
                continue

            projection = _build_gmail_thread_projection(thread_id, [_to_gmail_message_snapshot(row) for row in rows])
            connection.execute(
                """
                INSERT INTO gmail_thread_projections (
                  user_id, thread_id, latest_message_id, latest_received_at, latest_subject, latest_sender,
                  snippet, participants, label_ids, message_count, unread, tombstoned, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, thread_id) DO UPDATE SET
                  latest_message_id = excluded.latest_message_id,
                  latest_received_at = excluded.latest_received_at,
                  latest_subject = excluded.latest_subject,
                  latest_sender = excluded.latest_sender,
                  snippet = excluded.snippet,
                  participants = excluded.participants,
                  label_ids = excluded.label_ids,
                  message_count = excluded.message_count,
                  unread = excluded.unread,
                  tombstoned = excluded.tombstoned,
                  updated_at = excluded.updated_at
                """,
                (
                    projection.user_id,
                    projection.thread_id,
                    projection.latest_message_id,
                    projection.latest_received_at,
                    projection.latest_subject,
                    projection.latest_sender,
                    projection.snippet,
                    json.dumps(projection.participants, ensure_ascii=True),
                    json.dumps(projection.label_ids, ensure_ascii=True),
                    projection.message_count,
                    1 if projection.unread else 0,
                    1 if projection.tombstoned else 0,
                    projection.updated_at,
                ),
            )


def list_mailbox_thread_projections(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    label: str = "inbox",
    limit: int = 100,
    cursor: str | None = None,
) -> tuple[list[StoredGmailThreadProjection], int, str | None]:
    """Return mailbox thread rows filtered by Gmail label semantics."""
    bounded_limit = max(1, min(limit, 250))
    offset = _parse_cursor_offset(cursor)
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM gmail_thread_projections
            WHERE user_id = ? AND tombstoned = 0
            ORDER BY latest_received_at DESC, thread_id DESC
            """,
            (user_id,),
        ).fetchall()

    filtered = [
        projection
        for projection in (_to_gmail_thread_projection(row) for row in rows)
        if _projection_matches_mailbox_label(projection, label)
    ]
    page = filtered[offset : offset + bounded_limit]
    next_offset = offset + bounded_limit
    next_cursor = str(next_offset) if next_offset < len(filtered) else None
    return page, len(filtered), next_cursor


def list_recent_gmail_thread_projections(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    since_iso: str,
    label: str = "inbox",
    limit: int = 100,
) -> list[StoredGmailThreadProjection]:
    """Return recent Gmail thread projections for fast first-run dashboard construction."""
    bounded_limit = max(1, min(limit, 250))
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM gmail_thread_projections
            WHERE user_id = ?
              AND tombstoned = 0
              AND latest_received_at >= ?
            ORDER BY latest_received_at DESC, thread_id DESC
            LIMIT ?
            """,
            (user_id, since_iso, bounded_limit),
        ).fetchall()

    return [
        projection
        for projection in (_to_gmail_thread_projection(row) for row in rows)
        if _projection_matches_mailbox_label(projection, label)
    ]


def list_gmail_thread_message_snapshots(
    database_path: str,
    *,
    thread_id: str,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 25,
    offset: int = 0,
) -> list[StoredGmailMessageSnapshot]:
    """Return active Gmail message snapshots in chronological reading order."""
    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM gmail_message_snapshots
            WHERE user_id = ?
              AND thread_id = ?
              AND tombstoned = 0
            ORDER BY COALESCE(internal_date, updated_at, created_at) ASC, message_id ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, thread_id, bounded_limit, bounded_offset),
        ).fetchall()
    return [_to_gmail_message_snapshot(row) for row in rows]


def get_gmail_thread_message_snapshot_count(
    database_path: str,
    *,
    thread_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> int:
    """Return active message count for one Gmail thread snapshot."""
    with connect(database_path) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM gmail_message_snapshots
                WHERE user_id = ? AND thread_id = ? AND tombstoned = 0
                """,
                (user_id, thread_id),
            ).fetchone()[0]
        )


def get_gmail_thread_projection(
    database_path: str,
    *,
    thread_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> StoredGmailThreadProjection | None:
    """Return one materialized Gmail thread projection."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM gmail_thread_projections
            WHERE user_id = ? AND thread_id = ? AND tombstoned = 0
            """,
            (user_id, thread_id),
        ).fetchone()
    return _to_gmail_thread_projection(row) if row is not None else None


def list_gmail_message_snapshots(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[StoredGmailMessageSnapshot]:
    """Return Gmail message snapshots for tests and local diagnostics."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM gmail_message_snapshots
            WHERE user_id = ?
            ORDER BY message_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [_to_gmail_message_snapshot(row) for row in rows]


def list_gmail_history_events(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[StoredGmailHistoryEvent]:
    """Return Gmail history events for tests and local diagnostics."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM gmail_history_events
            WHERE user_id = ?
            ORDER BY history_id ASC, event_type ASC, message_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [_to_gmail_history_event(row) for row in rows]


def create_dashboard_import_job(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> StoredDashboardImportJob:
    """Create a queued dashboard import/preparation job."""
    now = utc_now_iso()
    job = StoredDashboardImportJob(
        id=str(uuid4()),
        user_id=user_id,
        status="queued",
        stage="queued",
        imported_count=0,
        total_count=None,
        source_records=0,
        changed_entities=0,
        refreshed_entities=0,
        result_status=None,
        error_message=None,
        created_at=now,
        started_at=None,
        stage_started_at=now,
        stage_durations={},
        completed_at=None,
        updated_at=now,
    )

    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO dashboard_import_jobs (
              id, user_id, status, stage, imported_count, total_count, source_records,
              changed_entities, refreshed_entities, result_status, error_message,
              created_at, started_at, stage_started_at, stage_durations, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.user_id,
                job.status,
                job.stage,
                job.imported_count,
                job.total_count,
                job.source_records,
                job.changed_entities,
                job.refreshed_entities,
                job.result_status,
                job.error_message,
                job.created_at,
                job.started_at,
                job.stage_started_at,
                json.dumps(job.stage_durations, sort_keys=True),
                job.completed_at,
                job.updated_at,
            ),
        )

    return job


def mark_dashboard_import_job_running(database_path: str, job_id: str) -> StoredDashboardImportJob:
    """Transition a dashboard import/preparation job to running."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = ?, started_at = COALESCE(started_at, ?), completed_at = NULL,
                stage = ?, stage_started_at = ?, stage_durations = ?,
                error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            ("running", now, "starting", now, "{}", now, job_id),
        )
    return require_dashboard_import_job(database_path, job_id)


def update_dashboard_import_job_progress(
    database_path: str,
    job_id: str,
    *,
    stage: str | None = None,
    imported_count: int | None = None,
    total_count: int | None = None,
    source_records: int | None = None,
    changed_entities: int | None = None,
    refreshed_entities: int | None = None,
) -> StoredDashboardImportJob:
    """Persist incremental dashboard import progress."""
    current = require_dashboard_import_job(database_path, job_id)
    now = utc_now_iso()
    next_stage = stage if stage is not None else current.stage
    stage_started_at = current.stage_started_at
    stage_durations = current.stage_durations
    if next_stage != current.stage:
        stage_durations = _record_stage_duration(current, now)
        stage_started_at = now
        _log_dashboard_import_stage_duration(current.stage, stage_durations.get(current.stage))
    elif stage_started_at is None:
        stage_started_at = now

    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET stage = ?, imported_count = ?, total_count = ?, source_records = ?,
                changed_entities = ?, refreshed_entities = ?, stage_started_at = ?,
                stage_durations = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_stage,
                imported_count if imported_count is not None else current.imported_count,
                total_count if total_count is not None else current.total_count,
                source_records if source_records is not None else current.source_records,
                changed_entities if changed_entities is not None else current.changed_entities,
                refreshed_entities if refreshed_entities is not None else current.refreshed_entities,
                stage_started_at,
                json.dumps(stage_durations, sort_keys=True),
                now,
                job_id,
            ),
        )
    return require_dashboard_import_job(database_path, job_id)


def mark_dashboard_import_job_succeeded(
    database_path: str,
    job_id: str,
    *,
    result_status: str,
    source_records: int,
    changed_entities: int,
    refreshed_entities: int,
) -> StoredDashboardImportJob:
    """Persist a successful dashboard import/preparation result."""
    now = utc_now_iso()
    current = require_dashboard_import_job(database_path, job_id)
    stage_durations = _record_stage_duration(current, now)
    _log_dashboard_import_stage_duration(current.stage, stage_durations.get(current.stage))
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = ?, stage = ?, imported_count = ?, source_records = ?, changed_entities = ?, refreshed_entities = ?,
                result_status = ?, error_message = NULL, stage_started_at = ?,
                stage_durations = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "succeeded",
                "completed",
                max(current.imported_count, source_records),
                source_records,
                changed_entities,
                refreshed_entities,
                result_status,
                now,
                json.dumps(stage_durations, sort_keys=True),
                now,
                now,
                job_id,
            ),
        )
    return require_dashboard_import_job(database_path, job_id)


def mark_dashboard_import_job_failed(database_path: str, job_id: str, *, error_message: str) -> StoredDashboardImportJob:
    """Persist a failed dashboard import/preparation result."""
    now = utc_now_iso()
    current = require_dashboard_import_job(database_path, job_id)
    stage_durations = _record_stage_duration(current, now)
    _log_dashboard_import_stage_duration(current.stage, stage_durations.get(current.stage))
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = ?, stage = ?, result_status = ?, error_message = ?,
                stage_started_at = ?, stage_durations = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            ("failed", "failed", "failed", error_message, now, json.dumps(stage_durations, sort_keys=True), now, now, job_id),
        )
    return require_dashboard_import_job(database_path, job_id)


def get_dashboard_import_job(database_path: str, job_id: str) -> StoredDashboardImportJob | None:
    """Load one dashboard import/preparation job by id."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM dashboard_import_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _to_dashboard_import_job(row) if row is not None else None


def require_dashboard_import_job(database_path: str, job_id: str) -> StoredDashboardImportJob:
    """Load one dashboard import/preparation job, raising when the id is invalid."""
    job = get_dashboard_import_job(database_path, job_id)
    if job is None:
        raise ValueError(f"Dashboard import job not found: {job_id}")
    return job


def get_latest_dashboard_import_job(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredDashboardImportJob | None:
    """Load the newest dashboard import/preparation job for a user."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM dashboard_import_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _to_dashboard_import_job(row) if row is not None else None


def get_active_dashboard_import_job(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredDashboardImportJob | None:
    """Load the newest queued/running import job for one user."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM dashboard_import_jobs
            WHERE user_id = ?
              AND status IN ('queued', 'running')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _to_dashboard_import_job(row) if row is not None else None


def mark_stale_dashboard_import_jobs_failed(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    stale_before: str,
    error_message: str,
) -> int:
    """Fail queued/running import jobs that have not reported progress recently."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = 'failed',
                stage = 'failed',
                result_status = 'failed',
                error_message = ?,
                completed_at = ?,
                updated_at = ?
            WHERE user_id = ?
              AND status IN ('queued', 'running')
              AND updated_at < ?
            """,
            (error_message, now, now, user_id, stale_before),
        )
        return int(cursor.rowcount)


def create_first_run_import_job(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> StoredFirstRunImportJob:
    """Create a queued first-run Gmail/dashboard setup job."""
    now = utc_now_iso()
    job = StoredFirstRunImportJob(
        id=str(uuid4()),
        user_id=user_id,
        status="queued",
        stage="queued",
        fetched_count=0,
        total_count=None,
        thread_count=0,
        dashboard_item_count=0,
        inbox_ready_at=None,
        dashboard_ready_at=None,
        full_import_started_at=None,
        full_import_completed_at=None,
        error_message=None,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO first_run_import_jobs (
              id, user_id, status, stage, fetched_count, total_count, thread_count,
              dashboard_item_count, inbox_ready_at, dashboard_ready_at,
              full_import_started_at, full_import_completed_at, error_message,
              created_at, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.user_id,
                job.status,
                job.stage,
                job.fetched_count,
                job.total_count,
                job.thread_count,
                job.dashboard_item_count,
                job.inbox_ready_at,
                job.dashboard_ready_at,
                job.full_import_started_at,
                job.full_import_completed_at,
                job.error_message,
                job.created_at,
                job.started_at,
                job.completed_at,
                job.updated_at,
            ),
        )
    return job


def mark_first_run_import_job_running(database_path: str, job_id: str) -> StoredFirstRunImportJob:
    """Transition a first-run import job to running."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE first_run_import_jobs
            SET status = 'running', stage = 'starting', started_at = COALESCE(started_at, ?),
                completed_at = NULL, error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, now, job_id),
        )
    return require_first_run_import_job(database_path, job_id)


def update_first_run_import_job_progress(
    database_path: str,
    job_id: str,
    *,
    stage: str | None = None,
    fetched_count: int | None = None,
    total_count: int | None = None,
    thread_count: int | None = None,
    dashboard_item_count: int | None = None,
    inbox_ready: bool = False,
    dashboard_ready: bool = False,
    full_import_started: bool = False,
    full_import_completed: bool = False,
) -> StoredFirstRunImportJob:
    """Persist first-run setup progress and readiness gates."""
    current = require_first_run_import_job(database_path, job_id)
    now = utc_now_iso()
    next_stage = stage if stage is not None else current.stage
    next_fetched_count = fetched_count if fetched_count is not None else current.fetched_count
    next_total_count = total_count if total_count is not None else current.total_count
    next_thread_count = thread_count if thread_count is not None else current.thread_count
    next_dashboard_item_count = (
        dashboard_item_count if dashboard_item_count is not None else current.dashboard_item_count
    )
    next_inbox_ready_at = current.inbox_ready_at or (now if inbox_ready else None)
    next_dashboard_ready_at = current.dashboard_ready_at or (now if dashboard_ready else None)
    next_full_import_started_at = current.full_import_started_at or (now if full_import_started else None)
    next_full_import_completed_at = current.full_import_completed_at or (now if full_import_completed else None)
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE first_run_import_jobs
            SET stage = ?, fetched_count = ?, total_count = ?, thread_count = ?,
                dashboard_item_count = ?,
                inbox_ready_at = ?,
                dashboard_ready_at = ?,
                full_import_started_at = ?,
                full_import_completed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_stage,
                next_fetched_count,
                next_total_count,
                next_thread_count,
                next_dashboard_item_count,
                next_inbox_ready_at,
                next_dashboard_ready_at,
                next_full_import_started_at,
                next_full_import_completed_at,
                now,
                job_id,
            ),
        )
    return require_first_run_import_job(database_path, job_id)


def mark_first_run_import_job_succeeded(database_path: str, job_id: str) -> StoredFirstRunImportJob:
    """Persist full first-run job completion."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE first_run_import_jobs
            SET status = 'succeeded', stage = 'completed', error_message = NULL,
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, job_id),
        )
    return require_first_run_import_job(database_path, job_id)


def mark_first_run_import_job_failed(database_path: str, job_id: str, *, error_message: str) -> StoredFirstRunImportJob:
    """Persist first-run job failure."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE first_run_import_jobs
            SET status = 'failed', stage = 'failed', error_message = ?,
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (error_message, now, now, job_id),
        )
    return require_first_run_import_job(database_path, job_id)


def get_first_run_import_job(database_path: str, job_id: str) -> StoredFirstRunImportJob | None:
    """Load one first-run setup job by id."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM first_run_import_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return _to_first_run_import_job(row) if row is not None else None


def require_first_run_import_job(database_path: str, job_id: str) -> StoredFirstRunImportJob:
    """Load one first-run setup job, raising when missing."""
    job = get_first_run_import_job(database_path, job_id)
    if job is None:
        raise ValueError(f"First-run import job not found: {job_id}")
    return job


def get_latest_first_run_import_job(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredFirstRunImportJob | None:
    """Load the newest first-run setup job for a user."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM first_run_import_jobs
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _to_first_run_import_job(row) if row is not None else None


def get_active_first_run_import_job(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredFirstRunImportJob | None:
    """Load the newest queued/running first-run setup job for one user."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM first_run_import_jobs
            WHERE user_id = ?
              AND status IN ('queued', 'running')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _to_first_run_import_job(row) if row is not None else None


def create_entity(database_path: str, canonical_key: str, user_id: str = DEFAULT_USER_ID) -> StoredEntity:
    """Create a new canonical entity row."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM entities WHERE canonical_key = ? AND user_id = ? LIMIT 1",
            (canonical_key, user_id),
        ).fetchone()

        if row is not None:
            return _to_entity(row)

        entity = StoredEntity(
            id=str(uuid4()),
            canonical_key=canonical_key,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )

        connection.execute(
            "INSERT INTO entities (id, user_id, canonical_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (entity.id, user_id, entity.canonical_key, entity.created_at, entity.updated_at),
        )

    return entity


def get_entity(database_path: str, entity_id: str, *, user_id: str = DEFAULT_USER_ID) -> StoredEntity | None:
    """Load one entity row without hydrating source-record members."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM entities
            WHERE id = ? AND user_id = ?
            LIMIT 1
            """,
            (entity_id, user_id),
        ).fetchone()
    return _to_entity(row) if row is not None else None


def attach_thread_to_entity(
    database_path: str,
    entity_id: str,
    source: str,
    thread_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    """Ensure a source-scoped thread belongs to one entity and bump entity freshness."""
    created_at = utc_now_iso()

    with connect(database_path) as connection:
        connection.execute(
            """
            DELETE FROM entity_thread_memberships
            WHERE user_id = ?
              AND source = ?
              AND thread_id = ?
              AND entity_id != ?
            """,
            (user_id, source, thread_id, entity_id),
        )
        connection.execute(
            """
            INSERT INTO entity_thread_memberships (id, user_id, entity_id, source, thread_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, source, thread_id) DO UPDATE SET
              user_id = excluded.user_id
            """,
            (str(uuid4()), user_id, entity_id, source, thread_id, created_at),
        )
        connection.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (created_at, entity_id),
        )


def attach_record_to_entity(database_path: str, entity_id: str, source_record_id: str) -> None:
    """Ensure a source record belongs to exactly one entity and bump entity freshness."""
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM entity_members WHERE source_record_id = ? AND entity_id != ?",
            (source_record_id, entity_id),
        )
        connection.execute(
            """
            INSERT INTO entity_members (id, entity_id, source_record_id)
            VALUES (?, ?, ?)
            ON CONFLICT(source_record_id) DO UPDATE SET entity_id = excluded.entity_id
            """,
            (str(uuid4()), entity_id, source_record_id),
        )
        connection.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (utc_now_iso(), entity_id),
        )


def merge_entities(database_path: str, target_entity_id: str, source_entity_id: str) -> None:
    """Move all memberships from one entity into another and delete the source entity."""
    if target_entity_id == source_entity_id:
        return

    updated_at = utc_now_iso()

    with connect(database_path) as connection:
        connection.execute(
            "UPDATE entity_members SET entity_id = ? WHERE entity_id = ?",
            (target_entity_id, source_entity_id),
        )
        connection.execute(
            "UPDATE entity_thread_memberships SET entity_id = ? WHERE entity_id = ?",
            (target_entity_id, source_entity_id),
        )
        connection.execute(
            "UPDATE trace_records SET entity_id = ? WHERE entity_id = ?",
            (target_entity_id, source_entity_id),
        )
        connection.execute("DELETE FROM entity_states WHERE entity_id = ?", (source_entity_id,))
        connection.execute("DELETE FROM entity_ai_suggestions WHERE entity_id = ?", (source_entity_id,))
        connection.execute("DELETE FROM entities WHERE id = ?", (source_entity_id,))
        connection.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (updated_at, target_entity_id),
        )


def upsert_entity_state(database_path: str, entity_id: str, current_state: str, due_at: str | None) -> None:
    """Insert or update the current derived state for an entity."""
    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id FROM entity_states WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        state_id = existing["id"] if existing is not None else str(uuid4())
        updated_at = utc_now_iso()
        connection.execute(
            """
            INSERT INTO entity_states (id, entity_id, current_state, due_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
              current_state = excluded.current_state,
              due_at = excluded.due_at,
              updated_at = excluded.updated_at
            """,
            (state_id, entity_id, current_state, due_at, updated_at),
        )
        connection.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (updated_at, entity_id),
        )


def upsert_ai_suggestion(
    database_path: str,
    *,
    entity_id: str,
    title: str,
    explanation: str,
    action: str,
    suggested_timing: str,
    suggested_priority: int,
    suggested_visibility: bool,
    model: str,
    generated_from_updated_at: str,
) -> None:
    """Insert or replace the cached AI judgment associated with an entity."""
    generated_at = utc_now_iso()

    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id FROM entity_ai_suggestions WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        suggestion_id = existing["id"] if existing is not None else str(uuid4())
        connection.execute(
            """
            INSERT INTO entity_ai_suggestions (
              id, entity_id, title, explanation, action, suggested_timing, suggested_priority,
              suggested_visibility, model, generated_at, generated_from_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
              title = excluded.title,
              explanation = excluded.explanation,
              action = excluded.action,
              suggested_timing = excluded.suggested_timing,
              suggested_priority = excluded.suggested_priority,
              suggested_visibility = excluded.suggested_visibility,
              model = excluded.model,
              generated_at = excluded.generated_at,
              generated_from_updated_at = excluded.generated_from_updated_at
            """,
            (
                suggestion_id,
                entity_id,
                title,
                explanation,
                action,
                suggested_timing,
                suggested_priority,
                1 if suggested_visibility else 0,
                model,
                generated_at,
                generated_from_updated_at,
            ),
        )


def append_trace_record(
    database_path: str,
    *,
    stage: str,
    input: dict[str, object],
    output: dict[str, object],
    user_id: str,
    entity_id: str | None = None,
    source_record_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Append one replayable trace event for a pipeline stage."""
    resolved_trace_id = trace_id or entity_id or source_record_id or str(uuid4())

    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO trace_records (
              id, trace_id, entity_id, source_record_id, user_id, stage, input, output, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                resolved_trace_id,
                entity_id,
                source_record_id,
                user_id,
                stage,
                json.dumps(input, ensure_ascii=True),
                json.dumps(output, ensure_ascii=True),
                utc_now_iso(),
            ),
        )


def upsert_feed_projection(
    database_path: str,
    *,
    user_id: str,
    entity_id: str,
    pipeline_output: dict[str, object],
) -> None:
    """Persist one backend-owned feed projection for a canonical entity."""
    updated_at = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO feed_projections (entity_id, user_id, pipeline_output, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
              user_id = excluded.user_id,
              pipeline_output = excluded.pipeline_output,
              updated_at = excluded.updated_at
            """,
            (
                entity_id,
                user_id,
                json.dumps(pipeline_output, ensure_ascii=True),
                updated_at,
            ),
        )


def delete_feed_projection(database_path: str, entity_id: str) -> None:
    """Remove one cached feed projection when its entity is deleted or hidden."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM feed_projections WHERE entity_id = ?", (entity_id,))


def list_feed_projection_payloads(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[dict[str, object]]:
    """Return persisted feed projection payloads for sectioning into a feed."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT pipeline_output
            FROM feed_projections
            WHERE user_id = ?
            ORDER BY updated_at DESC, entity_id ASC
            """,
            (user_id,),
        ).fetchall()

    payloads: list[dict[str, object]] = []
    for row in rows:
        payload = json.loads(row["pipeline_output"])
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def get_feed_projection_count(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> int:
    """Return how many cached feed projections are available for the user."""
    with connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM feed_projections WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
        )


def get_source_record_count(database_path: str, *, user_id: str | None = None) -> int:
    """Return the total number of active persisted source records."""
    user_clause = "user_id = ? AND " if user_id is not None else ""
    params: tuple[object, ...] = (user_id,) if user_id is not None else ()
    with connect(database_path) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM source_records WHERE {user_clause}deleted_at IS NULL",
                params,
            ).fetchone()[0]
        )


def get_history_source_record_count(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> int:
    """Return the total number of source records available to the history projection."""
    with connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM source_records WHERE user_id = ? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()[0]
        )


def list_source_record_ids(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 10000,
    offset: int = 0,
) -> list[str]:
    """Return source-record ids in history order for bounded refresh workflows."""
    bounded_limit = max(1, min(limit, 10000))
    bounded_offset = max(0, offset)

    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM source_records
            WHERE user_id = ?
              AND deleted_at IS NULL
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, bounded_limit, bounded_offset),
        ).fetchall()

    return [str(row["id"]) for row in rows]


def _record_stage_duration(job: StoredDashboardImportJob, ended_at: str) -> dict[str, float]:
    """Return updated per-stage duration totals for a job stage transition."""
    if not job.stage or job.stage in {"completed", "failed"}:
        return dict(job.stage_durations)

    started_at = job.stage_started_at or job.updated_at or job.started_at
    if started_at is None:
        return dict(job.stage_durations)

    started = _parse_iso_timestamp(started_at)
    ended = _parse_iso_timestamp(ended_at)
    if started is None or ended is None:
        return dict(job.stage_durations)

    elapsed_seconds = max(0.0, (ended - started).total_seconds())
    durations = dict(job.stage_durations)
    durations[job.stage] = round(float(durations.get(job.stage, 0.0)) + elapsed_seconds, 3)
    return durations


def _parse_iso_timestamp(value: str) -> datetime | None:
    """Parse an ISO timestamp persisted by this repository."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _log_dashboard_import_stage_duration(stage: str, duration: float | None) -> None:
    """Print lightweight import timings for local debugging."""
    if duration is None:
        return
    try:
        print(json.dumps({"dashboard_import_stage": stage, "duration_seconds": duration}, sort_keys=True))
    except BrokenPipeError:
        return


def list_history_source_records(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 100,
    offset: int = 0,
) -> list[StoredHistorySourceRecord]:
    """Page persisted source records with app-owned entity/state/suggestion/outcome enrichment."""
    bounded_limit = max(1, min(limit, 250))
    bounded_offset = max(0, offset)

    with connect(database_path) as connection:
        rows = connection.execute(
            """
            WITH paged_source_records AS (
              SELECT *
              FROM source_records
              WHERE user_id = ?
                AND deleted_at IS NULL
              ORDER BY timestamp DESC, id DESC
              LIMIT ? OFFSET ?
            ),
            latest_outcomes AS (
              SELECT *
              FROM (
                SELECT
                  entity_outcomes.*,
                  ROW_NUMBER() OVER (
                    PARTITION BY entity_id
                    ORDER BY created_at DESC, id DESC
                  ) AS row_number
                FROM entity_outcomes
                WHERE user_id = ?
              )
              WHERE row_number = 1
            )
            SELECT
              paged_source_records.*,
              entity_members.entity_id AS history_entity_id,
              source_record_summaries.summary AS history_source_summary,
              entity_states.current_state AS history_current_state,
              entity_ai_suggestions.title AS history_suggestion_title,
              entity_ai_suggestions.explanation AS history_suggestion_summary,
              latest_outcomes.outcome_type AS history_outcome_type,
              latest_outcomes.created_at AS history_outcome_created_at
            FROM paged_source_records
            LEFT JOIN source_record_summaries
              ON source_record_summaries.source_record_id = paged_source_records.id
            LEFT JOIN entity_members ON entity_members.source_record_id = paged_source_records.id
            LEFT JOIN entity_states ON entity_states.entity_id = entity_members.entity_id
            LEFT JOIN entity_ai_suggestions ON entity_ai_suggestions.entity_id = entity_members.entity_id
            LEFT JOIN latest_outcomes ON latest_outcomes.entity_id = entity_members.entity_id
            ORDER BY paged_source_records.timestamp DESC, paged_source_records.id DESC
            """,
            (user_id, bounded_limit, bounded_offset, user_id),
        ).fetchall()

    return [_to_history_source_record(row) for row in rows]


def list_gmail_history_source_records(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[StoredHistorySourceRecord]:
    """Return persisted Gmail records with app-owned entity/state enrichment for Gmail-like views."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            WITH latest_outcomes AS (
              SELECT *
              FROM (
                SELECT
                  entity_outcomes.*,
                  ROW_NUMBER() OVER (
                    PARTITION BY entity_id
                    ORDER BY created_at DESC, id DESC
                  ) AS row_number
                FROM entity_outcomes
                WHERE user_id = ?
              )
              WHERE row_number = 1
            )
            SELECT
              source_records.*,
              entity_members.entity_id AS history_entity_id,
              source_record_summaries.summary AS history_source_summary,
              entity_states.current_state AS history_current_state,
              entity_ai_suggestions.title AS history_suggestion_title,
              entity_ai_suggestions.explanation AS history_suggestion_summary,
              latest_outcomes.outcome_type AS history_outcome_type,
              latest_outcomes.created_at AS history_outcome_created_at
            FROM source_records
            LEFT JOIN source_record_summaries
              ON source_record_summaries.source_record_id = source_records.id
            LEFT JOIN entity_members ON entity_members.source_record_id = source_records.id
            LEFT JOIN entity_states ON entity_states.entity_id = entity_members.entity_id
            LEFT JOIN entity_ai_suggestions ON entity_ai_suggestions.entity_id = entity_members.entity_id
            LEFT JOIN latest_outcomes ON latest_outcomes.entity_id = entity_members.entity_id
            WHERE source_records.user_id = ?
              AND source_records.source = 'gmail'
              AND source_records.deleted_at IS NULL
            ORDER BY source_records.timestamp DESC, source_records.id DESC
            """,
            (user_id, user_id),
        ).fetchall()

    return [_to_history_source_record(row) for row in rows]


def get_entity_member_count(database_path: str) -> int:
    """Return the total number of entity membership rows."""
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM entity_members").fetchone()[0])


def list_unlinked_source_records(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[StoredSourceRecord]:
    """Return source records that have not yet been attached to any entity."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT source_records.*
            FROM source_records
            LEFT JOIN entity_members ON entity_members.source_record_id = source_records.id
            WHERE entity_members.id IS NULL
              AND source_records.user_id = ?
              AND source_records.deleted_at IS NULL
            ORDER BY timestamp ASC
            """,
            (user_id,),
        ).fetchall()
    return [_to_source_record(row) for row in rows]


def list_entities_missing_state_ids(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Return entity ids that still need state derivation."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT entities.id
            FROM entities
            LEFT JOIN entity_states ON entity_states.entity_id = entities.id
            WHERE entity_states.id IS NULL
              AND entities.user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def find_entity_by_member_record_id(
    database_path: str,
    source_record_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredEntity | None:
    """Look up the entity that already owns a given source record."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT entities.*
            FROM entities
            JOIN entity_members ON entity_members.entity_id = entities.id
            WHERE entity_members.source_record_id = ?
              AND entities.user_id = ?
            LIMIT 1
            """,
            (source_record_id, user_id),
        ).fetchone()
    return _to_entity(row) if row is not None else None


def find_entity_by_thread_id(
    database_path: str,
    source: str,
    thread_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> StoredEntity | None:
    """Find an existing entity by thread id for exact thread-level grouping."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT entities.*
            FROM entities
            JOIN entity_thread_memberships ON entity_thread_memberships.entity_id = entities.id
            WHERE entity_thread_memberships.source = ?
              AND entity_thread_memberships.thread_id = ?
              AND entities.user_id = ?
            LIMIT 1
            """,
            (source, thread_id, user_id),
        ).fetchone()

        if row is None:
            row = connection.execute(
                """
                SELECT entities.*
                FROM entities
                JOIN entity_members ON entity_members.entity_id = entities.id
                JOIN source_records ON source_records.id = entity_members.source_record_id
                WHERE source_records.source = ?
                  AND source_records.thread_id = ?
                  AND source_records.deleted_at IS NULL
                  AND source_records.user_id = ?
                  AND entities.user_id = ?
                LIMIT 1
                """,
                (source, thread_id, user_id, user_id),
            ).fetchone()
    return _to_entity(row) if row is not None else None


def list_candidate_records(
    database_path: str,
    sender_domain: str | None,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[tuple[StoredSourceRecord, StoredEntity]]:
    """Load recent candidate records to support heuristic and AI grouping."""
    query = """
        SELECT source_records.id, source_records.source, source_records.thread_id, source_records.subject,
               source_records.sender, source_records.timestamp, source_records.created_at,
               entities.id AS entity_id, entities.canonical_key, entities.created_at AS entity_created_at,
               entities.updated_at AS entity_updated_at
        FROM source_records
        JOIN entity_members ON entity_members.source_record_id = source_records.id
        JOIN entities ON entities.id = entity_members.entity_id
        WHERE source_records.subject IS NOT NULL
          AND source_records.deleted_at IS NULL
          AND source_records.user_id = ?
          AND entities.user_id = ?
    """
    params: list[str] = [user_id, user_id]

    if sender_domain:
        query += " AND source_records.sender LIKE ?"
        params.append(f"%{sender_domain}%")

    query += " ORDER BY source_records.timestamp DESC LIMIT 200"

    with connect(database_path) as connection:
        rows = connection.execute(query, params).fetchall()

    results: list[tuple[StoredSourceRecord, StoredEntity]] = []

    for row in rows:
        results.append(
            (
                StoredSourceRecord(
                    id=str(row["id"]),
                    source=str(row["source"]),
                    thread_id=row["thread_id"],
                    subject=row["subject"],
                    sender=row["sender"],
                    timestamp=str(row["timestamp"]),
                    raw_payload={
                        "subject": row["subject"] or "",
                        "from": row["sender"] or "",
                        "sender": row["sender"] or "",
                    },
                    created_at=str(row["created_at"]),
                ),
                StoredEntity(
                    id=str(row["entity_id"]),
                    canonical_key=str(row["canonical_key"]),
                    created_at=str(row["entity_created_at"]),
                    updated_at=str(row["entity_updated_at"]),
                ),
            )
        )

    return results


def get_entity_states(database_path: str, entity_ids: Iterable[str]) -> dict[str, StoredEntityState]:
    """Fetch derived state rows keyed by entity id."""
    ids = list(entity_ids)

    if not ids:
        return {}

    placeholders = ", ".join("?" for _ in ids)

    with connect(database_path) as connection:
        rows = connection.execute(
            f"SELECT * FROM entity_states WHERE entity_id IN ({placeholders})",
            ids,
        ).fetchall()

    return {str(row["entity_id"]): _to_entity_state(row) for row in rows}


def get_loaded_entity(database_path: str, entity_id: str, *, user_id: str | None = None) -> LoadedEntity | None:
    """Return one fully-loaded entity with members, state, and AI suggestion."""
    entities = list_loaded_entities(database_path, [entity_id], user_id=user_id)
    return entities[0] if entities else None


def list_entity_member_source_records_lightweight(
    database_path: str,
    entity_id: str,
    *,
    user_id: str | None = None,
) -> list[StoredSourceRecord]:
    """Return entity member records without heavy raw payload hydration."""
    user_clause = " AND source_records.user_id = ?" if user_id is not None else ""
    params: list[object] = [entity_id, user_id] if user_id is not None else [entity_id]
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT source_records.id, source_records.source, source_records.thread_id,
                   source_records.subject, source_records.sender, source_records.timestamp,
                   source_records.created_at
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
              AND source_records.deleted_at IS NULL
              {user_clause}
            ORDER BY source_records.timestamp ASC
            """,
            params,
        ).fetchall()

    return [
        StoredSourceRecord(
            id=str(row["id"]),
            source=str(row["source"]),
            thread_id=row["thread_id"],
            subject=row["subject"],
            sender=row["sender"],
            timestamp=str(row["timestamp"]),
            raw_payload={
                "subject": row["subject"] or "",
                "from": row["sender"] or "",
                "sender": row["sender"] or "",
            },
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def list_all_loaded_entities(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[LoadedEntity]:
    """Return every entity with its associated state, AI cache, and members."""
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id FROM entities WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()

    return list_loaded_entities(database_path, [str(row["id"]) for row in rows], user_id=user_id)


def list_loaded_entities(
    database_path: str,
    entity_ids: list[str],
    *,
    user_id: str | None = None,
) -> list[LoadedEntity]:
    """Hydrate multiple entities into a convenient in-memory structure."""
    if not entity_ids:
        return []

    placeholders = ", ".join("?" for _ in entity_ids)
    entity_user_clause = " AND user_id = ?" if user_id is not None else ""
    entity_params: list[object] = [*entity_ids, user_id] if user_id is not None else [*entity_ids]

    with connect(database_path) as connection:
        entity_rows = connection.execute(
            f"SELECT * FROM entities WHERE id IN ({placeholders}){entity_user_clause}",
            entity_params,
        ).fetchall()
        state_rows = connection.execute(
            f"SELECT * FROM entity_states WHERE entity_id IN ({placeholders})",
            entity_ids,
        ).fetchall()
        suggestion_rows = connection.execute(
            f"SELECT * FROM entity_ai_suggestions WHERE entity_id IN ({placeholders})",
            entity_ids,
        ).fetchall()
        thread_rows = connection.execute(
            f"""
            SELECT *
            FROM entity_thread_memberships
            WHERE entity_id IN ({placeholders})
            ORDER BY source ASC, thread_id ASC
            """,
            entity_ids,
        ).fetchall()
        member_rows = connection.execute(
            f"""
            SELECT entity_members.entity_id, source_records.*
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id IN ({placeholders})
              AND source_records.deleted_at IS NULL
            ORDER BY source_records.timestamp ASC
            """,
            entity_ids,
        ).fetchall()

    state_by_entity = {str(row["entity_id"]): _to_entity_state(row) for row in state_rows}
    suggestion_by_entity = {
        str(row["entity_id"]): _to_ai_suggestion(row) for row in suggestion_rows
    }
    thread_members_by_entity: dict[str, list[StoredEntityThreadMembership]] = {
        entity_id: [] for entity_id in entity_ids
    }
    for row in thread_rows:
        thread_members_by_entity.setdefault(str(row["entity_id"]), []).append(
            StoredEntityThreadMembership(
                id=str(row["id"]),
                entity_id=str(row["entity_id"]),
                source=str(row["source"]),
                thread_id=str(row["thread_id"]),
                created_at=str(row["created_at"]),
            )
        )
    members_by_entity: dict[str, list[StoredSourceRecord]] = {entity_id: [] for entity_id in entity_ids}

    for row in member_rows:
        members_by_entity.setdefault(str(row["entity_id"]), []).append(
            StoredSourceRecord(
                id=str(row["id"]),
                source=str(row["source"]),
                thread_id=row["thread_id"],
                subject=row["subject"],
                sender=row["sender"],
                timestamp=str(row["timestamp"]),
                raw_payload=json.loads(row["raw_payload"]),
                created_at=str(row["created_at"]),
            )
        )

    for entity_id, members in members_by_entity.items():
        if thread_members_by_entity.get(entity_id):
            continue

        deduped_threads: list[str] = []
        for member in members:
            if member.thread_id and member.thread_id not in deduped_threads:
                deduped_threads.append(member.thread_id)
                thread_members_by_entity.setdefault(entity_id, []).append(
                    StoredEntityThreadMembership(
                        id=f"inferred:{entity_id}:{member.source}:{member.thread_id}",
                        entity_id=entity_id,
                        source=member.source,
                        thread_id=member.thread_id,
                        created_at=member.created_at,
                    )
                )

    loaded = []

    for row in entity_rows:
        entity = _to_entity(row)
        loaded.append(
            LoadedEntity(
                entity=entity,
                state=state_by_entity.get(entity.id),
                ai_suggestion=suggestion_by_entity.get(entity.id),
                thread_memberships=thread_members_by_entity.get(entity.id, []),
                members=members_by_entity.get(entity.id, []),
            )
        )

    return loaded


def list_trace_records_for_entity(
    database_path: str,
    entity_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[StoredTraceRecord]:
    """Return all trace rows related to one entity or any of its member source records."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM trace_records
            WHERE entity_id = ?
              AND user_id = ?
               OR source_record_id IN (
                    SELECT source_record_id
                    FROM entity_members
                    WHERE entity_id = ?
                  )
              AND user_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (entity_id, user_id, entity_id, user_id),
        ).fetchall()

    return [_to_trace_record(row) for row in rows]


def create_manual_task(
    database_path: str,
    *,
    user_id: str,
    title: str,
    notes: str | None = None,
    section: str = "today",
    due_at: str | None = None,
) -> StoredManualTask:
    """Create a backend-owned task and its feed entity projection."""
    now = utc_now_iso()
    task_id = str(uuid4())
    entity = create_entity(database_path, f"manual-task:{task_id}", user_id=user_id)
    task = StoredManualTask(
        id=task_id,
        user_id=user_id,
        entity_id=entity.id,
        title=title,
        notes=notes,
        section=section,
        due_at=due_at,
        status="open",
        created_at=now,
        updated_at=now,
    )

    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO manual_tasks (
              id, user_id, entity_id, title, notes, section, due_at, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.user_id,
                task.entity_id,
                task.title,
                task.notes,
                task.section,
                task.due_at,
                task.status,
                task.created_at,
                task.updated_at,
            ),
        )

    _write_manual_task_projection(database_path, task)
    return task


def update_manual_task(
    database_path: str,
    task_id: str,
    *,
    user_id: str,
    title: str | None = None,
    notes: str | None = None,
    section: str | None = None,
    due_at: str | None = None,
    status: str | None = None,
) -> StoredManualTask | None:
    """Patch a backend-owned task and refresh its feed projection."""
    existing = get_manual_task(database_path, task_id, user_id=user_id)
    if existing is None:
        return None

    task = StoredManualTask(
        id=existing.id,
        user_id=existing.user_id,
        entity_id=existing.entity_id,
        title=title if title is not None else existing.title,
        notes=notes if notes is not None else existing.notes,
        section=section if section is not None else existing.section,
        due_at=due_at if due_at is not None else existing.due_at,
        status=status if status is not None else existing.status,
        created_at=existing.created_at,
        updated_at=utc_now_iso(),
    )

    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE manual_tasks
            SET title = ?, notes = ?, section = ?, due_at = ?, status = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                task.title,
                task.notes,
                task.section,
                task.due_at,
                task.status,
                task.updated_at,
                task.id,
                task.user_id,
            ),
        )

    _write_manual_task_projection(database_path, task)
    return task


def delete_manual_task(database_path: str, task_id: str, *, user_id: str) -> bool:
    """Delete a backend-owned task and suppress its entity through cascade cleanup."""
    task = get_manual_task(database_path, task_id, user_id=user_id)
    if task is None:
        return False

    with connect(database_path) as connection:
        connection.execute("DELETE FROM manual_tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        connection.execute("DELETE FROM feed_projections WHERE entity_id = ?", (task.entity_id,))
        connection.execute("DELETE FROM entities WHERE id = ?", (task.entity_id,))
    return True


def get_manual_task(database_path: str, task_id: str, *, user_id: str) -> StoredManualTask | None:
    """Load a backend-owned task by id."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM manual_tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        ).fetchone()
    return _to_manual_task(row) if row is not None else None


def get_manual_task_by_entity_id(database_path: str, entity_id: str, *, user_id: str) -> StoredManualTask | None:
    """Load a backend-owned task by entity id."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM manual_tasks WHERE entity_id = ? AND user_id = ?",
            (entity_id, user_id),
        ).fetchone()
    return _to_manual_task(row) if row is not None else None


def append_entity_outcome(
    database_path: str,
    *,
    user_id: str,
    entity_id: str,
    outcome_type: str,
    snooze_until: str | None = None,
    note: str | None = None,
) -> StoredEntityOutcome:
    """Persist one explicit user outcome against an entity."""
    outcome = StoredEntityOutcome(
        id=str(uuid4()),
        user_id=user_id,
        entity_id=entity_id,
        outcome_type=outcome_type,
        snooze_until=snooze_until,
        note=note,
        created_at=utc_now_iso(),
    )
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO entity_outcomes (id, user_id, entity_id, outcome_type, snooze_until, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.id,
                outcome.user_id,
                outcome.entity_id,
                outcome.outcome_type,
                outcome.snooze_until,
                outcome.note,
                outcome.created_at,
            ),
        )
    return outcome


def get_latest_entity_outcomes(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, StoredEntityOutcome]:
    """Return the latest outcome per entity for feed suppression."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM entity_outcomes
            WHERE user_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (user_id,),
        ).fetchall()
    latest: dict[str, StoredEntityOutcome] = {}
    for row in rows:
        outcome = _to_entity_outcome(row)
        latest[outcome.entity_id] = outcome
    return latest


def upsert_gmail_draft(database_path: str, draft: StoredGmailDraft) -> StoredGmailDraft:
    """Persist the local mapping for an explicitly-created Gmail draft."""
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO gmail_drafts (
              id, user_id, entity_id, gmail_draft_id, gmail_message_id, thread_id, to_recipients,
              cc_recipients, bcc_recipients, subject, body, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, gmail_draft_id) DO UPDATE SET
              entity_id = excluded.entity_id,
              gmail_message_id = excluded.gmail_message_id,
              thread_id = excluded.thread_id,
              to_recipients = excluded.to_recipients,
              cc_recipients = excluded.cc_recipients,
              bcc_recipients = excluded.bcc_recipients,
              subject = excluded.subject,
              body = excluded.body,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                draft.id,
                draft.user_id,
                draft.entity_id,
                draft.gmail_draft_id,
                draft.gmail_message_id,
                draft.thread_id,
                draft.to_recipients,
                draft.cc_recipients,
                draft.bcc_recipients,
                draft.subject,
                draft.body,
                draft.status,
                draft.created_at,
                draft.updated_at,
            ),
        )
    return draft


def get_gmail_draft(database_path: str, draft_id: str, *, user_id: str) -> StoredGmailDraft | None:
    """Load a Gmail draft mapping by local id or Gmail draft id."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM gmail_drafts
            WHERE user_id = ? AND (id = ? OR gmail_draft_id = ?)
            LIMIT 1
            """,
            (user_id, draft_id, draft_id),
        ).fetchone()
    return _to_gmail_draft(row) if row is not None else None


def get_source_record_count_for_entity(
    database_path: str,
    entity_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    thread_id: str | None = None,
) -> int:
    """Return the active source-record count for one entity."""
    thread_clause = "AND source_records.thread_id = ?" if thread_id is not None else ""
    params: tuple[object, ...] = (entity_id, thread_id) if thread_id is not None else (entity_id,)
    with connect(database_path) as connection:
        row = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
              AND source_records.user_id = ?
              {thread_clause}
              AND source_records.deleted_at IS NULL
            """,
            (entity_id, user_id, thread_id) if thread_id is not None else (entity_id, user_id),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def get_latest_source_record_for_entity(
    database_path: str,
    entity_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    thread_id: str | None = None,
) -> StoredSourceRecord | None:
    """Return the newest active source record for one entity."""
    thread_clause = "AND source_records.thread_id = ?" if thread_id is not None else ""
    params: tuple[object, ...] = (entity_id, thread_id) if thread_id is not None else (entity_id,)
    with connect(database_path) as connection:
        row = connection.execute(
            f"""
            SELECT source_records.*
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
              AND source_records.user_id = ?
              {thread_clause}
              AND source_records.deleted_at IS NULL
            ORDER BY source_records.timestamp DESC, source_records.id DESC
            LIMIT 1
            """,
            (entity_id, user_id, thread_id) if thread_id is not None else (entity_id, user_id),
        ).fetchone()
    return _to_source_record(row) if row is not None else None


def list_gmail_thread_ids_for_entity(
    database_path: str,
    entity_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> list[str]:
    """Return distinct active Gmail thread ids attached to one entity."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT source_records.thread_id
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
              AND source_records.user_id = ?
              AND source_records.source = 'gmail'
              AND source_records.thread_id IS NOT NULL
              AND source_records.deleted_at IS NULL
            ORDER BY source_records.thread_id ASC
            """,
            (entity_id, user_id),
        ).fetchall()
    return [str(row["thread_id"]) for row in rows if row["thread_id"] is not None]


def list_source_records_for_entity(
    database_path: str,
    entity_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    limit: int | None = None,
    offset: int = 0,
    thread_id: str | None = None,
) -> list[StoredSourceRecord]:
    """Return active source records for one entity in chronological order."""
    bounded_offset = max(0, int(offset))
    bounded_limit = None if limit is None else max(0, int(limit))
    pagination_clause = ""
    thread_clause = "AND source_records.thread_id = ?" if thread_id is not None else ""
    params: tuple[object, ...] = (entity_id, thread_id) if thread_id is not None else (entity_id,)
    if bounded_limit is not None:
        pagination_clause = "LIMIT ? OFFSET ?"
        params = (
            (entity_id, thread_id, bounded_limit, bounded_offset)
            if thread_id is not None
            else (entity_id, bounded_limit, bounded_offset)
        )

    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT source_records.*
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
              AND source_records.user_id = ?
              {thread_clause}
              AND source_records.deleted_at IS NULL
            ORDER BY source_records.timestamp ASC, source_records.id ASC
            {pagination_clause}
            """,
            (
                (entity_id, user_id, thread_id, bounded_limit, bounded_offset)
                if thread_id is not None and bounded_limit is not None
                else (entity_id, user_id, thread_id)
                if thread_id is not None
                else (entity_id, user_id, bounded_limit, bounded_offset)
                if bounded_limit is not None
                else (entity_id, user_id)
            ),
        ).fetchall()
    return [_to_source_record(row) for row in rows]


def _write_manual_task_projection(database_path: str, task: StoredManualTask) -> None:
    """Keep manual tasks visible through the same entity/feed machinery as email work."""
    status_state = "done" if task.status == "done" else "open"
    source_record = StoredSourceRecord(
        id=f"manual-task:{task.id}",
        source="manual",
        thread_id=task.id,
        subject=task.title,
        sender="manual",
        timestamp=task.updated_at,
        raw_payload={
            "user_id": task.user_id,
            "task_id": task.id,
            "subject": task.title,
            "body": task.notes or "",
            "section": task.section,
            "due_at": task.due_at,
            "status": task.status,
        },
        created_at=task.created_at,
    )
    upsert_source_records(database_path, [source_record])
    attach_record_to_entity(database_path, task.entity_id, source_record.id)
    upsert_entity_state(database_path, task.entity_id, status_state, task.due_at)
    upsert_ai_suggestion(
        database_path,
        entity_id=task.entity_id,
        title=task.title,
        explanation=task.notes or "This is a task you added directly.",
        action="none",
        suggested_timing=task.section,
        suggested_priority=70 if task.section == "now" else 50,
        suggested_visibility=task.status != "done",
        model="manual-task",
        generated_from_updated_at=utc_now_iso(),
    )


def _to_source_record(row: sqlite3.Row) -> StoredSourceRecord:
    return StoredSourceRecord(
        id=str(row["id"]),
        source=str(row["source"]),
        thread_id=row["thread_id"],
        subject=row["subject"],
        sender=row["sender"],
        timestamp=str(row["timestamp"]),
        raw_payload=json.loads(row["raw_payload"]),
        created_at=str(row["created_at"]),
        deleted_at=str(row["deleted_at"]) if "deleted_at" in row.keys() and row["deleted_at"] is not None else None,
    )


def _to_manual_task(row: sqlite3.Row) -> StoredManualTask:
    return StoredManualTask(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        entity_id=str(row["entity_id"]),
        title=str(row["title"]),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        section=str(row["section"]),
        due_at=str(row["due_at"]) if row["due_at"] is not None else None,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_entity_outcome(row: sqlite3.Row) -> StoredEntityOutcome:
    return StoredEntityOutcome(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        entity_id=str(row["entity_id"]),
        outcome_type=str(row["outcome_type"]),
        snooze_until=str(row["snooze_until"]) if row["snooze_until"] is not None else None,
        note=str(row["note"]) if row["note"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _to_gmail_draft(row: sqlite3.Row) -> StoredGmailDraft:
    return StoredGmailDraft(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        entity_id=str(row["entity_id"]) if row["entity_id"] is not None else None,
        gmail_draft_id=str(row["gmail_draft_id"]),
        gmail_message_id=str(row["gmail_message_id"]) if row["gmail_message_id"] is not None else None,
        thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
        to_recipients=str(row["to_recipients"]),
        cc_recipients=str(row["cc_recipients"]) if row["cc_recipients"] is not None else None,
        bcc_recipients=str(row["bcc_recipients"]) if row["bcc_recipients"] is not None else None,
        subject=str(row["subject"]),
        body=str(row["body"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_gmail_message_snapshot(row: sqlite3.Row) -> StoredGmailMessageSnapshot:
    return StoredGmailMessageSnapshot(
        user_id=str(row["user_id"]),
        message_id=str(row["message_id"]),
        thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
        history_id=str(row["history_id"]) if row["history_id"] is not None else None,
        internal_date=str(row["internal_date"]) if row["internal_date"] is not None else None,
        label_ids=list(json.loads(row["label_ids"])),
        raw_payload=json.loads(row["raw_payload"]),
        fetch_status=str(row["fetch_status"]),
        tombstoned=bool(row["tombstoned"]),
        tombstoned_at=str(row["tombstoned_at"]) if row["tombstoned_at"] is not None else None,
        last_fetched_at=str(row["last_fetched_at"]) if row["last_fetched_at"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_gmail_thread_projection(row: sqlite3.Row) -> StoredGmailThreadProjection:
    return StoredGmailThreadProjection(
        user_id=str(row["user_id"]),
        thread_id=str(row["thread_id"]),
        latest_message_id=str(row["latest_message_id"]),
        latest_received_at=str(row["latest_received_at"]),
        latest_subject=str(row["latest_subject"]) if row["latest_subject"] is not None else None,
        latest_sender=str(row["latest_sender"]) if row["latest_sender"] is not None else None,
        snippet=str(row["snippet"]) if row["snippet"] is not None else None,
        participants=[str(value) for value in json.loads(row["participants"])],
        label_ids=[str(value) for value in json.loads(row["label_ids"])],
        message_count=int(row["message_count"]),
        unread=bool(row["unread"]),
        tombstoned=bool(row["tombstoned"]),
        updated_at=str(row["updated_at"]),
    )


def _build_gmail_thread_projection(
    thread_id: str,
    snapshots: list[StoredGmailMessageSnapshot],
) -> StoredGmailThreadProjection:
    active_snapshots = [snapshot for snapshot in snapshots if not snapshot.tombstoned]
    ordered = active_snapshots or snapshots
    latest = ordered[0]
    latest_payload = latest.raw_payload
    label_ids = sorted({label_id for snapshot in active_snapshots for label_id in snapshot.label_ids})
    participants = _collect_gmail_snapshot_participants(ordered)
    latest_received_at = latest.internal_date or latest.last_fetched_at or latest.updated_at or latest.created_at
    return StoredGmailThreadProjection(
        user_id=latest.user_id,
        thread_id=thread_id,
        latest_message_id=latest.message_id,
        latest_received_at=latest_received_at,
        latest_subject=_string_payload(latest_payload, "subject"),
        latest_sender=_string_payload(latest_payload, "from"),
        snippet=_string_payload(latest_payload, "snippet") or _string_payload(latest_payload, "body_preview"),
        participants=participants,
        label_ids=label_ids,
        message_count=len(active_snapshots),
        unread="UNREAD" in label_ids,
        tombstoned=len(active_snapshots) == 0,
        updated_at=utc_now_iso(),
    )


def _collect_gmail_snapshot_participants(snapshots: list[StoredGmailMessageSnapshot]) -> list[str]:
    participants: list[str] = []
    for snapshot in snapshots:
        payload = snapshot.raw_payload
        values = [
            _string_payload(payload, "from"),
            _string_payload(payload, "to"),
            _string_payload(payload, "cc"),
            _string_payload(payload, "bcc"),
        ]
        for name, address in getaddresses([value for value in values if value]):
            normalized = address.strip() or name.strip()
            if normalized and normalized not in participants:
                participants.append(normalized)
            if len(participants) >= 4:
                return participants
    return participants


def _projection_matches_mailbox_label(projection: StoredGmailThreadProjection, label: str) -> bool:
    labels = set(projection.label_ids)
    normalized = label.strip().lower() or "inbox"

    if normalized == "inbox":
        return "INBOX" in labels and "TRASH" not in labels and "SPAM" not in labels
    if normalized == "sent":
        return "SENT" in labels and "TRASH" not in labels and "SPAM" not in labels
    if normalized == "drafts":
        return "DRAFT" in labels and "TRASH" not in labels
    if normalized == "trash":
        return "TRASH" in labels
    if normalized == "archive":
        return labels.isdisjoint({"INBOX", "TRASH", "SPAM", "SENT", "DRAFT"})
    if normalized == "all":
        return labels.isdisjoint({"TRASH", "SPAM", "DRAFT"})

    return "INBOX" in labels and "TRASH" not in labels and "SPAM" not in labels


def _parse_cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        parsed = int(cursor)
    except ValueError:
        return 0
    return max(0, parsed)


def _string_payload(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _to_gmail_history_event(row: sqlite3.Row) -> StoredGmailHistoryEvent:
    return StoredGmailHistoryEvent(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        history_id=str(row["history_id"]),
        event_type=str(row["event_type"]),
        message_id=str(row["message_id"]),
        thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
        label_ids=list(json.loads(row["label_ids"])),
        message_payload=json.loads(row["message_payload"]),
        created_at=str(row["created_at"]),
    )


def _to_dashboard_import_job(row: sqlite3.Row) -> StoredDashboardImportJob:
    return StoredDashboardImportJob(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        status=str(row["status"]),
        stage=str(row["stage"]),
        imported_count=int(row["imported_count"]),
        total_count=int(row["total_count"]) if row["total_count"] is not None else None,
        source_records=int(row["source_records"]),
        changed_entities=int(row["changed_entities"]),
        refreshed_entities=int(row["refreshed_entities"]),
        result_status=str(row["result_status"]) if row["result_status"] is not None else None,
        error_message=str(row["error_message"]) if row["error_message"] is not None else None,
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        stage_started_at=str(row["stage_started_at"]) if row["stage_started_at"] is not None else None,
        stage_durations=_parse_stage_durations(row["stage_durations"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
        updated_at=str(row["updated_at"]),
    )


def _to_first_run_import_job(row: sqlite3.Row) -> StoredFirstRunImportJob:
    return StoredFirstRunImportJob(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        status=str(row["status"]),
        stage=str(row["stage"]),
        fetched_count=int(row["fetched_count"]),
        total_count=int(row["total_count"]) if row["total_count"] is not None else None,
        thread_count=int(row["thread_count"]),
        dashboard_item_count=int(row["dashboard_item_count"]),
        inbox_ready_at=str(row["inbox_ready_at"]) if row["inbox_ready_at"] is not None else None,
        dashboard_ready_at=str(row["dashboard_ready_at"]) if row["dashboard_ready_at"] is not None else None,
        full_import_started_at=str(row["full_import_started_at"]) if row["full_import_started_at"] is not None else None,
        full_import_completed_at=str(row["full_import_completed_at"]) if row["full_import_completed_at"] is not None else None,
        error_message=str(row["error_message"]) if row["error_message"] is not None else None,
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
        updated_at=str(row["updated_at"]),
    )


def _to_history_source_record(row: sqlite3.Row) -> StoredHistorySourceRecord:
    return StoredHistorySourceRecord(
        source_record=_to_source_record(row),
        entity_id=str(row["history_entity_id"]) if row["history_entity_id"] is not None else None,
        source_summary=str(row["history_source_summary"]) if row["history_source_summary"] is not None else None,
        current_state=str(row["history_current_state"]) if row["history_current_state"] is not None else None,
        suggestion_title=str(row["history_suggestion_title"])
        if row["history_suggestion_title"] is not None
        else None,
        suggestion_summary=str(row["history_suggestion_summary"])
        if row["history_suggestion_summary"] is not None
        else None,
        outcome_type=str(row["history_outcome_type"]) if row["history_outcome_type"] is not None else None,
        outcome_created_at=str(row["history_outcome_created_at"])
        if row["history_outcome_created_at"] is not None
        else None,
    )


def _parse_stage_durations(value: object) -> dict[str, float]:
    if not isinstance(value, str) or not value:
        return {}

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}

    if not isinstance(decoded, dict):
        return {}

    durations: dict[str, float] = {}
    for key, raw_duration in decoded.items():
        if not isinstance(key, str):
            continue
        if isinstance(raw_duration, (int, float)):
            durations[key] = float(raw_duration)
    return durations


def _to_user(row: sqlite3.Row) -> StoredUser:
    return StoredUser(
        id=str(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]) if row["display_name"] is not None else None,
        google_sub=str(row["google_sub"]),
        access_enabled=bool(row["access_enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_app_session(row: sqlite3.Row) -> StoredAppSession:
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


def _to_oauth_login_session(row: sqlite3.Row) -> StoredOAuthLoginSession:
    return StoredOAuthLoginSession(
        state=str(row["state"]),
        code_verifier=str(row["code_verifier"]),
        redirect_to=str(row["redirect_to"]) if row["redirect_to"] is not None else None,
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
    )


def _to_mobile_login_code(row: sqlite3.Row) -> StoredMobileLoginCode:
    return StoredMobileLoginCode(
        code_hash=str(row["code_hash"]),
        user_id=str(row["user_id"]),
        expires_at=str(row["expires_at"]),
        consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _to_google_oauth_token(row: sqlite3.Row) -> StoredGoogleOAuthToken:
    return StoredGoogleOAuthToken(
        user_id=str(row["user_id"]),
        token_json_encrypted=str(row["token_json_encrypted"]),
        updated_at=str(row["updated_at"]),
    )


def _to_dashboard_briefing(row: sqlite3.Row) -> StoredDashboardBriefing:
    return StoredDashboardBriefing(
        user_id=str(row["user_id"]),
        payload=json.loads(row["payload"]),
        updated_at=str(row["updated_at"]),
    )


def _to_entity(row: sqlite3.Row) -> StoredEntity:
    return StoredEntity(
        id=str(row["id"]),
        canonical_key=str(row["canonical_key"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _to_entity_state(row: sqlite3.Row) -> StoredEntityState:
    return StoredEntityState(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        current_state=str(row["current_state"]),
        due_at=row["due_at"],
        updated_at=str(row["updated_at"]),
    )


def _to_ai_suggestion(row: sqlite3.Row) -> StoredEntityAiSuggestion:
    return StoredEntityAiSuggestion(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        title=str(row["title"]),
        explanation=str(row["explanation"]),
        action=str(row["action"]),
        suggested_timing=str(row["suggested_timing"]),
        suggested_priority=int(row["suggested_priority"]),
        suggested_visibility=bool(row["suggested_visibility"]),
        model=str(row["model"]),
        generated_at=str(row["generated_at"]),
        generated_from_updated_at=str(row["generated_from_updated_at"]),
    )


def _to_trace_record(row: sqlite3.Row) -> StoredTraceRecord:
    return StoredTraceRecord(
        id=str(row["id"]),
        trace_id=str(row["trace_id"]),
        entity_id=str(row["entity_id"]) if row["entity_id"] is not None else None,
        source_record_id=str(row["source_record_id"]) if row["source_record_id"] is not None else None,
        user_id=str(row["user_id"]),
        stage=str(row["stage"]),
        input=json.loads(row["input"]),
        output=json.loads(row["output"]),
        created_at=str(row["created_at"]),
    )


def _to_gmail_sync_state(row: sqlite3.Row) -> StoredGmailSyncState:
    return StoredGmailSyncState(
        user_id=str(row["user_id"]),
        last_history_id=str(row["last_history_id"]) if row["last_history_id"] is not None else None,
        last_full_sync_at=str(row["last_full_sync_at"]) if row["last_full_sync_at"] is not None else None,
        watch_expiration_at=str(row["watch_expiration_at"]) if row["watch_expiration_at"] is not None else None,
        last_sync_started_at=str(row["last_sync_started_at"]) if row["last_sync_started_at"] is not None else None,
        last_sync_completed_at=str(row["last_sync_completed_at"]) if row["last_sync_completed_at"] is not None else None,
        last_sync_error=str(row["last_sync_error"]) if row["last_sync_error"] is not None else None,
    )
