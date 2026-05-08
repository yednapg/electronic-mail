from __future__ import annotations

"""Feed endpoint that orchestrates sync, memory hydration, and projection."""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.config import load_settings
from app.schemas.domain import FeedResponse, SourceRecord
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


router = APIRouter()
settings = load_settings()


@router.get("/feed", response_model=FeedResponse)
def feed() -> FeedResponse:
    """Build the current feed from persisted memory and optional Google sync."""
    source_records = []

    if settings.google_configured and has_stored_google_tokens():
        source_records = fetch_google_source_records(settings)

    changed_entity_ids = hydrate_persistent_memory(str(settings.database_path), source_records)
    refresh_ai_suggestions_for_entities(str(settings.database_path), changed_entity_ids)
    current_time = datetime.now(timezone.utc).isoformat()
    refresh_feed_projections_for_entities(str(settings.database_path), changed_entity_ids, current_time)
    return build_feed_from_projection_cache(str(settings.database_path)) or build_feed_from_entities(
        str(settings.database_path),
        current_time,
        record_trace=False,
    )


@router.get("/raw-feed", response_model=list[SourceRecord])
def raw_feed() -> list[SourceRecord]:
    """Return the current raw Gmail source records exactly as they enter the pipeline."""
    if not settings.google_configured or not has_stored_google_tokens():
        return []

    return fetch_raw_gmail_source_records(settings)


@router.get("/raw-gmail-api")
def raw_gmail_api() -> list[dict[str, object]]:
    """Return the raw Gmail API message payloads before normalization."""
    if not settings.google_configured or not has_stored_google_tokens():
        return []

    return fetch_raw_gmail_api_messages(settings)


@router.get("/raw-gmail-api-clean")
def raw_gmail_api_clean() -> list[dict[str, object]]:
    """Return a readable Gmail API debugging view with decoded body text and part summaries."""
    if not settings.google_configured or not has_stored_google_tokens():
        return []

    return fetch_clean_gmail_api_messages(settings)


@router.post("/rebuild-memory", response_model=FeedResponse)
def rebuild_memory() -> FeedResponse:
    """Rebuild entity memory from persisted source records without re-syncing Gmail."""
    changed_entity_ids = rebuild_persistent_memory(str(settings.database_path))
    refresh_ai_suggestions_for_entities(str(settings.database_path), changed_entity_ids)
    current_time = datetime.now(timezone.utc).isoformat()
    refresh_feed_projections_for_entities(str(settings.database_path), changed_entity_ids, current_time)
    return build_feed_from_projection_cache(str(settings.database_path)) or build_feed_from_entities(
        str(settings.database_path),
        current_time,
        record_trace=False,
    )
