from __future__ import annotations

"""Small dataclass models representing persisted SQLite rows and joins."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredSourceRecord:
    """Raw normalized source record stored in SQLite."""

    id: str
    source: str
    thread_id: str | None
    subject: str | None
    sender: str | None
    timestamp: str
    raw_payload: dict[str, Any]
    created_at: str


@dataclass
class StoredEntity:
    """Canonical grouped entity that multiple source records can attach to."""

    id: str
    canonical_key: str
    created_at: str
    updated_at: str


@dataclass
class StoredEntityState:
    """Current derived lifecycle state for an entity."""

    id: str
    entity_id: str
    current_state: str
    due_at: str | None
    updated_at: str


@dataclass
class StoredEntityAiSuggestion:
    """Cached AI judgment snapshot for an entity."""

    id: str
    entity_id: str
    title: str
    explanation: str
    action: str
    suggested_timing: str
    suggested_priority: int
    suggested_visibility: bool
    model: str
    generated_at: str
    generated_from_updated_at: str


@dataclass
class LoadedEntity:
    """Convenient in-memory view of an entity plus state, AI suggestion, and members."""

    entity: StoredEntity
    state: StoredEntityState | None
    ai_suggestion: StoredEntityAiSuggestion | None
    members: list[StoredSourceRecord] = field(default_factory=list)


@dataclass
class StoredTraceRecord:
    """Persisted trace row for replaying pipeline decisions and transitions."""

    id: str
    trace_id: str
    entity_id: str | None
    source_record_id: str | None
    user_id: str
    stage: str
    input: dict[str, Any]
    output: dict[str, Any]
    created_at: str
