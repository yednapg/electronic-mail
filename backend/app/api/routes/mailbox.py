from __future__ import annotations

"""Mailbox endpoints backed by AI-created mail groups."""

from base64 import urlsafe_b64decode
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import load_settings
from app.db.jobs import enqueue_job
from app.db.repository import get_user_by_email
from app.schemas.domain import MailboxResponse, MailboxSyncStateResponse, MailboxSyncTriggerResponse, ThreadReaderResponse
from app.services.auth import require_current_user
from app.services.mail_groups import build_group_detail_response, build_mailbox_response, build_mailbox_sync_state, enqueue_mailbox_sync

router = APIRouter(tags=["mailbox"])
settings = load_settings()


@router.get("/v1/mailbox", response_model=MailboxResponse)
def mailbox(
    request: Request,
    label: str = Query(default="inbox"),
    limit: int = Query(default=100, ge=1, le=250),
    cursor: str | None = Query(default=None),
) -> MailboxResponse:
    user = require_current_user(settings, request)
    return build_mailbox_response(settings, user_id=user.id, label=label, limit=limit, cursor=cursor)


@router.get("/v1/mailbox/threads/{group_id}", response_model=ThreadReaderResponse)
def mailbox_thread(
    request: Request,
    group_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ThreadReaderResponse:
    user = require_current_user(settings, request)
    detail = build_group_detail_response(settings, user_id=user.id, group_id=group_id, limit=limit, offset=offset)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mail group not found")
    return detail


@router.get("/v1/mailbox/sync-state", response_model=MailboxSyncStateResponse)
def mailbox_sync_state(request: Request) -> MailboxSyncStateResponse:
    user = require_current_user(settings, request)
    return build_mailbox_sync_state(settings, user_id=user.id)


@router.post("/v1/mailbox/sync", response_model=MailboxSyncTriggerResponse, status_code=202)
def mailbox_sync(request: Request) -> MailboxSyncTriggerResponse:
    user = require_current_user(settings, request)
    state = build_mailbox_sync_state(settings, user_id=user.id)
    if not state.connected:
        return MailboxSyncTriggerResponse(status="not_connected", state=state)
    job_id = enqueue_mailbox_sync(settings, user_id=user.id)
    return MailboxSyncTriggerResponse(status="queued", state=state, job_id=job_id)


@router.get("/v1/events/mailbox")
async def mailbox_events(request: Request) -> StreamingResponse:
    user = require_current_user(settings, request)

    async def event_stream():
        while True:
            state = build_mailbox_sync_state(settings, user_id=user.id)
            yield f"event: sync-state\ndata: {json.dumps(state.model_dump())}\n\n"
            await asyncio.sleep(15)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/v1/mailbox/pubsub", status_code=202)
async def mailbox_pubsub(request: Request) -> dict[str, object]:
    payload = await request.json()
    message = payload.get("message") if isinstance(payload, dict) and isinstance(payload.get("message"), dict) else payload
    data = message.get("data") if isinstance(message, dict) else None
    decoded: dict[str, Any] = {}
    if isinstance(data, str):
        try:
            padding = "=" * (-len(data) % 4)
            decoded = json.loads(urlsafe_b64decode(f"{data}{padding}".encode()).decode())
        except Exception:
            decoded = {}
    email_address = str(decoded.get("emailAddress") or "").strip().lower()
    history_id = str(decoded.get("historyId") or "")
    user = get_user_by_email(str(settings.database_path), email_address) if email_address else None
    if user is None:
        return {"status": "ignored", "reason": "unknown_user"}
    dedupe = f"gmail-pubsub:{user.id}:{history_id}"
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_pubsub_sync",
        queue="critical",
        user_id=user.id,
        dedupe_key=dedupe,
        payload={"user_id": user.id, "email_address": email_address, "history_id": history_id, "batch_size": 100, "pubsub": payload},
        priority=80,
    )
    return {"status": "queued", "job_id": job.id}
