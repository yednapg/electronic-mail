from __future__ import annotations

"""Dashboard endpoint that returns auth, profile, briefing, and feed state."""

from fastapi import APIRouter

from app.core.config import load_settings
from app.schemas.domain import DashboardResponse
from app.services.dashboard import build_dashboard_response, prepare_dashboard_state


router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard() -> DashboardResponse:
    """Return the full dashboard payload for the local app."""
    return build_dashboard_response(settings)


@router.post("/v1/dashboard/prepare")
def prepare_dashboard() -> dict[str, object]:
    """Run the slow post-login preparation before showing the dashboard."""
    return prepare_dashboard_state(settings)
