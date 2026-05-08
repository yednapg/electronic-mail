from __future__ import annotations

"""Backend dashboard assembly for auth, feed, and natural-language briefing."""

from datetime import datetime, timezone
import json
from pathlib import Path

from app.core.config import Settings
from app.db.models import StoredDashboardImportJob
from app.db.repository import (
    DEFAULT_USER_ID,
    create_dashboard_import_job,
    get_dashboard_import_job,
    get_latest_dashboard_import_job,
    list_all_loaded_entities,
    mark_dashboard_import_job_failed,
    mark_dashboard_import_job_running,
    mark_dashboard_import_job_succeeded,
    update_dashboard_import_job_progress,
)
from app.schemas.domain import (
    DashboardBriefing,
    DashboardImportJobResponse,
    DashboardProfile,
    DashboardResponse,
    FeedResponse,
)
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
    load_google_account_profile,
)


def build_dashboard_response(settings: Settings) -> DashboardResponse:
    """Return the full dashboard payload for the current local user."""
    auth = get_google_auth_state(settings)

    if not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())

    feed = build_feed_from_entities(str(settings.database_path), datetime.now(timezone.utc).isoformat())
    profile = load_google_account_profile()
    briefing = load_dashboard_briefing_cache(settings) or generate_dashboard_briefing(feed, profile)

    resolved_profile = _merge_profile(profile, briefing)
    return DashboardResponse(auth=auth, profile=resolved_profile, briefing=briefing, feed=feed)


