from __future__ import annotations

"""Compose and reply sends for the native mailbox surface."""

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from hashlib import sha256
from html import escape as html_escape
import logging
import mimetypes
from typing import Any

from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.core.error_safety import GoogleCredentialsUnavailable, safe_google_error
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    PendingSendRecord,
    claim_pending_send,
    get_mail_group_detail,
    get_pending_send,
    list_outbox_sends,
    list_messages_for_gmail_thread,
    mark_pending_send_failed,
    mark_pending_send_sent,
    upsert_gmail_messages,
    upsert_pending_send,
    user_can_write_gmail,
)
from app.db.repository import get_user
from app.db.user_mail_guard import UserMailWorkBlocked, shared_user_mail_lock
from app.schemas.domain import MailComposeRequest, MailReplyRequest, MailSendResponse
from app.services.email_extraction import mark_full_gmail_payload_body_fetch_status, parse_gmail_message
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    MultipleSentGmailMessagesFound,
    fetch_gmail_attachment,
    fetch_gmail_message,
    find_sent_gmail_message_by_rfc822_message_id,
    missing_google_scopes,
    send_gmail_raw_message,
)
from app.services.mailbox_events import DASHBOARD_CHANGED, MAILBOX_CHANGED, emit_mailbox_event
from app.services.mail_groups import enqueue_projection_refresh, gmail_attachments_for_message, rebuild_touched_mail_groups, refresh_app_session_snapshot

logger = logging.getLogger(__name__)

_AMBIGUOUS_GOOGLE_HTTP_STATUSES = {408, 409, 425, 429}
_GOOGLE_AUTH_HTTP_STATUSES = {401, 403}


@dataclass(frozen=True)
class PreparedResponseMessage:
    """Fully resolved RFC message fields shared by response drafts and sends."""

    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_text: str
    body_html: str | None
    headers: dict[str, Any]
    attachments: list[dict[str, str]]
    gmail_thread_id: str | None


class MailSendConfirmationPending(RuntimeError):
    """Signal the worker to retry reconciliation without authorizing resend."""


def send_compose(settings: Settings, *, user_id: str, request: MailComposeRequest) -> MailSendResponse:
    if not _can_send(settings, user_id=user_id):
        return _reauth_required(settings, request.client_send_id)
    try:
        to = _validated_addresses(request.to)
        cc = _validated_addresses(request.cc)
        bcc = _validated_addresses(request.bcc)
        _validate_subject(request.subject)
    except ValueError as exc:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error=str(exc))
    if not to:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error="Add at least one recipient.")
    subject = request.subject.strip()
    body_text = request.body_text.strip()
    try:
        attachments = _validated_attachments(request.attachments)
    except ValueError as exc:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error=str(exc))
    record = upsert_pending_send(
        str(settings.database_path),
        user_id=user_id,
        client_send_id=request.client_send_id,
        send_type="compose",
        mailbox_thread_id=None,
        gmail_thread_id=None,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_text=body_text,
        body_html=request.body_html,
        headers={},
        attachments=attachments,
        created_at=request.created_at,
    )
    return _send_or_queue(settings, user_id=user_id, record=record)


def send_reply(settings: Settings, *, user_id: str, mailbox_thread_id: str, request: MailReplyRequest) -> MailSendResponse:
    if not _can_send(settings, user_id=user_id):
        return _reauth_required(settings, request.client_send_id, mailbox_thread_id=mailbox_thread_id)
    body_text = request.body_text.strip()
    if not body_text and request.mode != "forward":
        return MailSendResponse(client_send_id=request.client_send_id, mailbox_thread_id=mailbox_thread_id, state="failed", error="Write a reply.")
    try:
        prepared = prepare_response_message(
            settings,
            user_id=user_id,
            mailbox_thread_id=mailbox_thread_id,
            source_message_id=request.source_message_id,
            mode=request.mode,
            to=request.to,
            cc=request.cc,
            bcc=request.bcc,
            subject=request.subject,
            body_text=body_text,
            body_html=request.body_html,
            attachments=request.attachments,
            include_quoted_original=request.include_quoted_original,
            include_original_attachments=request.include_original_attachments,
            explicit_fields=set(request.model_fields_set),
        )
    except ValueError as exc:
        return MailSendResponse(
            client_send_id=request.client_send_id,
            mailbox_thread_id=mailbox_thread_id,
            state="failed",
            error=str(exc),
        )
    if (request.mode == "forward" and not prepared.to) or (
        request.mode != "forward" and not (prepared.to or prepared.cc or prepared.bcc)
    ):
        return MailSendResponse(
            client_send_id=request.client_send_id,
            mailbox_thread_id=mailbox_thread_id,
            state="failed",
            error="Add at least one recipient." if request.mode == "forward" else "No reply recipient found.",
        )
    record = upsert_pending_send(
        str(settings.database_path),
        user_id=user_id,
        client_send_id=request.client_send_id,
        send_type=request.mode,
        mailbox_thread_id=mailbox_thread_id,
        gmail_thread_id=prepared.gmail_thread_id,
        to=prepared.to,
        cc=prepared.cc,
        bcc=prepared.bcc,
        subject=prepared.subject,
        body_text=prepared.body_text,
        body_html=prepared.body_html,
        headers=prepared.headers,
        attachments=prepared.attachments,
        created_at=request.created_at,
    )
    return _send_or_queue(settings, user_id=user_id, record=record)


