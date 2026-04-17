from __future__ import annotations

"""SQLite persistence helpers for source records, entities, state, and AI cache."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterator, Iterable
from uuid import uuid4

from app.db.models import (
    LoadedEntity,
    StoredEntity,
    StoredEntityAiSuggestion,
    StoredEntityState,
    StoredGmailSyncState,
    StoredSourceRecord,
    StoredEntityThreadMembership,
    StoredTraceRecord,
)


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
              source TEXT NOT NULL,
              thread_id TEXT,
              subject TEXT,
              sender TEXT,
              timestamp TEXT NOT NULL,
              raw_payload TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
              id TEXT PRIMARY KEY,
              canonical_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entity_members (
              id TEXT PRIMARY KEY,
              entity_id TEXT NOT NULL,
              source_record_id TEXT NOT NULL UNIQUE,
              FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE,
              FOREIGN KEY(source_record_id) REFERENCES source_records(id) ON DELETE CASCADE,
              UNIQUE(entity_id, source_record_id)
            );

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

            CREATE TABLE IF NOT EXISTS gmail_sync_state (
              user_id TEXT PRIMARY KEY,
              last_history_id TEXT,
              last_full_sync_at TEXT
            );
            """
        )


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
        connection.execute("DELETE FROM trace_records")
        connection.execute("DELETE FROM entity_ai_suggestions")
        connection.execute("DELETE FROM entity_members")
        connection.execute("DELETE FROM entity_thread_memberships")
        connection.execute("DELETE FROM entity_states")
        connection.execute("DELETE FROM entities")
        connection.execute("DELETE FROM source_records")
        connection.execute("DELETE FROM gmail_sync_state")


def clear_derived_memory(database_path: str) -> None:
    """Remove derived entity memory while preserving synced source records."""
    with connect(database_path) as connection:
        connection.execute("DELETE FROM trace_records")
        connection.execute("DELETE FROM entity_ai_suggestions")
        connection.execute("DELETE FROM entity_members")
        connection.execute("DELETE FROM entity_thread_memberships")
        connection.execute("DELETE FROM entity_states")
        connection.execute("DELETE FROM entities")


def upsert_source_records(database_path: str, records: Iterable[StoredSourceRecord]) -> None:
    """Persist normalized source records, updating existing rows by record id."""
    with connect(database_path) as connection:
        for record in records:
            connection.execute(
                """
                INSERT INTO source_records (id, source, thread_id, subject, sender, timestamp, raw_payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source = excluded.source,
                  thread_id = excluded.thread_id,
                  subject = excluded.subject,
                  sender = excluded.sender,
                  timestamp = excluded.timestamp,
                  raw_payload = excluded.raw_payload
                """,
                (
                    record.id,
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


def create_entity(database_path: str, canonical_key: str) -> StoredEntity:
    """Create a new canonical entity row."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM entities WHERE canonical_key = ? LIMIT 1",
            (canonical_key,),
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
            "INSERT INTO entities (id, canonical_key, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (entity.id, entity.canonical_key, entity.created_at, entity.updated_at),
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


def get_source_record_count(database_path: str) -> int:
    """Return the total number of persisted source records."""
    with connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0])


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
