from __future__ import annotations

"""Idempotent Gmail draft create, edit, reopen, delete, and send workflows."""

import base64
from hashlib import sha256
import json
import logging
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
    mark_client_draft_sending,
    mark_client_draft_sent,
    prune_empty_mail_groups,
    restore_client_draft_after_definite_send_failure,
    upsert_client_draft,
    upsert_gmail_messages,
    user_can_write_gmail,
)
from app.db.user_mail_guard import shared_user_mail_lock
from app.schemas.domain import MailDraftAttachment, MailDraftResponse, MailDraftSaveRequest, MailDraftSendRequest, MailSendResponse
from app.services.email_extraction import mark_full_gmail_payload_body_fetch_status, parse_gmail_message
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    MultipleSentGmailMessagesFound,
    create_gmail_draft,
    delete_gmail_draft,
    fetch_gmail_attachment,
    fetch_gmail_draft,
    fetch_gmail_message,
    find_gmail_draft_by_message_id,
    find_gmail_draft_by_rfc822_message_id,
    find_sent_gmail_message_by_rfc822_message_id,
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
_AMBIGUOUS_GOOGLE_HTTP_STATUSES = {408, 409, 425, 429}
_GOOGLE_AUTH_HTTP_STATUSES = {401, 403}
logger = logging.getLogger(__name__)


class MailDraftIdentityConflict(RuntimeError):
    """A client draft identity was bound to a different provider operation."""


def _require_mutable_draft_mapping(
    existing: ClientDraftRecord | None,
    *,
    requested_gmail_draft_id: str | None,
) -> None:
    if existing and existing.state in {"sent", "deleted"}:
        raise MailDraftIdentityConflict("This draft is already finalized.")
    if existing and existing.state == "sending":
        raise MailDraftIdentityConflict(
            "This draft is being sent and cannot be changed until delivery is confirmed."
        )
    if (
        existing
        and existing.gmail_draft_id
        and requested_gmail_draft_id
        and existing.gmail_draft_id != requested_gmail_draft_id
    ):
        raise MailDraftIdentityConflict("This client draft is already bound to a different Gmail draft.")


