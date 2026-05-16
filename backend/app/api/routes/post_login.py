from __future__ import annotations

"""Post-login product-readiness endpoint."""

from fastapi import APIRouter, Request

from app.core.config import load_settings
from app.schemas.domain import PostLoginReadinessResponse
from app.services.auth import require_current_user
from app.services.mail_groups import build_post_login_readiness_response

router = APIRouter(tags=["post-login"])
settings = load_settings()


@router.get("/v1/post-login/readiness", response_model=PostLoginReadinessResponse)
def post_login_readiness(request: Request) -> PostLoginReadinessResponse:
    user = require_current_user(settings, request)
    return build_post_login_readiness_response(settings, user=user)
