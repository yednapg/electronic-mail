from __future__ import annotations

"""Read-only history endpoint backed by persisted source records."""

from fastapi import APIRouter, Query

from app.core.config import load_settings
from app.db.repository import DEFAULT_USER_ID
from app.schemas.domain import HistoryResponse
from app.services.history import build_history_response


router = APIRouter(tags=["history"])
settings = load_settings()


@router.get("/v1/history", response_model=HistoryResponse)
def history(
    limit: int = Query(default=100, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> HistoryResponse:
    """Return persisted imported records grouped chronologically for the History view."""
    return build_history_response(
        str(settings.database_path),
        user_id=DEFAULT_USER_ID,
        limit=limit,
        offset=offset,
    )
