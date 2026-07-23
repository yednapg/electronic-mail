from __future__ import annotations

"""Idempotent Gmail draft create, edit, reopen, delete, and send workflows."""

import base64
from hashlib import sha256
import json
from typing import Any

from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.core.error_safety import GoogleCredentialsUnavailable
from app.db.mail_groups import (
    ClientDraftRecord,
    GmailMessageRecord,
    client_draft_lock,
    delete_gmail_messages,
    get_client_draft,
    get_mail_group_detail,
    list_messages_for_gmail_thread,
    mark_client_draft_deleted,
    mark_client_draft_sent,
    prune_empty_mail_groups,
    upsert_client_draft,
    upsert_gmail_messages,
    user_can_write_gmail,
)
from app.schemas.domain import MailDraftAttachment, MailDraftResponse, MailDraftSaveRequest, MailDraftSendRequest, MailSendResponse
from app.services.email_extraction import parse_gmail_message
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    create_gmail_draft,
    delete_gmail_draft,
    fetch_gmail_attachment,
    fetch_gmail_draft,
    fetch_gmail_message,
    find_gmail_draft_by_message_id,
    find_gmail_draft_by_rfc822_message_id,
    find_gmail_message_by_rfc822_message_id,
    missing_google_scopes,
    send_gmail_draft,
    update_gmail_draft,
)
from app.services.mailbox_events import MAILBOX_CHANGED, emit_mailbox_event
from app.services.mailbox_sends import (
    _clean_addresses,
    _raw_message_from_fields,
    _validated_addresses,
    _validated_attachments,
    _validate_subject,
    prepare_response_message,
)
from app.services.mail_groups import enqueue_projection_refresh, gmail_attachments_for_message, rebuild_touched_mail_groups, refresh_app_session_snapshot

_FORWARD_ORIGINALS_HASH_PREFIX = "forward-originals:"


