from __future__ import annotations

"""First-login setup endpoints."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.core.config import load_settings
from app.schemas.domain import FirstRunImportJobResponse
from app.services.auth import require_current_user
from app.services.first_run import (
    create_or_reuse_first_run_import_job,
    get_first_run_import_job_status,
    get_latest_first_run_import_job_status,
    run_first_run_import_job,
)

router = APIRouter()
settings = load_settings()


@router.post("/v1/first-run/import-jobs", response_model=FirstRunImportJobResponse, status_code=202)
def create_first_run_import_job(request: Request, background_tasks: BackgroundTasks) -> FirstRunImportJobResponse:
    """Start the first-run setup job that gates initial Dashboard/Inbox entry."""
    user = require_current_user(settings, request)
    job, should_start = create_or_reuse_first_run_import_job(settings, user_id=user.id)
    if should_start:
        background_tasks.add_task(run_first_run_import_job, settings, job.id, user_id=user.id)
    return job


@router.get("/v1/first-run/import-jobs/latest", response_model=FirstRunImportJobResponse)
def latest_first_run_import_job(request: Request) -> FirstRunImportJobResponse:
    """Return the newest first-run setup job for the current user."""
    user = require_current_user(settings, request)
    job = get_latest_first_run_import_job_status(settings, user_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="No first-run import job found")
    return job


@router.get("/v1/first-run/import-jobs/{job_id}", response_model=FirstRunImportJobResponse)
def first_run_import_job(request: Request, job_id: str) -> FirstRunImportJobResponse:
    """Return one first-run setup job for the current user."""
    user = require_current_user(settings, request)
    job = get_first_run_import_job_status(settings, job_id, user_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="First-run import job not found")
    return job