def save_draft(settings: Settings, *, user_id: str, request: MailDraftSaveRequest) -> MailDraftResponse:
    if not _can_manage_drafts(settings, user_id=user_id):
        return _reauth_required(settings, request.client_draft_id)
    try:
        to = _validated_addresses(request.to)
        cc = _validated_addresses(request.cc)
        bcc = _validated_addresses(request.bcc)
        _validate_subject(request.subject)
    except ValueError as exc:
        return MailDraftResponse(client_draft_id=request.client_draft_id, state="failed", error=str(exc))

    database_url = str(settings.database_path)
    # Resolve an already-established provider identity before entering the
    # ordered provider->client lock scope. The mapping is read again inside the
    # lock and remains authoritative there.
    lock_gmail_draft_id = request.gmail_draft_id
    if not lock_gmail_draft_id:
        preexisting = get_client_draft(
            database_url,
            user_id=user_id,
            client_draft_id=request.client_draft_id,
        )
        lock_gmail_draft_id = preexisting.gmail_draft_id if preexisting else None
    # Always acquire user -> provider draft -> client draft. Every mutation uses
    # this global order, so delete and adoption cannot form a lock cycle.
    with shared_user_mail_lock(database_url, user_id=user_id), client_draft_lock(
        database_url,
        user_id=user_id,
        client_draft_id=request.client_draft_id,
        gmail_draft_id=lock_gmail_draft_id,
    ) as draft_lock:
        existing = get_client_draft(database_url, user_id=user_id, client_draft_id=request.client_draft_id)
        _require_mutable_draft_mapping(
            existing,
            requested_gmail_draft_id=request.gmail_draft_id,
        )
        # Once established, the durable mapping is authoritative over a stale
        # client/path value. A record without a provider ID may still adopt the
        # ID returned to a client before an earlier checkpoint response failed.
        gmail_draft_id = (existing.gmail_draft_id if existing else None) or request.gmail_draft_id
        if gmail_draft_id and gmail_draft_id != lock_gmail_draft_id and draft_lock is not None:
            draft_lock.adopt_gmail_draft_id(gmail_draft_id)
            lock_gmail_draft_id = gmail_draft_id
            existing = get_client_draft(
                database_url,
                user_id=user_id,
                client_draft_id=request.client_draft_id,
            )
            _require_mutable_draft_mapping(
                existing,
                requested_gmail_draft_id=gmail_draft_id,
            )
        message_header = _draft_rfc822_message_id(request.client_draft_id)
        try:
            if not gmail_draft_id:
                recovered = find_gmail_draft_by_rfc822_message_id(
                    settings,
                    user_id=user_id,
                    rfc822_message_id=message_header,
                )
                gmail_draft_id = str(recovered.get("id") or "") if recovered else None
                if gmail_draft_id and gmail_draft_id != lock_gmail_draft_id and draft_lock is not None:
                    draft_lock.adopt_gmail_draft_id(gmail_draft_id)
                    lock_gmail_draft_id = gmail_draft_id
                    existing = get_client_draft(
                        database_url,
                        user_id=user_id,
                        client_draft_id=request.client_draft_id,
                    )
                    _require_mutable_draft_mapping(
                        existing,
                        requested_gmail_draft_id=gmail_draft_id,
                    )
                if not gmail_draft_id and existing and existing.state == "creating":
                    return MailDraftResponse(
                        client_draft_id=request.client_draft_id,
                        state="failed",
                        error="The earlier draft save is still being confirmed. Retry shortly.",
                    )

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
            return MailDraftResponse(client_draft_id=request.client_draft_id, state="failed", error=str(exc))
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

        provider_create = not gmail_draft_id
        if existing is None or existing.state in {"creating", "failed"}:
            # This checkpoint closes the otherwise unavoidable gap between a
            # successful Gmail create and learning its provider ID locally. A
            # retry of an ambiguous create reconciles by Message-ID and never
            # treats a temporarily empty search result as permission to create
            # a second draft.
            existing = upsert_client_draft(
                database_url,
                user_id=user_id,
                client_draft_id=request.client_draft_id,
                gmail_draft_id=gmail_draft_id,
                gmail_message_id=existing.gmail_message_id if existing else None,
                gmail_thread_id=effective_gmail_thread_id,
                content_hash=content_hash,
                state="creating",
                created_at=existing.created_at if existing else request.created_at,
            )

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
            if provider_create:
                _mark_definite_create_failure(
                    settings,
                    user_id=user_id,
                    record=existing,
                    error="Google credentials are not connected.",
                )
            return _reauth_required(settings, request.client_draft_id)
        except HttpError as exc:
            status = _google_http_status(exc)
            if status in _GOOGLE_AUTH_HTTP_STATUSES:
                if provider_create:
                    _mark_definite_create_failure(
                        settings,
                        user_id=user_id,
                        record=existing,
                        error="Google needs full mail permission.",
                    )
                return _reauth_required(settings, request.client_draft_id)
            if provider_create and _is_definite_google_client_rejection(exc):
                error = "Google rejected the draft. Check its fields and try saving again."
                _mark_definite_create_failure(
                    settings,
                    user_id=user_id,
                    record=existing,
                    error=error,
                )
                return MailDraftResponse(
                    client_draft_id=request.client_draft_id,
                    state="failed",
                    error=error,
                )
            raise

        gmail_draft_id = str(result.get("id") or gmail_draft_id or "") or None
        result_message = result.get("message") if isinstance(result.get("message"), dict) else {}
        gmail_message_id = str(result_message.get("id") or "") or None
        gmail_thread_id = str(result_message.get("threadId") or effective_gmail_thread_id or "") or None
        if not gmail_draft_id:
            return MailDraftResponse(
                client_draft_id=request.client_draft_id,
                state="failed",
                error="The draft was accepted but its Gmail identity is still being confirmed. Retry shortly.",
            )

        authoritative_rebound: ClientDraftRecord | None = None
        if provider_create and draft_lock is not None:
            # Gmail identity did not exist when this operation began. Drop the
            # client-only lock and reacquire provider -> client before making
            # the provider success durable. Delete can win this handoff, but it
            # can no longer race a blind `saved` checkpoint.
            draft_lock.adopt_gmail_draft_id(gmail_draft_id)
            rebound = get_client_draft(
                database_url,
                user_id=user_id,
                client_draft_id=request.client_draft_id,
            )
            _require_mutable_draft_mapping(
                rebound,
                requested_gmail_draft_id=gmail_draft_id,
            )
            existing = rebound or existing
            if rebound is not None and (
                rebound.state == "saved"
                or (rebound.state == "creating" and rebound.content_hash != content_hash)
            ):
                # Releasing the client-only lock is required to establish the
                # global provider -> client order. A waiter can finish a newer
                # save—or leave an ambiguous newer create—during that handoff,
                # so its durable checkpoint is now authoritative. Confirm the
                # provider still exists, but never overwrite the waiter's
                # content hash with this older request.
                authoritative_rebound = rebound
            try:
                confirmed = fetch_gmail_draft(
                    settings,
                    user_id=user_id,
                    gmail_draft_id=gmail_draft_id,
                    format="minimal",
                )
            except GoogleCredentialsUnavailable:
                return _reauth_required(settings, request.client_draft_id)
            except HttpError as exc:
                status = _google_http_status(exc)
                if status in _GOOGLE_AUTH_HTTP_STATUSES:
                    return _reauth_required(settings, request.client_draft_id)
                if status == 404:
                    error = "The new Gmail draft was deleted before saving completed. Create a new draft."
                    upsert_client_draft(
                        database_url,
                        user_id=user_id,
                        client_draft_id=request.client_draft_id,
                        gmail_draft_id=gmail_draft_id,
                        gmail_message_id=gmail_message_id,
                        gmail_thread_id=gmail_thread_id,
                        content_hash=content_hash,
                        state="deleted",
                        created_at=existing.created_at if existing else request.created_at,
                        error=error,
                    )
                    return MailDraftResponse(
                        client_draft_id=request.client_draft_id,
                        gmail_draft_id=gmail_draft_id,
                        state="failed",
                        error=error,
                    )
                raise
            confirmed_message = confirmed.get("message") if isinstance(confirmed.get("message"), dict) else {}
            gmail_message_id = str(confirmed_message.get("id") or gmail_message_id or "") or None
            gmail_thread_id = str(confirmed_message.get("threadId") or gmail_thread_id or "") or None

        if authoritative_rebound is not None:
            imported: GmailMessageRecord | None = None
            try:
                imported = _import_gmail_message(
                    settings,
                    user_id=user_id,
                    message_id=gmail_message_id or authoritative_rebound.gmail_message_id,
                )
            except Exception as exc:
                _log_post_checkpoint_failure("draft_handoff_projection", user_id=user_id, exc=exc)
            fallback = effective_request if authoritative_rebound.content_hash == content_hash else None
            response = _draft_response(authoritative_rebound, imported, fallback=fallback)
            if authoritative_rebound.state == "creating":
                return response.model_copy(
                    update={
                        "state": "failed",
                        "error": "A newer draft save is still being confirmed. Retry shortly.",
                    }
                )
            return response

        # Provider success is the durable save boundary. Persist its identity
        # before any optional hydration, parsing, projection, or notification.
        record = upsert_client_draft(
            database_url,
            user_id=user_id,
            client_draft_id=request.client_draft_id,
            gmail_draft_id=gmail_draft_id,
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            content_hash=content_hash,
            state="saved",
            created_at=existing.created_at if existing else request.created_at,
        )
        old_message_id = existing.gmail_message_id if existing else None
        imported: GmailMessageRecord | None = None
        try:
            if not gmail_message_id:
                hydrated = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="minimal")
                result_message = hydrated.get("message") if isinstance(hydrated.get("message"), dict) else {}
                gmail_message_id = str(result_message.get("id") or "") or None
                gmail_thread_id = str(result_message.get("threadId") or gmail_thread_id or "") or None
                if gmail_message_id:
                    record = upsert_client_draft(
                        database_url,
                        user_id=user_id,
                        client_draft_id=request.client_draft_id,
                        gmail_draft_id=gmail_draft_id,
                        gmail_message_id=gmail_message_id,
                        gmail_thread_id=gmail_thread_id,
                        content_hash=content_hash,
                        state="saved",
                        created_at=record.created_at,
                    )
            imported = _import_gmail_message(settings, user_id=user_id, message_id=gmail_message_id)
            if old_message_id and old_message_id != gmail_message_id:
                _remove_local_messages(settings, user_id=user_id, message_ids=[old_message_id])
        except Exception as exc:
            _log_post_checkpoint_failure("draft_projection", user_id=user_id, exc=exc)
        try:
            _after_draft_change(settings, user_id=user_id, source="draft_saved")
        except Exception as exc:
            _log_post_checkpoint_failure("draft_notification", user_id=user_id, exc=exc)
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
    client_draft_id = mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}"
    with shared_user_mail_lock(database_url, user_id=user_id), client_draft_lock(
        database_url,
        user_id=user_id,
        client_draft_id=client_draft_id,
        gmail_draft_id=gmail_draft_id,
    ):
        # Send/delete/save all hold the same provider lock. Re-read after any
        # waiter finishes and never let a late GET regress a finalized mapping.
        current = get_client_draft(database_url, user_id=user_id, gmail_draft_id=gmail_draft_id)
        if current is not None:
            mapping = current
            client_draft_id = current.client_draft_id
            if current.state in {"sending", "sent", "deleted"}:
                return _noneditable_draft_response(current)
        try:
            draft = fetch_gmail_draft(settings, user_id=user_id, gmail_draft_id=gmail_draft_id, format="full")
        except GoogleCredentialsUnavailable:
            return _reauth_required(settings, client_draft_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, client_draft_id)
            if getattr(exc.resp, "status", None) == 404:
                return MailDraftResponse(client_draft_id=client_draft_id, state="failed", error="Draft not found.")
            raise
        message_payload = draft.get("message") if isinstance(draft.get("message"), dict) else None
        if message_payload is None:
            return MailDraftResponse(client_draft_id=client_draft_id, state="failed", error="Draft message is missing.")
        try:
            imported = _record_from_payload(settings, message_payload, user_id=user_id)
        except GoogleCredentialsUnavailable:
            return _reauth_required(settings, client_draft_id)
        except HttpError as exc:
            if getattr(exc.resp, "status", None) in {401, 403}:
                return _reauth_required(settings, client_draft_id)
            raise
        upsert_gmail_messages(database_url, [imported])
        record = upsert_client_draft(
            database_url,
            user_id=user_id,
            client_draft_id=client_draft_id,
            gmail_draft_id=gmail_draft_id,
            gmail_message_id=imported.message_id,
            gmail_thread_id=imported.gmail_thread_id,
            # Only a completed save has a request hash known to match the
            # provider. A GET that resolves an ambiguous `creating` checkpoint
            # must not bless its intended hash when Gmail may contain older
            # content; using the observed body hash forces the next explicit
            # save to compare unequal and update Gmail.
            content_hash=(
                mapping.content_hash
                if mapping is not None and mapping.state == "saved"
                else imported.body_hash
            ),
            state="saved",
            created_at=mapping.created_at if mapping else imported.created_at,
        )
        return _draft_response(record, imported)


