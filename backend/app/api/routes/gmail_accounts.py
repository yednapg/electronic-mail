from __future__ import annotations

"""Authenticated Gmail-account metadata for settings and account pickers."""

import asyncio
from base64 import urlsafe_b64decode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from app.core.config import load_settings
from app.db.repository import (
    get_user,
    get_engine,
    get_gmail_account,
    gmail_account_import_ready,
    list_gmail_accounts,
    mark_gmail_account_ready,
    mark_gmail_account_importing,
    multi_account_migration_verified,
)
from sqlalchemy import text
from app.schemas.domain import (
    GmailAccountLinkStartRequest,
    GmailAccountLinkStartResponse,
    GmailAccountResponse,
    GmailAccountsResponse,
    MailboxResponse,
    MailComposeRequest,
    MailDraftResponse,
    MailDraftSaveRequest,
    MailDraftSendRequest,
    MailReplyRequest,
    MailSendResponse,
    MailboxSyncStateResponse,
    MailboxSyncTriggerResponse,
    QueuedThreadActionRequest,
    QueuedThreadActionResponse,
    ThreadReaderResponse,
)
from app.services.account_mailbox import (
    build_account_mailbox_response,
    build_account_thread_response,
    fetch_account_attachment,
    parse_scoped_thread_id,
    scope_mailbox_response,
    scope_thread_response,
    verify_account_mailbox_access,
)
from app.services.auth import require_current_user
from app.services.account_mailbox_writes import (
    delete_draft as delete_account_draft,
    enqueue_action as enqueue_account_action,
    outbox as account_outbox,
    retry_send as retry_account_send,
    read_draft as read_account_draft,
    save_draft as save_account_draft,
    send_compose as send_account_compose,
    send_draft as send_account_draft,
    send_reply as send_account_reply,
    send_status as account_send_status,
)
from app.services.integrations.google import get_google_auth_url
from app.services.mail_groups import (
    build_group_detail_response, build_mailbox_response,
    build_mailbox_sync_state, enqueue_first_run, enqueue_mailbox_sync,
)
from app.db.account_scope import gmail_account_scope
from app.db.account_mail import AccountWriteIdempotencyConflict
from app.services.mailbox_actions import enqueue_thread_action
from app.services.mailbox_drafts import delete_draft_by_id, save_draft, send_saved_draft
from app.services.mailbox_drafts import get_draft as get_primary_draft
from app.services.mailbox_sends import (
    get_send_status, list_outbox_statuses, retry_send, send_compose, send_reply,
)
from app.services.mailbox_events import (
    HEARTBEAT,
    SYNC_STATE,
    event_payload,
    format_sse_event,
    latest_event,
    list_events_after,
    parse_last_event_id,
)


router = APIRouter(tags=["gmail-accounts"])
settings = load_settings()


def _multi_account_enabled_for_user(database_url: str, *, user_id: str) -> bool:
    return bool(
        settings.multi_gmail_enabled
        and settings.multi_gmail_verified_backup_id
        and multi_account_migration_verified(database_url, user_id=user_id)
    )


@router.get("/v1/gmail-accounts", response_model=GmailAccountsResponse)
def gmail_accounts(request: Request) -> GmailAccountsResponse:
    """Return account metadata only; this endpoint never combines mail rows."""
    current_user = require_current_user(settings, request)
    database_url = str(settings.database_path)
    stored_user = get_user(database_url, current_user.id)
    if stored_user is None:  # The authenticated session raced account deletion.
        raise HTTPException(status_code=404, detail="Account not found")

    migration_verified = multi_account_migration_verified(
        database_url,
        user_id=current_user.id,
    )
    accounts = list_gmail_accounts(database_url, user_id=current_user.id)
    return GmailAccountsResponse(
        # A deployment flag can never bypass the per-user preservation gate.
        multi_account_enabled=_multi_account_enabled_for_user(
            database_url,
            user_id=current_user.id,
        ),
        migration_verified=migration_verified,
        max_accounts=settings.multi_gmail_max_accounts,
        primary_gmail_account_id=stored_user.primary_gmail_account_id,
        accounts=[
            GmailAccountResponse(
                id=account.id,
                email=account.email,
                display_name=account.display_name,
                state=account.state,  # type: ignore[arg-type]
                is_primary=account.id == stored_user.primary_gmail_account_id,
                initial_ready_at=account.initial_ready_at,
            )
            for account in accounts
        ],
    )


