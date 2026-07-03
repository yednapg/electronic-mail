from __future__ import annotations

"""Single app-session snapshot for post-login, dashboard, and Gmail."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.schemas.domain import AppSessionResponse, SmartInboxResponse, SmartReadinessResponse, SmartWorkQueueResponse
from app.services.auth import require_current_user
from app.services.mail_groups import build_app_session_response

router = APIRouter(tags=["app-session"])
settings = load_settings()


@router.get("/v1/app/session", response_model=AppSessionResponse)
def app_session(request: Request) -> AppSessionResponse:
    user = require_current_user(settings, request)
    return build_app_session_response(settings, user=user)


@router.get("/v1/smart-inbox", response_model=SmartInboxResponse)
def smart_inbox(request: Request) -> SmartInboxResponse:
    user = require_current_user(settings, request)
    return build_app_session_response(settings, user=user).smart_inbox


@router.get("/v1/smart-work-queue", response_model=SmartWorkQueueResponse)
def smart_work_queue(request: Request) -> SmartWorkQueueResponse:
    user = require_current_user(settings, request)
    return build_app_session_response(settings, user=user).smart_work_queue


@router.get("/v1/smart-readiness", response_model=SmartReadinessResponse)
def smart_readiness(request: Request) -> SmartReadinessResponse:
    user = require_current_user(settings, request)
    return build_app_session_response(settings, user=user).smart_readiness
