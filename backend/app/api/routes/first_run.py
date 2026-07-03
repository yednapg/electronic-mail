from __future__ import annotations

"""First-login setup endpoints backed by durable Gmail import jobs."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.db.jobs import get_job
from app.db.mail_groups import count_dashboard_mail_groups, count_gmail_messages, count_mailbox_threads, get_app_session_snapshot, get_import_state
from app.schemas.domain import FirstRunImportJobResponse, SmartInboxResponse
from app.services.auth import require_current_user
from app.services.mail_group_config import DASHBOARD_DAYS, SMART_HOT_WINDOW_DAYS
from app.services.mail_groups import enqueue_first_run, ensure_background_import_work, hot_window_product_ready
from app.services.smart_inbox_quality import smart_inbox_response_is_product_ready

router = APIRouter()
settings = load_settings()


@router.post("/v1/first-run/import-jobs", response_model=FirstRunImportJobResponse, status_code=202)
def create_first_run_import_job(request: Request) -> FirstRunImportJobResponse:
    user = require_current_user(settings, request)
    state = get_import_state(str(settings.database_path), user_id=user.id)
    if _first_run_ready(user.id, state):
        return _first_run_response(user.id, job_id=f"first-run:{user.id}")
    if state and state.first_batch_imported_at:
        ensure_background_import_work(settings, user_id=user.id)
        return _first_run_response(user.id, job_id=f"first-run:{user.id}")
    job_id = enqueue_first_run(settings, user_id=user.id)
    return _first_run_response(user.id, job_id=job_id)


@router.get("/v1/first-run/import-jobs/latest", response_model=FirstRunImportJobResponse)
def latest_first_run_import_job(request: Request) -> FirstRunImportJobResponse:
    user = require_current_user(settings, request)
    return _first_run_response(user.id, job_id=f"first-run:{user.id}")


@router.get("/v1/first-run/import-jobs/{job_id}", response_model=FirstRunImportJobResponse)
def first_run_import_job(request: Request, job_id: str) -> FirstRunImportJobResponse:
    user = require_current_user(settings, request)
    return _first_run_response(user.id, job_id=job_id)


def _first_run_response(user_id: str, *, job_id: str) -> FirstRunImportJobResponse:
    state = get_import_state(str(settings.database_path), user_id=user_id)
    job = get_job(str(settings.database_path), job_id)
    hot_window_ready = _hot_window_ready(user_id, state)
    ready = bool(state and state.first_batch_imported_at and state.first_groups_ready_at and hot_window_ready)
    job_is_active = bool(job and job.status in {"queued", "running"})
    error_message = None if job_is_active else (state.last_sync_error if state else None) or (job.last_error if job else None)
    status = "succeeded" if ready else "failed" if error_message and not ready else (job.status if job else "queued")
    if status in {"dead", "cancelled"}:
        status = "failed"
    if status not in {"queued", "running", "succeeded", "failed"}:
        status = "queued"
    if ready:
        stage = "ready"
    elif not state or not state.first_batch_imported_at:
        stage = "importing_gmail"
    elif not state.first_groups_ready_at:
        stage = "ai_grouping"
    elif not hot_window_ready:
        stage = "preparing_inbox"
    else:
        stage = "queued"
    quality_status = "ready" if ready else "failed" if error_message else "pending"
    counts = _first_run_counts(user_id)
    return FirstRunImportJobResponse(
        id=job.id if job else job_id,
        user_id=user_id,
        status=status,  # type: ignore[arg-type]
        stage=stage,
        fetched_count=counts["fetched_count"],
        total_count=None,
        thread_count=counts["thread_count"],
        dashboard_item_count=counts["dashboard_item_count"],
        inbox_ready_at=state.first_batch_imported_at if state else None,
        first_groups_ready_at=state.first_groups_ready_at if state else None,
        dashboard_ready_at=state.first_dashboard_ready_at if state else None,
        canonical_dashboard_ready_at=state.first_dashboard_ready_at if state else None,
        hot_window_ready_at=_hot_window_import_completed_at(state) if hot_window_ready else None,
        quality_status=quality_status,  # type: ignore[arg-type]
        quality_error=error_message,
        full_import_started_at=getattr(state, "full_backfill_started_at", None) if state else None,
        full_import_completed_at=getattr(state, "full_backfill_completed_at", None) if state else None,
        error_message=error_message,
        created_at=job.created_at if job else (state.updated_at if state else ""),
        started_at=job.started_at if job else (state.last_import_started_at if state else None),
        completed_at=job.completed_at if job else (state.last_import_completed_at if state else None),
        updated_at=job.updated_at if job else (state.updated_at if state else ""),
    )


def _first_run_ready(user_id: str, state) -> bool:
    return bool(state and state.first_batch_imported_at and state.first_groups_ready_at and _hot_window_ready(user_id, state))


def _first_run_counts(user_id: str) -> dict[str, int]:
    database_url = str(settings.database_path)
    recent_since = (datetime.now(timezone.utc) - timedelta(days=SMART_HOT_WINDOW_DAYS)).isoformat()
    dashboard_since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    return {
        "fetched_count": count_gmail_messages(database_url, user_id=user_id, since_iso=recent_since),
        "thread_count": count_mailbox_threads(database_url, user_id=user_id, label="inbox", since_iso=recent_since),
        "dashboard_item_count": count_dashboard_mail_groups(database_url, user_id=user_id, since_iso=dashboard_since),
    }


def _hot_window_ready(user_id: str, state) -> bool:
    if not (state and _hot_window_import_completed_at(state)):
        return False
    snapshot = get_app_session_snapshot(str(settings.database_path), user_id=user_id)
    if snapshot is not None and snapshot.smart_inbox:
        smart_inbox = SmartInboxResponse.model_validate(snapshot.smart_inbox)
        return smart_inbox_response_is_product_ready(smart_inbox)
    return hot_window_product_ready(settings, user_id=user_id, state=state)


def _hot_window_import_completed_at(state) -> str | None:
    if state is None:
        return None
    return getattr(state, "hot_window_completed_at", None) or getattr(state, "full_backfill_completed_at", None)