def delete_draft_by_id(settings: Settings, *, user_id: str, gmail_draft_id: str) -> bool:
    if not _can_manage_drafts(settings, user_id=user_id):
        return False
    database_url = str(settings.database_path)
    with shared_user_mail_lock(database_url, user_id=user_id):
        mapping = get_client_draft(
            database_url,
            user_id=user_id,
            gmail_draft_id=gmail_draft_id,
        )
        # App-created drafts resolve to the same client identity used by save
        # and send. An unmapped provider draft has no returned client identity
        # known to this app, so its Gmail ID is the only deterministic fallback.
        draft_lock_id = mapping.client_draft_id if mapping else f"gmail:{gmail_draft_id}"
        with client_draft_lock(
            database_url,
            user_id=user_id,
            client_draft_id=draft_lock_id,
            gmail_draft_id=gmail_draft_id,
        ):
            # The mapping may have been committed while delete waited on the
            # provider lock. Re-read it inside the lock so the durable row is
            # finalized as deleted rather than left as a stale saved draft.
            mapping = get_client_draft(
                database_url,
                user_id=user_id,
                gmail_draft_id=gmail_draft_id,
            )
            if mapping is not None and mapping.state in {"sending", "sent"}:
                raise MailDraftIdentityConflict(
                    "This draft is being sent or was already sent and cannot be deleted."
                )
            return _delete_draft_by_id_locked(
                settings,
                user_id=user_id,
                gmail_draft_id=gmail_draft_id,
                mapping=mapping,
            )


