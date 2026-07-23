from __future__ import annotations

"""Mailbox endpoints backed by raw Gmail threads."""

from base64 import urlsafe_b64decode
import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.core.config import load_settings
from app.core.error_safety import safe_google_error
from app.db.jobs import enqueue_job
from app.db.mail_groups import MailboxCursorError, MailSendIdempotencyConflict, list_messages_by_ids
from app.db.repository import get_user_by_email
from app.db.user_mail_guard import UserMailWorkBlocked
from app.schemas.domain import MailComposeRequest, MailDraftResponse, MailDraftSaveRequest, MailDraftSendRequest, MailReplyRequest, MailSendResponse, MailboxRealtimeStateResponse, MailboxResponse, MailboxSyncStateResponse, MailboxSyncTriggerResponse, QueuedThreadActionRequest, QueuedThreadActionResponse, ThreadReaderResponse
from app.services.auth import require_current_user
from app.services.gmail_importer import run_gmail_delta_sync
from app.services.gmail_watch import ensure_gmail_watch
from app.services.integrations.google import fetch_gmail_attachment
from app.services.mailbox_actions import ThreadActionIdempotencyConflict, enqueue_thread_action
from app.services.mailbox_drafts import delete_draft_by_id, get_draft, save_draft, send_saved_draft
from app.services.mailbox_events import GMAIL_PUBSUB_RECEIVED, HEARTBEAT, SYNC_STATE, emit_mailbox_event, event_payload, format_sse_event, latest_event, list_events_after, parse_last_event_id
from app.services.mailbox_search import enqueue_mailbox_search_hydration
from app.services.mailbox_sends import _validate_subject, _validated_addresses, _validated_attachments, get_send_status, list_outbox_statuses, retry_send, send_compose, send_reply
from app.services.mail_groups import build_app_session_response, build_group_detail_response, build_mailbox_realtime_state, build_mailbox_response, build_mailbox_sync_state, enqueue_mailbox_sync, gmail_attachments_for_message, refresh_app_session_snapshot

router = APIRouter(tags=["mailbox"])
MAX_PUBSUB_BODY_BYTES = 64 * 1024
settings = load_settings()
logger = logging.getLogger(__name__)


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


@router.get("/v1/mailbox/search", response_model=MailboxResponse)
def mailbox_search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    label: str = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
    hydrate: bool = Query(default=True),
) -> MailboxResponse:
    """Search the canonical local mailbox without blocking on Google's API.

    Gmail synchronization continuously hydrates this database in the background.
    Performing a live Gmail search here made every interactive query wait on an
    external network round trip plus full-message downloads.
    """
    user = require_current_user(settings, request)
    try:
        response = build_mailbox_response(
            settings,
            user_id=user.id,
            label=label,
            limit=limit,
            cursor=cursor,
            search_query=q.strip(),
        )
    except MailboxCursorError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if hydrate and cursor is None:
        try:
            enqueue_mailbox_search_hydration(
                settings,
                user_id=user.id,
                query=q,
                label=label,
                limit=limit,
            )
        except Exception as exc:
            logger.warning(
                "Gmail search hydration could not be queued; local results returned user_id=%s error=%s",
                user.id,
                type(exc).__name__,
            )
    return response


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


@router.get("/v1/mailbox/messages/{message_id}/attachments/{attachment_id}")
def mailbox_attachment(request: Request, message_id: str, attachment_id: str) -> Response:
    user = require_current_user(settings, request)
    messages = list_messages_by_ids(str(settings.database_path), user_id=user.id, message_ids=[message_id])
    if not messages:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    attachment = next(
        (item for item in gmail_attachments_for_message(messages[0]) if item.attachment_id == attachment_id),
        None,
    )
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    try:
        payload = fetch_gmail_attachment(settings, user_id=user.id, message_id=message_id, attachment_id=attachment_id)
        raw_data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_data, str) or not raw_data:
            raise RuntimeError("Gmail attachment payload is empty")
        padding = "=" * (-len(raw_data) % 4)
        content = urlsafe_b64decode(f"{raw_data}{padding}".encode())
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to fetch Gmail attachment") from exc

    filename = _safe_attachment_filename(attachment.filename)
    return Response(
        content=content,
        media_type=attachment.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _attachment_content_disposition(filename)},
    )


@router.post("/v1/mailbox/thread-actions", response_model=QueuedThreadActionResponse, status_code=202)
def mailbox_thread_action(request: Request, payload: QueuedThreadActionRequest) -> QueuedThreadActionResponse:
    user = require_current_user(settings, request)
    try:
        return enqueue_thread_action(settings, user_id=user.id, request=payload)
    except ThreadActionIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/v1/mailbox/compose", response_model=MailSendResponse, status_code=202)
