from __future__ import annotations

"""SQLite persistence helpers for source records, entities, state, and AI cache."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterator, Iterable
from uuid import uuid4

from app.db.models import (
    StoredDashboardImportJob,
    StoredEntityOutcome,
    LoadedEntity,
    StoredGmailDraft,
    StoredGmailHistoryEvent,
    StoredGmailMessageSnapshot,
    StoredHistorySourceRecord,
    StoredEntity,
    StoredEntityAiSuggestion,
    StoredEntityState,
    StoredGmailSyncState,
    StoredManualTask,
    StoredSourceRecord,
    StoredEntityThreadMembership,
    StoredTraceRecord,
)


DEFAULT_USER_ID = "google-dev-user"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def initialize_database(database_path: str) -> None:
    """Create the SQLite schema if it does not already exist."""
    with connect(database_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS source_records (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'google-dev-user',
              source TEXT NOT NULL,
              thread_id TEXT,
              subject TEXT,
              sender TEXT,
              timestamp TEXT NOT NULL,
              raw_payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_source_records_timestamp
              ON source_records(timestamp DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_source_records_user_timestamp
              ON source_records(user_id, timestamp DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_source_records_thread
              ON source_records(source, thread_id);

            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL DEFAULT 'google-dev-user',
              canonical_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
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
              entity_id TEXT NOT NULL,
              source TEXT NOT NULL,
              thread_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
              UNIQUE(source, thread_id),
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
              last_full_sync_at TEXT
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
              completed_at TEXT,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dashboard_import_jobs_latest
              ON dashboard_import_jobs(user_id, created_at DESC);

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
              gmail_draft_id TEXT NOT NULL UNIQUE,
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
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE SET NULL
            );
            """
        )
        _ensure_column(connection, "source_records", "user_id", "TEXT NOT NULL DEFAULT 'google-dev-user'")
        _ensure_column(connection, "entities", "user_id", "TEXT NOT NULL DEFAULT 'google-dev-user'")
        _ensure_column(connection, "gmail_message_snapshots", "internal_date", "TEXT")
        _ensure_column(connection, "dashboard_import_jobs", "stage", "TEXT NOT NULL DEFAULT 'queued'")
        _ensure_column(connection, "dashboard_import_jobs", "imported_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "dashboard_import_jobs", "total_count", "INTEGER")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_internal_date
              ON gmail_message_snapshots(user_id, internal_date)
            """
        )


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column to older local SQLite databases when needed."""
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if any(str(row["name"]) == column for row in rows):
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@contextmanager
def connect(database_path: str) -> Iterator[sqlite3.Connection]:
    """Open a transaction-scoped SQLite connection with row access by column name."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def clear_all_data(database_path: str) -> None:
    """Remove all persisted runtime data while preserving the schema."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM gmail_history_events")
        connection.execute("DELETE FROM gmail_message_snapshots")
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
        connection.execute("DELETE FROM source_records")
        connection.execute("DELETE FROM gmail_sync_state")
        connection.execute("DELETE FROM dashboard_import_jobs")


