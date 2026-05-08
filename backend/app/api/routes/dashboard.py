from __future__ import annotations

"""Dashboard endpoint that returns auth, profile, briefing, and feed state."""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.config import load_settings
from app.schemas.domain import DashboardImportJobResponse, DashboardResponse
from app.services.dashboard import (
    build_dashboard_response,
    create_queued_dashboard_import_job,
    get_dashboard_import_job_status,
    get_latest_dashboard_import_job_status,
    prepare_dashboard_state,
    run_dashboard_import_job,
)


router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard() -> DashboardResponse:
    """Return the full dashboard payload for the local app."""
    return build_dashboard_response(settings)


@router.post("/v1/dashboard/prepare")
def prepare_dashboard() -> dict[str, object]:
    """Compatibility path for explicit post-login preparation."""
    return prepare_dashboard_state(settings)


@router.post("/v1/dashboard/import-jobs", response_model=DashboardImportJobResponse, status_code=202)
def create_dashboard_import_job(background_tasks: BackgroundTasks) -> DashboardImportJobResponse:
    """Create a backend-owned dashboard import/preparation job and return its status."""
    job = create_queued_dashboard_import_job(settings)
    background_tasks.add_task(run_dashboard_import_job, settings, job.id)
    return job


@router.get("/v1/dashboard/import-jobs/latest", response_model=DashboardImportJobResponse)
def latest_dashboard_import_job() -> DashboardImportJobResponse:
    """Return the latest backend-owned dashboard import/preparation job status."""
    job = get_latest_dashboard_import_job_status(settings)
    if job is None:
        raise HTTPException(status_code=404, detail="No dashboard import job found")
    return job


@router.get("/v1/dashboard/import-jobs/{job_id}", response_model=DashboardImportJobResponse)
def dashboard_import_job(job_id: str) -> DashboardImportJobResponse:
    """Return one backend-owned dashboard import/preparation job status."""
    job = get_dashboard_import_job_status(settings, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Dashboard import job not found")
    return job