def save_draft(settings: Settings, *, user_id: str, request: MailDraftSaveRequest) -> MailDraftResponse:
    if not _can_manage_drafts(settings, user_id=user_id):
        return _reauth_required(settings, request.client_draft_id)
    try:
        to = _validated_addresses(request.to)
        cc = _validated_addresses(request.cc)
        bcc = _validated_addresses(request.bcc)
        _validate_subject(request.subject)
    except ValueError as exc:
        return MailDraftResponse(client_draft_id=request.client_draft_id, state="failed", error="Operation failed.")

    database_url = str(settings.database_path)
    with client_draft_lock(database_url, user_id=user_id, client_draft_id=request.client_draft_id):
        existing = get_client_draft(database_url, user_id=user_id, client_draft_id=request.client_draft_id)
        gmail_draft_id = request.gmail_draft_id or (existing.gmail_draft_id if existing else None)
        message_header = _draft_rfc822_message_id(request.client_draft_id)
        try:
            if not gmail_draft_id:
                recovered = find_gmail_draft_by_rfc822_message_id(
                    settings,
                    user_id=user_id,
                    rfc822_message_id=message_header,
                )
                gmail_draft_id = str(recovered.get("id") or "") if recovered else None

            existing_message = (
                _gmail_draft_message(settings, user_id=user_id, gmail_draft_id=gmail_draft_id)
                if gmail_draft_id
                else None
            )
            retained = (
                _preserved_attachment_inputs(
                    settings,
                    user_id=user_id,
                    gmail_draft_id=gmail_draft_id,
                    retained_attachment_ids=request.retained_attachment_ids,
                    message=existing_message,
                )
                if gmail_draft_id and (request.attachments is None or request.retained_attachment_ids is not None)
                else []
            )
            attachments = _validated_attachments(
                [*_attachment_values(retained), *_attachment_values(_validated_attachments(request.attachments or []))]
            )
            effective_to = to
            effective_cc = cc
            effective_bcc = bcc
            effective_subject = request.subject
            effective_body_text = request.body_text
            effective_body_html = request.body_html
            effective_headers = _preserved_thread_headers(existing_message)
            effective_gmail_thread_id = (
                request.gmail_thread_id
                or (existing_message.gmail_thread_id if existing_message else None)
                or (existing.gmail_thread_id if existing else None)
            )

            if request.response_mode is not None:
                if not request.mailbox_thread_id:
                    raise ValueError("A mailbox thread is required for a response draft.")
                desired_forward_originals_state = "included" if request.include_original_attachments else "excluded"
                previous_forward_originals_state = _forward_originals_state(
                    existing.content_hash if existing else None
                )
                reconcile_forward_originals = (
                    request.response_mode == "forward"
                    and gmail_draft_id is not None
                    and previous_forward_originals_state != desired_forward_originals_state
                )
                prepared = prepare_response_message(
                    settings,
                    user_id=user_id,
                    mailbox_thread_id=request.mailbox_thread_id,
                    source_message_id=request.source_message_id,
                    mode=request.response_mode,
                    to=request.to,
                    cc=request.cc,
                    bcc=request.bcc,
                    subject=request.subject or None,
                    body_text=request.body_text,
                    body_html=request.body_html,
                    attachments=_attachment_values(attachments),
                    include_quoted_original=request.include_quoted_original,
                    include_original_attachments=(
                        request.include_original_attachments
                        and (gmail_draft_id is None or reconcile_forward_originals)
                    ),
                    # Forward source files are downloaded only on initial
                    # inclusion or when the user's toggle changes. The opaque
                    # content-hash prefix persists that state without a schema
                    # migration, while content fingerprints make recovery from
                    # older unmarked drafts safe and duplicate-free.
                    reconcile_original_attachments=reconcile_forward_originals,
                    explicit_fields=set(request.model_fields_set),
                )
                effective_to = prepared.to
                effective_cc = prepared.cc
                effective_bcc = prepared.bcc
                effective_subject = prepared.subject
                effective_body_text = prepared.body_text
                effective_body_html = prepared.body_html
                effective_headers = prepared.headers
                attachments = prepared.attachments
                if request.response_mode == "forward" and gmail_draft_id:
                    # A forward starts a new Gmail thread on create, then keeps
                    # that newly assigned thread identity across draft updates.
                    effective_gmail_thread_id = (
                        (existing_message.gmail_thread_id if existing_message else None)
                        or (existing.gmail_thread_id if existing else None)
                    )
                else:
                    effective_gmail_thread_id = prepared.gmail_thread_id
        except ValueError as exc:
            return MailDraftResponse(client_draft_id=request.client_draft_id, state="failed", error="Operation failed.")
        except GoogleCredentialsUnavailable:
            return _reauth_required(settings, request.client_draft_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, request.client_draft_id)
            raise

        effective_request = request.model_copy(
            update={
                "gmail_thread_id": effective_gmail_thread_id,
                "to": effective_to,
                "cc": effective_cc,
                "bcc": effective_bcc,
                "subject": effective_subject,
                "body_text": effective_body_text,
                "body_html": effective_body_html,
            }
        )
        content_hash = _draft_content_hash(
            effective_request,
            to=effective_to,
            cc=effective_cc,
            bcc=effective_bcc,
            attachments=attachments,
            headers=effective_headers,
        )
        if existing and existing.state == "saved" and existing.content_hash == content_hash and existing.gmail_draft_id:
            return get_draft(settings, user_id=user_id, mailbox_thread_id=existing.gmail_draft_id)

        raw = _raw_message_from_fields(
            to=effective_to,
            cc=effective_cc,
            bcc=effective_bcc,
            subject=effective_subject,
            body_text=effective_body_text,
            body_html=effective_body_html,
            headers={**effective_headers, "Message-ID": message_header},
            attachments=attachments,
        )
        try:
            result = (
                update_gmail_draft(
                    settings,
                    user_id=user_id,
                    gmail_draft_id=gmail_draft_id,
                    raw_message=raw,
                    gmail_thread_id=effective_gmail_thread_id,
                )
                if gmail_draft_id
                else create_gmail_draft(
                    settings,
                    user_id=user_id,
                    raw_message=raw,
                    gmail_thread_id=effective_gmail_thread_id,
                )
            )
        except GoogleCredentialsUnavailable:
            return _reauth_required(settings, request.client_draft_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, request.client_draft_id)
            raise

        gmail_draft_id = str(result.get("id") or gmail_draft_id or "") or None
        result_message = result.get("message") if isinstance(result.get("message"), dict) else {}
        gmail_message_id = str(result_message.get("id") or "") or None
        gmail_thread_id = str(result_message.get("threadId") or effective_gmail_thread_id or "") or None
        if gmail_draft_id and not gmail_message_id:
            try:
                hydrated = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="minimal")
            except GoogleCredentialsUnavailable:
                return _reauth_required(settings, request.client_draft_id)
            except HttpError as exc:
                if getattr(exc.resp, "status", None) in {401, 403}:
                    return _reauth_required(settings, request.client_draft_id)
                raise
            result_message = hydrated.get("message") if isinstance(hydrated.get("message"), dict) else {}
            gmail_message_id = str(result_message.get("id") or "") or None
            gmail_thread_id = str(result_message.get("threadId") or gmail_thread_id or "") or None

        old_message_id = existing.gmail_message_id if existing else None
        try:
            imported = _import_gmail_message(settings, user_id=user_id, message_id=gmail_message_id)
        except GoogleCredentialsUnavailable:
            # The Gmail draft already exists and is recoverable through its
            # stable Message-ID on the next save attempt.
            return _reauth_required(settings, request.client_draft_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, request.client_draft_id)
            raise
        if old_message_id and old_message_id != gmail_message_id:
            _remove_local_messages(settings, user_id=user_id, message_ids=[old_message_id])
        record = upsert_client_draft(
            database_url,
            user_id=user_id,
            client_draft_id=request.client_draft_id,
            gmail_draft_id=gmail_draft_id,
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            content_hash=content_hash,
            state="saved",
            created_at=request.created_at,
        )
        _after_draft_change(settings, user_id=user_id, source="draft_saved")
        return _draft_response(record, imported, fallback=effective_request)