def clear_derived_memory(database_path: str) -> None:
    """Remove derived source memory while preserving synced records and manual tasks."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM trace_records")
        connection.execute("DELETE FROM feed_projections WHERE entity_id NOT IN (SELECT entity_id FROM manual_tasks)")
        connection.execute(
            "DELETE FROM entity_ai_suggestions WHERE entity_id NOT IN (SELECT entity_id FROM manual_tasks)"
        )
        connection.execute("DELETE FROM entity_members WHERE entity_id NOT IN (SELECT entity_id FROM manual_tasks)")
        connection.execute(
            "DELETE FROM entity_thread_memberships WHERE entity_id NOT IN (SELECT entity_id FROM manual_tasks)"
        )
        connection.execute("DELETE FROM entity_states WHERE entity_id NOT IN (SELECT entity_id FROM manual_tasks)")
        connection.execute("DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM manual_tasks)")


def upsert_source_records(database_path: str, records: Iterable[StoredSourceRecord]) -> None:
    """Persist normalized source records, updating existing rows by record id."""
    with connect(database_path) as connection:
        for record in records:
            connection.execute(
                """
                INSERT INTO source_records (
                  id, user_id, source, thread_id, subject, sender, timestamp, raw_payload, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  user_id = excluded.user_id,
                  source = excluded.source,
                  thread_id = excluded.thread_id,
                  subject = excluded.subject,
                  sender = excluded.sender,
                  timestamp = excluded.timestamp,
                  raw_payload = excluded.raw_payload
                """,
                (
                    record.id,
                    record.raw_payload.get("user_id") if isinstance(record.raw_payload.get("user_id"), str) else DEFAULT_USER_ID,
                    record.source,
                    record.thread_id,
                    record.subject,
                    record.sender,
                    record.timestamp,
                    json.dumps(record.raw_payload, ensure_ascii=True),
                    record.created_at,
                ),
            )


def list_existing_source_record_ids(database_path: str, ids: Iterable[str]) -> set[str]:
    """Return the subset of source-record ids that already exist in SQLite."""
    unique_ids = sorted({record_id for record_id in ids if record_id})

    if not unique_ids:
        return set()

    placeholders = ", ".join("?" for _ in unique_ids)

    with connect(database_path) as connection:
        rows = connection.execute(
            f"SELECT id FROM source_records WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()

    return {str(row["id"]) for row in rows}


def list_source_records_by_ids(database_path: str, ids: Iterable[str]) -> list[StoredSourceRecord]:
    """Return a bounded set of source records by primary key."""
    unique_ids = sorted({record_id for record_id in ids if record_id})

    if not unique_ids:
        return []

    placeholders = ", ".join("?" for _ in unique_ids)

    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM source_records
            WHERE id IN ({placeholders})
            ORDER BY timestamp DESC, id DESC
            """,
            unique_ids,
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
) -> None:
    """Insert or update the resumable Gmail sync cursor for one user."""
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO gmail_sync_state (user_id, last_history_id, last_full_sync_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              last_history_id = excluded.last_history_id,
              last_full_sync_at = excluded.last_full_sync_at
            """,
            (user_id, last_history_id, last_full_sync_at),
        )


def upsert_gmail_message_snapshots(
    database_path: str,
    snapshots: Iterable[StoredGmailMessageSnapshot],
) -> None:
    """Persist Gmail message snapshots without mutating Gmail itself."""
    with connect(database_path) as connection:
        for snapshot in snapshots:
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


def upsert_gmail_history_events(
    database_path: str,
    events: Iterable[StoredGmailHistoryEvent],
) -> None:
    """Persist Gmail History API events and apply tombstones to local snapshots."""
    with connect(database_path) as connection:
        for event in events:
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
        completed_at=None,
        updated_at=now,
    )

    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO dashboard_import_jobs (
              id, user_id, status, stage, imported_count, total_count, source_records,
              changed_entities, refreshed_entities, result_status, error_message,
              created_at, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                stage = ?, error_message = NULL, updated_at = ?
            WHERE id = ?
            """,
            ("running", now, "starting", now, job_id),
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
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET stage = ?, imported_count = ?, total_count = ?, source_records = ?,
                changed_entities = ?, refreshed_entities = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                stage if stage is not None else current.stage,
                imported_count if imported_count is not None else current.imported_count,
                total_count if total_count is not None else current.total_count,
                source_records if source_records is not None else current.source_records,
                changed_entities if changed_entities is not None else current.changed_entities,
                refreshed_entities if refreshed_entities is not None else current.refreshed_entities,
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
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = ?, stage = ?, imported_count = ?, source_records = ?, changed_entities = ?, refreshed_entities = ?,
                result_status = ?, error_message = NULL, completed_at = ?, updated_at = ?
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
                now,
                job_id,
            ),
        )
    return require_dashboard_import_job(database_path, job_id)


def mark_dashboard_import_job_failed(database_path: str, job_id: str, *, error_message: str) -> StoredDashboardImportJob:
    """Persist a failed dashboard import/preparation result."""
    now = utc_now_iso()
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE dashboard_import_jobs
            SET status = ?, stage = ?, result_status = ?, error_message = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            ("failed", "failed", "failed", error_message, now, now, job_id),
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


def attach_thread_to_entity(database_path: str, entity_id: str, source: str, thread_id: str) -> None:
    """Ensure a source-scoped thread belongs to one entity and bump entity freshness."""
    created_at = utc_now_iso()

    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO entity_thread_memberships (id, entity_id, source, thread_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, thread_id) DO UPDATE SET
              entity_id = excluded.entity_id
            """,
            (str(uuid4()), entity_id, source, thread_id, created_at),
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


def get_source_record_count(database_path: str) -> int:
    """Return the total number of persisted source records."""
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0])


def get_history_source_record_count(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> int:
    """Return the total number of source records available to the history projection."""
    with connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM source_records WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
        )


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
              entity_states.current_state AS history_current_state,
              entity_ai_suggestions.title AS history_suggestion_title,
              entity_ai_suggestions.explanation AS history_suggestion_summary,
              latest_outcomes.outcome_type AS history_outcome_type,
              latest_outcomes.created_at AS history_outcome_created_at
            FROM paged_source_records
            LEFT JOIN entity_members ON entity_members.source_record_id = paged_source_records.id
            LEFT JOIN entity_states ON entity_states.entity_id = entity_members.entity_id
            LEFT JOIN entity_ai_suggestions ON entity_ai_suggestions.entity_id = entity_members.entity_id
            LEFT JOIN latest_outcomes ON latest_outcomes.entity_id = entity_members.entity_id
            ORDER BY paged_source_records.timestamp DESC, paged_source_records.id DESC
            """,
            (user_id, bounded_limit, bounded_offset, user_id),
        ).fetchall()

    return [
        StoredHistorySourceRecord(
            source_record=_to_source_record(row),
            entity_id=str(row["history_entity_id"]) if row["history_entity_id"] is not None else None,
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
        for row in rows
    ]


def get_entity_member_count(database_path: str) -> int:
    """Return the total number of entity membership rows."""
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM entity_members").fetchone()[0])


def list_unlinked_source_records(database_path: str) -> list[StoredSourceRecord]:
    """Return source records that have not yet been attached to any entity."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT source_records.*
            FROM source_records
            LEFT JOIN entity_members ON entity_members.source_record_id = source_records.id
            WHERE entity_members.id IS NULL
            ORDER BY timestamp ASC
            """
        ).fetchall()
    return [_to_source_record(row) for row in rows]