def _delete_draft_by_id_locked(
    settings: Settings,
    *,
    user_id: str,
    gmail_draft_id: str,
    mapping: ClientDraftRecord | None,
) -> bool:
    database_url = str(settings.database_path)
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
        deleted = mark_client_draft_deleted(
            database_url,
            user_id=user_id,
            client_draft_id=mapping.client_draft_id,
        )
        if deleted is None:
            raise MailDraftIdentityConflict(
                "This draft is being sent or was already sent and cannot be deleted."
            )
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
    with shared_user_mail_lock(database_url, user_id=user_id), client_draft_lock(
        database_url,
        user_id=user_id,
        client_draft_id=request.client_draft_id,
        gmail_draft_id=gmail_draft_id,
    ):
        mapping = get_client_draft(database_url, user_id=user_id, client_draft_id=request.client_draft_id)
        if mapping is None or not mapping.gmail_draft_id:
            raise MailDraftIdentityConflict("Save this draft before sending it.")
        if mapping.gmail_draft_id != gmail_draft_id:
            raise MailDraftIdentityConflict("This client draft is bound to a different Gmail draft.")
        if mapping.state == "sent":
            if mapping.last_client_send_id != request.client_send_id:
                raise MailDraftIdentityConflict("This draft was already sent with a different send identity.")
            return MailSendResponse(
                client_send_id=request.client_send_id,
                gmail_thread_id=mapping.gmail_thread_id,
                gmail_message_id=mapping.sent_message_id,
                state="sent",
                sent_at=mapping.updated_at,
            )
        if mapping.state == "sending":
            if mapping.last_client_send_id != request.client_send_id:
                raise MailDraftIdentityConflict("A different send attempt is already being confirmed.")
            return _reconcile_sending_draft(settings, user_id=user_id, mapping=mapping)
        if mapping.state != "saved":
            raise MailDraftIdentityConflict("This draft is not ready to send.")

        sending = mark_client_draft_sending(
            database_url,
            user_id=user_id,
            client_draft_id=request.client_draft_id,
            client_send_id=request.client_send_id,
        )
        if sending is None:
            raise MailDraftIdentityConflict("This draft changed before it could be sent.")
        try:
            result = send_gmail_draft(settings, user_id=user_id, gmail_draft_id=mapping.gmail_draft_id)
        except GoogleCredentialsUnavailable:
            restore_client_draft_after_definite_send_failure(
                database_url,
                user_id=user_id,
                client_draft_id=request.client_draft_id,
                client_send_id=request.client_send_id,
                error="Google credentials are not connected.",
            )
            return _send_reauth_required(settings, request.client_send_id)
        except HttpError as exc:
            status = _google_http_status(exc)
            if status in _GOOGLE_AUTH_HTTP_STATUSES:
                restore_client_draft_after_definite_send_failure(
                    database_url,
                    user_id=user_id,
                    client_draft_id=request.client_draft_id,
                    client_send_id=request.client_send_id,
                    error="Google needs full mail permission.",
                )
                return _send_reauth_required(settings, request.client_send_id)
            if status == 404:
                return _reconcile_sending_draft(settings, user_id=user_id, mapping=sending)
            if _is_definite_google_client_rejection(exc):
                error = "Google rejected the send. Check the draft and try again."
                restored = restore_client_draft_after_definite_send_failure(
                    database_url,
                    user_id=user_id,
                    client_draft_id=request.client_draft_id,
                    client_send_id=request.client_send_id,
                    error=error,
                )
                if restored is None:
                    raise RuntimeError("Definite draft send failure could not be persisted") from exc
                return MailSendResponse(
                    client_send_id=request.client_send_id,
                    gmail_thread_id=restored.gmail_thread_id,
                    state="failed",
                    error=error,
                )
            _log_post_checkpoint_failure("draft_send_ambiguous", user_id=user_id, exc=exc)
            return _pending_draft_send_response(sending)
        except Exception as exc:
            _log_post_checkpoint_failure("draft_send_ambiguous", user_id=user_id, exc=exc)
            return _pending_draft_send_response(sending)
        gmail_message_id = str(result.get("id") or "") or None
        gmail_thread_id = str(result.get("threadId") or "") or None
        if not gmail_message_id:
            return _reconcile_sending_draft(settings, user_id=user_id, mapping=sending)
        return _finalize_draft_send(
            settings,
            user_id=user_id,
            mapping=sending,
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
        )


