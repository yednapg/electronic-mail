from __future__ import annotations

"""Core domain models shared across ingest, memory, and feed projection."""

from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal["gmail", "calendar"]
NeedType = Literal["decision", "awareness"]
ActionType = Literal["inline", "external", "none"]
EffortLevel = Literal["quick", "deep"]
TimingBand = Literal["now", "today", "later", "hidden"]
ActionConfidence = Literal["high", "medium", "low"]
LifecycleState = Literal["active", "scheduled", "resolved", "suppressed"]
EntityCurrentState = Literal["open", "waiting", "done"]
GmailThreadAction = Literal["archive", "unarchive"]
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
