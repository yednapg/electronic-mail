from __future__ import annotations

"""Core domain models shared across ingest, memory, and feed projection."""

from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal["gmail", "calendar", "manual"]
NeedType = Literal["decision", "awareness"]
ActionType = Literal["inline", "external", "none"]
EffortLevel = Literal["quick", "deep"]
TimingBand = Literal["now", "today", "later", "hidden"]
ActionConfidence = Literal["high", "medium", "low"]
LifecycleState = Literal["active", "scheduled", "resolved", "suppressed"]
EntityCurrentState = Literal["open", "waiting", "done"]
GmailThreadAction = Literal["archive", "unarchive", "mark_read"]
DashboardImportJobStatus = Literal["queued", "running", "succeeded", "failed"]
TraceStage = Literal[
    "ingestion",
    "normalization",
    "grouping",
    "state_derivation",
    "decision",
    "action_selection",
    "timing",
    "ranking",
    "output",
    "lifecycle_transition",
]


class SourceRecord(BaseModel):
    """Normalized source record entering the backend pipeline."""

    id: str
    user_id: str
    source: SourceType
    thread_id: str
    raw_payload: dict[str, Any]
    received_at: str


class AttentionItemDetail(BaseModel):
    """Human-facing detail copy shared by web and iOS clients."""

    body: list[str] = Field(default_factory=list)
    action_label: str
    source_label: str


class AttentionItem(BaseModel):
    """User-facing feed item returned to the frontend."""

    id: str
    entity_id: str
    user_id: str
    need_type: NeedType
    action_type: ActionType
    effort_level: EffortLevel
    timing_band: TimingBand
    action_confidence: ActionConfidence
    primary_action: str
    fallback_action: str
    title: str
    why_this_is_here: str
    detail: AttentionItemDetail | None = None
    due_at: str | None = None
    importance_level: Literal["high", "medium", "low"] | None = None
    lifecycle_state: str | None = None
    current_state: EntityCurrentState | None = None
    source: SourceType | None = None
    gmail_thread_id: str | None = None
    gmail_thread_action: GmailThreadAction | None = None
    trace_id: str
    created_at: str


class PipelineEntity(BaseModel):
    """Canonical entity shape used during feed construction."""

    id: str
    user_id: str
    source: SourceType | None = None
    current_state: EntityCurrentState
    due_at: str | None
    importance: bool
    lifecycle_state: LifecycleState
    created_at: str
    updated_at: str
    thread_id: str | None = None
    group_id: str | None = None


class PipelineOutput(BaseModel):
    """Per-entity pipeline result before sectioning and ranking."""

    entity: PipelineEntity
    attention_item: AttentionItem | None
    suppressed: bool
    suppression_reason: str | None


class FeedResponse(BaseModel):
    """Final sectioned response consumed by the web dashboard."""

    now: list[AttentionItem] = Field(default_factory=list)
    today: list[AttentionItem] = Field(default_factory=list)
    worth_knowing: list[AttentionItem] = Field(default_factory=list)


class GoogleAuthState(BaseModel):
    """Resolved Google connection status for the local app."""

    available: bool
    connected: bool
    connect_url: str | None = None


class DashboardProfile(BaseModel):
    """Identity information resolved from the connected Google account."""

    email: str | None = None
    display_name: str | None = None


class DashboardBriefing(BaseModel):
    """Natural-language top summary for the dashboard."""

    headline: str
    brief: str


class DashboardResponse(BaseModel):
    """Full dashboard payload consumed by the web app."""

    auth: GoogleAuthState
    profile: DashboardProfile | None = None
    briefing: DashboardBriefing | None = None
    feed: FeedResponse = Field(default_factory=FeedResponse)


class DashboardImportJobResponse(BaseModel):
    """Durable status for backend-owned dashboard import/preparation."""

    id: str
    user_id: str
    status: DashboardImportJobStatus
    stage: str
    imported_count: int
    total_count: int | None = None
    source_records: int
    changed_entities: int
    refreshed_entities: int
    result_status: str | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class TraceRecord(BaseModel):
    """Replayable trace event emitted by one stage of the backend pipeline."""

    id: str
    trace_id: str
    entity_id: str | None = None
    source_record_id: str | None = None
    user_id: str
    stage: TraceStage
    input: dict[str, Any]
    output: dict[str, Any]
    created_at: str