def prepare_response_message(
    settings: Settings,
    *,
    user_id: str,
    mailbox_thread_id: str,
    source_message_id: str | None,
    mode: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str | None,
    body_text: str,
    body_html: str | None,
    attachments: list[Any],
    include_quoted_original: bool,
    include_original_attachments: bool,
    explicit_fields: set[str],
    reconcile_original_attachments: bool = False,
) -> PreparedResponseMessage:
    """Resolve one response payload exactly as Gmail will receive it.

    This intentionally does not require a recipient or non-empty body: Gmail
    drafts may be incomplete. The send path performs those final checks after
    calling this shared preparation function.
    """

    if mode not in {"reply", "reply_all", "forward"}:
        raise ValueError("Email response mode is invalid.")
    context = _reply_context(
        settings,
        user_id=user_id,
        mailbox_thread_id=mailbox_thread_id,
        source_message_id=source_message_id,
    )
    if context is None:
        raise ValueError("Email thread not found.")
    explicit_to = _validated_addresses(to)
    explicit_cc = _validated_addresses(cc)
    explicit_bcc = _validated_addresses(bcc)
    prepared_attachments = _validated_attachments(attachments)
    prepared_body_text = body_text
    prepared_body_html = body_html

    if mode == "forward":
        prepared_to = explicit_to
        prepared_cc = explicit_cc
        prepared_bcc = explicit_bcc
        prepared_subject = (subject or context["forward_subject"]).strip()
        headers: dict[str, Any] = {}
        gmail_thread_id = None
        if include_quoted_original:
            prepared_body_text = _forward_body(prepared_body_text, context)
            prepared_body_html = _quoted_html_body(body_html, body_text, context, forward=True)
        if include_original_attachments or reconcile_original_attachments:
            original_attachments = _validated_attachments(
                _original_attachment_values(settings, user_id=user_id, message=context["original_message"])
            )
            prepared_attachments = _reconcile_original_attachments(
                prepared_attachments,
                original_attachments,
                include=include_original_attachments,
            )
    elif mode == "reply_all":
        prepared_to, prepared_cc, prepared_bcc = _reply_recipients(
            to=explicit_to if "to" in explicit_fields else context["to"],
            cc=explicit_cc if "cc" in explicit_fields else context["reply_all_cc"],
            bcc=explicit_bcc,
            user_email=context["user_email"],
        )
        prepared_subject = (subject or context["reply_subject"]).strip()
        headers = context["headers"]
        gmail_thread_id = context["gmail_thread_id"]
        if include_quoted_original:
            prepared_body_text = _reply_body(prepared_body_text, context)
            prepared_body_html = _quoted_html_body(body_html, body_text, context, forward=False)
    else:
        prepared_to, prepared_cc, prepared_bcc = _reply_recipients(
            to=explicit_to if "to" in explicit_fields else context["to"],
            cc=explicit_cc,
            bcc=explicit_bcc,
            user_email=context["user_email"],
        )
        prepared_subject = (subject or context["reply_subject"]).strip()
        headers = context["headers"]
        gmail_thread_id = context["gmail_thread_id"]
        if include_quoted_original:
            prepared_body_text = _reply_body(prepared_body_text, context)
            prepared_body_html = _quoted_html_body(body_html, body_text, context, forward=False)

    _validate_subject(prepared_subject)
    return PreparedResponseMessage(
        to=prepared_to,
        cc=prepared_cc,
        bcc=prepared_bcc,
        subject=prepared_subject,
        body_text=prepared_body_text,
        body_html=prepared_body_html,
        headers=headers,
        attachments=prepared_attachments,
        gmail_thread_id=gmail_thread_id,
    )


