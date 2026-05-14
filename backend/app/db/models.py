from __future__ import annotations

"""Small dataclass models representing persisted backend rows and joins."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredUser:
    """Signed-in app user resolved from Google OAuth."""

    id: str
    email: str
    display_name: str | None
    google_sub: str
    access_enabled: bool
    created_at: str
    updated_at: str


@dataclass
class StoredAppSession:
    """Opaque app session used by web cookies and iOS bearer auth."""

    id: str
    user_id: str
    token_hash: str
    platform: str
    expires_at: str
    revoked_at: str | None
    last_seen_at: str | None
    created_at: str


@dataclass
class StoredOAuthLoginSession:
    """PKCE OAuth session keyed by Google state."""

    state: str
    code_verifier: str
    redirect_to: str | None
    expires_at: str
    created_at: str


@dataclass
class StoredMobileLoginCode:
    """One-time login code exchanged by iOS after OAuth callback."""

    code_hash: str
    user_id: str
    expires_at: str
    consumed_at: str | None
    created_at: str


@dataclass
class StoredGoogleOAuthToken:
    """Encrypted Google OAuth credentials for one app user."""

    user_id: str
    token_json_encrypted: str
    updated_at: str


@dataclass
class StoredDashboardBriefing:
    """Cached dashboard briefing payload for one app user."""

    user_id: str
    payload: dict[str, Any]
    updated_at: str


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
    deleted_at: str | None = None


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
    watch_expiration_at: str | None = None
    last_sync_started_at: str | None = None
    last_sync_completed_at: str | None = None
    last_sync_error: str | None = None


@dataclass
class StoredGmailMessageSnapshot:
    """Durable local snapshot of a Gmail message and label state."""

    user_id: str
    message_id: str
    thread_id: str | None
    history_id: str | None
    internal_date: str | None
    label_ids: list[str]
    raw_payload: dict[str, Any]
    fetch_status: str
    tombstoned: bool
    tombstoned_at: str | None
    last_fetched_at: str | None
    created_at: str
    updated_at: str


@dataclass
class StoredGmailHistoryEvent:
    """Durable Gmail History API event captured before message hydration."""

    id: str
    user_id: str
    history_id: str
    event_type: str
    message_id: str
    thread_id: str | None
    label_ids: list[str]
    message_payload: dict[str, Any]
    created_at: str


@dataclass
class StoredGmailThreadProjection:
    """Fast thread-level mailbox row derived from Gmail message snapshots."""

    user_id: str
    thread_id: str
    latest_message_id: str
    latest_received_at: str
    latest_subject: str | None
    latest_sender: str | None
    snippet: str | None
    participants: list[str]
    label_ids: list[str]
    message_count: int
    unread: bool
    tombstoned: bool
    updated_at: str


@dataclass
class StoredSourceRecordSummary:
    """Generated compact summary for one persisted source record."""

    source_record_id: str
    user_id: str
    summary: str
    model: str
    generated_from_hash: str
    generated_at: str


@dataclass
class StoredDashboardImportJob:
    """Durable backend-owned dashboard import/preparation job status."""

    id: str
    user_id: str
    status: str
    stage: str
    imported_count: int
    total_count: int | None
    source_records: int
    changed_entities: int
    refreshed_entities: int
    result_status: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    stage_started_at: str | None
    stage_durations: dict[str, float]
    completed_at: str | None
    updated_at: str


@dataclass
class StoredFirstRunImportJob:
    """Durable first-run Gmail plus fast-dashboard setup job status."""

    id: str
    user_id: str
    status: str
    stage: str
    fetched_count: int
    total_count: int | None
    thread_count: int
    dashboard_item_count: int
    inbox_ready_at: str | None
    dashboard_ready_at: str | None
    full_import_started_at: str | None
    full_import_completed_at: str | None
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


@dataclass
class StoredHistorySourceRecord:
    """Read-only history projection row assembled from persisted backend state."""

    source_record: StoredSourceRecord
    entity_id: str | None
    source_summary: str | None
    current_state: str | None
    suggestion_title: str | None
    suggestion_summary: str | None
    outcome_type: str | None
    outcome_created_at: str | None