def _reconcile_sending_draft(
    settings: Settings,
    *,
    user_id: str,
    mapping: ClientDraftRecord,
) -> MailSendResponse:
    try:
        recovered = find_sent_gmail_message_by_rfc822_message_id(
            settings,
            user_id=user_id,
            rfc822_message_id=_draft_rfc822_message_id(mapping.client_draft_id),
        )
    except MultipleSentGmailMessagesFound:
        return _pending_draft_send_response(
            mapping,
            error="Multiple Sent copies matched this delivery. Delivery is ambiguous and will not be retried automatically.",
        )
    except GoogleCredentialsUnavailable:
        return _send_reauth_required(settings, mapping.last_client_send_id or "")
    except HttpError as exc:
        if getattr(exc.resp, "status", None) in {401, 403}:
            return _send_reauth_required(settings, mapping.last_client_send_id or "")
        _log_post_checkpoint_failure("draft_send_reconcile", user_id=user_id, exc=exc)
        return _pending_draft_send_response(mapping)
    except Exception as exc:
        _log_post_checkpoint_failure("draft_send_reconcile", user_id=user_id, exc=exc)
        return _pending_draft_send_response(mapping)
    if not recovered:
        return _pending_draft_send_response(mapping)
    gmail_message_id = str(recovered.get("id") or "") or None
    if not gmail_message_id:
        return _pending_draft_send_response(mapping)
    return _finalize_draft_send(
        settings,
        user_id=user_id,
        mapping=mapping,
        gmail_message_id=gmail_message_id,
        gmail_thread_id=str(recovered.get("threadId") or "") or mapping.gmail_thread_id,
    )