def run_pending_send(settings: Settings, *, user_id: str, server_send_id: str) -> MailSendResponse | None:
    record = get_pending_send(str(settings.database_path), user_id=user_id, server_send_id=server_send_id)
    if record is None:
        return None
    if record.state == "sent":
        return _response_from_record(record)
    try:
        sent = _perform_send(settings, user_id=user_id, record=record)
        if sent.state == "sending" or (sent.state == "queued" and sent.error):
            raise MailSendConfirmationPending(
                f"Mail delivery confirmation is still pending for {sent.server_send_id}"
            )
        return _response_from_record(sent)
    except MailSendConfirmationPending:
        raise
    except UserMailWorkBlocked:
        raise
    except GoogleCredentialsUnavailable as exc:
        safe_error = safe_google_error(exc, operation="mail delivery")
        mark_pending_send_failed(
            str(settings.database_path),
            user_id=user_id,
            server_send_id=record.server_send_id,
            error=safe_error,
        )
        raise
    except HttpError as exc:
        safe_error = safe_google_error(exc, operation="mail delivery")
        if _google_http_status(exc) in _GOOGLE_AUTH_HTTP_STATUSES:
            failed = mark_pending_send_failed(
                str(settings.database_path),
                user_id=user_id,
                server_send_id=record.server_send_id,
                error="Google needs mail send permission.",
            )
            return _response_from_record(failed or record)
        if _is_definite_google_client_rejection(exc):
            failed = mark_pending_send_failed(
                str(settings.database_path),
                user_id=user_id,
                server_send_id=record.server_send_id,
                error=safe_error,
            )
            return _response_from_record(failed or record)
        # Gmail may have accepted the message even though the response was
        # lost. Keep the `sending` checkpoint so this worker can never turn a
        # single search miss into permission to send the RFC message again.
        current = _current_pending_send(settings, user_id=user_id, fallback=record)
        if current.state == "sending" or (current.state == "queued" and current.error):
            raise MailSendConfirmationPending(
                f"Mail delivery confirmation is still pending for {current.server_send_id}"
            ) from exc
        raise
    except Exception as exc:
        logger.warning(
            "Mail delivery outcome remains ambiguous user_id=%s server_send_id=%s error=%s",
            user_id,
            record.server_send_id,
            type(exc).__name__,
        )
        current = _current_pending_send(settings, user_id=user_id, fallback=record)
        if current.state == "sending" or (current.state == "queued" and current.error):
            raise MailSendConfirmationPending(
                f"Mail delivery confirmation is still pending for {current.server_send_id}"
            ) from exc
        raise


def _send_or_queue(settings: Settings, *, user_id: str, record: PendingSendRecord) -> MailSendResponse:
    if record.state == "sent":
        return _response_from_record(record)
    if record.state in {"sending", "queued"} and record.gmail_message_id:
        return _response_from_record(record)
    try:
        sent = _perform_send(settings, user_id=user_id, record=record)
        return _response_from_record(sent)
    except UserMailWorkBlocked:
        return _reauth_required(
            settings,
            record.client_send_id,
            mailbox_thread_id=record.mailbox_thread_id,
        )
    except GoogleCredentialsUnavailable as exc:
        safe_error = safe_google_error(exc, operation="mail delivery")
        failed = mark_pending_send_failed(
            str(settings.database_path),
            user_id=user_id,
            server_send_id=record.server_send_id,
            error=safe_error,
        )
        response = _reauth_required(settings, record.client_send_id, mailbox_thread_id=record.mailbox_thread_id)
        response.server_send_id = record.server_send_id
        response.error = (failed or record).error or safe_error
        return response
    except HttpError as exc:
        if _google_http_status(exc) in _GOOGLE_AUTH_HTTP_STATUSES:
            failed = mark_pending_send_failed(
                str(settings.database_path),
                user_id=user_id,
                server_send_id=record.server_send_id,
                error="Google needs mail send permission.",
            )
            response = _reauth_required(settings, record.client_send_id, mailbox_thread_id=record.mailbox_thread_id)
            response.server_send_id = record.server_send_id
            response.error = (failed or record).error
            return response
        if _is_definite_google_client_rejection(exc):
            failed = mark_pending_send_failed(
                str(settings.database_path),
                user_id=user_id,
                server_send_id=record.server_send_id,
                error=safe_google_error(exc, operation="mail delivery"),
            )
            return _response_from_record(failed or record)
        return _response_from_record(_current_pending_send(settings, user_id=user_id, fallback=record))
    except Exception as exc:
        logger.warning(
            "Mail delivery outcome remains ambiguous user_id=%s server_send_id=%s error=%s",
            user_id,
            record.server_send_id,
            type(exc).__name__,
        )
        return _response_from_record(_current_pending_send(settings, user_id=user_id, fallback=record))


