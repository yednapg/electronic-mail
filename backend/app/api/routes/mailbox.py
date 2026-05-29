from __future__ import annotations

"""Mailbox endpoints backed by AI-created mail groups."""

from base64 import urlsafe_b64decode
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.core.config import load_settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import MailboxCursorError
from app.db.repository import get_user_by_email
from app.schemas.domain import MailComposeRequest, MailReplyRequest, MailSendResponse, MailboxResponse, MailboxSyncStateResponse, MailboxSyncTriggerResponse, QueuedThreadActionRequest, QueuedThreadActionResponse, ThreadReaderResponse
from app.services.auth import require_current_user
from app.services.gmail_importer import run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mailbox_actions import enqueue_thread_action
from app.services.mailbox_events import HEARTBEAT, SYNC_STATE, event_payload, format_sse_event, list_events_after, parse_last_event_id
from app.services.mailbox_sends import send_compose, send_reply
from app.services.mail_groups import build_app_session_response, build_group_detail_response, build_mailbox_response, build_mailbox_sync_state, enqueue_mailbox_sync, refresh_app_session_snapshot

router = APIRouter(tags=["mailbox"])
settings = load_settings()


@router.get("/v1/mailbox", response_model=MailboxResponse)
def mailbox(
    request: Request,
    label: str = Query(default="inbox"),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> MailboxResponse:
    user = require_current_user(settings, request)
    try:
        return build_mailbox_response(settings, user_id=user.id, label=label, limit=limit, cursor=cursor)
    except MailboxCursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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


@router.post("/v1/mailbox/thread-actions", response_model=QueuedThreadActionResponse, status_code=202)
def mailbox_thread_action(request: Request, payload: QueuedThreadActionRequest) -> QueuedThreadActionResponse:
    user = require_current_user(settings, request)
    return enqueue_thread_action(settings, user_id=user.id, request=payload)


@router.post("/v1/mailbox/compose", response_model=MailSendResponse, status_code=202)
def mailbox_compose(request: Request, payload: MailComposeRequest) -> MailSendResponse:
    user = require_current_user(settings, request)
    return send_compose(settings, user_id=user.id, request=payload)


@router.post("/v1/mailbox/threads/{mailbox_thread_id}/reply", response_model=MailSendResponse, status_code=202)
def mailbox_reply(request: Request, mailbox_thread_id: str, payload: MailReplyRequest) -> MailSendResponse:
    user = require_current_user(settings, request)
    return send_reply(settings, user_id=user.id, mailbox_thread_id=mailbox_thread_id, request=payload)


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


@router.post("/v1/mailbox/sync-now", response_model=MailboxSyncTriggerResponse)
def mailbox_sync_now(request: Request) -> MailboxSyncTriggerResponse:
    user = require_current_user(settings, request)
    state = build_mailbox_sync_state(settings, user_id=user.id)
    if not state.connected:
        return MailboxSyncTriggerResponse(status="not_connected", state=state)
    try:
        ensure_gmail_watch(settings, user_id=user.id)
        run_gmail_delta_sync(settings, user_id=user.id, batch_size=50)
        run_gmail_import_batch(settings, user_id=user.id, batch_size=100, first_run=False)
        refresh_app_session_snapshot(settings, user_id=user.id)
    except RuntimeError as exc:
        message = str(exc)
        if "credentials" in message.lower() or "not connected" in message.lower():
            return MailboxSyncTriggerResponse(status="not_connected", state=build_mailbox_sync_state(settings, user_id=user.id))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc
    return MailboxSyncTriggerResponse(status="synced", state=build_mailbox_sync_state(settings, user_id=user.id))


@router.get("/v1/events/mailbox")
async def mailbox_events(request: Request) -> StreamingResponse:
    user = require_current_user(settings, request)
    last_event_id = parse_last_event_id(request.headers.get("last-event-id"))

    async def event_stream():
        nonlocal last_event_id
        heartbeat_ticks = 0
        while True:
            if await request.is_disconnected():
                break
            for record in list_events_after(settings, user_id=user.id, after_id=last_event_id, limit=100):
                last_event_id = record.id
                yield format_sse_event(record.event_type, event_payload(record), event_id=record.id)
            heartbeat_ticks += 1
            if heartbeat_ticks >= 15:
                heartbeat_ticks = 0
                state = build_mailbox_sync_state(settings, user_id=user.id)
                yield format_sse_event(SYNC_STATE, state.model_dump(mode="json"))
            else:
                yield format_sse_event(HEARTBEAT, {"ok": True})
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _verify_pubsub_push(request: Request) -> None:
    if not settings.is_production_like:
        return
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Pub/Sub push token")
    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), settings.resolved_gmail_pubsub_push_audience)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Pub/Sub push token") from exc
    expected_email = settings.gmail_pubsub_push_service_account_email
    if expected_email:
        actual_email = str(claims.get("email") or "").strip().lower()
        if actual_email != expected_email or claims.get("email_verified") is False:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unexpected Pub/Sub push identity")


@router.post("/v1/mailbox/pubsub", status_code=202)
async def mailbox_pubsub(request: Request) -> dict[str, object]:
    _verify_pubsub_push(request)
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
    if not email_address or not history_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Pub/Sub Gmail emailAddress or historyId")
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
