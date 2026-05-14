from __future__ import annotations

"""Trace replay endpoint for operator/debug inspection."""

from fastapi import APIRouter, HTTPException, Request

from app.core.config import load_settings
from app.db.repository import get_entity, get_loaded_entity, list_trace_records_for_entity
from app.schemas.domain import TraceRecord, TraceReplayResponse
from app.services.auth import require_current_user


router = APIRouter()
settings = load_settings()


@router.get("/trace/{entity_id}", response_model=TraceReplayResponse)
def trace_replay(request: Request, entity_id: str) -> TraceReplayResponse:
    """Replay all persisted trace rows for one entity and its member source records."""
    user = require_current_user(settings, request)
    entity = get_entity(str(settings.database_path), entity_id, user_id=user.id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    loaded = get_loaded_entity(str(settings.database_path), entity_id, user_id=user.id)

    if loaded is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    records = list_trace_records_for_entity(str(settings.database_path), entity_id, user_id=user.id)

    return TraceReplayResponse(
        entity_id=entity_id,
        source_record_ids=[record.id for record in loaded.members],
        items=[
            TraceRecord.model_validate(
                {
                    "id": record.id,
                    "trace_id": record.trace_id,
                    "entity_id": record.entity_id,
                    "source_record_id": record.source_record_id,
                    "user_id": record.user_id,
                    "stage": record.stage,
                    "input": record.input,
                    "output": record.output,
                    "created_at": record.created_at,
                }
            )
            for record in records
        ],
    )
