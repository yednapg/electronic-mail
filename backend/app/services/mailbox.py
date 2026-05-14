from __future__ import annotations

"""Mailbox-first Gmail projections backed by local Gmail snapshot tables."""

from collections import OrderedDict
from datetime import datetime
from typing import cast

from app.db.models import StoredGmailMessageSnapshot, StoredGmailThreadProjection
from app.db.repository import (
    DEFAULT_USER_ID,
    get_gmail_sync_state,
    get_gmail_thread_message_snapshot_count,
    get_gmail_thread_projection,
    get_google_oauth_token,
    list_gmail_thread_message_snapshots,
    list_mailbox_thread_projections,
)
from app.schemas.domain import (
    GmailThreadRow,
    GmailThreadSection,
    GmailViewResponse,
    MailboxLabel,
    MailboxResponse,
    MailboxSyncStateResponse,
    SourceType,
    ThreadMessage,
    ThreadReaderResponse,
)
from app.services.history import compact_text
from app.services.integrations.google import get_google_auth_state


VISIBLE_BUCKETS = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("last-seven-days", "Last seven days"),
    ("earlier-this-month", "Earlier this month"),
]
MAILBOX_LABELS = {"inbox", "sent", "drafts", "trash", "archive", "all"}


def build_mailbox_response(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    label: str = "inbox",
    limit: int = 100,
    cursor: str | None = None,
    current_time: str | None = None,
) -> MailboxResponse:
    """Return a label-filtered mailbox view from the local Gmail snapshot ledger."""
    mailbox_label = normalize_mailbox_label(label)
    projections, total_threads, next_cursor = list_mailbox_thread_projections(
        database_path,
        user_id=user_id,
        label=mailbox_label,
        limit=limit,
        cursor=cursor,
    )
    reference = parse_datetime(current_time) if current_time is not None else datetime.now().astimezone()
    rows = [to_gmail_thread_row(projection) for projection in projections]
    return MailboxResponse(
        label=mailbox_label,
        total_threads=total_threads,
        next_cursor=next_cursor,
        sections=bucket_thread_rows(rows, reference),
    )


def build_gmail_view_response_from_mailbox(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    current_time: str | None = None,
) -> GmailViewResponse:
    """Compatibility shape for the old Gmail view endpoint."""
    mailbox = build_mailbox_response(
        database_path,
        user_id=user_id,
        label="inbox",
        limit=250,
        current_time=current_time,
    )
    return GmailViewResponse(total_threads=mailbox.total_threads, sections=mailbox.sections)


def build_mailbox_thread_response(
    database_path: str,
    *,
    thread_id: str,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 25,
    offset: int = 0,
) -> ThreadReaderResponse:
    """Open a Gmail thread directly from snapshots without requiring an entity."""
    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    total_messages = get_gmail_thread_message_snapshot_count(
        database_path,
        user_id=user_id,
        thread_id=thread_id,
    )
    projection = get_gmail_thread_projection(database_path, user_id=user_id, thread_id=thread_id)
    snapshots = list_gmail_thread_message_snapshots(
        database_path,
        user_id=user_id,
        thread_id=thread_id,
        limit=bounded_limit,
        offset=bounded_offset,
    )
    subject = projection.latest_subject if projection is not None else None
    return ThreadReaderResponse(
        entity_id=f"gmail-thread:{thread_id}",
        user_id=user_id,
        source="gmail",
        gmail_thread_id=thread_id,
        subject=subject or latest_subject_from_snapshots(snapshots) or "Gmail thread",
        total_messages=total_messages,
        limit=bounded_limit,
        offset=bounded_offset,
        has_more=bounded_offset + len(snapshots) < total_messages,
        messages=[to_thread_message(snapshot) for snapshot in snapshots],
    )


