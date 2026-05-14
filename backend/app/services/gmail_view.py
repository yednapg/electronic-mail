from __future__ import annotations

"""Raw Gmail-first thread projection for the secondary Gmail view."""

from collections import OrderedDict
from datetime import datetime
from email.utils import getaddresses

from app.db.models import StoredHistorySourceRecord
from app.db.repository import DEFAULT_USER_ID, list_gmail_history_source_records
from app.schemas.domain import GmailThreadRow, GmailThreadSection, GmailThreadUpdate, GmailViewResponse
from app.services.history import (
    compact_text,
    normalize_current_state,
    normalize_outcome_type,
    string_payload,
    to_lifecycle_state,
)


VISIBLE_BUCKETS = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("last-seven-days", "Last seven days"),
    ("earlier-this-month", "Earlier this month"),
]

MAX_LIFECYCLE_UPDATES = 6
SYNTHETIC_SUMMARY_PREFIXES = (
    "this is still waiting on the other side for ",
    "this still needs attention for ",
    "this still needs a decision for ",
    "this was already completed for ",
)


def build_gmail_view_response(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    current_time: str | None = None,
) -> GmailViewResponse:
    """Build the compatibility Gmail view from the Gmail snapshot mailbox ledger."""
    from app.services.mailbox import build_gmail_view_response_from_mailbox

    return build_gmail_view_response_from_mailbox(
        database_path,
        user_id=user_id,
        current_time=current_time,
    )


def group_records_into_threads(rows: list[StoredHistorySourceRecord]) -> list[GmailThreadRow]:
    """Collapse persisted Gmail source records into one row per Gmail thread."""
    grouped: OrderedDict[str, list[StoredHistorySourceRecord]] = OrderedDict()

    for row in rows:
        thread_id = row.source_record.thread_id or row.source_record.id
        grouped.setdefault(thread_id, []).append(row)

    thread_rows = [to_thread_row(thread_id, records) for thread_id, records in grouped.items()]
    return sorted(thread_rows, key=lambda row: row.latest_received_at, reverse=True)


def to_thread_row(thread_id: str, rows: list[StoredHistorySourceRecord]) -> GmailThreadRow:
    """Build one Gmail thread row from newest-first records."""
    sorted_rows = sorted(rows, key=lambda row: row.source_record.timestamp, reverse=True)
    latest = sorted_rows[0]
    record = latest.source_record
    payload = record.raw_payload
    current_state = normalize_current_state(latest.current_state)
    summary = clean_gmail_summary(latest.source_summary or latest.suggestion_summary or string_payload(payload, "summary"), payload)
    snippet = compact_text(string_payload(payload, "snippet") or string_payload(payload, "body"))

    return GmailThreadRow(
        thread_id=thread_id,
        entity_id=latest.entity_id,
        latest_source_record_id=record.id,
        latest_received_at=record.timestamp,
        latest_subject=string_payload(payload, "subject") or record.subject,
        latest_sender=string_payload(payload, "from") or string_payload(payload, "sender") or record.sender,
        participants=collect_thread_participants(sorted_rows),
        message_count=len(sorted_rows),
        summary=summary,
        snippet=snippet,
        current_state=current_state,  # type: ignore[arg-type]
        lifecycle_state=to_lifecycle_state(current_state),  # type: ignore[arg-type]
        outcome_type=normalize_outcome_type(latest.outcome_type),  # type: ignore[arg-type]
        lifecycle_updates=build_lifecycle_updates(sorted_rows),
    )


def bucket_thread_rows(rows: list[GmailThreadRow], reference: datetime) -> list[GmailThreadSection]:
    """Place thread rows into non-overlapping Gmail date buckets."""
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
    """Return the raw Gmail bucket id/title for one received timestamp."""
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


def collect_thread_participants(rows: list[StoredHistorySourceRecord]) -> list[str]:
    """Collect a short sender/recipient list for one Gmail thread row."""
    participants: list[str] = []

    for row in rows:
        payload = row.source_record.raw_payload
        values = [
            string_payload(payload, "from") or row.source_record.sender,
            string_payload(payload, "to"),
            string_payload(payload, "cc"),
            string_payload(payload, "bcc"),
        ]
        for _name, address in getaddresses([value for value in values if value]):
            normalized = address.strip() or _name.strip()
            if normalized and normalized not in participants:
                participants.append(normalized)
            if len(participants) >= 4:
                return participants

    return participants


def build_lifecycle_updates(rows: list[StoredHistorySourceRecord]) -> list[GmailThreadUpdate]:
    """Return newest relevant raw updates in reading order for a grouped Gmail thread."""
    latest_rows = rows[:MAX_LIFECYCLE_UPDATES]
    return [to_lifecycle_update(row) for row in reversed(latest_rows)]


def to_lifecycle_update(row: StoredHistorySourceRecord) -> GmailThreadUpdate:
    """Convert one source record into compact lifecycle text for the Gmail view."""
    record = row.source_record
    payload = record.raw_payload
    summary = clean_gmail_summary(row.source_summary, payload) or compact_text(
        string_payload(payload, "snippet") or string_payload(payload, "body")
    )

    return GmailThreadUpdate(
        source_record_id=record.id,
        received_at=record.timestamp,
        subject=string_payload(payload, "subject") or record.subject,
        sender=string_payload(payload, "from") or string_payload(payload, "sender") or record.sender,
        summary=summary,
    )


def parse_datetime(value: str) -> datetime:
    """Parse persisted ISO timestamps for date bucketing."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def clean_gmail_summary(summary: str | None, payload: dict[str, object]) -> str | None:
    """Prefer real email text over old synthetic lifecycle summary copy."""
    candidate = compact_text(summary or "")
    if candidate and not is_synthetic_summary(candidate):
        return candidate

    fallback = compact_text(string_payload(payload, "snippet") or string_payload(payload, "body"))
    if fallback and not is_synthetic_summary(fallback):
        return fallback

    if candidate:
        return strip_synthetic_summary_prefix(candidate)

    return None


def is_synthetic_summary(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return any(normalized.startswith(prefix) for prefix in SYNTHETIC_SUMMARY_PREFIXES)


def strip_synthetic_summary_prefix(value: str) -> str:
    stripped = value.strip()
    lowered = stripped.lower()
    for prefix in SYNTHETIC_SUMMARY_PREFIXES:
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip().rstrip(" .")
    return stripped
