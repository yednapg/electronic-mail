from __future__ import annotations

"""Dashboard endpoint backed by AI-created mail groups."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.schemas.domain import DashboardResponse
from app.services.auth import auth_state_for_request, get_current_user
from app.services.mail_groups import build_app_session_response, build_dashboard_response

router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
@router.get("/v1/dashboard", response_model=DashboardResponse)
def dashboard(request: Request) -> DashboardResponse:
    user = get_current_user(settings, request)
    auth = auth_state_for_request(settings, request)
    if user is None:
        return build_dashboard_response(settings, user_id=None, auth=auth)
    return build_app_session_response(settings, user=user).dashboard
