from __future__ import annotations

"""Backend dashboard assembly for auth, feed, and natural-language briefing."""

from datetime import datetime, timezone

from app.core.config import Settings
from app.schemas.domain import DashboardBriefing, DashboardProfile, DashboardResponse, FeedResponse
from app.services.ai.decision import generate_dashboard_briefing
from app.services.feed.memory_pipeline import (
    build_feed_from_entities,
    hydrate_persistent_memory,
    refresh_ai_suggestions_for_entities,
)
from app.services.integrations.google import (
    fetch_google_account_profile,
    fetch_google_source_records,
    get_google_auth_state,
)


def build_dashboard_response(settings: Settings) -> DashboardResponse:
    """Return the full dashboard payload for the current local user."""
    auth = get_google_auth_state(settings)

    if not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())

    source_records = fetch_google_source_records(settings)
    changed_entity_ids = hydrate_persistent_memory(str(settings.database_path), source_records)
    refresh_ai_suggestions_for_entities(str(settings.database_path), changed_entity_ids)
    feed = build_feed_from_entities(str(settings.database_path), datetime.now(timezone.utc).isoformat())
    profile = fetch_google_account_profile(settings)
    briefing = generate_dashboard_briefing(feed, profile)

    resolved_profile = _merge_profile(profile, briefing)
    return DashboardResponse(auth=auth, profile=resolved_profile, briefing=briefing, feed=feed)


def _merge_profile(
    profile: DashboardProfile | None,
    briefing: DashboardBriefing,
) -> DashboardProfile | None:
    """Backfill a display name from the generated briefing when available."""
    if profile is None:
        return None

    display_name = profile.display_name or _extract_display_name_from_headline(briefing.headline)
    if display_name == profile.display_name:
        return profile

    return DashboardProfile(email=profile.email, display_name=display_name)


def _extract_display_name_from_headline(headline: str) -> str | None:
    """Pull a simple display name out of a greeting headline when present."""
    if "," not in headline:
        return None

    _, suffix = headline.split(",", 1)
    candidate = suffix.strip().rstrip(".")
    return candidate or None