def get_send_status(settings: Settings, *, user_id: str, server_send_id: str) -> MailSendResponse | None:
    record = get_pending_send(str(settings.database_path), user_id=user_id, server_send_id=server_send_id)
    return _response_from_record(record) if record is not None else None


def list_outbox_statuses(settings: Settings, *, user_id: str, limit: int = 100) -> list[MailSendResponse]:
    return [
        _response_from_record(record)
        for record in list_outbox_sends(str(settings.database_path), user_id=user_id, limit=limit)
    ]


def retry_send(settings: Settings, *, user_id: str, server_send_id: str) -> MailSendResponse | None:
    record = get_pending_send(str(settings.database_path), user_id=user_id, server_send_id=server_send_id)
    if record is None:
        return None
    if record.state == "sent":
        return _response_from_record(record)
    if not _can_send(settings, user_id=user_id):
        response = _reauth_required(settings, record.client_send_id, mailbox_thread_id=record.mailbox_thread_id)
        response.server_send_id = record.server_send_id
        return response
    return _send_or_queue(settings, user_id=user_id, record=record)


def _perform_send(settings: Settings, *, user_id: str, record: PendingSendRecord) -> PendingSendRecord:
    database_url = str(settings.database_path)
    with shared_user_mail_lock(database_url, user_id=user_id):
        return _perform_send_locked(settings, user_id=user_id, record=record)


def _perform_send_locked(settings: Settings, *, user_id: str, record: PendingSendRecord) -> PendingSendRecord:
    """Resolve, deliver, and durably finalize a send while deletion is excluded."""
    database_url = str(settings.database_path)
    current = get_pending_send(database_url, user_id=user_id, server_send_id=record.server_send_id) or record
    if current.state == "sent":
        return current

    # A previous request may have reached Gmail before its response (or our DB
    # update) was lost. Search by our stable RFC Message-ID before retrying an
    # ambiguous delivery so a timeout or worker crash cannot create a second mail.
    if current.state == "sending" or current.error:
        try:
            recovered = find_sent_gmail_message_by_rfc822_message_id(
                settings,
                user_id=user_id,
                rfc822_message_id=_send_rfc822_message_id(current.server_send_id),
            )
        except MultipleSentGmailMessagesFound as exc:
            if _is_ambiguous_pending_send(current):
                raise MailSendConfirmationPending(
                    f"Multiple deliveries require manual reconciliation for {current.server_send_id}"
                ) from exc
            raise
        except Exception as exc:
            if _is_ambiguous_pending_send(current):
                # A credential, transport, or provider failure occurred while
                # checking an earlier ambiguous attempt—not while sending a
                # new message. Preserve the `sending` checkpoint so outer
                # error classification cannot authorize another delivery.
                raise MailSendConfirmationPending(
                    f"Mail delivery confirmation is still pending for {current.server_send_id}"
                ) from exc
            raise
        if recovered:
            return _finalize_sent_message(settings, user_id=user_id, record=current, result=recovered)
        if _is_ambiguous_pending_send(current):
            # `sending` is an ambiguous provider outcome, not a stale lease.
            # Search is reconciliation only; an empty result may mean Gmail's
            # index has not caught up and must never authorize a second send.
            return current

    # Everything that can fail locally happens before the durable provider
    # claim. If message construction or job persistence fails, the record stays
    # queued/failed and a retry is conclusive because Gmail was never called.
    raw = _raw_message(current)
    _enqueue_send_job(settings, user_id=user_id, record=current, run_after_seconds=150)

    sending = claim_pending_send(database_url, user_id=user_id, server_send_id=current.server_send_id)
    if sending is None:
        # Another API replica or worker owns the send. Returning its current
        # state lets the client poll without issuing a second Gmail request.
        return get_pending_send(database_url, user_id=user_id, server_send_id=current.server_send_id) or current

    # This delayed durable job is a crash-recovery safety net for synchronous
    # sends. On success it observes `sent`; after a hard crash it searches Gmail
    # without ever converting an empty search result into resend authority.
    result = send_gmail_raw_message(settings, user_id=user_id, raw_message=raw, gmail_thread_id=sending.gmail_thread_id)
    return _finalize_sent_message(settings, user_id=user_id, record=sending, result=result)


