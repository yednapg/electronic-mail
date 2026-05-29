from __future__ import annotations

"""Durable backend-to-client mailbox events and SSE formatting."""

import json
from typing import Any

from app.core.config import Settings
from app.db.mail_groups import MailboxEventRecord, insert_mailbox_event, list_mailbox_events_after


MAILBOX_CHANGED = "mailbox-changed"
DASHBOARD_CHANGED = "dashboard-changed"
SYNC_STATE = "sync-state"
HEARTBEAT = "heartbeat"


def emit_mailbox_event(
    settings: Settings,
    *,
    user_id: str,
    event_type: str,
    mailbox_label: str | None = None,
    payload: dict[str, Any] | None = None,
) -> MailboxEventRecord:
    return insert_mailbox_event(
        str(settings.database_path),
        user_id=user_id,
        event_type=event_type,
        mailbox_label=mailbox_label,
        payload=payload or {},
    )


def list_events_after(settings: Settings, *, user_id: str, after_id: int | None, limit: int = 100) -> list[MailboxEventRecord]:
    return list_mailbox_events_after(str(settings.database_path), user_id=user_id, after_id=after_id, limit=limit)


def parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def format_sse_event(event: str, data: dict[str, Any], *, event_id: int | str | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def event_payload(record: MailboxEventRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "event_type": record.event_type,
        "mailbox_label": record.mailbox_label,
        "created_at": record.created_at,
        "payload": record.payload,
    }