def get_draft(settings: Settings, *, user_id: str, mailbox_thread_id: str) -> MailDraftResponse:
    if not _can_manage_drafts(settings, user_id=user_id):
        return _reauth_required(settings, f"gmail:{mailbox_thread_id}")
    database_url = str(settings.database_path)
    mapping = get_client_draft(database_url, user_id=user_id, gmail_draft_id=mailbox_thread_id)
    gmail_draft_id = mapping.gmail_draft_id if mapping else None
    local_message = _local_draft_message(settings, user_id=user_id, mailbox_thread_id=mailbox_thread_id)
    if not gmail_draft_id and local_message is not None:
        message_header = local_message.headers.get("message-id") or local_message.headers.get("Message-ID")
        try:
            found = (
                find_gmail_draft_by_rfc822_message_id(settings, user_id=user_id, rfc822_message_id=message_header)
                if message_header
                else find_gmail_draft_by_message_id(settings, user_id=user_id, message_id=local_message.message_id)
            )
        except GoogleCredentialsUnavailable:
            return _reauth_required(settings, mapping.client_draft_id if mapping else f"gmail:{mailbox_thread_id}")
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, mapping.client_draft_id if mapping else f"gmail:{mailbox_thread_id}")
            raise
        gmail_draft_id = str(found.get("id") or "") if found else None
    if not gmail_draft_id:
        # The path may itself be a Gmail draft ID from a create response.
        gmail_draft_id = mailbox_thread_id
    try:
        draft = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="full")
    except GoogleCredentialsUnavailable:
        return _reauth_required(settings, mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}")
    except HttpError as exc:
        if getattr(exc.resp, "status", None) in {401, 403}:
            return _reauth_required(settings, mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}")
        if getattr(exc.resp, "status", None) == 404:
            return MailDraftResponse(client_draft_id=mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}", state="failed", error="Draft not found.")
        raise
    message_payload = draft.get("message") if isinstance(draft.get("message"), dict) else None
    if message_payload is None:
        return MailDraftResponse(client_draft_id=mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}", state="failed", error="Draft message is missing.")
    imported = _record_from_payload(message_payload, user_id=user_id)
    upsert_gmail_messages(database_url, [imported])
    client_draft_id = mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}"
    record = upsert_client_draft(
        database_url,
        user_id=user_id,
        client_draft_id=client_draft_id,
        gmail_draft_id=gmail_draft_id,
        gmail_message_id=imported.message_id,
        gmail_thread_id=imported.gmail_thread_id,
        content_hash=mapping.content_hash if mapping else imported.body_hash,
        state="saved",
        created_at=mapping.created_at if mapping else imported.created_at,
    )
    return _draft_response(record, imported)