def mailbox_compose(request: Request, payload: MailComposeRequest) -> MailSendResponse:
    user = require_current_user(settings, request)
    _validate_outgoing_payload(payload, require_to=True)
    try:
        return send_compose(settings, user_id=user.id, request=payload)
    except MailSendIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/v1/mailbox/outbox", response_model=list[MailSendResponse])
def mailbox_outbox(request: Request, limit: int = Query(default=100, ge=1, le=200)) -> list[MailSendResponse]:
    user = require_current_user(settings, request)
    return list_outbox_statuses(settings, user_id=user.id, limit=limit)


@router.get("/v1/mailbox/sends/{server_send_id}", response_model=MailSendResponse)
def mailbox_send_status(request: Request, server_send_id: str) -> MailSendResponse:
    user = require_current_user(settings, request)
    response = get_send_status(settings, user_id=user.id, server_send_id=server_send_id)
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Send not found")
    return response


@router.post("/v1/mailbox/sends/{server_send_id}/retry", response_model=MailSendResponse, status_code=202)
def mailbox_send_retry(request: Request, server_send_id: str) -> MailSendResponse:
    user = require_current_user(settings, request)
    response = retry_send(settings, user_id=user.id, server_send_id=server_send_id)
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Send not found")
    return response


@router.post("/v1/mailbox/threads/{mailbox_thread_id}/reply", response_model=MailSendResponse, status_code=202)
def mailbox_reply(request: Request, mailbox_thread_id: str, payload: MailReplyRequest) -> MailSendResponse:
    user = require_current_user(settings, request)
    _validate_outgoing_payload(payload, require_to=payload.mode == "forward")
    try:
        return send_reply(settings, user_id=user.id, mailbox_thread_id=mailbox_thread_id, request=payload)
    except MailSendIdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/v1/mailbox/drafts", response_model=MailDraftResponse, status_code=201)
def mailbox_draft_create(request: Request, payload: MailDraftSaveRequest) -> MailDraftResponse:
    user = require_current_user(settings, request)
    _validate_outgoing_payload(payload, require_to=False)
    return save_draft(settings, user_id=user.id, request=payload)


@router.get("/v1/mailbox/drafts/{mailbox_thread_id}", response_model=MailDraftResponse)
def mailbox_draft_get(request: Request, mailbox_thread_id: str) -> MailDraftResponse:
    user = require_current_user(settings, request)
    response = get_draft(settings, user_id=user.id, mailbox_thread_id=mailbox_thread_id)
    if response.state == "failed" and response.error == "Draft not found.":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=response.error)
    return response


@router.put("/v1/mailbox/drafts/{gmail_draft_id}", response_model=MailDraftResponse)
def mailbox_draft_update(request: Request, gmail_draft_id: str, payload: MailDraftSaveRequest) -> MailDraftResponse:
    user = require_current_user(settings, request)
    _validate_outgoing_payload(payload, require_to=False)
    return save_draft(settings, user_id=user.id, request=payload.model_copy(update={"gmail_draft_id": gmail_draft_id}))


