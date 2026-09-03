from __future__ import annotations

"""Account-scoped Gmail writes used by multi-account endpoints."""

import base64
from email.utils import getaddresses, parseaddr
from hashlib import sha256
import logging
from typing import Any

from app.core.config import Settings
from app.db.account_mail import (
    create_or_get_action, create_or_get_send, get_action, get_draft, get_send,
    list_sends, update_action, update_draft_state, update_send, upsert_draft,
)
from app.db.repository import get_gmail_account
from app.db.jobs import enqueue_job
from app.db.user_mail_guard import shared_gmail_account_mail_lock
from app.schemas.domain import (
    MailComposeRequest, MailDraftAttachment, MailDraftResponse, MailDraftSaveRequest,
    MailDraftSendRequest, MailReplyRequest, MailSendResponse,
    QueuedThreadActionRequest, QueuedThreadActionResponse,
)
from app.services.account_mailbox import parse_scoped_thread_id, scoped_thread_id, _record_from_payload
from app.services.integrations.google import create_gmail_service, missing_google_scopes, GMAIL_FULL_SCOPE
from app.services.mailbox_sends import (
    _clean_addresses, _forward_body, _forward_subject, _quoted_html_body,
    _raw_message, _raw_message_from_fields, _references_header, _reply_body,
    _reply_recipients, _reply_subject, _send_rfc822_message_id,
    _validated_addresses, _validated_attachments, _validate_subject,
)
from app.services.mailbox_events import MAILBOX_CHANGED, emit_mailbox_event


logger = logging.getLogger(__name__)


def _owned_account(settings: Settings, *, user_id: str, gmail_account_id: str):
    account = get_gmail_account(str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id)
    if account is None or account.state != "ready":
        raise LookupError("Gmail account is not available")
    return account


def _safe_operation_error() -> str:
    return "Gmail could not complete this operation. Please try again."


def _schedule_post_write_sync(settings: Settings, *, user_id: str,
                              gmail_account_id: str, source: str) -> None:
    """Refresh only the Gmail account that was changed at the provider."""
    try:
        enqueue_job(
            str(settings.database_path),
            kind="gmail_delta_sync",
            queue="critical",
            user_id=user_id,
            gmail_account_id=gmail_account_id,
            dedupe_key=f"gmail-post-write-sync:{user_id}:{gmail_account_id}",
            priority=80,
            payload={
                "user_id": user_id,
                "gmail_account_id": gmail_account_id,
                "source": source,
            },
        )
        emit_mailbox_event(
            settings, user_id=user_id, gmail_account_id=gmail_account_id,
            event_type=MAILBOX_CHANGED, payload={"source": source},
        )
    except Exception as exc:
        # The provider mutation is already durable. A local refresh failure must
        # never turn it into a retry that could repeat a send or destructive action.
        logger.warning(
            "gmail_account_write.post_sync_failed",
            extra={"event_fields": {
                "event": "gmail_account_write.post_sync_failed",
                "user_id": user_id,
                "gmail_account_id": gmail_account_id,
                "source": source,
                "exception_type": type(exc).__name__,
            }},
        )