def _finalize_sent_message(
    settings: Settings,
    *,
    user_id: str,
    record: PendingSendRecord,
    result: dict[str, object],
) -> PendingSendRecord:
    gmail_message_id = str(result.get("id") or "") or None
    gmail_thread_id = str(result.get("threadId") or "") or record.gmail_thread_id
    sent = mark_pending_send_sent(
        str(settings.database_path),
        user_id=user_id,
        server_send_id=record.server_send_id,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
    ) or record
    # Delivery is complete once Gmail and the durable send record agree. Local
    # projection refresh is best-effort and must never turn a sent mail back
    # into a retryable send if parsing or event emission fails afterward.
    try:
        if gmail_message_id:
            _import_sent_message(settings, user_id=user_id, message_id=gmail_message_id)
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_CHANGED,
            mailbox_label="sent",
            payload={"source": "send", "send_type": record.send_type, "gmail_message_id": gmail_message_id},
        )
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=DASHBOARD_CHANGED,
            payload={"source": "send", "send_type": record.send_type},
        )
    except Exception as exc:
        logger.warning("Sent-mail projection refresh failed user_id=%s error=%s", user_id, type(exc).__name__)
    return sent


def _current_pending_send(
    settings: Settings,
    *,
    user_id: str,
    fallback: PendingSendRecord,
) -> PendingSendRecord:
    return get_pending_send(
        str(settings.database_path),
        user_id=user_id,
        server_send_id=fallback.server_send_id,
    ) or fallback


def _is_ambiguous_pending_send(record: PendingSendRecord) -> bool:
    return record.state == "sending" or (record.state == "queued" and bool(record.error))


def _google_http_status(exc: HttpError) -> int | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_definite_google_client_rejection(exc: HttpError) -> bool:
    status = _google_http_status(exc)
    return (
        status is not None
        and 400 <= status < 500
        and status not in _GOOGLE_AUTH_HTTP_STATUSES
        and status not in _AMBIGUOUS_GOOGLE_HTTP_STATUSES
    )


def _enqueue_send_job(
    settings: Settings,
    *,
    user_id: str,
    record: PendingSendRecord,
    error: str | None = None,
    run_after_seconds: int,
) -> None:
    payload: dict[str, str] = {"user_id": user_id, "server_send_id": record.server_send_id}
    if error:
        payload["last_error"] = error[:500]
    enqueue_job(
        str(settings.database_path),
        kind="gmail_send_message",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"gmail-send:{user_id}:{record.server_send_id}",
        priority=90,
        payload=payload,
        run_after_seconds=run_after_seconds,
    )


def _import_sent_message(settings: Settings, *, user_id: str, message_id: str) -> None:
    payload = fetch_gmail_message(settings, user_id=user_id, message_id=message_id, format="full")
    parsed = parse_gmail_message(
        payload,
        user_id=user_id,
        inline_attachment_resolver=_gmail_body_attachment_resolver(settings, user_id=user_id),
    )
    parsed = mark_full_gmail_payload_body_fetch_status(parsed)
    record = GmailMessageRecord(created_at="", updated_at="", **parsed)
    upsert_gmail_messages(str(settings.database_path), [record])
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[record.message_id], use_ai=False)
    enqueue_projection_refresh(settings, user_id=user_id, priority=25)


