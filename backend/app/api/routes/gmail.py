from __future__ import annotations

"""Explicit Gmail thread mutation endpoints."""

from fastapi import APIRouter, HTTPException, Request, status
from googleapiclient.errors import HttpError

from app.core.config import load_settings
from app.core.error_safety import safe_google_error
from app.db.user_mail_guard import shared_user_mail_lock
from app.schemas.domain import GmailThreadMutationResponse
from app.services.auth import require_current_user
from app.services.integrations.google import (
    archive_gmail_thread as archive_gmail_thread_service,
    mark_gmail_thread_read,
    unarchive_gmail_thread as unarchive_gmail_thread_service,
)

router = APIRouter(tags=["gmail"])
settings = load_settings()


@router.post("/gmail/threads/{thread_id}/archive", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/archive", response_model=GmailThreadMutationResponse)
def archive_thread(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    user = require_current_user(settings, http_request)
    _require_google()
    try:
        with shared_user_mail_lock(str(settings.database_path), user_id=user.id):
            archive_gmail_thread_service(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=safe_google_error(exc, operation="archive")) from exc
    except HttpError as exc:
        raise HTTPException(status_code=_http_status(exc), detail=safe_google_error(exc, operation="archive")) from exc
    return GmailThreadMutationResponse(thread_id=thread_id, action="archive")


@router.post("/gmail/threads/{thread_id}/unarchive", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/unarchive", response_model=GmailThreadMutationResponse)
def unarchive_thread(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    user = require_current_user(settings, http_request)
    _require_google()
    try:
        with shared_user_mail_lock(str(settings.database_path), user_id=user.id):
            unarchive_gmail_thread_service(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=safe_google_error(exc, operation="unarchive")) from exc
    except HttpError as exc:
        raise HTTPException(status_code=_http_status(exc), detail=safe_google_error(exc, operation="unarchive")) from exc
    return GmailThreadMutationResponse(thread_id=thread_id, action="unarchive")


@router.post("/gmail/threads/{thread_id}/mark-read", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/mark-read", response_model=GmailThreadMutationResponse)
def mark_thread_read(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    user = require_current_user(settings, http_request)
    _require_google()
    try:
        with shared_user_mail_lock(str(settings.database_path), user_id=user.id):
            mark_gmail_thread_read(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=safe_google_error(exc, operation="mark-read")) from exc
    except HttpError as exc:
        raise HTTPException(status_code=_http_status(exc), detail=safe_google_error(exc, operation="mark-read")) from exc
    return GmailThreadMutationResponse(thread_id=thread_id, action="mark_read")


def _require_google() -> None:
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")


def _http_status(exc: HttpError) -> int:
    response_status = getattr(getattr(exc, "resp", None), "status", None)
    return response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
