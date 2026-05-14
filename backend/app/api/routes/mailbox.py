from __future__ import annotations

"""Mailbox-first Gmail endpoints backed by local Gmail snapshots."""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import load_settings
from app.schemas.domain import (
    MailboxResponse,
    MailboxSyncStateResponse,
    MailboxSyncTriggerResponse,
    ThreadReaderResponse,
)
from app.services.mailbox import (
    build_mailbox_response,
    build_mailbox_sync_state_response,
    build_mailbox_thread_response,
)
from app.services.mailbox_sync import ensure_gmail_watch, handle_pubsub_notification, queueable_mailbox_sync
from app.services.auth import require_current_user


router = APIRouter(tags=["mailbox"])
settings = load_settings()


@router.get("/v1/mailbox", response_model=MailboxResponse)
def mailbox(
    request: Request,
    label: str = Query(default="inbox"),
    limit: int = Query(default=100, ge=1, le=250),
    cursor: str | None = Query(default=None),
) -> MailboxResponse:
    """Return a label-filtered Gmail mailbox from local snapshots."""
    user = require_current_user(settings, request)
    return build_mailbox_response(
        str(settings.database_path),
        user_id=user.id,
        label=label,
        limit=limit,
        cursor=cursor,
    )


@router.get("/v1/mailbox/threads/{thread_id}", response_model=ThreadReaderResponse)
def mailbox_thread(
    request: Request,
    thread_id: str,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ThreadReaderResponse:
    """Open a Gmail thread directly from the mailbox snapshot ledger."""
    user = require_current_user(settings, request)
    thread = build_mailbox_thread_response(
        str(settings.database_path),
        user_id=user.id,
        thread_id=thread_id,
        limit=limit,
        offset=offset,
    )
    if thread.total_messages == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return thread


@router.get("/v1/mailbox/sync-state", response_model=MailboxSyncStateResponse)
def mailbox_sync_state(request: Request) -> MailboxSyncStateResponse:
    """Return Gmail sync/watch status for cache reconciliation."""
    user = require_current_user(settings, request)
    return build_mailbox_sync_state_response(settings, user_id=user.id)


@router.post("/v1/mailbox/sync", response_model=MailboxSyncTriggerResponse, status_code=202)
def mailbox_sync(request: Request, background_tasks: BackgroundTasks) -> MailboxSyncTriggerResponse:
    """Queue a Gmail sync without blocking mailbox/dashboard routes."""
    user = require_current_user(settings, request)
    state = build_mailbox_sync_state_response(settings, user_id=user.id)
    auth = state.connected
    if not auth:
        return MailboxSyncTriggerResponse(status="not_connected", state=state)

    background_tasks.add_task(ensure_gmail_watch, settings, user_id=user.id)
    background_tasks.add_task(queueable_mailbox_sync, settings, user_id=user.id)
    return MailboxSyncTriggerResponse(status="queued", state=state)


@router.get("/v1/events/mailbox")
async def mailbox_events(request: Request) -> StreamingResponse:
    """SSE stream for mailbox cache invalidation and sync-state heartbeats."""

    user = require_current_user(settings, request)

    async def event_stream():
        while True:
            state = build_mailbox_sync_state_response(settings, user_id=user.id)
            yield f"event: sync-state\ndata: {json.dumps(state.model_dump())}\n\n"
            await asyncio.sleep(15)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/v1/mailbox/pubsub", status_code=202)
async def mailbox_pubsub(request: Request) -> dict[str, object]:
    """Process one Gmail Pub/Sub push notification by the notified mailbox owner."""
    payload = await request.json()
    message = payload.get("message") if isinstance(payload, dict) and isinstance(payload.get("message"), dict) else payload
    state = handle_pubsub_notification(settings, message)
    return {"status": "processed", "state": state.model_dump()}
