from __future__ import annotations

"""HTTP wrappers around the in-process AI service layer."""

from fastapi import APIRouter

from app.schemas.ai import (
    CalendarContextRequest,
    CalendarContextResponse,
    DecideRequest,
    DecideResponse,
    EntityGroupingRequest,
    EntityGroupingResponse,
    FeedEntityJudgmentRequest,
    FeedEntityJudgmentResponse,
)
from app.services.ai.decision import (
    decide_entities,
    describe_calendar_context,
    judge_feed_entities,
    resolve_entity_group,
)


router = APIRouter()


@router.post("/decide", response_model=DecideResponse)
def decide(request: DecideRequest) -> DecideResponse:
    """Turn normalized entities into decision items."""
    return DecideResponse(items=decide_entities(request.entities))


@router.post("/calendar-context", response_model=CalendarContextResponse)
def calendar_context(request: CalendarContextRequest) -> CalendarContextResponse:
    """Generate dashboard-friendly copy for calendar rows."""
    return CalendarContextResponse(items=describe_calendar_context(request.items))


@router.post("/judge-feed-entities", response_model=FeedEntityJudgmentResponse)
def judge_feed_items(request: FeedEntityJudgmentRequest) -> FeedEntityJudgmentResponse:
    """Judge which persisted entities should appear in the user feed."""
    return FeedEntityJudgmentResponse(items=judge_feed_entities(request.entities))


@router.post("/resolve-entity-group", response_model=EntityGroupingResponse)
def resolve_group(request: EntityGroupingRequest) -> EntityGroupingResponse:
    """Decide whether a new record should attach to an existing entity."""
    return resolve_entity_group(request)