class TraceReplayResponse(BaseModel):
    """Full trace payload for replaying one entity from member records to output."""

    entity_id: str
    source_record_ids: list[str] = Field(default_factory=list)
    items: list[TraceRecord] = Field(default_factory=list)


class TaskCreateRequest(BaseModel):
    """Create a backend-owned manual task."""

    title: str
    notes: str | None = None
    section: Literal["now", "today", "later"] = "today"
    due_at: str | None = None


class TaskUpdateRequest(BaseModel):
    """Patch a backend-owned manual task."""

    title: str | None = None
    notes: str | None = None
    section: Literal["now", "today", "later"] | None = None
    due_at: str | None = None
    status: Literal["open", "done"] | None = None


class TaskResponse(BaseModel):
    """Backend-owned manual task response."""

    id: str
    user_id: str
    entity_id: str
    title: str
    notes: str | None = None
    section: Literal["now", "today", "later"]
    due_at: str | None = None
    status: Literal["open", "done"]
    created_at: str
    updated_at: str


class EntityOutcomeRequest(BaseModel):
    """Explicit app-state outcome for one entity."""

    note: str | None = None


class EntitySnoozeRequest(EntityOutcomeRequest):
    """Snooze an entity until a backend-owned timestamp."""

    snooze_until: str


class EntityOutcomeResponse(BaseModel):
    """Persisted entity outcome."""

    id: str
    user_id: str
    entity_id: str
    outcome_type: Literal["complete", "snooze", "dismiss"]
    snooze_until: str | None = None
    note: str | None = None
    created_at: str


class GmailDraftRequest(BaseModel):
    """Create or update an explicit Gmail draft."""

    to: str
    cc: str | None = None
    bcc: str | None = None
    subject: str
    body: str
    entity_id: str | None = None
    thread_id: str | None = None


class GmailDraftResponse(BaseModel):
    """Local/backend representation of a Gmail draft."""

    id: str
    user_id: str
    entity_id: str | None = None
    gmail_draft_id: str
    gmail_message_id: str | None = None
    thread_id: str | None = None
    to: str
    cc: str | None = None
    bcc: str | None = None
    subject: str
    body: str
    status: Literal["draft", "sent", "deleted"]
    created_at: str
    updated_at: str


class GmailThreadMutationResponse(BaseModel):
    """Response payload for explicit Gmail thread mutations."""

    thread_id: str
    action: GmailThreadAction


class ThreadMessage(BaseModel):
    """One message in a backend-owned thread reader payload."""

    id: str
    source: SourceType
    thread_id: str | None = None
    from_address: str | None = None
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str
    snippet: str | None = None
    label_ids: list[str] = Field(default_factory=list)
    received_at: str


class ThreadReaderResponse(BaseModel):
    """Thread reader payload shared by web and iOS."""

    entity_id: str
    user_id: str
    source: SourceType | None = None
    gmail_thread_id: str | None = None
    subject: str | None = None
    total_messages: int
    limit: int
    offset: int
    has_more: bool
    messages: list[ThreadMessage] = Field(default_factory=list)


class HistoryRow(BaseModel):
    """One compact historical line backed by a persisted source record."""

    source_record_id: str
    entity_id: str | None = None
    source: SourceType
    thread_id: str | None = None
    received_at: str
    subject: str | None = None
    title: str | None = None
    sender: str | None = None
    snippet: str | None = None
    summary: str | None = None
    current_state: EntityCurrentState | None = None
    lifecycle_state: LifecycleState | None = None
    outcome_type: Literal["complete", "snooze", "dismiss"] | None = None
    outcome_created_at: str | None = None


class HistoryDayGroup(BaseModel):
    """History rows for one calendar day."""

    date: str
    rows: list[HistoryRow] = Field(default_factory=list)


class HistoryMonthGroup(BaseModel):
    """History days for one calendar month."""

    month: str
    days: list[HistoryDayGroup] = Field(default_factory=list)


class HistoryYearGroup(BaseModel):
    """History months for one calendar year."""

    year: str
    months: list[HistoryMonthGroup] = Field(default_factory=list)


class HistoryResponse(BaseModel):
    """Paginated persisted-history projection grouped for the history UI."""

    limit: int
    offset: int
    total: int
    years: list[HistoryYearGroup] = Field(default_factory=list)
