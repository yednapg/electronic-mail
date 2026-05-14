from __future__ import annotations

"""First-login setup orchestration for Gmail Inbox plus fast Dashboard readiness."""

from app.core.config import Settings
from app.db.models import StoredFirstRunImportJob
from app.db.repository import (
    DEFAULT_USER_ID,
    create_first_run_import_job,
    get_active_first_run_import_job,
    get_first_run_import_job,
    get_latest_first_run_import_job,
    list_mailbox_thread_projections,
    mark_first_run_import_job_failed,
    mark_first_run_import_job_running,
    mark_first_run_import_job_succeeded,
    update_first_run_import_job_progress,
)
from app.schemas.domain import FirstRunImportJobResponse
from app.services.fast_dashboard import build_fast_dashboard_feed_from_gmail_threads, count_fast_dashboard_items
from app.services.integrations.google import backfill_full_gmail_source_records, fetch_recent_inbox_gmail_metadata


FIRST_RUN_STALE_ERROR = "First-run setup was superseded or stopped before completing."


def create_or_reuse_first_run_import_job(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> tuple[FirstRunImportJobResponse, bool]:
    """Create one first-run setup job unless one already owns setup work."""
    database_path = str(settings.database_path)
    latest = get_latest_first_run_import_job(database_path, user_id=user_id)
    if latest is not None and latest.inbox_ready_at and latest.dashboard_ready_at:
        return to_first_run_import_job_response(latest), False

    active = get_active_first_run_import_job(database_path, user_id=user_id)
    if active is not None:
        return to_first_run_import_job_response(active), False

    job = create_first_run_import_job(database_path, user_id=user_id)
    return to_first_run_import_job_response(job), True


def get_first_run_import_job_status(
    settings: Settings,
    job_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> FirstRunImportJobResponse | None:
    """Return one first-run setup job for the current user."""
    job = get_first_run_import_job(str(settings.database_path), job_id)
    if job is None or job.user_id != user_id:
        return None
    return to_first_run_import_job_response(job)


def get_latest_first_run_import_job_status(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> FirstRunImportJobResponse | None:
    """Return the latest first-run setup job for the current user."""
    job = get_latest_first_run_import_job(str(settings.database_path), user_id=user_id)
    return to_first_run_import_job_response(job) if job is not None else None


def run_first_run_import_job(settings: Settings, job_id: str, *, user_id: str = DEFAULT_USER_ID) -> StoredFirstRunImportJob:
    """Run first-login setup, mark visible readiness, then continue older import."""
    database_path = str(settings.database_path)
    mark_first_run_import_job_running(database_path, job_id)

    try:
        latest_fetched_count = 0
        latest_total_count: int | None = None

        def update_gmail_progress(stage: str, fetched_count: int, total_count: int | None) -> None:
            nonlocal latest_fetched_count, latest_total_count
            latest_fetched_count = fetched_count
            latest_total_count = total_count
            update_first_run_import_job_progress(
                database_path,
                job_id,
                stage=stage,
                fetched_count=fetched_count,
                total_count=total_count,
            )

        update_first_run_import_job_progress(database_path, job_id, stage="gmail_recent_sync")
        latest_fetched_count, latest_total_count = fetch_recent_inbox_gmail_metadata(
            settings,
            user_id=user_id,
            progress_callback=update_gmail_progress,
        )

        update_first_run_import_job_progress(database_path, job_id, stage="inbox_projection")
        _threads, thread_count, _cursor = list_mailbox_thread_projections(
            database_path,
            user_id=user_id,
            label="inbox",
            limit=1,
        )
        update_first_run_import_job_progress(
            database_path,
            job_id,
            stage="inbox_ready",
            fetched_count=latest_fetched_count,
            total_count=latest_total_count,
            thread_count=thread_count,
            inbox_ready=True,
        )

        update_first_run_import_job_progress(database_path, job_id, stage="dashboard_fast_feed")
        fast_feed = build_fast_dashboard_feed_from_gmail_threads(database_path, user_id=user_id)
        dashboard_item_count = count_fast_dashboard_items(fast_feed)
        update_first_run_import_job_progress(
            database_path,
            job_id,
            stage="ready",
            dashboard_item_count=dashboard_item_count,
            dashboard_ready=True,
        )

        update_first_run_import_job_progress(database_path, job_id, stage="full_import", full_import_started=True)
        backfill_full_gmail_source_records(settings, user_id=user_id)
        update_first_run_import_job_progress(database_path, job_id, stage="full_import_completed", full_import_completed=True)
        return mark_first_run_import_job_succeeded(database_path, job_id)
    except Exception as exc:
        return mark_first_run_import_job_failed(database_path, job_id, error_message=str(exc))


def to_first_run_import_job_response(job: StoredFirstRunImportJob) -> FirstRunImportJobResponse:
    """Convert persisted first-run job state to the API schema."""
    return FirstRunImportJobResponse(
        id=job.id,
        user_id=job.user_id,
        status=job.status,
        stage=job.stage,
        fetched_count=job.fetched_count,
        total_count=job.total_count,
        thread_count=job.thread_count,
        dashboard_item_count=job.dashboard_item_count,
        inbox_ready_at=job.inbox_ready_at,
        dashboard_ready_at=job.dashboard_ready_at,
        full_import_started_at=job.full_import_started_at,
        full_import_completed_at=job.full_import_completed_at,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )
