from __future__ import annotations

"""Durable job and ops endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import load_settings
from app.db.jobs import get_job, get_queue_health
from app.schemas.domain import BackgroundJobResponse, OpsHealthResponse
from app.services.auth import require_current_user

router = APIRouter(tags=["jobs"])
settings = load_settings()


@router.get("/v1/jobs/{job_id}", response_model=BackgroundJobResponse)
def job_status(request: Request, job_id: str) -> BackgroundJobResponse:
    user = require_current_user(settings, request)
    job = get_job(str(settings.database_path), job_id)
    if job is None or (job.user_id is not None and job.user_id != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_response(job)


@router.get("/v1/ops/health", response_model=OpsHealthResponse)
def ops_health(request: Request) -> OpsHealthResponse:
    user = require_current_user(settings, request)
    admin_emails = {email.strip().lower() for email in __import__("os").getenv("OPS_ADMIN_EMAILS", "").split(",") if email.strip()}
    if admin_emails and user.email.lower() not in admin_emails:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ops admin access required")
    health = get_queue_health(str(settings.database_path))
    return OpsHealthResponse(
        queue_depth=health.queue_depth,
        dead_jobs=health.dead_jobs,
        stale_running_jobs=health.stale_running_jobs,
        oldest_queued_age_seconds=health.oldest_queued_age_seconds,
        workers=health.workers,
    )


def _job_response(job) -> BackgroundJobResponse:
    return BackgroundJobResponse(
        id=job.id,
        kind=job.kind,
        queue=job.queue,
        status=job.status,
        user_id=job.user_id,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        last_error=job.last_error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )
