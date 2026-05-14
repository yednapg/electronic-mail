from __future__ import annotations

"""Backend dashboard assembly for auth, feed, and natural-language briefing."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.core.config import Settings
from app.db.models import StoredDashboardImportJob
from app.db.repository import (
    DEFAULT_USER_ID,
    create_dashboard_import_job,
    get_active_dashboard_import_job,
    get_dashboard_import_job,
    get_latest_dashboard_import_job,
    mark_stale_dashboard_import_jobs_failed,
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
from app.services.ai.decision import generate_dashboard_briefing, sanitize_dashboard_briefing
from app.services.feed.memory_pipeline import (
    build_feed_from_projection_cache,
    build_feed_from_entities,
    hydrate_persistent_memory,
    list_entity_ids_needing_ai_refresh,
    list_stale_feed_projection_entity_ids,
    refresh_feed_projections_for_entities,
    refresh_ai_suggestions_for_entities,
)
from app.services.integrations.google import (
    backfill_full_gmail_source_records,
    fetch_google_account_profile,
    fetch_google_source_records,
    get_google_auth_state,
    load_google_account_profile,
    should_run_gmail_full_history_backfill,
)
from app.services.source_record_summaries import refresh_source_record_summaries


IMPORT_JOB_STALE_AFTER_SECONDS = 30 * 60
STALE_IMPORT_JOB_MESSAGE = "Dashboard import job became stale before completing."


def build_dashboard_response(settings: Settings) -> DashboardResponse:
    """Return the full dashboard payload for the current local user."""
    auth = get_google_auth_state(settings)

    if not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())

    current_time = datetime.now(timezone.utc).isoformat()
    feed = build_feed_from_projection_cache(str(settings.database_path)) or build_feed_from_entities(
        str(settings.database_path),
        current_time,
        record_trace=False,
    )
    profile = load_google_account_profile()
    briefing = load_dashboard_briefing_cache(settings) or generate_dashboard_briefing(feed, profile)
    briefing = sanitize_dashboard_briefing(briefing, feed, profile)

    resolved_profile = _merge_profile(profile, briefing)
    return DashboardResponse(auth=auth, profile=resolved_profile, briefing=briefing, feed=feed)


def create_queued_dashboard_import_job(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> DashboardImportJobResponse:
    """Create a queued backend-owned dashboard import/preparation job."""
    job, _should_start = create_or_reuse_dashboard_import_job(settings, user_id=user_id)
    return job


def create_or_reuse_dashboard_import_job(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> tuple[DashboardImportJobResponse, bool]:
    """Create one import job unless a fresh queued/running job already owns the work."""
    database_path = str(settings.database_path)
    fail_stale_dashboard_import_jobs(settings, user_id=user_id)
    active_job = get_active_dashboard_import_job(database_path, user_id=user_id)
    if active_job is not None:
        return to_dashboard_import_job_response(active_job), False

    job = create_dashboard_import_job(database_path, user_id=user_id)
    return to_dashboard_import_job_response(job), True


def create_new_dashboard_import_job(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> DashboardImportJobResponse:
    """Create a queued job without single-flight reuse for explicit compatibility paths."""
    job = create_dashboard_import_job(str(settings.database_path), user_id=user_id)
    return to_dashboard_import_job_response(job)


def start_dashboard_import_job(settings: Settings, *, user_id: str = DEFAULT_USER_ID) -> DashboardImportJobResponse:
    """Create and run a backend-owned dashboard import/preparation job inline."""
    job = create_new_dashboard_import_job(settings, user_id=user_id)
    return to_dashboard_import_job_response(run_dashboard_import_job(settings, job.id, run_backfill=False))


def run_dashboard_import_job(
    settings: Settings,
    job_id: str,
    *,
    run_backfill: bool = True,
) -> StoredDashboardImportJob:
    """Run a queued dashboard import/preparation job and persist the final status."""
    mark_dashboard_import_job_running(str(settings.database_path), job_id)

    try:
        result = _prepare_dashboard_state(settings, job_id=job_id)
    except Exception as exc:
        return mark_dashboard_import_job_failed(str(settings.database_path), job_id, error_message=str(exc))

    succeeded = mark_dashboard_import_job_succeeded(
        str(settings.database_path),
        job_id,
        result_status=str(result["status"]),
        source_records=int(result["source_records"]),
        changed_entities=int(result["changed_entities"]),
        refreshed_entities=int(result.get("refreshed_entities", 0)),
    )
    if run_backfill and result["status"] == "ready":
        run_gmail_full_history_backfill(settings)
    return succeeded


def get_dashboard_import_job_status(settings: Settings, job_id: str) -> DashboardImportJobResponse | None:
    """Return persisted status for one dashboard import/preparation job."""
    database_path = str(settings.database_path)
    job = get_dashboard_import_job(database_path, job_id)
    if job is not None and job.status in {"queued", "running"}:
        fail_stale_dashboard_import_jobs(settings, user_id=job.user_id)
        job = get_dashboard_import_job(database_path, job_id)
    return to_dashboard_import_job_response(job) if job is not None else None


def get_latest_dashboard_import_job_status(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> DashboardImportJobResponse | None:
    """Return the newest persisted dashboard import/preparation job status."""
    fail_stale_dashboard_import_jobs(settings, user_id=user_id)
    job = get_latest_dashboard_import_job(str(settings.database_path), user_id=user_id)
    return to_dashboard_import_job_response(job) if job is not None else None


def fail_stale_dashboard_import_jobs(settings: Settings, *, user_id: str = DEFAULT_USER_ID) -> int:
    """Mark queued/running import jobs failed when they have stopped reporting progress."""
    stale_before = (datetime.now(timezone.utc) - timedelta(seconds=IMPORT_JOB_STALE_AFTER_SECONDS)).isoformat()
    return mark_stale_dashboard_import_jobs_failed(
        str(settings.database_path),
        user_id=user_id,
        stale_before=stale_before,
        error_message=STALE_IMPORT_JOB_MESSAGE,
    )


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
        "stage_started_at": job.stage_started_at,
        "stage_durations": job.stage_durations,
        "imported_count": job.imported_count,
        "total_count": job.total_count,
    }
    if job.error_message:
        response["error_message"] = job.error_message
    return response


def run_gmail_full_history_backfill(settings: Settings) -> None:
    """Import older Gmail records after the first recent dashboard has been marked ready."""
    if not should_run_gmail_full_history_backfill(settings):
        return

    backfill_full_gmail_source_records(settings)


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
        stage_started_at=job.stage_started_at,
        stage_durations=job.stage_durations,
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
    gmail_source_records = [record for record in source_records if record.source == "gmail"]
    non_gmail_source_records = [record for record in source_records if record.source != "gmail"]
    imported_count = max(latest_imported_count, len(gmail_source_records)) + len(non_gmail_source_records)
    update_progress("source_summary", imported_count=imported_count, source_records=imported_count)
    refresh_source_record_summaries(database_path, [record.id for record in source_records])
    update_progress("memory_hydration", imported_count=imported_count, source_records=imported_count)
    changed_entity_ids = hydrate_persistent_memory(database_path, source_records, include_unlinked=True)
    stale_ai_entity_ids = list_entity_ids_needing_ai_refresh(database_path)
    stale_projection_entity_ids = list_stale_feed_projection_entity_ids(database_path)
    update_progress("ai_refresh", changed_entities=len(changed_entity_ids))
    refresh_entity_ids = sorted(set(changed_entity_ids) | set(stale_ai_entity_ids) | set(stale_projection_entity_ids))
    refresh_ai_suggestions_for_entities(database_path, refresh_entity_ids)
    update_progress("feed_build", refreshed_entities=len(refresh_entity_ids))
    current_time = datetime.now(timezone.utc).isoformat()
    refresh_feed_projections_for_entities(database_path, refresh_entity_ids, current_time)
    feed = build_feed_from_projection_cache(database_path) or build_feed_from_entities(
        database_path,
        current_time,
        record_trace=False,
    )
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
