from __future__ import annotations

"""Pydantic request/response contracts for the AI-facing routes and services."""

from typing import Literal

from pydantic import BaseModel, Field


SourceType = Literal["gmail", "calendar"]
TimingBand = Literal["now", "today", "later", "hidden"]
ImportanceLevel = Literal["high", "medium", "low"]
ActionConfidence = Literal["high", "medium", "low"]


class EntityInput(BaseModel):
    """Compact entity payload used for one-shot decisioning."""

    id: str
    source: SourceType
    subject: str
    body: str
    sender: str
    participants: list[str] = Field(default_factory=list)
    timestamp: str
    due_at: str | None = None
    thread_summary: str | None = None


class DecisionOutput(BaseModel):
    """Action judgment returned by the decisioning service."""

    id: str
    is_decision: bool
    title: str
    why_this_is_here: str
    primary_action: str
    timing_band: TimingBand
    importance_level: ImportanceLevel
    action_confidence: ActionConfidence


class DecideRequest(BaseModel):
    entities: list[EntityInput]


class DecideResponse(BaseModel):
    items: list[DecisionOutput]


class CalendarContextInput(BaseModel):
    """Calendar context used to generate one natural dashboard sentence."""

    id: str
    subject: str
    participants: list[str] = Field(default_factory=list)
    timing_band: TimingBand
    day_phrase: str
    time_phrase: str | None = None


class CalendarContextOutput(BaseModel):
    id: str
    why_this_is_here: str


class CalendarContextRequest(BaseModel):
    items: list[CalendarContextInput]


class CalendarContextResponse(BaseModel):
    items: list[CalendarContextOutput]


class FeedTimelineEntry(BaseModel):
    id: str
    source: SourceType
    subject: str
    sender: str | None = None
    timestamp: str
    body_snippet: str | None = None
    thread_id: str | None = None


class FeedEntityContextInput(BaseModel):
    """Persisted entity memory sent to the feed-judgment layer."""

    id: str
    source: SourceType
    current_state: str
    due_at: str | None = None
    latest_subject: str
    entity_summary: str = ""
    latest_sender: str | None = None
    latest_timestamp: str
    participants: list[str] = Field(default_factory=list)
    sender_domains: list[str] = Field(default_factory=list)
    record_count: int
    reminder_count: int = 0
    lifecycle_hints: list[str] = Field(default_factory=list)
    timeline: list[FeedTimelineEntry] = Field(default_factory=list)


class FeedEntityJudgmentOutput(BaseModel):
    """Judgment signal returned for a persisted entity."""

    id: str
    title: str
    explanation: str
    action: str
    suggested_timing: TimingBand
    suggested_priority: int
    suggested_visibility: bool


class FeedEntityJudgmentRequest(BaseModel):
    entities: list[FeedEntityContextInput]


class FeedEntityJudgmentResponse(BaseModel):
    items: list[FeedEntityJudgmentOutput]


class DashboardBriefingItemInput(BaseModel):
    """Compact feed item context used for top-of-dashboard summarization."""

    title: str
    why_this_is_here: str
    source: SourceType | None = None
    timing_band: TimingBand
    primary_action: str
    due_at: str | None = None


class DashboardBriefingInput(BaseModel):
    """Inputs used to generate the dashboard headline and briefing copy."""

    current_time: str
    account_email: str | None = None
    meeting_count: int
    task_count: int
    reply_count: int
    payment_count: int
    free_after_label: str
    items: list[DashboardBriefingItemInput] = Field(default_factory=list)


class DashboardBriefingOutput(BaseModel):
    """Generated dashboard summary copy."""

    display_name: str | None = None
    headline: str
    brief: str


class DashboardBriefingResponse(BaseModel):
    items: list[DashboardBriefingOutput]


class EntityGroupingCandidateInput(BaseModel):
    """Existing entity candidate considered during record grouping."""

    entity_id: str
    canonical_key: str
    latest_subject: str
    latest_sender: str | None = None
    current_state: str | None = None
    summary: str


class EntityGroupingRequest(BaseModel):
    subject: str
    snippet: str
    candidates: list[EntityGroupingCandidateInput] = Field(default_factory=list)


class EntityGroupingResponse(BaseModel):
    entity_id: str | None = None
    confidence: float
