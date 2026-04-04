from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SourceType = Literal["gmail", "calendar"]
TimingBand = Literal["now", "today", "later", "hidden"]
ImportanceLevel = Literal["high", "medium", "low"]
ActionConfidence = Literal["high", "medium", "low"]


class EntityInput(BaseModel):
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