@router.delete("/v1/mailbox/drafts/{gmail_draft_id}", status_code=204)
def mailbox_draft_delete(request: Request, gmail_draft_id: str) -> Response:
    user = require_current_user(settings, request)
    if not delete_draft_by_id(settings, user_id=user.id, gmail_draft_id=gmail_draft_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google needs full mail permission.")
    return Response(status_code=204)


@router.post("/v1/mailbox/drafts/{gmail_draft_id}/send", response_model=MailSendResponse)
def mailbox_draft_send(request: Request, gmail_draft_id: str, payload: MailDraftSendRequest) -> MailSendResponse:
    user = require_current_user(settings, request)
    return send_saved_draft(settings, user_id=user.id, gmail_draft_id=gmail_draft_id, request=payload)


@router.get("/v1/mailbox/sync-state", response_model=MailboxSyncStateResponse)
def mailbox_sync_state(request: Request) -> MailboxSyncStateResponse:
    user = require_current_user(settings, request)
    return build_mailbox_sync_state(settings, user_id=user.id)


@router.get("/v1/mailbox/realtime-state", response_model=MailboxRealtimeStateResponse)
def mailbox_realtime_state(request: Request) -> MailboxRealtimeStateResponse:
    user = require_current_user(settings, request)
    return build_mailbox_realtime_state(settings, user_id=user.id)


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
        refresh_app_session_snapshot(settings, user_id=user.id)
    except RuntimeError as exc:
        message = str(exc)
        if "credentials" in message.lower() or "not connected" in message.lower():
            return MailboxSyncTriggerResponse(status="not_connected", state=build_mailbox_sync_state(settings, user_id=user.id))
        logger.warning("Gmail sync failed user_id=%s error=%s", user.id, type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=safe_google_error(exc, operation="mail sync")) from exc
    return MailboxSyncTriggerResponse(status="synced", state=build_mailbox_sync_state(settings, user_id=user.id))


@router.get("/v1/events/mailbox")
async def mailbox_events(request: Request) -> StreamingResponse:
    user = require_current_user(settings, request)
    last_event_id = parse_last_event_id(request.headers.get("last-event-id"))
    initial_state: MailboxSyncStateResponse | None = None
    if last_event_id is None:
        # A fresh native process has already loaded an authoritative session and
        # mailbox snapshot. Start immediately before the latest durable event
        # instead of replaying up to seven days, then send a revision handshake
        # so a change racing that snapshot still triggers one refresh. Replaying
        # that single event preserves targeted hydration/search invalidations
        # which do not necessarily advance the mailbox revision.
        baseline = await asyncio.to_thread(
            latest_event,
            settings,
            user_id=user.id,
        )
        last_event_id = max(baseline.id - 1, 0) if baseline is not None else 0
        initial_state = await asyncio.to_thread(
            build_mailbox_sync_state,
            settings,
            user_id=user.id,
        )

    async def event_stream():
        nonlocal initial_state, last_event_id
        heartbeat_ticks = 0
        if initial_state is not None:
            yield format_sse_event(
                SYNC_STATE,
                initial_state.model_dump(mode="json"),
                event_id=last_event_id,
            )
            initial_state = None
        while True:
            if await request.is_disconnected():
                break
            records = await asyncio.to_thread(
                list_events_after,
                settings,
                user_id=user.id,
                after_id=last_event_id,
                limit=100,
            )
            for record in records:
                last_event_id = record.id
                yield format_sse_event(record.event_type, event_payload(record), event_id=record.id)
            heartbeat_ticks += 1
            if heartbeat_ticks >= 15:
                heartbeat_ticks = 0
                state = await asyncio.to_thread(
                    build_mailbox_sync_state,
                    settings,
                    user_id=user.id,
                )
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
        if actual_email != expected_email or claims.get("email_verified") is not True:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unexpected Pub/Sub push identity")


@router.post("/v1/mailbox/pubsub", status_code=202)
async def mailbox_pubsub(request: Request) -> dict[str, object]:
    _verify_pubsub_push(request)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_PUBSUB_BODY_BYTES:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Pub/Sub payload is too large")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length") from None
    raw_payload = await request.body()
    if len(raw_payload) > MAX_PUBSUB_BODY_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Pub/Sub payload is too large")
    try:
        payload = json.loads(raw_payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Pub/Sub JSON payload") from None
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
    if not email_address or not history_id or not history_id.isdigit():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Pub/Sub Gmail emailAddress or historyId")
    user = get_user_by_email(str(settings.database_path), email_address) if email_address else None
    if user is None:
        logger.info("Ignoring Gmail Pub/Sub notification for an unknown account")
        return {"status": "ignored", "reason": "unknown_user"}
    logger.info("Received Gmail Pub/Sub notification user_id=%s", user.id)
    try:
        emit_mailbox_event(
            settings,
            user_id=user.id,
            event_type=GMAIL_PUBSUB_RECEIVED,
            payload={"email_address": email_address, "history_id": history_id},
        )
        dedupe = f"gmail-pubsub:{user.id}:{history_id}"
        job = enqueue_job(
            str(settings.database_path),
            kind="gmail_pubsub_sync",
            queue="critical",
            user_id=user.id,
            dedupe_key=dedupe,
            payload={
                "user_id": user.id,
                "history_id": history_id,
                "batch_size": 100,
                "source": "pubsub",
            },
            priority=80,
        )
    except UserMailWorkBlocked:
        return {"status": "ignored", "reason": "disconnected"}
    return {"status": "queued", "job_id": job.id}


def _safe_attachment_filename(filename: str) -> str:
    cleaned = "".join(
        "_" if character in '/\\:\0\r\n\t";=' or ord(character) < 32 or ord(character) == 127 else character
        for character in filename
    ).strip(" .")
    return (cleaned or "attachment")[:255]


def _attachment_content_disposition(filename: str) -> str:
    safe = _safe_attachment_filename(filename)
    ascii_fallback = "".join(character if 32 <= ord(character) < 127 else "_" for character in safe)
    encoded = quote(safe, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _validate_outgoing_payload(payload: Any, *, require_to: bool) -> None:
    try:
        to = _validated_addresses(list(getattr(payload, "to", []) or []))
        cc = _validated_addresses(list(getattr(payload, "cc", []) or []))
        bcc = _validated_addresses(list(getattr(payload, "bcc", []) or []))
        if len({address.lower() for address in [*to, *cc, *bcc]}) > 100:
            raise ValueError("A message can have at most 100 total recipients.")
        if require_to and not to:
            raise ValueError("Add at least one recipient.")
        subject = getattr(payload, "subject", None)
        if subject is not None:
            _validate_subject(str(subject))
        attachments = getattr(payload, "attachments", None)
        if attachments is not None:
            _validated_attachments(list(attachments))
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_413_CONTENT_TOO_LARGE if "limit" in message.lower() or "exceed" in message.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=message) from exc
