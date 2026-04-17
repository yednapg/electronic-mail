from __future__ import annotations

"""State derivation from grouped source-record history."""

import re
from datetime import datetime, timezone

from app.db.models import StoredSourceRecord
from app.db.repository import append_trace_record, upsert_entity_state


MONTH_PATTERN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)

STATE_PRIORITY = {
    "received": 1,
    "pending_deadline": 2,
    "awaiting_reply": 3,
    "processing": 4,
    "scheduled": 5,
    "shipped": 6,
    "delivered": 7,
    "resolved": 8,
}


def derive_and_store_entity_state(database_path: str, entity_id: str, records: list[StoredSourceRecord]) -> None:
    """Compute the best current state for an entity and persist it."""
    current_state, due_at = derive_state(records)
    upsert_entity_state(database_path, entity_id, current_state, due_at)
    append_trace_record(
        database_path,
        stage="state_derivation",
        user_id="local-user",
        entity_id=entity_id,
        trace_id=entity_id,
        source_record_id=records[-1].id if records else None,
        input={
            "record_ids": [record.id for record in records],
            "record_count": len(records),
        },
        output={
            "current_state": current_state,
            "due_at": due_at,
        },
    )


def derive_state(records: list[StoredSourceRecord]) -> tuple[str, str | None]:
    """Collapse a record history into one current state plus an optional due date."""
    sorted_records = sorted(records, key=lambda record: record.timestamp)
    extracted_states = [state for record in sorted_records for state in extract_record_states(record)]
    fallback_state = "scheduled" if sorted_records and sorted_records[-1].source == "calendar" else "received"
    current_state = fallback_state

    for state in extracted_states:
        if STATE_PRIORITY.get(state, 0) > STATE_PRIORITY.get(current_state, 0):
            current_state = state

    due_at = find_latest_relevant_due_at(list(reversed(sorted_records)), current_state)
    return current_state, due_at


def extract_record_states(record: StoredSourceRecord) -> list[str]:
    """Extract every lifecycle signal present in one record's text."""
    text = get_record_text(record)
    source = record.source
    states: list[str] = []

    if re.search(r"\b(delivered|delivery complete)\b", text, re.IGNORECASE):
        states.append("delivered")
    if re.search(r"\b(shipped|on the way|dispatched)\b", text, re.IGNORECASE):
        states.append("shipped")
    if re.search(r"\b(processing|being prepared|in progress)\b", text, re.IGNORECASE):
        states.append("processing")
    if re.search(r"\b(order received|received)\b", text, re.IGNORECASE):
        states.append("received")
    if re.search(
        r"\b(under review|taken up for|appropriate review|interim response|request has been raised|activation will be completed|subject to internal checks)\b",
        text,
        re.IGNORECASE,
    ):
        states.append("processing")
    if re.search(r"\b(completed|paid)\b", text, re.IGNORECASE):
        states.append("resolved")
    if re.search(
        r"\b(successfully reversed|final response has been shared|completed from our end|processed successfully)\b",
        text,
        re.IGNORECASE,
    ):
        states.append("resolved")
    if re.search(r"\b(confirmed|scheduled)\b", text, re.IGNORECASE) or source == "calendar":
        states.append("scheduled")
    if re.search(r"\b(reply|let me know)\b", text, re.IGNORECASE):
        states.append("awaiting_reply")
    if re.search(r"\b(due|expires|deadline|respond by|within \d+ working days?)\b", text, re.IGNORECASE):
        states.append("pending_deadline")

    return states


def find_latest_relevant_due_at(records: list[StoredSourceRecord], current_state: str) -> str | None:
    """Pick the freshest due date that actually matches the winning lifecycle state."""
    for record in records:
        text = get_record_text(record)

        if current_state == "pending_deadline" and not re.search(r"\b(due|expires|deadline)\b", text, re.IGNORECASE):
            continue

        if current_state == "scheduled" and not (
            re.search(r"\b(confirmed|scheduled)\b", text, re.IGNORECASE) or record.source == "calendar"
        ):
            continue

        due_at = extract_due_at(record)

        if due_at is not None:
            return due_at

    return None


def extract_due_at(record: StoredSourceRecord) -> str | None:
    """Accept only explicit structured dates or parseable month-day phrases."""
    payload = record.raw_payload
    text = get_record_text(record).lower()

    if "no deadline" in text:
        return None

    for key in ("start", "due_at", "dueAt"):
        explicit_value = payload.get(key)

        if isinstance(explicit_value, str):
            normalized = to_valid_iso(explicit_value)

            if normalized is not None:
                return normalized

    match = MONTH_PATTERN.search(text)

    if match is None:
        return None

    year = "" if "," in match.group(0) else f", {datetime.fromisoformat(record.timestamp.replace('Z', '+00:00')).year}"
    return to_valid_iso(f"{match.group(0)}{year}")


def get_record_text(record: StoredSourceRecord) -> str:
    """Build one searchable text blob from subject and body-like fields."""
    parts = [
        record.subject or "",
        payload_string(record.raw_payload, "subject") or "",
        payload_string(record.raw_payload, "body") or "",
    ]
    return " ".join(parts).strip()


def payload_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def to_valid_iso(value: str) -> str | None:
    """Normalize supported date strings into UTC ISO timestamps."""
    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(value.strip(), "%B %d, %Y")
        except ValueError:
            try:
                parsed = datetime.strptime(value.strip(), "%b %d, %Y")
            except ValueError:
                return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).isoformat()