def build_mailbox_sync_state_response(settings, *, user_id: str = DEFAULT_USER_ID) -> MailboxSyncStateResponse:
    """Return cache-friendly Gmail sync/watch status."""
    auth = get_google_auth_state(settings)
    connected = (
        get_google_oauth_token(str(settings.database_path), user_id=user_id) is not None
        if user_id != DEFAULT_USER_ID
        else auth.connected
    )
    sync_state = get_gmail_sync_state(str(settings.database_path), user_id)
    _rows, total_threads, _cursor = list_mailbox_thread_projections(
        str(settings.database_path),
        user_id=user_id,
        label="all",
        limit=1,
    )
    return MailboxSyncStateResponse(
        connected=connected,
        last_history_id=sync_state.last_history_id if sync_state is not None else None,
        last_full_sync_at=sync_state.last_full_sync_at if sync_state is not None else None,
        watch_expiration_at=sync_state.watch_expiration_at if sync_state is not None else None,
        last_sync_started_at=sync_state.last_sync_started_at if sync_state is not None else None,
        last_sync_completed_at=sync_state.last_sync_completed_at if sync_state is not None else None,
        last_sync_error=sync_state.last_sync_error if sync_state is not None else None,
        total_threads=total_threads,
    )


def to_gmail_thread_row(projection: StoredGmailThreadProjection) -> GmailThreadRow:
    """Adapt one thread projection to the existing Gmail row contract."""
    return GmailThreadRow(
        thread_id=projection.thread_id,
        entity_id=None,
        latest_source_record_id=projection.latest_message_id,
        latest_received_at=projection.latest_received_at,
        latest_subject=projection.latest_subject,
        latest_sender=projection.latest_sender,
        participants=projection.participants,
        message_count=projection.message_count,
        summary=projection.snippet,
        snippet=projection.snippet,
        label_ids=projection.label_ids,
        unread=projection.unread,
        lifecycle_updates=[],
    )


def bucket_thread_rows(rows: list[GmailThreadRow], reference: datetime) -> list[GmailThreadSection]:
    """Place thread rows into non-overlapping mailbox date buckets."""
    bucket_rows: OrderedDict[str, list[GmailThreadRow]] = OrderedDict((bucket_id, []) for bucket_id, _title in VISIBLE_BUCKETS)
    bucket_titles = {bucket_id: title for bucket_id, title in VISIBLE_BUCKETS}
    reference_date = reference.date()

    for row in rows:
        received = parse_datetime(row.latest_received_at)
        bucket_id, title = bucket_for_received_at(received, reference_date)
        bucket_titles[bucket_id] = title
        bucket_rows.setdefault(bucket_id, []).append(row)

    return [
        GmailThreadSection(id=bucket_id, title=bucket_titles[bucket_id], rows=rows)
        for bucket_id, rows in bucket_rows.items()
        if rows
    ]


def bucket_for_received_at(received: datetime, reference_date) -> tuple[str, str]:
    received_date = received.date()
    age_days = (reference_date - received_date).days

    if age_days <= 0:
        return "today", "Today"
    if age_days == 1:
        return "yesterday", "Yesterday"
    if 2 <= age_days <= 7:
        return "last-seven-days", "Last seven days"
    if received_date.year == reference_date.year and received_date.month == reference_date.month:
        return "earlier-this-month", "Earlier this month"

    bucket_id = f"month-{received_date.year:04d}-{received_date.month:02d}"
    return bucket_id, received.strftime("%B %Y")


def to_thread_message(snapshot: StoredGmailMessageSnapshot) -> ThreadMessage:
    payload = snapshot.raw_payload
    return ThreadMessage(
        id=snapshot.message_id,
        source=cast(SourceType, "gmail"),
        thread_id=snapshot.thread_id,
        from_address=string_payload(payload, "from"),
        to=string_payload(payload, "to"),
        cc=string_payload(payload, "cc"),
        bcc=string_payload(payload, "bcc"),
        subject=string_payload(payload, "subject"),
        body=string_payload(payload, "body") or string_payload(payload, "body_preview") or "",
        snippet=string_payload(payload, "snippet"),
        label_ids=snapshot.label_ids,
        received_at=snapshot.internal_date or snapshot.last_fetched_at or snapshot.updated_at,
    )


def latest_subject_from_snapshots(snapshots: list[StoredGmailMessageSnapshot]) -> str | None:
    for snapshot in reversed(snapshots):
        subject = string_payload(snapshot.raw_payload, "subject")
        if subject:
            return subject
    return None


def normalize_mailbox_label(label: str) -> MailboxLabel:
    normalized = label.strip().lower()
    if normalized in MAILBOX_LABELS:
        return cast(MailboxLabel, normalized)
    return "inbox"


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def string_payload(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    compacted = compact_text(value)
    return compacted or None
