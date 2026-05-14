from __future__ import annotations

"""Mail group APIs."""

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.core.config import load_settings
from app.schemas.domain import GmailViewResponse, MailboxResponse, ThreadReaderResponse
from app.services.auth import require_current_user
from app.services.mail_groups import build_group_detail_response, build_mailbox_response

router = APIRouter(tags=["mail-groups"])
settings = load_settings()


@router.get("/v1/mail-groups", response_model=MailboxResponse)
def mail_groups(request: Request, limit: int = Query(default=150, ge=1, le=250)) -> MailboxResponse:
    user = require_current_user(settings, request)
    return build_mailbox_response(settings, user_id=user.id, label="all", limit=limit)


@router.get("/v1/gmail-view", response_model=GmailViewResponse)
def gmail_view(request: Request) -> GmailViewResponse:
    user = require_current_user(settings, request)
    mailbox = build_mailbox_response(settings, user_id=user.id, label="inbox", limit=250)
    return GmailViewResponse(total_threads=mailbox.total_threads, sections=mailbox.sections)


@router.get("/v1/mail-groups/{group_id}", response_model=ThreadReaderResponse)
def mail_group_detail(request: Request, group_id: str) -> ThreadReaderResponse:
    user = require_current_user(settings, request)
    detail = build_group_detail_response(settings, user_id=user.id, group_id=group_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mail group not found")
    return detail
