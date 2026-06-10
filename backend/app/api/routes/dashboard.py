from __future__ import annotations

"""Dashboard endpoint backed by AI-created mail groups."""

from fastapi import APIRouter, Query, Request

from app.core.config import load_settings
from app.schemas.domain import DashboardResponse
from app.services.auth import auth_state_for_request, get_current_user
from app.services.mail_groups import build_app_session_response, build_dashboard_response

router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
@router.get("/v1/dashboard", response_model=DashboardResponse)
def dashboard(request: Request, debug_classification: bool = Query(default=False)) -> DashboardResponse:
    user = get_current_user(settings, request)
    auth = auth_state_for_request(settings, request)
    if user is None:
        return build_dashboard_response(settings, user_id=None, auth=auth, debug_classification=debug_classification)
    if debug_classification:
        return build_dashboard_response(
            settings,
            user_id=user.id,
            auth=auth,
            profile=user.profile,
            debug_classification=True,
        )
    return build_app_session_response(settings, user=user).dashboard
