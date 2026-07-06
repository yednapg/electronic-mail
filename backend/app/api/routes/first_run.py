from __future__ import annotations

"""First-login setup endpoints backed by durable Gmail import jobs."""

from fastapi import APIRouter, HTTPException, Request

from app.core.config import load_settings
from app.db.jobs import get_job
from app.db.mail_groups import get_import_state
from app.schemas.domain import FirstRunImportJobResponse
from app.services.auth import require_current_user
from app.services.mail_groups import enqueue_first_run

router = APIRouter()
settings = load_settings()


@router.post("/v1/first-run/import-jobs", response_model=FirstRunImportJobResponse, status_code=202)
def create_first_run_import_job(request: Request) -> FirstRunImportJobResponse:
    user = require_current_user(settings, request)
    state = get_import_state(str(settings.database_path), user_id=user.id)
    if state and state.first_batch_imported_at:
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
    ready = bool(state and state.first_batch_imported_at)
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
    else:
        stage = "ready"
    quality_status = "ready" if ready else "failed" if error_message else "pending"
    return FirstRunImportJobResponse(
        id=job.id if job else job_id,
        user_id=user_id,
        status=status,  # type: ignore[arg-type]
        stage=stage,
        fetched_count=0,
        total_count=None,
        thread_count=0,
        dashboard_item_count=0,
        inbox_ready_at=state.first_batch_imported_at if state else None,
        first_groups_ready_at=state.first_groups_ready_at if state else None,
        dashboard_ready_at=state.first_dashboard_ready_at if state else None,
        canonical_dashboard_ready_at=state.first_dashboard_ready_at if state else None,
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
