from __future__ import annotations

"""Feed endpoint that orchestrates sync, memory hydration, and projection."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.db.repository import list_source_record_ids
from app.schemas.domain import FeedResponse, SourceRecord
from app.services.auth import require_current_user
from app.services.feed.memory_pipeline import (
    build_feed_from_projection_cache,
    build_feed_from_entities,
    hydrate_persistent_memory,
    rebuild_persistent_memory,
    refresh_feed_projections_for_entities,
    refresh_ai_suggestions_for_entities,
)
from app.services.integrations.google import (
    fetch_clean_gmail_api_messages,
    fetch_google_source_records,
    fetch_raw_gmail_api_messages,
    fetch_raw_gmail_source_records,
    has_stored_google_tokens,
)
from app.services.source_record_summaries import refresh_source_record_summaries


router = APIRouter()
settings = load_settings()


@router.get("/feed", response_model=FeedResponse)
def feed(request: Request) -> FeedResponse:
    """Build the current feed from persisted memory and optional Google sync."""
    user = require_current_user(settings, request)
    source_records = []

    if settings.google_configured:
        source_records = fetch_google_source_records(settings, user_id=user.id)

    refresh_source_record_summaries(str(settings.database_path), [record.id for record in source_records], user_id=user.id)
    changed_entity_ids = hydrate_persistent_memory(str(settings.database_path), source_records, user_id=user.id)
    refresh_ai_suggestions_for_entities(str(settings.database_path), changed_entity_ids, user_id=user.id)
    current_time = datetime.now(timezone.utc).isoformat()
    refresh_feed_projections_for_entities(str(settings.database_path), changed_entity_ids, current_time, user_id=user.id)
    return build_feed_from_projection_cache(str(settings.database_path), user_id=user.id) or build_feed_from_entities(
        str(settings.database_path),
        current_time,
        user_id=user.id,
        record_trace=False,
    )


@router.get("/raw-feed", response_model=list[SourceRecord])
def raw_feed(request: Request) -> list[SourceRecord]:
    """Return the current raw Gmail source records exactly as they enter the pipeline."""
    if not settings.google_configured:
        return []
    if getattr(settings, "app_env", "local") == "local" and not has_stored_google_tokens():
        return []
    user = require_current_user(settings, request)

    return fetch_raw_gmail_source_records(settings, user_id=user.id)


@router.get("/raw-gmail-api")
def raw_gmail_api(request: Request) -> list[dict[str, object]]:
    """Return the raw Gmail API message payloads before normalization."""
    if not settings.google_configured:
        return []
    if getattr(settings, "app_env", "local") == "local" and not has_stored_google_tokens():
        return []
    user = require_current_user(settings, request)

    return fetch_raw_gmail_api_messages(settings, user_id=user.id)


@router.get("/raw-gmail-api-clean")
def raw_gmail_api_clean(request: Request) -> list[dict[str, object]]:
    """Return a readable Gmail API debugging view with decoded body text and part summaries."""
    if not settings.google_configured:
        return []
    if getattr(settings, "app_env", "local") == "local" and not has_stored_google_tokens():
        return []
    user = require_current_user(settings, request)

    return fetch_clean_gmail_api_messages(settings, user_id=user.id)


@router.post("/rebuild-memory", response_model=FeedResponse)
def rebuild_memory(request: Request) -> FeedResponse:
    """Rebuild entity memory from persisted source records without re-syncing Gmail."""
    user = require_current_user(settings, request)
    refresh_source_record_summaries(
        str(settings.database_path),
        list_source_record_ids(str(settings.database_path), user_id=user.id),
        user_id=user.id,
    )
    changed_entity_ids = rebuild_persistent_memory(str(settings.database_path), user_id=user.id)
    refresh_ai_suggestions_for_entities(str(settings.database_path), changed_entity_ids, user_id=user.id)
    current_time = datetime.now(timezone.utc).isoformat()
    refresh_feed_projections_for_entities(str(settings.database_path), changed_entity_ids, current_time, user_id=user.id)
    return build_feed_from_projection_cache(str(settings.database_path), user_id=user.id) or build_feed_from_entities(
        str(settings.database_path),
        current_time,
        user_id=user.id,
        record_trace=False,
    )
