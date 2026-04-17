from __future__ import annotations

"""Explicit Gmail thread mutation endpoints."""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app.core.config import load_settings
from app.services.integrations.google import (
    archive_gmail_thread as archive_gmail_thread_service,
    unarchive_gmail_thread as unarchive_gmail_thread_service,
)


class GmailThreadMutationResponse(BaseModel):
    """Response payload for explicit Gmail thread mutations."""

    thread_id: str
    action: Literal["archive", "unarchive"]


router = APIRouter(tags=["gmail"])
settings = load_settings()


@router.post("/gmail/threads/{thread_id}/archive", response_model=GmailThreadMutationResponse)
def archive_thread(thread_id: str) -> GmailThreadMutationResponse:
    """Archive one Gmail thread on user request."""
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        archive_gmail_thread_service(settings, thread_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail archive failed: {exc}") from exc

    return GmailThreadMutationResponse(thread_id=thread_id, action="archive")


@router.post("/gmail/threads/{thread_id}/unarchive", response_model=GmailThreadMutationResponse)
def unarchive_thread(thread_id: str) -> GmailThreadMutationResponse:
    """Restore one Gmail thread to the inbox on user request."""
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        unarchive_gmail_thread_service(settings, thread_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail unarchive failed: {exc}") from exc

    return GmailThreadMutationResponse(thread_id=thread_id, action="unarchive")
