from __future__ import annotations

"""Dashboard endpoint that returns auth, profile, briefing, and feed state."""

from fastapi import APIRouter

from app.core.config import load_settings
from app.schemas.domain import DashboardResponse
from app.services.dashboard import build_dashboard_response


router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard() -> DashboardResponse:
    """Return the full dashboard payload for the local app."""
    return build_dashboard_response(settings)
