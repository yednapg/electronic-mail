from __future__ import annotations

"""Read-only grouping projection for persisted source-record history."""

from datetime import datetime

from app.db.models import StoredHistorySourceRecord
from app.db.repository import DEFAULT_USER_ID, get_history_source_record_count, list_history_source_records
from app.schemas.domain import HistoryDayGroup, HistoryMonthGroup, HistoryResponse, HistoryRow, HistoryYearGroup
from app.services.copy_quality import humanize_account_copy, infer_account_emails_from_records


STATE_ALIASES = {
    "done": "done",
    "resolved": "done",
    "completed": "done",
    "finished": "done",
    "closed": "done",
    "delivered": "done",
    "shipped": "done",
    "waiting": "waiting",
    "awaiting_reply": "waiting",
    "awaiting_rsvp": "waiting",
    "awaiting_payment": "waiting",
    "pending": "waiting",
    "pending_deadline": "waiting",
    "scheduled": "waiting",
    "received": "waiting",
    "processing": "waiting",
    "registered": "waiting",
    "acknowledged": "waiting",
    "under_review": "waiting",
    "open": "open",
}


def build_history_response(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 100,
    offset: int = 0,
) -> HistoryResponse:
    """Build a paginated year/month/day projection from local persisted records."""
    rows = list_history_source_records(database_path, user_id=user_id, limit=limit, offset=offset)
    account_emails = infer_account_emails_from_records(row.source_record for row in rows)
    return HistoryResponse(
        limit=limit,
        offset=offset,
        total=get_history_source_record_count(database_path, user_id=user_id),
        years=group_history_rows([to_history_row(row, account_emails=account_emails) for row in rows]),
    )


def group_history_rows(rows: list[HistoryRow]) -> list[HistoryYearGroup]:
    """Group already-ordered rows into year -> month -> day buckets."""
    years: list[HistoryYearGroup] = []
    year_lookup: dict[str, HistoryYearGroup] = {}
    month_lookup: dict[tuple[str, str], HistoryMonthGroup] = {}
    day_lookup: dict[tuple[str, str, str], HistoryDayGroup] = {}

    for row in rows:
        year, month, day = date_parts(row.received_at)

        year_group = year_lookup.get(year)
        if year_group is None:
            year_group = HistoryYearGroup(year=year)
            year_lookup[year] = year_group
            years.append(year_group)

        month_key = (year, month)
        month_group = month_lookup.get(month_key)
        if month_group is None:
            month_group = HistoryMonthGroup(month=month)
            month_lookup[month_key] = month_group
            year_group.months.append(month_group)

        day_key = (year, month, day)
        day_group = day_lookup.get(day_key)
        if day_group is None:
            day_group = HistoryDayGroup(date=day)
            day_lookup[day_key] = day_group
            month_group.days.append(day_group)

        day_group.rows.append(row)

    return years


def to_history_row(row: StoredHistorySourceRecord, *, account_emails: set[str] | None = None) -> HistoryRow:
    """Convert a stored joined row into the API contract."""
    record = row.source_record
    payload = record.raw_payload
    current_state = normalize_current_state(row.current_state)
    resolved_account_emails = account_emails or set()
    source_summary = humanize_account_copy(row.source_summary, record=record, account_emails=resolved_account_emails)
    suggestion_title = humanize_account_copy(row.suggestion_title, record=record, account_emails=resolved_account_emails)
    suggestion_summary = humanize_account_copy(
        row.suggestion_summary,
        record=record,
        account_emails=resolved_account_emails,
    )
    payload_title = humanize_account_copy(string_payload(payload, "title"), record=record, account_emails=resolved_account_emails)
    payload_summary = humanize_account_copy(
        string_payload(payload, "summary"),
        record=record,
        account_emails=resolved_account_emails,
    )
    return HistoryRow(
        source_record_id=record.id,
        entity_id=row.entity_id,
        source=record.source,  # type: ignore[arg-type]
        thread_id=record.thread_id,
        received_at=record.timestamp,
        subject=string_payload(payload, "subject") or record.subject,
        title=source_summary or payload_title or record.subject or suggestion_title,
        sender=string_payload(payload, "from") or string_payload(payload, "sender") or record.sender,
        snippet=compact_text(
            string_payload(payload, "snippet")
            or payload_summary
            or string_payload(payload, "notes")
            or string_payload(payload, "body")
        ),
        summary=source_summary or suggestion_summary or payload_summary,
        current_state=current_state,  # type: ignore[arg-type]
        lifecycle_state=to_lifecycle_state(current_state),  # type: ignore[arg-type]
        outcome_type=normalize_outcome_type(row.outcome_type),  # type: ignore[arg-type]
        outcome_created_at=row.outcome_created_at,
    )


def normalize_current_state(value: str | None) -> str | None:
    if value is None:
        return None
    return STATE_ALIASES.get(value.strip().lower(), "open")


def to_lifecycle_state(current_state: str | None) -> str | None:
    if current_state is None:
        return None
    if current_state == "done":
        return "resolved"
    return "active"


def normalize_outcome_type(value: str | None) -> str | None:
    if value in {"complete", "snooze", "dismiss"}:
        return value
    return None


def date_parts(timestamp: str) -> tuple[str, str, str]:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        date = parsed.date().isoformat()
    except ValueError:
        date = timestamp[:10] if len(timestamp) >= 10 else "unknown"

    if len(date) == 10 and date[4] == "-" and date[7] == "-":
        return date[:4], date[:7], date

    return "unknown", "unknown", date


def string_payload(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def compact_text(value: str | None, *, max_length: int = 280) -> str | None:
    if value is None:
        return None

    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}..."
