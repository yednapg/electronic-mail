from __future__ import annotations

"""Feed endpoint that orchestrates sync, memory hydration, and projection."""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.config import load_settings
from app.schemas.domain import FeedResponse
from app.services.feed.memory_pipeline import (
    build_feed_from_entities,
    hydrate_persistent_memory,
    refresh_ai_suggestions_for_entities,
)
from app.services.integrations.google import fetch_google_source_records, has_stored_google_tokens


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
    return build_feed_from_entities(str(settings.database_path), datetime.now(timezone.utc).isoformat())