def delete_draft_by_id(settings: Settings, *, user_id: str, gmail_draft_id: str) -> bool:
    if not _can_manage_drafts(settings, user_id=user_id):
        return False
    database_url = str(settings.database_path)
    mapping = get_client_draft(database_url, user_id=user_id, gmail_draft_id=gmail_draft_id)
    message_id = mapping.gmail_message_id if mapping else None
    if not message_id:
        try:
            payload = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="minimal")
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            message_id = str(message.get("id") or "") or None
        except GoogleCredentialsUnavailable:
            return False
        except HttpError as exc:
            if getattr(exc.resp, "status", None) != 404:
                raise
    try:
        delete_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id)
    except GoogleCredentialsUnavailable:
        return False
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 404:
            raise
    if message_id:
        _remove_local_messages(settings, user_id=user_id, message_ids=[message_id])
    if mapping:
        mark_client_draft_deleted(database_url, user_id=user_id, client_draft_id=mapping.client_draft_id)
    _after_draft_change(settings, user_id=user_id, source="draft_deleted")
    return True


def send_saved_draft(
    settings: Settings,
    *,
    user_id: str,
    gmail_draft_id: str,
    request: MailDraftSendRequest,
) -> MailSendResponse:
    if not _can_manage_drafts(settings, user_id=user_id):
        return _send_reauth_required(settings, request.client_send_id)
    database_url = str(settings.database_path)
    with client_draft_lock(database_url, user_id=user_id, client_draft_id=request.client_draft_id):
        mapping = get_client_draft(database_url, user_id=user_id, client_draft_id=request.client_draft_id)
        if mapping and mapping.state == "sent" and mapping.last_client_send_id == request.client_send_id:
            return MailSendResponse(
                client_send_id=request.client_send_id,
                gmail_thread_id=mapping.gmail_thread_id,
                gmail_message_id=mapping.sent_message_id,
                state="sent",
                sent_at=mapping.updated_at,
            )
        effective_draft_id = mapping.gmail_draft_id if mapping and mapping.gmail_draft_id else gmail_draft_id
        try:
            result = send_gmail_draft(settings, user_id=user_id, gmail_draft_id=effective_draft_id)
        except GoogleCredentialsUnavailable:
            return _send_reauth_required(settings, request.client_send_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _send_reauth_required(settings, request.client_send_id)
            if getattr(exc.resp, "status", None) == 404:
                try:
                    recovered = find_gmail_message_by_rfc822_message_id(
                        settings,
                        user_id=user_id,
                        rfc822_message_id=_draft_rfc822_message_id(request.client_draft_id),
                    )
                except GoogleCredentialsUnavailable:
                    return _send_reauth_required(settings, request.client_send_id)
                except HttpError as recovery_exc:
                    if getattr(recovery_exc.resp, "status", None) in {401, 403}:
                        return _send_reauth_required(settings, request.client_send_id)
                    raise
                if not recovered:
                    return MailSendResponse(client_send_id=request.client_send_id, state="failed", error="Draft not found.")
                result = recovered
            else:
                raise
        gmail_message_id = str(result.get("id") or "") or None
        gmail_thread_id = str(result.get("threadId") or "") or None
        try:
            imported = _import_gmail_message(settings, user_id=user_id, message_id=gmail_message_id)
        except GoogleCredentialsUnavailable:
            # Gmail accepted the send. Its stable draft Message-ID keeps the
            # operation recoverable after reauthentication without resending.
            return _send_reauth_required(settings, request.client_send_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _send_reauth_required(settings, request.client_send_id)
            raise
        if mapping and mapping.gmail_message_id and mapping.gmail_message_id != gmail_message_id:
            _remove_local_messages(settings, user_id=user_id, message_ids=[mapping.gmail_message_id])
        if mapping is None:
            mapping = upsert_client_draft(
                database_url,
                user_id=user_id,
                client_draft_id=request.client_draft_id,
                gmail_draft_id=effective_draft_id,
                gmail_message_id=None,
                gmail_thread_id=gmail_thread_id,
                content_hash="",
                state="saved",
                created_at=imported.created_at if imported else "1970-01-01T00:00:00+00:00",
            )
        mark_client_draft_sent(
            database_url,
            user_id=user_id,
            client_draft_id=request.client_draft_id,
            client_send_id=request.client_send_id,
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
        )
        _after_draft_change(settings, user_id=user_id, source="draft_sent")
        return MailSendResponse(
            client_send_id=request.client_send_id,
            gmail_thread_id=gmail_thread_id,
            gmail_message_id=gmail_message_id,
            state="sent",
        )


def _can_manage_drafts(settings: Settings, *, user_id: str) -> bool:
    return user_can_write_gmail(str(settings.database_path), user_id=user_id) and not missing_google_scopes(
        settings,
        user_id=user_id,
        required_scopes=[GMAIL_FULL_SCOPE],
    )


def _draft_rfc822_message_id(client_draft_id: str) -> str:
    digest = sha256(client_draft_id.encode("utf-8")).hexdigest()[:32]
    return f"<draft.{digest}@electronic-mail.local>"


def _draft_content_hash(
    request: MailDraftSaveRequest,
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    attachments: list[dict[str, str]],
    headers: dict[str, Any] | None = None,
) -> str:
    payload = {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": request.subject,
        "body_text": request.body_text,
        "body_html": request.body_html,
        "gmail_thread_id": request.gmail_thread_id,
        "attachments": [
            {
                "filename": item["filename"],
                "mime_type": item["mime_type"],
                "sha256": sha256(base64.b64decode(item["data_base64"], validate=True)).hexdigest(),
            }
            for item in attachments
        ],
    }
    if headers:
        payload["headers"] = headers
    if request.response_mode is not None:
        payload["response_context"] = {
            "response_mode": request.response_mode,
            "mailbox_thread_id": request.mailbox_thread_id,
            "source_message_id": request.source_message_id,
            "include_quoted_original": request.include_quoted_original,
            "include_original_attachments": request.include_original_attachments,
        }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if request.response_mode == "forward":
        state = "included" if request.include_original_attachments else "excluded"
        return f"{_FORWARD_ORIGINALS_HASH_PREFIX}{state}:{digest}"
    return digest


def _forward_originals_state(content_hash: str | None) -> str | None:
    if not content_hash or not content_hash.startswith(_FORWARD_ORIGINALS_HASH_PREFIX):
        return None
    state = content_hash.removeprefix(_FORWARD_ORIGINALS_HASH_PREFIX).partition(":")[0]
    return state if state in {"included", "excluded"} else None


def _preserved_attachment_inputs(
    settings: Settings,
    *,
    user_id: str,
    gmail_draft_id: str,
    retained_attachment_ids: list[str] | None = None,
    message: GmailMessageRecord | None = None,
) -> list[dict[str, str]]:
    record = message or _gmail_draft_message(settings, user_id=user_id, gmail_draft_id=gmail_draft_id)
    if record is None:
        return []
    inputs: list[dict[str, str]] = []
    available = gmail_attachments_for_message(record)
    requested = set(retained_attachment_ids) if retained_attachment_ids is not None else None
    if requested is not None:
        known = {attachment.attachment_id for attachment in available}
        missing = requested - known
        if missing:
            raise ValueError("A retained draft attachment no longer exists.")
    for attachment in available:
        if requested is not None and attachment.attachment_id not in requested:
            continue
        payload = fetch_gmail_attachment(
            settings,
            user_id=user_id,
            message_id=record.message_id,
            attachment_id=attachment.attachment_id,
        )
        raw_data = str(payload.get("data") or "")
        padding = "=" * (-len(raw_data) % 4)
        decoded = base64.urlsafe_b64decode(f"{raw_data}{padding}".encode("ascii"))
        inputs.append(
            {
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "data_base64": base64.b64encode(decoded).decode("ascii"),
            }
        )
    return _validated_attachments([_AttachmentValue(**item) for item in inputs])


def _gmail_draft_message(
    settings: Settings,
    *,
    user_id: str,
    gmail_draft_id: str,
) -> GmailMessageRecord | None:
    draft = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="full")
    message_payload = draft.get("message") if isinstance(draft.get("message"), dict) else None
    return _record_from_payload(message_payload, user_id=user_id) if message_payload else None


def _preserved_thread_headers(message: GmailMessageRecord | None) -> dict[str, str]:
    if message is None:
        return {}
    headers: dict[str, str] = {}
    for canonical, lower in (("In-Reply-To", "in-reply-to"), ("References", "references")):
        value = message.headers.get(lower) or message.headers.get(canonical)
        if value:
            headers[canonical] = str(value)
    return headers


class _AttachmentValue:
    def __init__(self, *, filename: str, mime_type: str, data_base64: str) -> None:
        self.filename = filename
        self.mime_type = mime_type
        self.data_base64 = data_base64


def _attachment_values(attachments: list[dict[str, str]]) -> list[_AttachmentValue]:
    return [_AttachmentValue(**item) for item in attachments]


def _local_draft_message(settings: Settings, *, user_id: str, mailbox_thread_id: str) -> GmailMessageRecord | None:
    database_url = str(settings.database_path)
    messages = list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=mailbox_thread_id)
    if not messages:
        detail = get_mail_group_detail(database_url, user_id=user_id, group_id=mailbox_thread_id)
        messages = detail.messages if detail else []
    drafts = [message for message in messages if "DRAFT" in {label.upper() for label in message.label_ids}]
    return max(drafts, key=lambda item: item.internal_date or item.updated_at) if drafts else None


