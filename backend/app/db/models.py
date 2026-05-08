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
class StoredEntityThreadMembership:
    """Explicit entity-to-thread membership row for source-scoped thread identity."""

    id: str
    entity_id: str
    source: str
    thread_id: str
    created_at: str


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
    thread_memberships: list[StoredEntityThreadMembership] = field(default_factory=list)
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


@dataclass
class StoredGmailSyncState:
    """Persisted Gmail mailbox sync cursor for resumable ingestion."""

    user_id: str
    last_history_id: str | None
    last_full_sync_at: str | None


@dataclass
class StoredDashboardImportJob:
    """Durable backend-owned dashboard import/preparation job status."""

    id: str
    user_id: str
    status: str
    source_records: int
    changed_entities: int
    refreshed_entities: int
    result_status: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass
class StoredManualTask:
    """Backend-owned task that is not backed by Gmail."""

    id: str
    user_id: str
    entity_id: str
    title: str
    notes: str | None
    section: str
    due_at: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass
class StoredEntityOutcome:
    """Durable user action applied to one entity."""

    id: str
    user_id: str
    entity_id: str
    outcome_type: str
    snooze_until: str | None
    note: str | None
    created_at: str


@dataclass
class StoredGmailDraft:
    """Local mapping for an explicit Gmail draft action."""

    id: str
    user_id: str
    entity_id: str | None
    gmail_draft_id: str
    gmail_message_id: str | None
    thread_id: str | None
    to_recipients: str
    cc_recipients: str | None
    bcc_recipients: str | None
    subject: str
    body: str
    status: str
    created_at: str
    updated_at: str