@router.post(
    "/v1/gmail-accounts/link",
    response_model=GmailAccountLinkStartResponse,
)
def start_gmail_account_link(
    payload: GmailAccountLinkStartRequest,
    request: Request,
) -> GmailAccountLinkStartResponse:
    """Start OAuth with an authenticated link intent, never a login intent."""
    current_user = require_current_user(settings, request)
    database_url = str(settings.database_path)
    if not _multi_account_enabled_for_user(database_url, user_id=current_user.id):
        raise HTTPException(
            status_code=409,
            detail="Adding Gmail accounts is not enabled for this preserved inbox yet",
        )
    if payload.redirect_to != settings.mobile_redirect_uri:
        raise HTTPException(status_code=400, detail="Unsupported Gmail-link redirect target")
    accounts = list_gmail_accounts(database_url, user_id=current_user.id)
    if len(accounts) >= settings.multi_gmail_max_accounts:
        raise HTTPException(status_code=409, detail="Maximum Gmail account count reached")
    return GmailAccountLinkStartResponse(
        authorization_url=get_google_auth_url(
            settings,
            redirect_to=payload.redirect_to,
            intent="link",
            initiating_user_id=current_user.id,
        )
    )


def _owned_account(request: Request, gmail_account_id: str):
    current_user = require_current_user(settings, request)
    account = get_gmail_account(
        str(settings.database_path),
        user_id=current_user.id,
        gmail_account_id=gmail_account_id,
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Gmail account not found")
    return current_user, account


def _is_primary(user_id: str, gmail_account_id: str) -> bool:
    user = get_user(str(settings.database_path), user_id)
    return user is not None and user.primary_gmail_account_id == gmail_account_id


def _require_account_write_ready(user_id: str, account) -> None:
    if account.state != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Gmail account is still importing; writes are not enabled yet",
        )
    if not _is_primary(user_id, account.id) and not settings.multi_gmail_writes_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secondary Gmail writes are not enabled for this rollout yet",
        )
    if not _is_primary(user_id, account.id) and not gmail_account_import_ready(
        str(settings.database_path),
        user_id=user_id,
        gmail_account_id=account.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Gmail account is still importing; writes are not enabled yet",
        )