def _can_write(settings: Settings, *, user_id: str, gmail_account_id: str) -> bool:
    _owned_account(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    return not missing_google_scopes(
        settings, user_id=user_id, gmail_account_id=gmail_account_id,
        required_scopes=[GMAIL_FULL_SCOPE],
    )


def _send_response(record) -> MailSendResponse:
    return MailSendResponse(
        gmail_account_id=record.gmail_account_id,
        client_send_id=record.client_send_id, server_send_id=record.server_send_id,
        mailbox_thread_id=record.mailbox_thread_id, gmail_thread_id=record.gmail_thread_id,
        gmail_message_id=record.gmail_message_id, state=record.state,
        queued_at=record.queued_at, sent_at=record.sent_at, error=record.error,
    )


def _reauth_send(settings: Settings, *, gmail_account_id: str, client_send_id: str,
                 mailbox_thread_id: str | None = None) -> MailSendResponse:
    return MailSendResponse(
        gmail_account_id=gmail_account_id, client_send_id=client_send_id,
        mailbox_thread_id=mailbox_thread_id, state="reauth_required",
        error="Google needs permission to send mail.",
        reauth_url=f"{settings.backend_origin}/auth/google?intent=reauthorize&gmail_account_id={gmail_account_id}",
    )


def send_compose(settings: Settings, *, user_id: str, gmail_account_id: str,
                 request: MailComposeRequest) -> MailSendResponse:
    if not _can_write(settings, user_id=user_id, gmail_account_id=gmail_account_id):
        return _reauth_send(settings, gmail_account_id=gmail_account_id, client_send_id=request.client_send_id)
    try:
        to, cc, bcc = (_validated_addresses(request.to), _validated_addresses(request.cc),
                       _validated_addresses(request.bcc))
        _validate_subject(request.subject)
        attachments = _validated_attachments(request.attachments)
    except ValueError as exc:
        return MailSendResponse(gmail_account_id=gmail_account_id, client_send_id=request.client_send_id,
                                state="failed", error="Operation failed.")
    if not to:
        return MailSendResponse(gmail_account_id=gmail_account_id, client_send_id=request.client_send_id,
                                state="failed", error="Add at least one recipient.")
    record = create_or_get_send(
        str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id,
        client_send_id=request.client_send_id, send_type="compose", mailbox_thread_id=None,
        gmail_thread_id=None, to=to, cc=cc, bcc=bcc, subject=request.subject.strip(),
        body_text=request.body_text.strip(), body_html=request.body_html, headers={},
        attachments=attachments, created_at=request.created_at,
    )
    return _queue_delivery(
        settings, user_id=user_id, gmail_account_id=gmail_account_id, record=record
    )


def _thread_records(settings: Settings, *, user_id: str, gmail_account_id: str,
                    mailbox_thread_id: str):
    parsed = parse_scoped_thread_id(mailbox_thread_id)
    if parsed is not None:
        parsed_account_id, gmail_thread_id = parsed
        if parsed_account_id != gmail_account_id:
            raise LookupError("Thread belongs to a different Gmail account")
    else:
        gmail_thread_id = mailbox_thread_id
    service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    payload = service.users().threads().get(userId="me", id=gmail_thread_id, format="full").execute()
    raw = payload.get("messages") if isinstance(payload, dict) else []
    records = [_record_from_payload(item, user_id=user_id) for item in raw if isinstance(item, dict)]
    records.sort(key=lambda item: item.internal_date or item.updated_at)
    return gmail_thread_id, records


def _addresses_without(value: str, owner_email: str) -> list[str]:
    return [f"{name} <{address}>" if name else address for name, address in getaddresses([value])
            if address and address.lower() != owner_email]


def send_reply(settings: Settings, *, user_id: str, gmail_account_id: str,
               mailbox_thread_id: str, request: MailReplyRequest) -> MailSendResponse:
    if not _can_write(settings, user_id=user_id, gmail_account_id=gmail_account_id):
        return _reauth_send(settings, gmail_account_id=gmail_account_id,
                            client_send_id=request.client_send_id, mailbox_thread_id=mailbox_thread_id)
    try:
        gmail_thread_id, messages = _thread_records(
            settings, user_id=user_id, gmail_account_id=gmail_account_id,
            mailbox_thread_id=mailbox_thread_id,
        )
        if not messages:
            raise ValueError("Email thread not found.")
        source = (next((item for item in messages if item.message_id == request.source_message_id), None)
                  if request.source_message_id else messages[-1])
        if source is None:
            raise ValueError("Selected email is not part of this thread.")
        account = _owned_account(settings, user_id=user_id, gmail_account_id=gmail_account_id)
        owner_email = account.email.lower()
        reply_to = source.headers.get("reply-to") or source.headers.get("Reply-To") or source.sender or ""
        sender_email = parseaddr(reply_to)[1].lower()
        default_to = [reply_to] if sender_email and sender_email != owner_email else _addresses_without(
            str(source.recipients.get("to") or ""), owner_email
        )
        default_cc = _clean_addresses([
            *_addresses_without(str(source.recipients.get("to") or ""), owner_email),
            *_addresses_without(str(source.recipients.get("cc") or ""), owner_email),
        ])
        headers = {"In-Reply-To": source.headers.get("message-id") or source.headers.get("Message-ID"),
                   "References": _references_header(source)}
        context = {
            "original_subject": source.subject or "", "original_sender": source.sender or "",
            "original_to": str(source.recipients.get("to") or ""),
            "original_date": source.internal_date or "",
            "original_body": source.text_body or source.snippet or "",
            "original_html": source.html_body_sanitized,
        }
        to, cc, bcc = (_validated_addresses(request.to), _validated_addresses(request.cc),
                       _validated_addresses(request.bcc))
        body_text, body_html = request.body_text.strip(), request.body_html
        attachments = _validated_attachments(request.attachments)
        if request.mode == "forward":
            final_to, final_cc, final_bcc = to, cc, bcc
            subject = (request.subject or _forward_subject(source.subject)).strip()
            final_headers: dict[str, Any] = {}
            final_thread_id = None
            if request.include_quoted_original:
                body_text = _forward_body(body_text, context)
                body_html = _quoted_html_body(body_html, request.body_text, context, forward=True)
        else:
            requested_to = to if "to" in request.model_fields_set else default_to
            requested_cc = (cc if "cc" in request.model_fields_set else default_cc) if request.mode == "reply_all" else cc
            final_to, final_cc, final_bcc = _reply_recipients(
                to=requested_to, cc=requested_cc, bcc=bcc, user_email=owner_email
            )
            subject = (request.subject or _reply_subject(source.subject)).strip()
            final_headers = {key: value for key, value in headers.items() if value}
            final_thread_id = gmail_thread_id
            if request.include_quoted_original:
                body_text = _reply_body(body_text, context)
                body_html = _quoted_html_body(body_html, request.body_text, context, forward=False)
        _validate_subject(subject)
    except (ValueError, LookupError) as exc:
        return MailSendResponse(gmail_account_id=gmail_account_id, client_send_id=request.client_send_id,
                                mailbox_thread_id=mailbox_thread_id, state="failed", error="Operation failed.")
    if not final_to and request.mode == "forward":
        return MailSendResponse(gmail_account_id=gmail_account_id, client_send_id=request.client_send_id,
                                mailbox_thread_id=mailbox_thread_id, state="failed", error="Add at least one recipient.")
    record = create_or_get_send(
        str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id,
        client_send_id=request.client_send_id, send_type=request.mode,
        mailbox_thread_id=scoped_thread_id(gmail_account_id, gmail_thread_id),
        gmail_thread_id=final_thread_id, to=final_to, cc=final_cc, bcc=final_bcc,
        subject=subject, body_text=body_text, body_html=body_html, headers=final_headers,
        attachments=attachments, created_at=request.created_at,
    )
    return _queue_delivery(
        settings, user_id=user_id, gmail_account_id=gmail_account_id, record=record
    )


def _queue_delivery(settings: Settings, *, user_id: str,
                    gmail_account_id: str, record) -> MailSendResponse:
    if record.state == "sent":
        return _send_response(record)
    enqueue_job(
        str(settings.database_path),
        kind="gmail_account_send_message",
        queue="critical",
        user_id=user_id,
        gmail_account_id=gmail_account_id,
        dedupe_key=f"gmail-send:{user_id}:{gmail_account_id}:{record.server_send_id}",
        priority=90,
        payload={
            "user_id": user_id,
            "gmail_account_id": gmail_account_id,
            "server_send_id": record.server_send_id,
        },
    )
    return _send_response(record)


def deliver_pending_send(settings: Settings, *, user_id: str,
                         gmail_account_id: str, server_send_id: str) -> MailSendResponse:
    record = get_send(
        str(settings.database_path), user_id=user_id,
        gmail_account_id=gmail_account_id, server_send_id=server_send_id,
    )
    if record is None:
        raise LookupError("Send not found")
    return _deliver(
        settings, user_id=user_id, gmail_account_id=gmail_account_id,
        record=record, raise_errors=True,
    )


def _deliver(settings: Settings, *, user_id: str, gmail_account_id: str, record,
             raise_errors: bool = False) -> MailSendResponse:
    if record.state == "sent":
        return _send_response(record)
    database_url = str(settings.database_path)
    try:
        with shared_gmail_account_mail_lock(database_url, user_id=user_id, gmail_account_id=gmail_account_id):
            current = get_send(
                database_url, user_id=user_id,
                gmail_account_id=gmail_account_id,
                server_send_id=record.server_send_id,
            ) or record
            if current.state == "sent":
                return _send_response(current)
            if current.state == "sending" or current.error:
                recovered = _find_sent_delivery(
                    settings, user_id=user_id,
                    gmail_account_id=gmail_account_id,
                    server_send_id=current.server_send_id,
                )
                if recovered is not None:
                    sent = update_send(
                        database_url, user_id=user_id,
                        gmail_account_id=gmail_account_id,
                        server_send_id=current.server_send_id, state="sent",
                        gmail_message_id=str(recovered.get("id") or "") or None,
                        gmail_thread_id=(
                            str(recovered.get("threadId") or "")
                            or current.gmail_thread_id
                        ),
                    )
                    _schedule_post_write_sync(
                        settings, user_id=user_id,
                        gmail_account_id=gmail_account_id,
                        source="gmail_account_send_reconciled",
                    )
                    return _send_response(sent or current)
                # Gmail search can lag after delivery. Never interpret an empty
                # reconciliation result as permission to send the message again.
                raise RuntimeError("Mail delivery confirmation is still pending")
            update_send(database_url, user_id=user_id, gmail_account_id=gmail_account_id,
                        server_send_id=record.server_send_id, state="sending")
            service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
            body: dict[str, Any] = {"raw": _raw_message(record)}
            if record.gmail_thread_id:
                body["threadId"] = record.gmail_thread_id
            result = service.users().messages().send(userId="me", body=body).execute()
            sent = update_send(
                database_url, user_id=user_id, gmail_account_id=gmail_account_id,
                server_send_id=record.server_send_id, state="sent",
                gmail_message_id=str(result.get("id") or "") or None,
                gmail_thread_id=str(result.get("threadId") or "") or record.gmail_thread_id,
            )
            _schedule_post_write_sync(
                settings, user_id=user_id, gmail_account_id=gmail_account_id,
                source="gmail_account_send_message",
            )
            return _send_response(sent or record)
    except Exception as exc:
        current = get_send(
            database_url, user_id=user_id,
            gmail_account_id=gmail_account_id,
            server_send_id=record.server_send_id,
        ) or record
        # Once `sending` is durable, Gmail may already have accepted the mail.
        # Keep the ambiguous checkpoint so every retry reconciles by Message-ID
        # and cannot issue a duplicate provider send.
        ambiguous = current.state == "sending"
        failed = update_send(
            database_url, user_id=user_id,
            gmail_account_id=gmail_account_id,
            server_send_id=record.server_send_id,
            state="sending" if ambiguous else "failed",
            error=(
                "Waiting for Gmail delivery confirmation."
                if ambiguous else _safe_operation_error()
            ),
        )
        if raise_errors:
            raise
        return _send_response(failed or record)


def _find_sent_delivery(settings: Settings, *, user_id: str,
                        gmail_account_id: str,
                        server_send_id: str) -> dict[str, Any] | None:
    service = create_gmail_service(
        settings, user_id=user_id, gmail_account_id=gmail_account_id,
    )
    response = service.users().messages().list(
        userId="me",
        q=f"in:sent rfc822msgid:{_send_rfc822_message_id(server_send_id)}",
        maxResults=10,
        includeSpamTrash=True,
    ).execute()
    candidates = response.get("messages") if isinstance(response, dict) else None
    if not isinstance(candidates, list):
        return None
    verified: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("id"):
            continue
        message = service.users().messages().get(
            userId="me", id=str(candidate["id"]), format="minimal",
        ).execute()
        if not isinstance(message, dict):
            continue
        labels = {
            str(label).upper()
            for label in message.get("labelIds", [])
            if isinstance(label, str)
        }
        if "SENT" in labels and "DRAFT" not in labels:
            verified.append(message)
    if len(verified) > 1:
        raise RuntimeError("Multiple Gmail deliveries require manual reconciliation")
    return verified[0] if verified else None


def send_status(settings: Settings, *, user_id: str, gmail_account_id: str,
                server_send_id: str) -> MailSendResponse | None:
    record = get_send(str(settings.database_path), user_id=user_id,
                      gmail_account_id=gmail_account_id, server_send_id=server_send_id)
    return _send_response(record) if record else None


def outbox(settings: Settings, *, user_id: str, gmail_account_id: str, limit: int) -> list[MailSendResponse]:
    return [_send_response(record) for record in list_sends(
        str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id, limit=limit
    )]


def retry_send(settings: Settings, *, user_id: str, gmail_account_id: str,
               server_send_id: str) -> MailSendResponse | None:
    record = get_send(str(settings.database_path), user_id=user_id,
                      gmail_account_id=gmail_account_id, server_send_id=server_send_id)
    return _queue_delivery(
        settings, user_id=user_id, gmail_account_id=gmail_account_id, record=record
    ) if record else None


def enqueue_action(settings: Settings, *, user_id: str, gmail_account_id: str,
                   request: QueuedThreadActionRequest) -> QueuedThreadActionResponse:
    parsed = parse_scoped_thread_id(request.mailbox_thread_id)
    if parsed is not None and parsed[0] != gmail_account_id:
        raise LookupError("Thread belongs to a different Gmail account")
    raw_thread_id = parsed[1] if parsed else request.mailbox_thread_id
    record = create_or_get_action(
        str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id,
        client_action_id=request.client_action_id, mailbox_thread_id=raw_thread_id,
        target_message_id=request.target_message_id, action=str(getattr(request.action, "value", request.action)),
        created_at=request.created_at,
    )
    enqueue_job(
        str(settings.database_path),
        kind="gmail_account_thread_action",
        queue="critical",
        user_id=user_id,
        gmail_account_id=gmail_account_id,
        dedupe_key=f"gmail-action:{user_id}:{gmail_account_id}:{record.server_action_id}",
        priority=90,
        payload={
            "user_id": user_id,
            "gmail_account_id": gmail_account_id,
            "server_action_id": record.server_action_id,
        },
    )
    return QueuedThreadActionResponse(
        gmail_account_id=gmail_account_id, client_action_id=record.client_action_id,
        server_action_id=record.server_action_id,
        mailbox_thread_id=scoped_thread_id(gmail_account_id, record.mailbox_thread_id),
        target_message_id=record.target_message_id, action=record.action, state=record.state,
        queued_at=record.queued_at, applied_at=record.applied_at, error=record.error,
    )


def deliver_pending_action(settings: Settings, *, user_id: str,
                           gmail_account_id: str, server_action_id: str):
    record = get_action(
        str(settings.database_path), user_id=user_id,
        gmail_account_id=gmail_account_id, server_action_id=server_action_id,
    )
    if record is None:
        raise LookupError("Mailbox action not found")
    return _apply_action(
        settings, user_id=user_id, gmail_account_id=gmail_account_id,
        record=record, raise_errors=True,
    )


def _apply_action(settings: Settings, *, user_id: str, gmail_account_id: str,
                  record, raise_errors: bool = False):
    if record.state == "applied":
        return record
    try:
        with shared_gmail_account_mail_lock(str(settings.database_path), user_id=user_id,
                                            gmail_account_id=gmail_account_id):
            update_action(str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id,
                          server_action_id=record.server_action_id, state="applying")
            service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
            resource = service.users().messages() if record.target_message_id else service.users().threads()
            target = record.target_message_id or record.mailbox_thread_id
            action = record.action
            if action == "move_trash": resource.trash(userId="me", id=target).execute()
            elif action == "restore_trash": resource.untrash(userId="me", id=target).execute()
            elif action == "delete_forever": resource.delete(userId="me", id=target).execute()
            else:
                adds, removes = _labels_for_action(action)
                resource.modify(userId="me", id=target,
                                body={"addLabelIds": adds, "removeLabelIds": removes}).execute()
            applied = update_action(
                str(settings.database_path), user_id=user_id,
                gmail_account_id=gmail_account_id,
                server_action_id=record.server_action_id, state="applied",
            ) or record
            _schedule_post_write_sync(
                settings, user_id=user_id, gmail_account_id=gmail_account_id,
                source="gmail_account_thread_action",
            )
            return applied
    except Exception as exc:
        failed = update_action(
            str(settings.database_path), user_id=user_id,
            gmail_account_id=gmail_account_id,
            server_action_id=record.server_action_id,
            state="failed", error=_safe_operation_error(),
        ) or record
        if raise_errors:
            raise
        return failed


def _labels_for_action(action: str) -> tuple[list[str], list[str]]:
    return {
        "mark_read": ([], ["UNREAD"]), "mark_unread": (["UNREAD"], []),
        "archive": ([], ["INBOX"]), "unarchive": (["INBOX"], []),
        "mark_spam": (["SPAM"], ["INBOX"]), "not_spam": (["INBOX"], ["SPAM"]),
        "star": (["STARRED"], []), "unstar": ([], ["STARRED"]),
    }.get(action, ([], []))


def save_draft(settings: Settings, *, user_id: str, gmail_account_id: str,
               request: MailDraftSaveRequest) -> MailDraftResponse:
    if not _can_write(settings, user_id=user_id, gmail_account_id=gmail_account_id):
        return MailDraftResponse(gmail_account_id=gmail_account_id,
                                 client_draft_id=request.client_draft_id,
                                 state="reauth_required", error="Google needs permission to manage drafts.")
    existing = get_draft(str(settings.database_path), user_id=user_id,
                         gmail_account_id=gmail_account_id, client_draft_id=request.client_draft_id)
    raw = _raw_message_from_fields(
        to=_validated_addresses(request.to), cc=_validated_addresses(request.cc),
        bcc=_validated_addresses(request.bcc), subject=request.subject,
        body_text=request.body_text, body_html=request.body_html, headers={},
        attachments=_validated_attachments(request.attachments or []),
    )
    service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    message: dict[str, Any] = {"raw": raw}
    if request.gmail_thread_id:
        message["threadId"] = request.gmail_thread_id
    if existing and existing.gmail_draft_id:
        result = service.users().drafts().update(
            userId="me", id=existing.gmail_draft_id,
            body={"id": existing.gmail_draft_id, "message": message},
        ).execute()
    else:
        result = service.users().drafts().create(userId="me", body={"message": message}).execute()
    draft_id = str(result.get("id") or "") or (existing.gmail_draft_id if existing else None)
    provider_message = result.get("message") if isinstance(result, dict) else {}
    message_id = (
        str(provider_message.get("id") or "") or None
        if isinstance(provider_message, dict)
        else None
    )
    thread_id = (
        str(provider_message.get("threadId") or "") or request.gmail_thread_id
        if isinstance(provider_message, dict)
        else request.gmail_thread_id
    )
    record = upsert_draft(
        str(settings.database_path), user_id=user_id, gmail_account_id=gmail_account_id,
        client_draft_id=request.client_draft_id, gmail_draft_id=draft_id,
        gmail_message_id=message_id, gmail_thread_id=thread_id,
        content_hash=sha256(raw.encode()).hexdigest(), state="saved", created_at=request.created_at,
    )
    return MailDraftResponse(gmail_account_id=gmail_account_id,
                             client_draft_id=record.client_draft_id,
                             gmail_draft_id=record.gmail_draft_id,
                             gmail_message_id=record.gmail_message_id,
                             gmail_thread_id=record.gmail_thread_id,
                             to=request.to, cc=request.cc, bcc=request.bcc,
                             subject=request.subject, body_text=request.body_text,
                             body_html=request.body_html, state="saved", saved_at=record.saved_at)


def read_draft(settings: Settings, *, user_id: str, gmail_account_id: str,
               gmail_draft_id: str) -> MailDraftResponse:
    _owned_account(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    mapping = get_draft(
        str(settings.database_path), user_id=user_id,
        gmail_account_id=gmail_account_id, gmail_draft_id=gmail_draft_id,
    )
    if mapping is None:
        raise LookupError("Draft not found")
    service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    payload = service.users().drafts().get(
        userId="me", id=gmail_draft_id, format="full"
    ).execute()
    raw_message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(raw_message, dict):
        raise LookupError("Draft not found")
    message = _record_from_payload(raw_message, user_id=user_id)
    recipients = message.recipients
    attachments: list[MailDraftAttachment] = []
    for descriptor in message.attachment_descriptors:
        attachment_id = str(descriptor.get("attachment_id") or "")
        if not attachment_id:
            continue
        attachments.append(MailDraftAttachment(
            filename=str(descriptor.get("filename") or "attachment"),
            mime_type=str(descriptor.get("mime_type") or "application/octet-stream"),
            message_id=message.message_id,
            attachment_id=attachment_id,
            download_url=(
                f"/v1/gmail-accounts/{gmail_account_id}/messages/"
                f"{message.message_id}/attachments/{attachment_id}"
            ),
        ))
    return MailDraftResponse(
        gmail_account_id=gmail_account_id,
        client_draft_id=mapping.client_draft_id,
        gmail_draft_id=mapping.gmail_draft_id,
        gmail_message_id=message.message_id,
        gmail_thread_id=message.gmail_thread_id,
        to=_clean_addresses([str(recipients.get("to") or "")]),
        cc=_clean_addresses([str(recipients.get("cc") or "")]),
        bcc=_clean_addresses([str(recipients.get("bcc") or "")]),
        subject=message.subject or "",
        body_text=message.text_body or "",
        body_html=message.html_body_sanitized,
        attachments=attachments,
        state="saved",
        saved_at=mapping.saved_at,
    )


def delete_draft(settings: Settings, *, user_id: str, gmail_account_id: str,
                 gmail_draft_id: str) -> None:
    record = get_draft(str(settings.database_path), user_id=user_id,
                       gmail_account_id=gmail_account_id, gmail_draft_id=gmail_draft_id)
    if record is None:
        raise LookupError("Draft not found")
    service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    service.users().drafts().delete(userId="me", id=gmail_draft_id).execute()
    update_draft_state(str(settings.database_path), user_id=user_id,
                       gmail_account_id=gmail_account_id,
                       client_draft_id=record.client_draft_id, state="deleted")


def send_draft(settings: Settings, *, user_id: str, gmail_account_id: str,
               request: MailDraftSendRequest) -> MailSendResponse:
    record = get_draft(str(settings.database_path), user_id=user_id,
                       gmail_account_id=gmail_account_id, client_draft_id=request.client_draft_id)
    if record is None or not record.gmail_draft_id:
        return MailSendResponse(gmail_account_id=gmail_account_id,
                                client_send_id=request.client_send_id,
                                state="failed", error="Draft not found.")
    service = create_gmail_service(settings, user_id=user_id, gmail_account_id=gmail_account_id)
    result = service.users().drafts().send(userId="me", body={"id": record.gmail_draft_id}).execute()
    message_id = str(result.get("id") or "") or None
    thread_id = str(result.get("threadId") or "") or record.gmail_thread_id
    update_draft_state(str(settings.database_path), user_id=user_id,
                       gmail_account_id=gmail_account_id,
                       client_draft_id=record.client_draft_id, state="sent",
                       client_send_id=request.client_send_id,
                       gmail_message_id=message_id, gmail_thread_id=thread_id)
    _schedule_post_write_sync(
        settings, user_id=user_id, gmail_account_id=gmail_account_id,
        source="gmail_account_draft_send",
    )
    return MailSendResponse(gmail_account_id=gmail_account_id,
                            client_send_id=request.client_send_id,
                            gmail_message_id=message_id, gmail_thread_id=thread_id,
                            state="sent")
