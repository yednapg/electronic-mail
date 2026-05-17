from __future__ import annotations

"""Backend-owned entity completion endpoints."""

from fastapi import APIRouter, HTTPException, Request, status
from googleapiclient.errors import HttpError

from app.core.config import load_settings
from app.db.mail_groups import (
    append_entity_outcome,
    get_mail_group_detail,
    get_manual_task_by_entity_id,
    update_manual_task,
)
from app.schemas.domain import EntityOutcomeRequest, EntityOutcomeResponse
from app.services.auth import require_current_user
from app.services.integrations.google import archive_gmail_thread as archive_gmail_thread_service
from app.services.mail_groups import refresh_app_session_snapshot


router = APIRouter(tags=["entities"])
settings = load_settings()


@router.post("/v1/entities/{entity_id}/complete", response_model=EntityOutcomeResponse)
def complete_entity(http_request: Request, entity_id: str, request: EntityOutcomeRequest | None = None) -> EntityOutcomeResponse:
    user = require_current_user(settings, http_request)
    database_url = str(settings.database_path)
    manual_task = get_manual_task_by_entity_id(database_url, user_id=user.id, entity_id=entity_id)
    if manual_task is not None:
        update_manual_task(database_url, manual_task.id, user_id=user.id, status="done")
        outcome = append_entity_outcome(
            database_url,
            user_id=user.id,
            entity_id=entity_id,
            outcome_type="complete",
            note=request.note if request is not None else None,
        )
        refresh_app_session_snapshot(settings, user_id=user.id)
        return _outcome_response(outcome)

    detail = get_mail_group_detail(database_url, user_id=user.id, group_id=entity_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

    thread_ids = sorted({message.gmail_thread_id for message in detail.messages if message.gmail_thread_id})
    for thread_id in thread_ids:
        try:
            archive_gmail_thread_service(settings, thread_id, user_id=user.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except HttpError as exc:
            raise HTTPException(status_code=_http_status(exc), detail=f"Gmail archive failed: {exc}") from exc

    outcome = append_entity_outcome(
        database_url,
        user_id=user.id,
        entity_id=entity_id,
        outcome_type="complete",
        note=request.note if request is not None else None,
    )
    refresh_app_session_snapshot(settings, user_id=user.id)
    return _outcome_response(outcome)


def _outcome_response(outcome) -> EntityOutcomeResponse:
    return EntityOutcomeResponse(
        id=outcome.id,
        user_id=outcome.user_id,
        entity_id=outcome.entity_id,
        outcome_type=outcome.outcome_type,
        snooze_until=outcome.snooze_until,
        note=outcome.note,
        created_at=outcome.created_at,
    )


def _http_status(exc: HttpError) -> int:
    response_status = getattr(getattr(exc, "resp", None), "status", None)
    return response_status if isinstance(response_status, int) else status.HTTP_502_BAD_GATEWAY
