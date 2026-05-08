from __future__ import annotations

"""Backend-owned entity state and thread-reader endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.core.config import load_settings
from app.db.repository import (
    DEFAULT_USER_ID,
    append_entity_outcome,
    get_loaded_entity,
    get_manual_task_by_entity_id,
    list_source_records_for_entity,
    update_manual_task,
    upsert_entity_state,
)
from app.schemas.domain import (
    EntityOutcomeRequest,
    EntityOutcomeResponse,
    EntitySnoozeRequest,
    ThreadMessage,
    ThreadReaderResponse,
)


router = APIRouter(tags=["entities"])
settings = load_settings()


@router.post("/v1/entities/{entity_id}/complete", response_model=EntityOutcomeResponse)
def complete_entity(entity_id: str, request: EntityOutcomeRequest | None = None) -> EntityOutcomeResponse:
    """Mark an entity complete in backend app state only."""
    ensure_entity(entity_id)
    upsert_entity_state(str(settings.database_path), entity_id, "done", None)
    manual_task = get_manual_task_by_entity_id(str(settings.database_path), entity_id, user_id=DEFAULT_USER_ID)
    if manual_task is not None:
        update_manual_task(str(settings.database_path), manual_task.id, user_id=DEFAULT_USER_ID, status="done")
    outcome = append_entity_outcome(
        str(settings.database_path),
        user_id=DEFAULT_USER_ID,
        entity_id=entity_id,
        outcome_type="complete",
        note=request.note if request is not None else None,
    )
    return to_outcome_response(outcome)


@router.post("/v1/entities/{entity_id}/snooze", response_model=EntityOutcomeResponse)
def snooze_entity(entity_id: str, request: EntitySnoozeRequest) -> EntityOutcomeResponse:
    """Snooze an entity in backend app state only."""
    ensure_entity(entity_id)
    outcome = append_entity_outcome(
        str(settings.database_path),
        user_id=DEFAULT_USER_ID,
        entity_id=entity_id,
        outcome_type="snooze",
        snooze_until=request.snooze_until,
        note=request.note,
    )
    return to_outcome_response(outcome)


@router.post("/v1/entities/{entity_id}/dismiss", response_model=EntityOutcomeResponse)
def dismiss_entity(entity_id: str, request: EntityOutcomeRequest | None = None) -> EntityOutcomeResponse:
    """Dismiss an entity in backend app state only."""
    ensure_entity(entity_id)
    outcome = append_entity_outcome(
        str(settings.database_path),
        user_id=DEFAULT_USER_ID,
        entity_id=entity_id,
        outcome_type="dismiss",
        note=request.note if request is not None else None,
    )
    return to_outcome_response(outcome)


@router.get("/v1/entities/{entity_id}/thread", response_model=ThreadReaderResponse)
def entity_thread(entity_id: str) -> ThreadReaderResponse:
    """Return the email-underneath/manual-task thread reader payload."""
    loaded = ensure_entity(entity_id)
    records = list_source_records_for_entity(str(settings.database_path), entity_id)
    source = records[-1].source if records else None
    gmail_thread_ids = sorted({record.thread_id for record in records if record.source == "gmail" and record.thread_id})
    subject = records[-1].subject if records else None
    return ThreadReaderResponse(
        entity_id=entity_id,
        user_id=DEFAULT_USER_ID,
        source=source,
        gmail_thread_id=gmail_thread_ids[0] if len(gmail_thread_ids) == 1 else None,
        subject=subject or loaded.entity.canonical_key,
        messages=[to_thread_message(record) for record in records],
    )


def ensure_entity(entity_id: str):
    loaded = get_loaded_entity(str(settings.database_path), entity_id)
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    return loaded


def to_outcome_response(outcome) -> EntityOutcomeResponse:
    return EntityOutcomeResponse(
        id=outcome.id,
        user_id=outcome.user_id,
        entity_id=outcome.entity_id,
        outcome_type=outcome.outcome_type,
        snooze_until=outcome.snooze_until,
        note=outcome.note,
        created_at=outcome.created_at,
    )


def to_thread_message(record) -> ThreadMessage:
    payload = record.raw_payload
    return ThreadMessage(
        id=record.id,
        source=record.source,
        thread_id=record.thread_id,
        from_address=string_payload(payload, "from") or record.sender,
        to=string_payload(payload, "to"),
        cc=string_payload(payload, "cc"),
        bcc=string_payload(payload, "bcc"),
        subject=string_payload(payload, "subject") or record.subject,
        body=string_payload(payload, "body") or "",
        snippet=string_payload(payload, "snippet"),
        label_ids=[str(label) for label in payload.get("label_ids", [])] if isinstance(payload.get("label_ids"), list) else [],
        received_at=record.timestamp,
    )


def string_payload(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