def create_queued_dashboard_import_job(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> DashboardImportJobResponse:
    """Create a queued backend-owned dashboard import/preparation job."""
    job = create_dashboard_import_job(str(settings.database_path), user_id=user_id)
    return to_dashboard_import_job_response(job)


def start_dashboard_import_job(settings: Settings, *, user_id: str = DEFAULT_USER_ID) -> DashboardImportJobResponse:
    """Create and run a backend-owned dashboard import/preparation job inline."""
    job = create_dashboard_import_job(str(settings.database_path), user_id=user_id)
    return to_dashboard_import_job_response(run_dashboard_import_job(settings, job.id))


def run_dashboard_import_job(settings: Settings, job_id: str) -> StoredDashboardImportJob:
    """Run a queued dashboard import/preparation job and persist the final status."""
    mark_dashboard_import_job_running(str(settings.database_path), job_id)

    try:
        result = _prepare_dashboard_state(settings, job_id=job_id)
    except Exception as exc:
        return mark_dashboard_import_job_failed(str(settings.database_path), job_id, error_message=str(exc))

    return mark_dashboard_import_job_succeeded(
        str(settings.database_path),
        job_id,
        result_status=str(result["status"]),
        source_records=int(result["source_records"]),
        changed_entities=int(result["changed_entities"]),
        refreshed_entities=int(result.get("refreshed_entities", 0)),
    )


def get_dashboard_import_job_status(settings: Settings, job_id: str) -> DashboardImportJobResponse | None:
    """Return persisted status for one dashboard import/preparation job."""
    job = get_dashboard_import_job(str(settings.database_path), job_id)
    return to_dashboard_import_job_response(job) if job is not None else None


def get_latest_dashboard_import_job_status(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> DashboardImportJobResponse | None:
    """Return the newest persisted dashboard import/preparation job status."""
    job = get_latest_dashboard_import_job(str(settings.database_path), user_id=user_id)
    return to_dashboard_import_job_response(job) if job is not None else None


def prepare_dashboard_state(settings: Settings) -> dict[str, object]:
    """Compatibility wrapper for explicit dashboard preparation."""
    job = start_dashboard_import_job(settings)
    response: dict[str, object] = {
        "status": job.result_status or job.status,
        "source_records": job.source_records,
        "changed_entities": job.changed_entities,
        "refreshed_entities": job.refreshed_entities,
        "job_id": job.id,
        "job_status": job.status,
        "job_stage": job.stage,
        "imported_count": job.imported_count,
        "total_count": job.total_count,
    }
    if job.error_message:
        response["error_message"] = job.error_message
    return response


def to_dashboard_import_job_response(job: StoredDashboardImportJob) -> DashboardImportJobResponse:
    """Convert a persisted dashboard import/preparation job to an API schema."""
    return DashboardImportJobResponse(
        id=job.id,
        user_id=job.user_id,
        status=job.status,
        stage=job.stage,
        imported_count=job.imported_count,
        total_count=job.total_count,
        source_records=job.source_records,
        changed_entities=job.changed_entities,
        refreshed_entities=job.refreshed_entities,
        result_status=job.result_status,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )


def _prepare_dashboard_state(settings: Settings, *, job_id: str | None = None) -> dict[str, object]:
    """Run the explicit Gmail/Calendar and AI prep before the dashboard is shown."""
    database_path = str(settings.database_path)
    latest_imported_count = 0

    def update_progress(
        stage: str,
        imported_count: int | None = None,
        total_count: int | None = None,
        **counts: int,
    ) -> None:
        nonlocal latest_imported_count
        if imported_count is not None:
            latest_imported_count = imported_count
        if job_id is None:
            return
        update_dashboard_import_job_progress(
            database_path,
            job_id,
            stage=stage,
            imported_count=imported_count,
            total_count=total_count,
            **counts,
        )

    auth = get_google_auth_state(settings)
    if not auth.connected:
        update_progress("not_connected", imported_count=0, total_count=0)
        return {
            "status": "not_connected",
            "source_records": 0,
            "changed_entities": 0,
            "refreshed_entities": 0,
        }

    update_progress("gmail_sync", imported_count=0)
    source_records = fetch_google_source_records(
        settings,
        progress_callback=lambda stage, imported_count, total_count: update_progress(
            stage,
            imported_count=imported_count,
            total_count=total_count,
            source_records=imported_count,
        ),
        collect_records=False,
    )
    imported_count = latest_imported_count + len(source_records)
    update_progress("memory_hydration", imported_count=imported_count, source_records=imported_count)
    changed_entity_ids = hydrate_persistent_memory(database_path, source_records or None)
    update_progress("ai_refresh", changed_entities=len(changed_entity_ids))
    refresh_entity_ids = [
        entity.entity.id
        for entity in list_all_loaded_entities(database_path)
    ]
    refresh_ai_suggestions_for_entities(database_path, refresh_entity_ids)
    update_progress("feed_build", refreshed_entities=len(refresh_entity_ids))
    feed = build_feed_from_entities(database_path, datetime.now(timezone.utc).isoformat())
    profile = load_google_account_profile() or fetch_google_account_profile(settings)
    update_progress("briefing", refreshed_entities=len(refresh_entity_ids))
    save_dashboard_briefing_cache(settings, generate_dashboard_briefing(feed, profile))
    return {
        "status": "ready",
        "source_records": imported_count,
        "changed_entities": len(changed_entity_ids),
        "refreshed_entities": len(refresh_entity_ids),
    }


def load_dashboard_briefing_cache(settings: Settings) -> DashboardBriefing | None:
    """Load the last prepared dashboard summary from backend-owned state."""
    cache_path = _dashboard_briefing_cache_path(settings)
    if not cache_path.exists():
        return None

    try:
        return DashboardBriefing.model_validate(json.loads(cache_path.read_text()))
    except Exception:
        return None


def save_dashboard_briefing_cache(settings: Settings, briefing: DashboardBriefing) -> None:
    """Persist the prepared dashboard summary for fast dashboard rendering."""
    cache_path = _dashboard_briefing_cache_path(settings)
    cache_path.write_text(json.dumps(briefing.model_dump(), indent=2))


def _dashboard_briefing_cache_path(settings: Settings) -> Path:
    """Return the dashboard briefing cache path next to the SQLite database."""
    return Path(settings.database_path).with_name(".dashboard-briefing.json")


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
