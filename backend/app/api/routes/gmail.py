from __future__ import annotations

"""Explicit Gmail thread mutation endpoints."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from googleapiclient.errors import HttpError

from app.core.config import load_settings
from app.db.models import StoredGmailDraft
from app.db.repository import get_gmail_draft, upsert_gmail_draft, utc_now_iso
from app.schemas.domain import GmailDraftRequest, GmailDraftResponse, GmailThreadMutationResponse
from app.services.auth import require_current_user
from app.services.integrations.google import (
    archive_gmail_thread as archive_gmail_thread_service,
    create_gmail_draft,
    delete_gmail_draft,
    mark_gmail_thread_read,
    send_gmail_draft,
    unarchive_gmail_thread as unarchive_gmail_thread_service,
    update_gmail_draft,
)


router = APIRouter(tags=["gmail"])
settings = load_settings()


@router.post("/gmail/threads/{thread_id}/archive", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/archive", response_model=GmailThreadMutationResponse)
def archive_thread(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    """Archive one Gmail thread on user request."""
    user = require_current_user(settings, http_request)
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        archive_gmail_thread_service(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail archive failed: {exc}") from exc

    return GmailThreadMutationResponse(thread_id=thread_id, action="archive")


@router.post("/gmail/threads/{thread_id}/unarchive", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/unarchive", response_model=GmailThreadMutationResponse)
def unarchive_thread(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    """Restore one Gmail thread to the inbox on user request."""
    user = require_current_user(settings, http_request)
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        unarchive_gmail_thread_service(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail unarchive failed: {exc}") from exc

    return GmailThreadMutationResponse(thread_id=thread_id, action="unarchive")


@router.post("/gmail/threads/{thread_id}/mark-read", response_model=GmailThreadMutationResponse)
@router.post("/v1/gmail/threads/{thread_id}/mark-read", response_model=GmailThreadMutationResponse)
def mark_thread_read(http_request: Request, thread_id: str) -> GmailThreadMutationResponse:
    """Mark one Gmail thread read on explicit user request."""
    user = require_current_user(settings, http_request)
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        mark_gmail_thread_read(settings, thread_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail mark-read failed: {exc}") from exc

    return GmailThreadMutationResponse(thread_id=thread_id, action="mark_read")


@router.post("/v1/gmail/drafts", response_model=GmailDraftResponse)
def create_draft(http_request: Request, request: GmailDraftRequest) -> GmailDraftResponse:
    """Create a real Gmail draft from an explicit user action."""
    user = require_current_user(settings, http_request)
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Google OAuth is not configured")

    try:
        payload = create_gmail_draft(
            settings,
            to=request.to,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body=request.body,
            thread_id=request.thread_id,
            user_id=user.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail draft create failed: {exc}") from exc

    draft = draft_from_payload(request, payload, user_id=user.id, status_value="draft")
    return to_draft_response(upsert_gmail_draft(str(settings.database_path), draft))


@router.patch("/v1/gmail/drafts/{draft_id}", response_model=GmailDraftResponse)
def update_draft(http_request: Request, draft_id: str, request: GmailDraftRequest) -> GmailDraftResponse:
    """Update a real Gmail draft from an explicit user action."""
    user = require_current_user(settings, http_request)
    existing = get_gmail_draft(str(settings.database_path), draft_id, user_id=user.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    try:
        payload = update_gmail_draft(
            settings,
            existing.gmail_draft_id,
            to=request.to,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body=request.body,
            thread_id=request.thread_id or existing.thread_id,
            user_id=user.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail draft update failed: {exc}") from exc

    draft = draft_from_payload(request, payload, existing=existing, user_id=user.id, status_value="draft")
    return to_draft_response(upsert_gmail_draft(str(settings.database_path), draft))


@router.post("/v1/gmail/drafts/{draft_id}/send", response_model=GmailDraftResponse)
def send_draft(http_request: Request, draft_id: str) -> GmailDraftResponse:
    """Send a Gmail draft from an explicit user action."""
    user = require_current_user(settings, http_request)
    existing = get_gmail_draft(str(settings.database_path), draft_id, user_id=user.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    try:
        payload = send_gmail_draft(settings, existing.gmail_draft_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail draft send failed: {exc}") from exc

    sent = StoredGmailDraft(
        **{
            **existing.__dict__,
            "gmail_message_id": str(payload.get("id") or existing.gmail_message_id or ""),
            "thread_id": str(payload.get("threadId") or existing.thread_id or ""),
            "status": "sent",
            "updated_at": utc_now_iso(),
        }
    )
    return to_draft_response(upsert_gmail_draft(str(settings.database_path), sent))


@router.delete("/v1/gmail/drafts/{draft_id}", response_model=GmailDraftResponse)
def delete_draft(http_request: Request, draft_id: str) -> GmailDraftResponse:
    """Delete a Gmail draft from an explicit user action."""
    user = require_current_user(settings, http_request)
    existing = get_gmail_draft(str(settings.database_path), draft_id, user_id=user.id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    try:
        delete_gmail_draft(settings, existing.gmail_draft_id, user_id=user.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        response_status = getattr(getattr(exc, "resp", None), "status", None)
        status_code = response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=f"Gmail draft delete failed: {exc}") from exc

    deleted = StoredGmailDraft(**{**existing.__dict__, "status": "deleted", "updated_at": utc_now_iso()})
    return to_draft_response(upsert_gmail_draft(str(settings.database_path), deleted))


def draft_from_payload(
    request: GmailDraftRequest,
    payload: dict[str, object],
    *,
    existing: StoredGmailDraft | None = None,
    user_id: str,
    status_value: str,
) -> StoredGmailDraft:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    now = utc_now_iso()
    return StoredGmailDraft(
        id=existing.id if existing is not None else str(uuid4()),
        user_id=user_id,
        entity_id=request.entity_id if request.entity_id is not None else (existing.entity_id if existing else None),
        gmail_draft_id=str(payload.get("id") or (existing.gmail_draft_id if existing else "")),
        gmail_message_id=str(message.get("id") or "") if isinstance(message, dict) and message.get("id") else None,
        thread_id=str(message.get("threadId") or request.thread_id or (existing.thread_id if existing else "") or "") or None,
        to_recipients=request.to,
        cc_recipients=request.cc,
        bcc_recipients=request.bcc,
        subject=request.subject,
        body=request.body,
        status=status_value,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )


def to_draft_response(draft: StoredGmailDraft) -> GmailDraftResponse:
    return GmailDraftResponse(
        id=draft.id,
        user_id=draft.user_id,
        entity_id=draft.entity_id,
        gmail_draft_id=draft.gmail_draft_id,
        gmail_message_id=draft.gmail_message_id,
        thread_id=draft.thread_id,
        to=draft.to_recipients,
        cc=draft.cc_recipients,
        bcc=draft.bcc_recipients,
        subject=draft.subject,
        body=draft.body,
        status=draft.status,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )
