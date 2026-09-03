from __future__ import annotations

"""Read one linked Gmail directly without entering the primary mailbox pipeline.

This deliberately does not write gmail_messages, mail_groups, AI tables, sync
history, drafts, or actions. It is the account-isolated read path used while
the original primary importer remains untouched.
"""

from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.db.mail_groups import GmailMessageRecord
from app.schemas.domain import MailboxResponse, ThreadReaderResponse
from app.services.email_extraction import parse_gmail_message
from app.services.integrations.google import create_gmail_service
from app.services.mail_groups import (
    _bucket_rows_in_order,
    _gmail_row_from_canonical_thread,
    _thread_message_from_gmail,
)


ACCOUNT_THREAD_PREFIX = "gmail-account"


def scoped_thread_id(gmail_account_id: str, gmail_thread_id: str) -> str:
    return f"{ACCOUNT_THREAD_PREFIX}:{gmail_account_id}:{gmail_thread_id}"


def parse_scoped_thread_id(value: str) -> tuple[str, str] | None:
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[0] != ACCOUNT_THREAD_PREFIX:
        return None
    return parts[1], parts[2]


def scope_mailbox_response(
    response: MailboxResponse,
    *,
    gmail_account_id: str,
) -> MailboxResponse:
    """Give rows a compound UI identity without changing Gmail's own ids."""
    for section in response.sections:
        for row in section.rows:
            raw_thread_id = row.thread_id
            row.thread_id = scoped_thread_id(gmail_account_id, raw_thread_id)
            row.entity_id = row.thread_id
            row.href = f"/v1/gmail-accounts/{gmail_account_id}/threads/{raw_thread_id}"
    return response


def scope_thread_response(
    response: ThreadReaderResponse,
    *,
    gmail_account_id: str,
    gmail_thread_id: str,
) -> ThreadReaderResponse:
    compound_id = scoped_thread_id(gmail_account_id, gmail_thread_id)
    response.entity_id = compound_id
    response.gmail_thread_id = compound_id
    for message in response.messages:
        for attachment in message.attachments:
            attachment.download_url = (
                f"/v1/gmail-accounts/{gmail_account_id}/messages/"
                f"{message.id}/attachments/{attachment.attachment_id}"
            )
    return response


def verify_account_mailbox_access(
    settings: Settings,
    *,
    user_id: str,
    gmail_account_id: str,
) -> None:
    service = create_gmail_service(
        settings,
        user_id=user_id,
        gmail_account_id=gmail_account_id,
    )
    service.users().getProfile(userId="me").execute()


def build_account_mailbox_response(
    settings: Settings,
    *,
    user_id: str,
    gmail_account_id: str,
    label: str,
    limit: int,
    cursor: str | None,
    search_query: str | None = None,
) -> MailboxResponse:
    service = create_gmail_service(
        settings,
        user_id=user_id,
        gmail_account_id=gmail_account_id,
    )
    # Full Gmail threads are intentionally hydrated before presentation. Keep
    # the first provider page small so the native inbox becomes interactive
    # quickly, then let its existing cursor loader request later pages.
    page_size = min(max(limit, 1), 10)
    request: dict[str, Any] = {
        "userId": "me",
        "maxResults": page_size,
        "includeSpamTrash": label in {"all", "spam", "trash"},
    }
    label_id = _gmail_label_id(label)
    if label_id:
        request["labelIds"] = [label_id]
    query_parts = [part for part in (_gmail_query(label), (search_query or "").strip()) if part]
    if query_parts:
        request["q"] = " ".join(query_parts)
    if cursor:
        request["pageToken"] = cursor

    listed = service.users().threads().list(**request).execute()
    thread_refs = listed.get("threads") if isinstance(listed, dict) else None
    records_by_thread: list[tuple[str, list[GmailMessageRecord]]] = []
    for item in thread_refs if isinstance(thread_refs, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        gmail_thread_id = str(item["id"])
        payload = service.users().threads().get(
            userId="me",
            id=gmail_thread_id,
            format="full",
        ).execute()
        messages = payload.get("messages") if isinstance(payload, dict) else None
        records = [
            _record_from_payload(message, user_id=user_id)
            for message in messages if isinstance(messages, list) and isinstance(message, dict)
        ]
        records = [record for record in records if record.message_id]
        if records:
            records.sort(key=lambda record: record.internal_date or record.updated_at)
            records_by_thread.append((gmail_thread_id, records))

    rows = []
    for gmail_thread_id, records in records_by_thread:
        row = _gmail_row_from_canonical_thread(gmail_thread_id, records, label, None)
        rows.append(row)

    unread = sum(1 for row in rows if row.unread)
    now = datetime.now(timezone.utc).isoformat()
    next_cursor = (
        str(listed.get("nextPageToken") or "") or None
        if isinstance(listed, dict)
        else None
    )
    response = MailboxResponse(
        label=label,  # type: ignore[arg-type]
        total_threads=len(rows),
        unread_threads=unread,
        next_cursor=next_cursor,
        loaded_threads=len(rows),
        sections=_bucket_rows_in_order(rows),
        ready_count=len(rows),
        pending_count=0,
        mailbox_revision=f"live:{gmail_account_id}:{now}",
        generated_at=now,
        full_import_running=False,
        full_import_completed=True,
        phase="ready",
        initial_window_complete=True,
    )
    return scope_mailbox_response(response, gmail_account_id=gmail_account_id)


def build_account_thread_response(
    settings: Settings,
    *,
    user_id: str,
    gmail_account_id: str,
    gmail_thread_id: str,
    limit: int,
    offset: int,
) -> ThreadReaderResponse:
    parsed = parse_scoped_thread_id(gmail_thread_id)
    if parsed is not None:
        parsed_account_id, gmail_thread_id = parsed
        if parsed_account_id != gmail_account_id:
            raise ValueError("Thread belongs to a different Gmail account")
    service = create_gmail_service(
        settings,
        user_id=user_id,
        gmail_account_id=gmail_account_id,
    )
    payload = service.users().threads().get(
        userId="me",
        id=gmail_thread_id,
        format="full",
    ).execute()
    raw_messages = payload.get("messages") if isinstance(payload, dict) else None
    records = [
        _record_from_payload(message, user_id=user_id)
        for message in raw_messages if isinstance(raw_messages, list) and isinstance(message, dict)
    ]
    records.sort(key=lambda record: record.internal_date or record.updated_at)
    page = records[offset : offset + limit]
    messages = [_thread_message_from_gmail(record) for record in page]
    for message in messages:
        for attachment in message.attachments:
            attachment.download_url = (
                f"/v1/gmail-accounts/{gmail_account_id}/messages/"
                f"{message.id}/attachments/{attachment.attachment_id}"
            )
    subject = records[-1].subject if records else None
    response = ThreadReaderResponse(
        entity_id=gmail_thread_id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=gmail_thread_id,
        subject=subject,
        title=subject,
        summary=records[-1].snippet if records else None,
        total_messages=len(records),
        limit=limit,
        offset=offset,
        has_more=offset + len(page) < len(records),
        messages=messages,
        content_revision=f"live:{gmail_account_id}:{gmail_thread_id}",
    )
    return scope_thread_response(
        response,
        gmail_account_id=gmail_account_id,
        gmail_thread_id=gmail_thread_id,
    )


def fetch_account_attachment(
    settings: Settings,
    *,
    user_id: str,
    gmail_account_id: str,
    message_id: str,
    attachment_id: str,
) -> dict[str, object]:
    service = create_gmail_service(
        settings,
        user_id=user_id,
        gmail_account_id=gmail_account_id,
    )
    return service.users().messages().attachments().get(
        userId="me",
        messageId=message_id,
        id=attachment_id,
    ).execute()


def _record_from_payload(payload: dict[str, Any], *, user_id: str) -> GmailMessageRecord:
    parsed = parse_gmail_message(payload, user_id=user_id)
    now = datetime.now(timezone.utc).isoformat()
    return GmailMessageRecord(
        **parsed,
        created_at=now,
        updated_at=now,
        body_fetch_status="ready",
        body_fetched_at=now,
        attachment_descriptors_ready=True,
    )


def _gmail_label_id(label: str) -> str | None:
    return {
        "inbox": "INBOX",
        "sent": "SENT",
        "drafts": "DRAFT",
        "spam": "SPAM",
        "trash": "TRASH",
        "starred": "STARRED",
    }.get(label)


def _gmail_query(label: str) -> str | None:
    if label == "archive":
        return "-in:inbox -in:sent -in:drafts -in:spam -in:trash"
    return None