def _gmail_body_attachment_resolver(settings: Settings, *, user_id: str):
    cache: dict[tuple[str, str], str | None] = {}

    def resolve(message_id: str, attachment_id: str) -> str | None:
        key = (message_id, attachment_id)
        if key not in cache:
            try:
                payload = fetch_gmail_attachment(
                    settings,
                    user_id=user_id,
                    message_id=message_id,
                    attachment_id=attachment_id,
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                cache[key] = data if isinstance(data, str) and data else None
            except GoogleCredentialsUnavailable:
                raise
            except HttpError as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status in {401, 403, "401", "403"}:
                    raise
                cache[key] = None
            except Exception:
                cache[key] = None
        return cache[key]

    return resolve


def _reply_context(
    settings: Settings,
    *,
    user_id: str,
    mailbox_thread_id: str,
    source_message_id: str | None = None,
) -> dict[str, Any] | None:
    detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=mailbox_thread_id)
    messages = detail.messages if detail is not None else list_messages_for_gmail_thread(
        str(settings.database_path),
        user_id=user_id,
        gmail_thread_id=mailbox_thread_id,
    )
    if not messages:
        return None
    if source_message_id is not None:
        source_message = next((item for item in messages if item.message_id == source_message_id), None)
        if source_message is None:
            raise ValueError("Selected email is not part of this thread.")
    else:
        source_message = max(messages, key=lambda item: item.internal_date or item.updated_at)
    user = get_user(str(settings.database_path), user_id)
    user_email = (user.email if user else "").lower()
    reply_to = source_message.headers.get("reply-to") or source_message.headers.get("Reply-To")
    sender_value = reply_to or source_message.sender or ""
    sender_email = parseaddr(sender_value)[1].lower()
    if sender_email and sender_email != user_email:
        to = [sender_value or sender_email]
    else:
        to = _addresses_without_user(str(source_message.recipients.get("to") or ""), user_email)
    reply_all_cc = _clean_addresses(
        [
            *_addresses_without_user(str(source_message.recipients.get("to") or ""), user_email),
            *_addresses_without_user(str(source_message.recipients.get("cc") or ""), user_email),
        ]
    )
    reply_targets = {parseaddr(value)[1].lower() for value in to}
    reply_all_cc = [value for value in reply_all_cc if parseaddr(value)[1].lower() not in reply_targets]
    headers = {
        "In-Reply-To": source_message.headers.get("message-id") or source_message.headers.get("Message-ID"),
        "References": _references_header(source_message),
    }
    return {
        "to": to,
        "reply_all_cc": reply_all_cc,
        "user_email": user_email,
        "reply_subject": _reply_subject(source_message.subject),
        "forward_subject": _forward_subject(source_message.subject),
        "headers": {key: value for key, value in headers.items() if value},
        "gmail_thread_id": source_message.gmail_thread_id,
        "original_subject": source_message.subject or "",
        "original_sender": source_message.sender or "",
        "original_to": str(source_message.recipients.get("to") or ""),
        "original_date": source_message.internal_date or "",
        "original_body": source_message.text_body or source_message.snippet or "",
        "original_html": source_message.html_body_sanitized,
        "original_message": source_message,
    }


def _raw_message(record: PendingSendRecord) -> str:
    return _raw_message_from_fields(
        to=record.to,
        cc=record.cc,
        bcc=record.bcc,
        subject=record.subject,
        body_text=record.body_text,
        body_html=record.body_html,
        headers={**record.headers, "Message-ID": _send_rfc822_message_id(record.server_send_id)},
        attachments=record.attachments,
    )


def _raw_message_from_fields(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    headers: dict[str, Any],
    attachments: list[dict[str, str]],
) -> str:
    message = EmailMessage()
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    for key, value in headers.items():
        if value and key not in message:
            message[key] = str(value)
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")
    for attachment in attachments:
        content = base64.b64decode(attachment["data_base64"], validate=True)
        mime_type = attachment.get("mime_type") or mimetypes.guess_type(attachment["filename"])[0] or "application/octet-stream"
        maintype, _, subtype = mime_type.partition("/")
        message.add_attachment(
            content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment["filename"],
        )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _send_rfc822_message_id(server_send_id: str) -> str:
    digest = sha256(server_send_id.encode("utf-8")).hexdigest()[:32]
    return f"<send.{digest}@electronic-mail.local>"


def _can_send(settings: Settings, *, user_id: str) -> bool:
    return user_can_write_gmail(str(settings.database_path), user_id=user_id) and not missing_google_scopes(
        settings,
        user_id=user_id,
        required_scopes=[GMAIL_FULL_SCOPE],
    )