def _runtime_role_bypasses_isolation() -> bool:
    with get_engine(str(settings.database_path)).connect() as connection:
        return bool(connection.execute(text(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )).scalar_one())


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _explicit_account_sync_state(*, user_id: str, account) -> MailboxSyncStateResponse:
    with get_engine(str(settings.database_path)).connect() as connection:
        row = connection.execute(text("""
            SELECT * FROM gmail_import_state
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
        """), {"user_id": user_id, "gmail_account_id": account.id}).mappings().first()
        total_threads = connection.execute(text("""
            SELECT COUNT(DISTINCT COALESCE(NULLIF(gmail_thread_id,''),message_id))
            FROM gmail_messages
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
        """), {"user_id": user_id, "gmail_account_id": account.id}).scalar_one()
        pending_actions = connection.execute(text("""
            SELECT COUNT(*) FROM gmail_pending_thread_actions
            WHERE user_id=:user_id AND gmail_account_id=:gmail_account_id
              AND state IN ('queued','applying','failed')
        """), {"user_id": user_id, "gmail_account_id": account.id}).scalar_one()
    values = dict(row) if row is not None else {}
    completed = bool(values.get("history_metadata_complete") and values.get("history_body_complete"))
    return MailboxSyncStateResponse(
        gmail_account_id=account.id,
        connected=account.state not in {"disconnected", "reauth_required", "deleting"},
        last_history_id=str(values.get("last_history_id") or "") or None,
        last_full_sync_at=_iso(values.get("last_import_completed_at")),
        watch_expiration_at=_iso(values.get("gmail_watch_expiration_at")),
        last_sync_started_at=_iso(values.get("last_import_started_at")),
        last_sync_completed_at=_iso(values.get("last_import_completed_at")),
        last_sync_error=str(values.get("last_sync_error") or "") or None,
        last_delta_sync_at=_iso(values.get("last_delta_sync_at")),
        total_threads=int(total_threads or 0),
        full_import_running=account.state == "importing",
        full_import_completed=completed,
        full_import_completed_at=_iso(values.get("last_import_completed_at")) if completed else None,
        pending_action_count=int(pending_actions or 0),
        sync_generation=str(values.get("sync_generation") or "") or None,
        phase=str(values.get("phase") or account.state),
        initial_target_count=values.get("initial_target_count"),
        initial_metadata_count=values.get("initial_metadata_count"),
        initial_body_target_count=values.get("initial_body_target_count"),
        initial_body_ready_count=values.get("initial_body_ready_count"),
        history_metadata_count=values.get("history_metadata_count"),
        history_body_ready_count=values.get("history_body_ready_count"),
        estimated_total_count=values.get("estimated_total_count"),
        initial_window_complete=values.get("initial_window_complete"),
        history_metadata_complete=values.get("history_metadata_complete"),
        history_body_complete=values.get("history_body_complete"),
        last_progress_at=_iso(values.get("last_progress_at")),
    )


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/setup",
    response_model=GmailAccountResponse,
)
def setup_gmail_account(request: Request, gmail_account_id: str) -> GmailAccountResponse:
    """Verify this Gmail's own credential, then make its picker entry visible."""
    current_user, account = _owned_account(request, gmail_account_id)
    try:
        verify_account_mailbox_access(
            settings,
            user_id=current_user.id,
            gmail_account_id=account.id,
        )
        if _is_primary(current_user.id, account.id):
            importing = mark_gmail_account_ready(
                str(settings.database_path), user_id=current_user.id,
                gmail_account_id=account.id,
            )
        else:
            importing = mark_gmail_account_importing(
                str(settings.database_path), user_id=current_user.id,
                gmail_account_id=account.id,
            )
            if not _runtime_role_bypasses_isolation():
                with gmail_account_scope(account.id):
                    enqueue_first_run(settings, user_id=current_user.id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The linked Gmail inbox could not be verified yet",
        ) from exc
    stored_user = get_user(str(settings.database_path), current_user.id)
    return GmailAccountResponse(
        id=importing.id,
        email=importing.email,
        display_name=importing.display_name,
        state=importing.state,  # type: ignore[arg-type]
        is_primary=stored_user is not None and importing.id == stored_user.primary_gmail_account_id,
        initial_ready_at=importing.initial_ready_at,
    )


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox",
    response_model=MailboxResponse,
)
def account_mailbox(
    request: Request,
    gmail_account_id: str,
    label: str = Query(default="inbox"),
    limit: int = Query(default=50, ge=1, le=1000),
    cursor: str | None = Query(default=None),
) -> MailboxResponse:
    current_user, account = _owned_account(request, gmail_account_id)
    stored_user = get_user(str(settings.database_path), current_user.id)
    if stored_user is not None and account.id == stored_user.primary_gmail_account_id:
        response = build_mailbox_response(
            settings,
            user_id=current_user.id,
            label=label,
            limit=limit,
            cursor=cursor,
        )
        return scope_mailbox_response(response, gmail_account_id=account.id)
    return build_account_mailbox_response(
        settings,
        user_id=current_user.id,
        gmail_account_id=account.id,
        label=label,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/search",
    response_model=MailboxResponse,
)
def account_mailbox_search(
    request: Request,
    gmail_account_id: str,
    q: str = Query(min_length=1, max_length=200),
    label: str = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> MailboxResponse:
    current_user, account = _owned_account(request, gmail_account_id)
    if _is_primary(current_user.id, account.id):
        response = build_mailbox_response(
            settings, user_id=current_user.id, label=label, limit=limit,
            cursor=cursor, search_query=q.strip(),
        )
        return scope_mailbox_response(response, gmail_account_id=account.id)
    return build_account_mailbox_response(
        settings, user_id=current_user.id, gmail_account_id=account.id,
        label=label, limit=limit, cursor=cursor, search_query=q,
    )


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/sync-state",
    response_model=MailboxSyncStateResponse,
)
def account_mailbox_sync_state(request: Request,
                               gmail_account_id: str) -> MailboxSyncStateResponse:
    user, account = _owned_account(request, gmail_account_id)
    if _is_primary(user.id, account.id):
        return build_mailbox_sync_state(settings, user_id=user.id).model_copy(
            update={"gmail_account_id": account.id}
        )
    if _runtime_role_bypasses_isolation():
        return _explicit_account_sync_state(user_id=user.id, account=account)
    with gmail_account_scope(account.id):
        return build_mailbox_sync_state(settings, user_id=user.id).model_copy(
            update={"gmail_account_id": account.id}
        )


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/sync",
    response_model=MailboxSyncTriggerResponse,
    status_code=202,
)
def account_mailbox_sync(request: Request,
                         gmail_account_id: str) -> MailboxSyncTriggerResponse:
    user, account = _owned_account(request, gmail_account_id)
    state = account_mailbox_sync_state(request, gmail_account_id)
    if not state.connected:
        return MailboxSyncTriggerResponse(status="not_connected", state=state)
    if not _is_primary(user.id, account.id) and _runtime_role_bypasses_isolation():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secondary Gmail import requires a non-superuser database runtime role",
        )
    with gmail_account_scope(account.id):
        job_id = enqueue_mailbox_sync(settings, user_id=user.id)
    return MailboxSyncTriggerResponse(status="queued", state=state, job_id=job_id)


