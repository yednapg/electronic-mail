from __future__ import annotations

"""Dashboard endpoint that returns auth, profile, briefing, and feed state."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.core.config import load_settings
from app.schemas.domain import DashboardImportJobResponse, DashboardResponse
from app.services.dashboard import (
    build_dashboard_response,
    create_or_reuse_dashboard_import_job,
    get_dashboard_import_job_status,
    get_latest_dashboard_import_job_status,
    prepare_dashboard_state,
    run_dashboard_import_job,
)
from app.services.auth import auth_state_for_request, get_current_user, require_current_user


router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(request: Request) -> DashboardResponse:
    """Return the full dashboard payload for the local app."""
    user = get_current_user(settings, request)
    if user is None:
        return build_dashboard_response(settings, user_id=None, auth=auth_state_for_request(settings, request))
    return build_dashboard_response(settings, user_id=user.id, auth=auth_state_for_request(settings, request), profile=user.profile)


@router.post("/v1/dashboard/prepare")
def prepare_dashboard(request: Request) -> dict[str, object]:
    """Compatibility path for explicit post-login preparation."""
    user = require_current_user(settings, request)
    return prepare_dashboard_state(settings, user_id=user.id)


@router.post("/v1/dashboard/import-jobs", response_model=DashboardImportJobResponse, status_code=202)
def create_dashboard_import_job(request: Request, background_tasks: BackgroundTasks) -> DashboardImportJobResponse:
    """Create a backend-owned dashboard import/preparation job and return its status."""
    user = require_current_user(settings, request)
    job, should_start = create_or_reuse_dashboard_import_job(settings, user_id=user.id)
    if should_start:
        background_tasks.add_task(run_dashboard_import_job, settings, job.id, user_id=user.id)
    return job


@router.get("/v1/dashboard/import-jobs/latest", response_model=DashboardImportJobResponse)
def latest_dashboard_import_job(request: Request) -> DashboardImportJobResponse:
    """Return the latest backend-owned dashboard import/preparation job status."""
    user = require_current_user(settings, request)
    job = get_latest_dashboard_import_job_status(settings, user_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="No dashboard import job found")
    return job


@router.get("/v1/dashboard/import-jobs/{job_id}", response_model=DashboardImportJobResponse)
def dashboard_import_job(request: Request, job_id: str) -> DashboardImportJobResponse:
    """Return one backend-owned dashboard import/preparation job status."""
    user = require_current_user(settings, request)
    job = get_dashboard_import_job_status(settings, job_id, user_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dashboard import job not found")
    return job
