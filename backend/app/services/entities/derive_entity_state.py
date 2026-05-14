from __future__ import annotations

"""State derivation from grouped source-record history."""

import re
from datetime import datetime, timezone

from app.db.models import StoredSourceRecord
from app.db.repository import DEFAULT_USER_ID, append_trace_record, upsert_entity_state
from app.services.ai.decision import classify_entity_state


MONTH_PATTERN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)


def derive_and_store_entity_state(database_path: str, entity_id: str, records: list[StoredSourceRecord]) -> None:
    """Compute the best current state for an entity and persist it."""
    current_state, due_at = derive_state(records)
    upsert_entity_state(database_path, entity_id, current_state, due_at)
    append_trace_record(
        database_path,
        stage="state_derivation",
        user_id=DEFAULT_USER_ID,
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
    current_state = classify_entity_state(sorted_records)
    due_at = find_latest_relevant_due_at(list(reversed(sorted_records)), current_state)
    return current_state, due_at


def find_latest_relevant_due_at(records: list[StoredSourceRecord], current_state: str) -> str | None:
    """Pick the freshest due date that actually matches the winning lifecycle state."""
    if current_state == "done":
        return None

    for record in records:
        text = get_record_text(record)

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