@router.get("/v1/gmail-accounts/{gmail_account_id}/events/mailbox")
async def account_mailbox_events(request: Request,
                                 gmail_account_id: str) -> StreamingResponse:
    """Stream only events produced by one Gmail account."""
    user, account = _owned_account(request, gmail_account_id)
    last_event_id = parse_last_event_id(request.headers.get("last-event-id"))
    initial_state: MailboxSyncStateResponse | None = None
    if last_event_id is None:
        baseline = await asyncio.to_thread(
            latest_event,
            settings,
            user_id=user.id,
            gmail_account_id=account.id,
        )
        last_event_id = max(baseline.id - 1, 0) if baseline is not None else 0
        initial_state = await asyncio.to_thread(
            account_mailbox_sync_state,
            request,
            account.id,
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
                gmail_account_id=account.id,
                after_id=last_event_id,
                limit=100,
            )
            for record in records:
                last_event_id = record.id
                yield format_sse_event(
                    record.event_type,
                    event_payload(record),
                    event_id=record.id,
                )
            heartbeat_ticks += 1
            if heartbeat_ticks >= 15:
                heartbeat_ticks = 0
                state = await asyncio.to_thread(
                    account_mailbox_sync_state,
                    request,
                    account.id,
                )
                yield format_sse_event(SYNC_STATE, state.model_dump(mode="json"))
            else:
                yield format_sse_event(
                    HEARTBEAT,
                    {"ok": True, "gmail_account_id": account.id},
                )
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/threads/{thread_id}",
    response_model=ThreadReaderResponse,
)
def account_thread(
    request: Request,
    gmail_account_id: str,
    thread_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ThreadReaderResponse:
    current_user, account = _owned_account(request, gmail_account_id)
    parsed = parse_scoped_thread_id(thread_id)
    raw_thread_id = parsed[1] if parsed is not None else thread_id
    if parsed is not None and parsed[0] != account.id:
        raise HTTPException(status_code=404, detail="Mail thread not found")
    stored_user = get_user(str(settings.database_path), current_user.id)
    if stored_user is not None and account.id == stored_user.primary_gmail_account_id:
        detail = build_group_detail_response(
            settings,
            user_id=current_user.id,
            group_id=raw_thread_id,
            limit=limit,
            offset=offset,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Mail thread not found")
        return scope_thread_response(
            detail,
            gmail_account_id=account.id,
            gmail_thread_id=raw_thread_id,
        )
    try:
        return build_account_thread_response(
            settings,
            user_id=current_user.id,
            gmail_account_id=account.id,
            gmail_thread_id=raw_thread_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Mail thread not found") from exc


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/messages/{message_id}/attachments/{attachment_id}"
)
def account_attachment(
    request: Request,
    gmail_account_id: str,
    message_id: str,
    attachment_id: str,
) -> Response:
    current_user, account = _owned_account(request, gmail_account_id)
    try:
        payload = fetch_account_attachment(
            settings,
            user_id=current_user.id,
            gmail_account_id=account.id,
            message_id=message_id,
            attachment_id=attachment_id,
        )
        raw_data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_data, str) or not raw_data:
            raise ValueError("Attachment is empty")
        padding = "=" * (-len(raw_data) % 4)
        content = urlsafe_b64decode(f"{raw_data}{padding}".encode())
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to fetch Gmail attachment") from exc
    return Response(content=content, media_type="application/octet-stream")


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/compose",
    response_model=MailSendResponse,
    status_code=202,
)
def account_compose(request: Request, gmail_account_id: str,
                    payload: MailComposeRequest) -> MailSendResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    try:
        response = (send_compose(settings, user_id=user.id, request=payload)
                    if _is_primary(user.id, account.id)
                    else send_account_compose(settings, user_id=user.id,
                                              gmail_account_id=account.id, request=payload))
    except AccountWriteIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return response.model_copy(update={"gmail_account_id": account.id})


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/threads/{thread_id}/reply",
    response_model=MailSendResponse,
    status_code=202,
)
def account_reply(request: Request, gmail_account_id: str, thread_id: str,
                  payload: MailReplyRequest) -> MailSendResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    parsed = parse_scoped_thread_id(thread_id)
    if parsed is not None and parsed[0] != account.id:
        raise HTTPException(status_code=404, detail="Mail thread not found")
    raw_thread_id = parsed[1] if parsed else thread_id
    try:
        response = (send_reply(settings, user_id=user.id, mailbox_thread_id=raw_thread_id, request=payload)
                    if _is_primary(user.id, account.id)
                    else send_account_reply(settings, user_id=user.id, gmail_account_id=account.id,
                                            mailbox_thread_id=raw_thread_id, request=payload))
    except AccountWriteIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return response.model_copy(update={"gmail_account_id": account.id})


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/thread-actions",
    response_model=QueuedThreadActionResponse,
    status_code=202,
)
def account_thread_action(request: Request, gmail_account_id: str,
                          payload: QueuedThreadActionRequest) -> QueuedThreadActionResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    parsed = parse_scoped_thread_id(payload.mailbox_thread_id)
    if parsed is not None and parsed[0] != account.id:
        raise HTTPException(status_code=404, detail="Mail thread not found")
    raw_payload = payload.model_copy(update={"mailbox_thread_id": parsed[1] if parsed else payload.mailbox_thread_id})
    try:
        response = (enqueue_thread_action(settings, user_id=user.id, request=raw_payload)
                    if _is_primary(user.id, account.id)
                    else enqueue_account_action(settings, user_id=user.id,
                                                gmail_account_id=account.id, request=raw_payload))
    except AccountWriteIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return response.model_copy(update={"gmail_account_id": account.id})


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/outbox",
    response_model=list[MailSendResponse],
)
def account_mailbox_outbox(request: Request, gmail_account_id: str,
                           limit: int = Query(default=100, ge=1, le=200)) -> list[MailSendResponse]:
    user, account = _owned_account(request, gmail_account_id)
    responses = (list_outbox_statuses(settings, user_id=user.id, limit=limit)
                 if _is_primary(user.id, account.id)
                 else account_outbox(settings, user_id=user.id,
                                     gmail_account_id=account.id, limit=limit))
    return [item.model_copy(update={"gmail_account_id": account.id}) for item in responses]


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/sends/{server_send_id}",
    response_model=MailSendResponse,
)
def scoped_send_status(request: Request, gmail_account_id: str,
                       server_send_id: str) -> MailSendResponse:
    user, account = _owned_account(request, gmail_account_id)
    response = (get_send_status(settings, user_id=user.id, server_send_id=server_send_id)
                if _is_primary(user.id, account.id)
                else account_send_status(settings, user_id=user.id,
                                         gmail_account_id=account.id,
                                         server_send_id=server_send_id))
    if response is None:
        raise HTTPException(status_code=404, detail="Send not found")
    return response.model_copy(update={"gmail_account_id": account.id})


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/sends/{server_send_id}/retry",
    response_model=MailSendResponse,
)
def scoped_send_retry(request: Request, gmail_account_id: str,
                      server_send_id: str) -> MailSendResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    response = (retry_send(settings, user_id=user.id, server_send_id=server_send_id)
                if _is_primary(user.id, account.id)
                else retry_account_send(settings, user_id=user.id,
                                        gmail_account_id=account.id,
                                        server_send_id=server_send_id))
    if response is None:
        raise HTTPException(status_code=404, detail="Send not found")
    return response.model_copy(update={"gmail_account_id": account.id})


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/drafts",
    response_model=MailDraftResponse,
    status_code=201,
)
def account_draft_save(request: Request, gmail_account_id: str,
                       payload: MailDraftSaveRequest) -> MailDraftResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    response = (save_draft(settings, user_id=user.id, request=payload)
                if _is_primary(user.id, account.id)
                else save_account_draft(settings, user_id=user.id,
                                        gmail_account_id=account.id, request=payload))
    return response.model_copy(update={"gmail_account_id": account.id})