def _record_from_payload(payload: dict[str, Any], *, user_id: str) -> GmailMessageRecord:
    parsed = parse_gmail_message(payload, user_id=user_id)
    return GmailMessageRecord(created_at="", updated_at="", **parsed)


def _import_gmail_message(settings: Settings, *, user_id: str, message_id: str | None) -> GmailMessageRecord | None:
    if not message_id:
        return None
    payload = fetch_gmail_message(settings, user_id=user_id, message_id=message_id, format="full")
    record = _record_from_payload(payload, user_id=user_id)
    upsert_gmail_messages(str(settings.database_path), [record])
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[record.message_id], use_ai=False)
    enqueue_projection_refresh(settings, user_id=user_id, priority=25)
    return record


def _remove_local_messages(settings: Settings, *, user_id: str, message_ids: list[str]) -> None:
    group_ids = delete_gmail_messages(str(settings.database_path), user_id=user_id, message_ids=message_ids)
    prune_empty_mail_groups(str(settings.database_path), user_id=user_id, group_ids=group_ids)


def _after_draft_change(settings: Settings, *, user_id: str, source: str) -> None:
    refresh_app_session_snapshot(settings, user_id=user_id)
    emit_mailbox_event(
        settings,
        user_id=user_id,
        event_type=MAILBOX_CHANGED,
        mailbox_label="drafts",
        payload={"source": source},
    )


