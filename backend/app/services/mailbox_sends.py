from __future__ import annotations

"""Compose and reply sends for the native mailbox surface."""

import base64
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    PendingSendRecord,
    get_mail_group_detail,
    get_pending_send,
    list_messages_for_gmail_thread,
    mark_pending_send_failed,
    mark_pending_send_sending,
    mark_pending_send_sent,
    upsert_gmail_messages,
    upsert_pending_send,
    user_can_write_gmail,
)
from app.db.repository import get_user
from app.schemas.domain import MailComposeRequest, MailReplyRequest, MailSendResponse
from app.services.email_extraction import parse_gmail_message
from app.services.integrations.google import (
    GMAIL_SEND_SCOPE,
    fetch_gmail_message,
    missing_google_scopes,
    send_gmail_raw_message,
)
from app.services.mailbox_events import DASHBOARD_CHANGED, MAILBOX_CHANGED, emit_mailbox_event
from app.services.mail_groups import enqueue_projection_refresh, rebuild_touched_mail_groups, refresh_app_session_snapshot


def send_compose(settings: Settings, *, user_id: str, request: MailComposeRequest) -> MailSendResponse:
    if not _can_send(settings, user_id=user_id):
        return _reauth_required(settings, request.client_send_id)
    to = _clean_addresses(request.to)
    if not to:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error="Add at least one recipient.")
    subject = request.subject.strip()
    body_text = request.body_text.strip()
    if not subject:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error="Add a subject.")
    if not body_text:
        return MailSendResponse(client_send_id=request.client_send_id, state="failed", error="Write a message.")
    record = upsert_pending_send(
        str(settings.database_path),
        user_id=user_id,
        client_send_id=request.client_send_id,
        send_type="compose",
        mailbox_thread_id=None,
        gmail_thread_id=None,
        to=to,
        cc=_clean_addresses(request.cc),
        bcc=_clean_addresses(request.bcc),
        subject=subject,
        body_text=body_text,
        body_html=request.body_html,
        headers={},
        created_at=request.created_at,
    )
    return _send_or_queue(settings, user_id=user_id, record=record)


def send_reply(settings: Settings, *, user_id: str, mailbox_thread_id: str, request: MailReplyRequest) -> MailSendResponse:
    if not _can_send(settings, user_id=user_id):
        return _reauth_required(settings, request.client_send_id, mailbox_thread_id=mailbox_thread_id)
    body_text = request.body_text.strip()
    if not body_text:
        return MailSendResponse(client_send_id=request.client_send_id, mailbox_thread_id=mailbox_thread_id, state="failed", error="Write a reply.")
    context = _reply_context(settings, user_id=user_id, mailbox_thread_id=mailbox_thread_id)
    if context is None:
        return MailSendResponse(client_send_id=request.client_send_id, mailbox_thread_id=mailbox_thread_id, state="failed", error="Email thread not found.")
    to = _clean_addresses(context["to"])
    if not to:
        return MailSendResponse(client_send_id=request.client_send_id, mailbox_thread_id=mailbox_thread_id, state="failed", error="No reply recipient found.")
    record = upsert_pending_send(
        str(settings.database_path),
        user_id=user_id,
        client_send_id=request.client_send_id,
        send_type="reply",
        mailbox_thread_id=mailbox_thread_id,
        gmail_thread_id=context["gmail_thread_id"],
        to=to,
        cc=_clean_addresses([*context["cc"], *request.cc]),
        bcc=_clean_addresses(request.bcc),
        subject=context["subject"],
        body_text=body_text,
        body_html=request.body_html,
        headers=context["headers"],
        created_at=request.created_at,
    )
    return _send_or_queue(settings, user_id=user_id, record=record)