def list_entities_missing_state_ids(database_path: str) -> list[str]:
    """Return entity ids that still need state derivation."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT entities.id
            FROM entities
            LEFT JOIN entity_states ON entity_states.entity_id = entities.id
            WHERE entity_states.id IS NULL
            """
        ).fetchall()
    return [str(row["id"]) for row in rows]


def find_entity_by_member_record_id(database_path: str, source_record_id: str) -> StoredEntity | None:
    """Look up the entity that already owns a given source record."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT entities.*
            FROM entities
            JOIN entity_members ON entity_members.entity_id = entities.id
            WHERE entity_members.source_record_id = ?
            LIMIT 1
            """,
            (source_record_id,),
        ).fetchone()
    return _to_entity(row) if row is not None else None


def find_entity_by_thread_id(database_path: str, source: str, thread_id: str) -> StoredEntity | None:
    """Find an existing entity by thread id for exact thread-level grouping."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT entities.*
            FROM entities
            JOIN entity_thread_memberships ON entity_thread_memberships.entity_id = entities.id
            WHERE entity_thread_memberships.source = ?
              AND entity_thread_memberships.thread_id = ?
            LIMIT 1
            """,
            (source, thread_id),
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
                LIMIT 1
                """,
                (source, thread_id),
            ).fetchone()
    return _to_entity(row) if row is not None else None


def list_candidate_records(database_path: str, sender_domain: str | None) -> list[tuple[StoredSourceRecord, StoredEntity]]:
    """Load recent candidate records to support heuristic and AI grouping."""
    query = """
        SELECT source_records.*, entities.id AS entity_id, entities.canonical_key, entities.created_at AS entity_created_at,
               entities.updated_at AS entity_updated_at
        FROM source_records
        JOIN entity_members ON entity_members.source_record_id = source_records.id
        JOIN entities ON entities.id = entity_members.entity_id
        WHERE source_records.subject IS NOT NULL
    """
    params: list[str] = []

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
                    raw_payload=json.loads(row["raw_payload"]),
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


def get_loaded_entity(database_path: str, entity_id: str) -> LoadedEntity | None:
    """Return one fully-loaded entity with members, state, and AI suggestion."""
    entities = list_loaded_entities(database_path, [entity_id])
    return entities[0] if entities else None


def list_all_loaded_entities(database_path: str) -> list[LoadedEntity]:
    """Return every entity with its associated state, AI cache, and members."""
    with connect(database_path) as connection:
        rows = connection.execute("SELECT id FROM entities ORDER BY created_at ASC").fetchall()

    return list_loaded_entities(database_path, [str(row["id"]) for row in rows])


def list_loaded_entities(database_path: str, entity_ids: list[str]) -> list[LoadedEntity]:
    """Hydrate multiple entities into a convenient in-memory structure."""
    if not entity_ids:
        return []

    placeholders = ", ".join("?" for _ in entity_ids)

    with connect(database_path) as connection:
        entity_rows = connection.execute(
            f"SELECT * FROM entities WHERE id IN ({placeholders})",
            entity_ids,
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


def list_trace_records_for_entity(database_path: str, entity_id: str) -> list[StoredTraceRecord]:
    """Return all trace rows related to one entity or any of its member source records."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM trace_records
            WHERE entity_id = ?
               OR source_record_id IN (
                    SELECT source_record_id
                    FROM entity_members
                    WHERE entity_id = ?
                  )
            ORDER BY created_at ASC, id ASC
            """,
            (entity_id, entity_id),
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
            ON CONFLICT(gmail_draft_id) DO UPDATE SET
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


def list_source_records_for_entity(database_path: str, entity_id: str) -> list[StoredSourceRecord]:
    """Return source records for one entity in chronological order."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT source_records.*
            FROM entity_members
            JOIN source_records ON source_records.id = entity_members.source_record_id
            WHERE entity_members.entity_id = ?
            ORDER BY source_records.timestamp ASC, source_records.id ASC
            """,
            (entity_id,),
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
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
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
    )
