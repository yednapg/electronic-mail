from __future__ import annotations

"""HTTP wrappers around the in-process AI service layer."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
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
from app.services.auth import require_current_user
from app.services.ai.decision import (
    decide_entities,
    describe_calendar_context,
    judge_feed_entities,
    resolve_entity_group,
)


router = APIRouter()
settings = load_settings()


@router.post("/decide", response_model=DecideResponse)
def decide(http_request: Request, request: DecideRequest) -> DecideResponse:
    """Turn normalized entities into decision items."""
    require_current_user(settings, http_request)
    return DecideResponse(items=decide_entities(request.entities))


@router.post("/calendar-context", response_model=CalendarContextResponse)
def calendar_context(http_request: Request, request: CalendarContextRequest) -> CalendarContextResponse:
    """Generate dashboard-friendly copy for calendar rows."""
    require_current_user(settings, http_request)
    return CalendarContextResponse(items=describe_calendar_context(request.items))


@router.post("/judge-feed-entities", response_model=FeedEntityJudgmentResponse)
def judge_feed_items(http_request: Request, request: FeedEntityJudgmentRequest) -> FeedEntityJudgmentResponse:
    """Judge which persisted entities should appear in the user feed."""
    require_current_user(settings, http_request)
    return FeedEntityJudgmentResponse(items=judge_feed_entities(request.entities))


@router.post("/resolve-entity-group", response_model=EntityGroupingResponse)
def resolve_group(http_request: Request, request: EntityGroupingRequest) -> EntityGroupingResponse:
    """Decide whether a new record should attach to an existing entity."""
    require_current_user(settings, http_request)
    return resolve_entity_group(request)