def _draft_response(
    record: ClientDraftRecord,
    message: GmailMessageRecord | None,
    *,
    fallback: MailDraftSaveRequest | None = None,
) -> MailDraftResponse:
    recipients = message.recipients if message else {}
    attachments = gmail_attachments_for_message(message) if message else []
    return MailDraftResponse(
        client_draft_id=record.client_draft_id,
        gmail_draft_id=record.gmail_draft_id,
        gmail_message_id=record.gmail_message_id,
        gmail_thread_id=record.gmail_thread_id,
        to=_clean_addresses([str(recipients.get("to") or "")]) if message else list(fallback.to if fallback else []),
        cc=_clean_addresses([str(recipients.get("cc") or "")]) if message else list(fallback.cc if fallback else []),
        bcc=_clean_addresses([str(recipients.get("bcc") or "")]) if message else list(fallback.bcc if fallback else []),
        subject=(message.subject or "") if message else (fallback.subject if fallback else ""),
        body_text=(message.text_body or "") if message else (fallback.body_text if fallback else ""),
        body_html=message.html_body_sanitized if message else (fallback.body_html if fallback else None),
        attachments=[
            MailDraftAttachment(
                filename=item.filename,
                mime_type=item.mime_type,
                message_id=item.message_id,
                attachment_id=item.attachment_id,
                download_url=item.download_url,
            )
            for item in attachments
        ],
        state=record.state if record.state in {"saved", "deleted", "sent", "failed", "reauth_required"} else "failed",  # type: ignore[arg-type]
        saved_at=record.saved_at,
        error=record.error,
    )


def _reauth_required(settings: Settings, client_draft_id: str) -> MailDraftResponse:
    return MailDraftResponse(
        client_draft_id=client_draft_id,
        state="reauth_required",
        error="Google needs full mail permission.",
        reauth_url=f"{settings.backend_origin}/auth/google",
    )


def _send_reauth_required(settings: Settings, client_send_id: str) -> MailSendResponse:
    return MailSendResponse(
        client_send_id=client_send_id,
        state="reauth_required",
        error="Google needs full mail permission.",
        reauth_url=f"{settings.backend_origin}/auth/google",
    )
