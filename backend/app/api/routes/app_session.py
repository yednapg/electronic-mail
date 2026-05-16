from __future__ import annotations

"""Single app-session snapshot for post-login, dashboard, and Gmail."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.schemas.domain import AppSessionResponse
from app.services.auth import require_current_user
from app.services.mail_groups import build_app_session_response

router = APIRouter(tags=["app-session"])
settings = load_settings()


@router.get("/v1/app/session", response_model=AppSessionResponse)
def app_session(request: Request) -> AppSessionResponse:
    user = require_current_user(settings, request)
    return build_app_session_response(settings, user=user)
