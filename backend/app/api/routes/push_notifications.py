from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.db.push_notifications import register_device, delete_device
from app.db.repository import get_active_app_session
from app.services.auth import require_current_user, hash_token, SESSION_COOKIE_NAME
from app.services.push_notifications import PushConfiguration

router = APIRouter(prefix="/v1/push", tags=["notifications"])


class DeviceRegistration(BaseModel):
    token: str = Field(min_length=32, max_length=512, pattern=r"^[0-9a-f]+$")
    platform: Literal["macos", "ios"]
    environment: Literal["sandbox", "production"]
    enabled: bool = True
    sound_enabled: bool = True
    preview_enabled: bool = False


def _session(request: Request):
    settings = request.app.state.settings
    user = require_current_user(settings, request)
    authorization = request.headers.get("Authorization", "")
    bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    token = request.cookies.get(SESSION_COOKIE_NAME) or bearer
    session = get_active_app_session(settings.database_path, token_hash=hash_token(settings, token),
                                     now=datetime.now(timezone.utc).isoformat())
    if session is None or session.user_id != user.id:
        raise HTTPException(401, "Authentication required")
    return settings, user, session


@router.get("/status")
def push_status(request: Request) -> dict:
    require_current_user(request.app.state.settings, request)
    return {"available": PushConfiguration.load().configured}


@router.put("/devices/{device_id}", status_code=204)
def put_device(device_id: UUID, body: DeviceRegistration, request: Request) -> Response:
    settings, user, session = _session(request)
    if body.enabled and not PushConfiguration.load().configured:
        raise HTTPException(503, "Push notifications are not configured on this server")
    register_device(settings, device_id=str(device_id), user_id=user.id, session_id=session.id, registration=body)
    return Response(status_code=204)


@router.delete("/devices/{device_id}", status_code=204)
def remove_device(device_id: UUID, request: Request) -> Response:
    settings, user, session = _session(request)
    delete_device(settings.database_path, device_id=str(device_id), user_id=user.id, session_id=session.id)
    return Response(status_code=204)