def _finalize_draft_send(
    settings: Settings,
    *,
    user_id: str,
    mapping: ClientDraftRecord,
    gmail_message_id: str,
    gmail_thread_id: str | None,
) -> MailSendResponse:
    sent = mark_client_draft_sent(
        str(settings.database_path),
        user_id=user_id,
        client_draft_id=mapping.client_draft_id,
        client_send_id=mapping.last_client_send_id or "",
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
    )
    if sent is None:
        raise RuntimeError("Draft send completion could not be persisted")

    # Gmail delivery and the durable mapping now agree. Projection work is
    # best-effort and must never turn a delivered message into a retryable send.
    try:
        _import_gmail_message(settings, user_id=user_id, message_id=gmail_message_id)
        if mapping.gmail_message_id and mapping.gmail_message_id != gmail_message_id:
            _remove_local_messages(settings, user_id=user_id, message_ids=[mapping.gmail_message_id])
    except Exception as exc:
        _log_post_checkpoint_failure("draft_sent_projection", user_id=user_id, exc=exc)
    try:
        _after_draft_change(settings, user_id=user_id, source="draft_sent")
    except Exception as exc:
        _log_post_checkpoint_failure("draft_sent_notification", user_id=user_id, exc=exc)
    return MailSendResponse(
        client_send_id=sent.last_client_send_id or "",
        gmail_thread_id=sent.gmail_thread_id,
        gmail_message_id=sent.sent_message_id,
        state="sent",
        sent_at=sent.updated_at,
    )