def _reauth_required(settings: Settings, client_send_id: str, *, mailbox_thread_id: str | None = None) -> MailSendResponse:
    return MailSendResponse(
        client_send_id=client_send_id,
        mailbox_thread_id=mailbox_thread_id,
        state="reauth_required",
        error="Google needs permission to send mail.",
        reauth_url=f"{settings.backend_origin}/auth/google",
    )


def _response_from_record(record: PendingSendRecord) -> MailSendResponse:
    return MailSendResponse(
        client_send_id=record.client_send_id,
        server_send_id=record.server_send_id,
        mailbox_thread_id=record.mailbox_thread_id,
        gmail_thread_id=record.gmail_thread_id,
        gmail_message_id=record.gmail_message_id,
        state=record.state,  # type: ignore[arg-type]
        queued_at=record.queued_at,
        sent_at=record.sent_at,
        error=record.error,
    )


def _reply_subject(subject: str | None) -> str:
    value = (subject or "").strip() or "No subject"
    return value if value.lower().startswith("re:") else f"Re: {value}"


def _forward_subject(subject: str | None) -> str:
    value = (subject or "").strip() or "No subject"
    return value if value.lower().startswith(("fwd:", "fw:")) else f"Fwd: {value}"


def _forward_body(body_text: str, context: dict[str, Any]) -> str:
    original = (
        "\n\n---------- Forwarded message ---------\n"
        f"From: {context['original_sender']}\n"
        f"Date: {context['original_date']}\n"
        f"Subject: {context['original_subject']}\n"
        f"To: {context['original_to']}\n\n"
        f"{context['original_body']}"
    )
    return f"{body_text.rstrip()}{original}"


def _reply_body(body_text: str, context: dict[str, Any]) -> str:
    quoted = "\n".join(f"> {line}" for line in str(context["original_body"]).splitlines())
    return (
        f"{body_text.rstrip()}\n\n"
        f"On {context['original_date']}, {context['original_sender']} wrote:\n"
        f"{quoted}"
    )


def _quoted_html_body(body_html: str | None, body_text: str, context: dict[str, Any], *, forward: bool) -> str:
    authored = body_html or f"<div>{html_escape(body_text).replace(chr(10), '<br>')}</div>"
    original = context.get("original_html") or f"<div>{html_escape(str(context['original_body'])).replace(chr(10), '<br>')}</div>"
    if forward:
        heading = (
            "---------- Forwarded message ---------<br>"
            f"From: {html_escape(str(context['original_sender']))}<br>"
            f"Date: {html_escape(str(context['original_date']))}<br>"
            f"Subject: {html_escape(str(context['original_subject']))}<br>"
            f"To: {html_escape(str(context['original_to']))}"
        )
    else:
        heading = f"On {html_escape(str(context['original_date']))}, {html_escape(str(context['original_sender']))} wrote:"
    return f"{authored}<br><div>{heading}</div><blockquote>{original}</blockquote>"


def _attachment_values(attachments: list[dict[str, str]]) -> list[Any]:
    return [SimpleAttachment(**item) for item in attachments]


def _reconcile_original_attachments(
    attachments: list[dict[str, str]],
    originals: list[dict[str, str]],
    *,
    include: bool,
) -> list[dict[str, str]]:
    """Apply a forward-original toggle without duplicating copied files."""

    original_keys = {_attachment_fingerprint(item) for item in originals}
    if not include:
        retained = [item for item in attachments if _attachment_fingerprint(item) not in original_keys]
        return _validated_attachments(_attachment_values(retained))
    merged = list(attachments)
    present = {_attachment_fingerprint(item) for item in merged}
    for item in originals:
        fingerprint = _attachment_fingerprint(item)
        if fingerprint in present:
            continue
        merged.append(item)
        present.add(fingerprint)
    return _validated_attachments(_attachment_values(merged))


def _attachment_fingerprint(attachment: dict[str, str]) -> tuple[str, str, str]:
    content = base64.b64decode(attachment["data_base64"], validate=True)
    return (
        attachment["filename"],
        attachment.get("mime_type", "application/octet-stream").lower(),
        sha256(content).hexdigest(),
    )


