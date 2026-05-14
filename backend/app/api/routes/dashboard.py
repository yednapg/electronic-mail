from __future__ import annotations

"""Dashboard endpoint backed by AI-created mail groups."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.schemas.domain import DashboardResponse
from app.services.auth import auth_state_for_request, get_current_user, require_current_user
from app.services.mail_groups import build_dashboard_response

router = APIRouter()
settings = load_settings()


@router.get("/dashboard", response_model=DashboardResponse)
@router.get("/v1/dashboard", response_model=DashboardResponse)
def dashboard(request: Request) -> DashboardResponse:
    user = get_current_user(settings, request)
    auth = auth_state_for_request(settings, request)
    if user is None:
        return build_dashboard_response(settings, user_id=None, auth=auth)
    return build_dashboard_response(settings, user_id=user.id, auth=auth, profile=user.profile)


@router.post("/v1/dashboard/prepare")
def prepare_dashboard(request: Request) -> dict[str, object]:
    user = require_current_user(settings, request)
    return {"status": "ready", "source": "mail_groups", "user_id": user.id}