def _pending_draft_send_response(
    mapping: ClientDraftRecord,
    *,
    error: str = "Delivery is still being confirmed. Retry safely.",
) -> MailSendResponse:
    return MailSendResponse(
        client_send_id=mapping.last_client_send_id or "",
        gmail_thread_id=mapping.gmail_thread_id,
        state="sending",
        error=error,
    )


def _mark_definite_create_failure(
    settings: Settings,
    *,
    user_id: str,
    record: ClientDraftRecord,
    error: str,
) -> None:
    upsert_client_draft(
        str(settings.database_path),
        user_id=user_id,
        client_draft_id=record.client_draft_id,
        gmail_draft_id=record.gmail_draft_id,
        gmail_message_id=record.gmail_message_id,
        gmail_thread_id=record.gmail_thread_id,
        content_hash=record.content_hash,
        state="failed",
        created_at=record.created_at,
        error=error,
    )


def _google_http_status(exc: HttpError) -> int | None:
    try:
        return int(getattr(exc.resp, "status", None))
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


def _log_post_checkpoint_failure(stage: str, *, user_id: str, exc: Exception) -> None:
    logger.warning(
        "Gmail draft post-checkpoint work failed",
        extra={
            "event_fields": {
                "event": "gmail_draft.post_checkpoint_failed",
                "stage": stage,
                "user_id": user_id,
                "exception_type": type(exc).__name__,
            }
        },
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
    return _record_from_payload(settings, message_payload, user_id=user_id) if message_payload else None


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


def _record_from_payload(settings: Settings, payload: dict[str, Any], *, user_id: str) -> GmailMessageRecord:
    parsed = parse_gmail_message(
        payload,
        user_id=user_id,
        inline_attachment_resolver=_gmail_body_attachment_resolver(settings, user_id=user_id),
    )
    parsed = mark_full_gmail_payload_body_fetch_status(parsed)
    if parsed["body_fetch_status"] != "fetched":
        raise RuntimeError("Gmail draft body could not be downloaded")
    return GmailMessageRecord(created_at="", updated_at="", **parsed)


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


def _import_gmail_message(settings: Settings, *, user_id: str, message_id: str | None) -> GmailMessageRecord | None:
    if not message_id:
        return None
    payload = fetch_gmail_message(settings, user_id=user_id, message_id=message_id, format="full")
    record = _record_from_payload(settings, payload, user_id=user_id)
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


def _noneditable_draft_response(record: ClientDraftRecord) -> MailDraftResponse:
    """Represent a finalized/in-flight mapping without exposing it as editable."""
    if record.state == "sending":
        return MailDraftResponse(
            client_draft_id=record.client_draft_id,
            gmail_draft_id=record.gmail_draft_id,
            gmail_message_id=record.gmail_message_id,
            gmail_thread_id=record.gmail_thread_id,
            state="failed",
            saved_at=record.saved_at,
            error="This draft is being sent and cannot be edited.",
        )
    return _draft_response(record, None)


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