def _original_attachment_values(settings: Settings, *, user_id: str, message: GmailMessageRecord) -> list[Any]:
    values: list[Any] = []
    for attachment in gmail_attachments_for_message(message):
        payload = fetch_gmail_attachment(
            settings,
            user_id=user_id,
            message_id=message.message_id,
            attachment_id=attachment.attachment_id,
        )
        encoded = str(payload.get("data") or "")
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(f"{encoded}{padding}".encode("ascii"))
        values.append(
            SimpleAttachment(
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                data_base64=base64.b64encode(decoded).decode("ascii"),
            )
        )
    return values


class SimpleAttachment:
    def __init__(self, *, filename: str, mime_type: str, data_base64: str) -> None:
        self.filename = filename
        self.mime_type = mime_type
        self.data_base64 = data_base64


def _references_header(message: GmailMessageRecord) -> str | None:
    references = str(message.headers.get("references") or message.headers.get("References") or "").strip()
    message_id = str(message.headers.get("message-id") or message.headers.get("Message-ID") or "").strip()
    return " ".join(part for part in [references, message_id] if part).strip() or None


def _addresses_without_user(value: str, user_email: str) -> list[str]:
    addresses = []
    for display, address in getaddresses([value]):
        if not address or address.lower() == user_email:
            continue
        addresses.append(f"{display} <{address}>" if display else address)
    return addresses


def _clean_addresses(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        for display, address in getaddresses([value]):
            if not address:
                continue
            rendered = f"{display} <{address}>" if display else address
            if address.lower() not in {parseaddr(item)[1].lower() for item in cleaned}:
                cleaned.append(rendered)
    return cleaned


def _reply_recipients(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    user_email: str,
) -> tuple[list[str], list[str], list[str]]:
    """Remove the signed-in user and cross-field duplicates from reply recipients."""

    excluded = {user_email.lower()} if user_email else set()

    def unique_field(values: list[str]) -> list[str]:
        recipients: list[str] = []
        for value in _clean_addresses(values):
            address = parseaddr(value)[1].lower()
            if not address or address in excluded:
                continue
            recipients.append(value)
            excluded.add(address)
        return recipients

    return unique_field(to), unique_field(cc), unique_field(bcc)


def _validated_attachments(values: list[Any]) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    total_bytes = 0
    for value in values:
        filename = str(getattr(value, "filename", "") or "").strip()
        mime_type = str(getattr(value, "mime_type", "") or "application/octet-stream").strip()
        data_base64 = str(getattr(value, "data_base64", "") or "").strip()
        if not filename or "/" in filename or "\\" in filename or "\r" in filename or "\n" in filename:
            raise ValueError("Attachment filename is invalid.")
        if "\r" in mime_type or "\n" in mime_type or "/" not in mime_type:
            raise ValueError(f"Attachment {filename} has an invalid MIME type.")
        try:
            decoded = base64.b64decode(data_base64, validate=True)
        except Exception as exc:
            raise ValueError(f"Attachment {filename} is not valid base64.") from exc
        if not decoded:
            raise ValueError(f"Attachment {filename} is empty.")
        if len(decoded) > 10 * 1024 * 1024:
            raise ValueError(f"Attachment {filename} exceeds the 10 MB per-file limit.")
        total_bytes += len(decoded)
        if total_bytes > 18 * 1024 * 1024:
            raise ValueError("Attachments exceed the 18 MB send limit.")
        attachments.append({"filename": filename, "mime_type": mime_type, "data_base64": data_base64})
    return attachments


def _validated_addresses(values: list[str]) -> list[str]:
    if len(values) > 100:
        raise ValueError("A message can have at most 100 recipients per field.")
    cleaned: list[str] = []
    for value in values:
        if "\r" in value or "\n" in value or len(value) > 512:
            raise ValueError("A recipient address is invalid.")
        parsed = getaddresses([value])
        if not parsed:
            raise ValueError(f"Recipient address is invalid: {value}")
        for display, address in parsed:
            address = address.strip()
            local, separator, domain = address.rpartition("@")
            if not separator or not local or not domain or "." not in domain or any(character.isspace() for character in address):
                raise ValueError(f"Recipient address is invalid: {value}")
            rendered = f"{display} <{address}>" if display else address
            if address.lower() not in {parseaddr(item)[1].lower() for item in cleaned}:
                cleaned.append(rendered)
    return cleaned


def _validate_subject(subject: str) -> None:
    if "\r" in subject or "\n" in subject:
        raise ValueError("Subject cannot contain line breaks.")