@router.put(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/drafts/{gmail_draft_id}",
    response_model=MailDraftResponse,
)
def account_draft_update(request: Request, gmail_account_id: str,
                         gmail_draft_id: str,
                         payload: MailDraftSaveRequest) -> MailDraftResponse:
    return account_draft_save(
        request, gmail_account_id,
        payload.model_copy(update={"gmail_draft_id": gmail_draft_id}),
    )


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/drafts/{gmail_draft_id}",
    response_model=MailDraftResponse,
)
def account_draft_get(request: Request, gmail_account_id: str,
                      gmail_draft_id: str) -> MailDraftResponse:
    user, account = _owned_account(request, gmail_account_id)
    if _is_primary(user.id, account.id):
        response = get_primary_draft(
            settings, user_id=user.id, mailbox_thread_id=gmail_draft_id,
        )
    else:
        try:
            response = read_account_draft(
                settings, user_id=user.id, gmail_account_id=account.id,
                gmail_draft_id=gmail_draft_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Draft not found") from exc
    return response.model_copy(update={"gmail_account_id": account.id})


@router.delete(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/drafts/{gmail_draft_id}",
    status_code=204,
)
def account_draft_delete(request: Request, gmail_account_id: str,
                         gmail_draft_id: str) -> Response:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    if _is_primary(user.id, account.id):
        if not delete_draft_by_id(settings, user_id=user.id, gmail_draft_id=gmail_draft_id):
            raise HTTPException(status_code=403, detail="Google needs full mail permission")
    else:
        try:
            delete_account_draft(settings, user_id=user.id,
                                 gmail_account_id=account.id,
                                 gmail_draft_id=gmail_draft_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Draft not found") from exc
    return Response(status_code=204)


@router.post(
    "/v1/gmail-accounts/{gmail_account_id}/mailbox/drafts/{gmail_draft_id}/send",
    response_model=MailSendResponse,
)
def account_draft_send(request: Request, gmail_account_id: str,
                       gmail_draft_id: str,
                       payload: MailDraftSendRequest) -> MailSendResponse:
    user, account = _owned_account(request, gmail_account_id)
    _require_account_write_ready(user.id, account)
    response = (send_saved_draft(settings, user_id=user.id,
                                 gmail_draft_id=gmail_draft_id, request=payload)
                if _is_primary(user.id, account.id)
                else send_account_draft(settings, user_id=user.id,
                                        gmail_account_id=account.id, request=payload))
    return response.model_copy(update={"gmail_account_id": account.id})