def run_pending_send(settings: Settings, *, user_id: str, server_send_id: str) -> MailSendResponse | None:
    record = get_pending_send(str(settings.database_path), user_id=user_id, server_send_id=server_send_id)
    if record is None:
        return None
    if record.state == "sent":
        return _response_from_record(record)
    try:
        sent = _perform_send(settings, user_id=user_id, record=record)
        return _response_from_record(sent)
    except Exception as exc:
        failed = mark_pending_send_failed(
            str(settings.database_path),
            user_id=user_id,
            server_send_id=record.server_send_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise RuntimeError((failed or record).error or str(exc)) from exc


def _send_or_queue(settings: Settings, *, user_id: str, record: PendingSendRecord) -> MailSendResponse:
    if record.state == "sent":
        return _response_from_record(record)
    if record.state in {"sending", "queued"} and record.gmail_message_id:
        return _response_from_record(record)
    try:
        sent = _perform_send(settings, user_id=user_id, record=record)
        return _response_from_record(sent)
    except HttpError as exc:
        if getattr(exc.resp, "status", None) in {401, 403}:
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
        _queue_send(settings, user_id=user_id, record=record, error=f"{type(exc).__name__}: {exc}")
        return _response_from_record(record)
    except Exception as exc:
        _queue_send(settings, user_id=user_id, record=record, error=f"{type(exc).__name__}: {exc}")
        return _response_from_record(record)


def _perform_send(settings: Settings, *, user_id: str, record: PendingSendRecord) -> PendingSendRecord:
    sending = mark_pending_send_sending(str(settings.database_path), user_id=user_id, server_send_id=record.server_send_id) or record
    raw = _raw_message(sending)
    result = send_gmail_raw_message(settings, user_id=user_id, raw_message=raw, gmail_thread_id=sending.gmail_thread_id)
    gmail_message_id = str(result.get("id") or "") or None
    gmail_thread_id = str(result.get("threadId") or "") or sending.gmail_thread_id
    sent = mark_pending_send_sent(
        str(settings.database_path),
        user_id=user_id,
        server_send_id=sending.server_send_id,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
    ) or sending
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
    return sent


def _queue_send(settings: Settings, *, user_id: str, record: PendingSendRecord, error: str) -> None:
    enqueue_job(
        str(settings.database_path),
        kind="gmail_send_message",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"gmail-send:{user_id}:{record.server_send_id}",
        priority=90,
        payload={"user_id": user_id, "server_send_id": record.server_send_id, "last_error": error[:500]},
    )


def _import_sent_message(settings: Settings, *, user_id: str, message_id: str) -> None:
    payload = fetch_gmail_message(settings, user_id=user_id, message_id=message_id, format="full")
    parsed = parse_gmail_message(payload, user_id=user_id)
    record = GmailMessageRecord(created_at="", updated_at="", **parsed)
    upsert_gmail_messages(str(settings.database_path), [record])
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[record.message_id], use_ai=False)
    enqueue_projection_refresh(settings, user_id=user_id, priority=25)


def _reply_context(settings: Settings, *, user_id: str, mailbox_thread_id: str) -> dict[str, Any] | None:
    detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=mailbox_thread_id)
    messages = detail.messages if detail is not None else list_messages_for_gmail_thread(
        str(settings.database_path),
        user_id=user_id,
        gmail_thread_id=mailbox_thread_id,
    )
    if not messages:
        return None
    latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
    user = get_user(str(settings.database_path), user_id)
    user_email = (user.email if user else "").lower()
    sender_email = parseaddr(latest.sender or "")[1].lower()
    if sender_email and sender_email != user_email:
        to = [latest.sender or sender_email]
    else:
        to = _addresses_without_user(str(latest.recipients.get("to") or ""), user_email)
    cc = _addresses_without_user(str(latest.recipients.get("cc") or ""), user_email)
    subject = _reply_subject(latest.subject)
    headers = {
        "In-Reply-To": latest.headers.get("message-id") or latest.headers.get("Message-ID"),
        "References": _references_header(latest),
    }
    return {
        "to": to,
        "cc": cc,
        "subject": subject,
        "headers": {key: value for key, value in headers.items() if value},
        "gmail_thread_id": latest.gmail_thread_id,
    }


def _raw_message(record: PendingSendRecord) -> str:
    message = EmailMessage()
    message["To"] = ", ".join(record.to)
    if record.cc:
        message["Cc"] = ", ".join(record.cc)
    if record.bcc:
        message["Bcc"] = ", ".join(record.bcc)
    message["Subject"] = record.subject
    for key, value in record.headers.items():
        if value and key not in message:
            message[key] = str(value)
    message.set_content(record.body_text)
    if record.body_html:
        message.add_alternative(record.body_html, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _can_send(settings: Settings, *, user_id: str) -> bool:
    return user_can_write_gmail(str(settings.database_path), user_id=user_id) and not missing_google_scopes(
        settings,
        user_id=user_id,
        required_scopes=[GMAIL_SEND_SCOPE],
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
            if rendered not in cleaned:
                cleaned.append(rendered)
    return cleaned
