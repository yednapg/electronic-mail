from __future__ import annotations

"""Strictly account-scoped aliases for the AI Inbox API."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from app.api.routes import ai_inbox as legacy
from app.core.config import load_settings
from app.db.account_scope import gmail_account_scope
from app.db.repository import get_engine, get_gmail_account, get_user, gmail_account_import_ready
from sqlalchemy import text
from app.schemas.ai_inbox import (
    AIGroupingExplanation, AIInboxResponse, AIMatterDetailResponse, AIMatterReplyResponse,
    AIOrganizationProfilePatch, AIOrganizationProfileResponse,
    AIOrganizationProgressResponse, MatterDecisionRequest,
    MatterDecisionResponse, MatterEntityActionRequest, MatterEntityActionResponse,
    MatterStatus,
    AITodoItem, AITodoResponse, AITodoUpdateRequest,
)
from app.schemas.domain import MailReplyRequest
from app.services.auth import require_current_user


router = APIRouter(tags=["gmail-account-ai"])
settings = load_settings()


def _scope(request: Request, gmail_account_id: str):
    user = require_current_user(settings, request)
    account = get_gmail_account(
        str(settings.database_path), user_id=user.id,
        gmail_account_id=gmail_account_id,
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Gmail account not found")
    if account.state != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI Inbox is available after this Gmail account finishes importing",
        )
    if not gmail_account_import_ready(
        str(settings.database_path),
        user_id=user.id,
        gmail_account_id=account.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI Inbox is available after this Gmail account finishes importing",
        )
    stored_user = get_user(str(settings.database_path), user.id)
    if stored_user is not None and account.id != stored_user.primary_gmail_account_id:
        if not settings.multi_gmail_ai_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secondary Gmail AI is not enabled for this rollout yet",
            )
        with get_engine(str(settings.database_path)).connect() as connection:
            bypasses_rls = connection.execute(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )).scalar_one()
        if bool(bypasses_rls):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secondary AI Inbox requires a non-superuser database runtime role so row isolation cannot be bypassed",
            )
    return gmail_account_scope(account.id)


@router.get("/v1/gmail-accounts/{gmail_account_id}/ai-organization/profile",
            response_model=AIOrganizationProfileResponse)
def profile(request: Request, gmail_account_id: str) -> AIOrganizationProfileResponse:
    with _scope(request, gmail_account_id):
        result = legacy.ai_organization_profile(request)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.patch("/v1/gmail-accounts/{gmail_account_id}/ai-organization/profile",
              response_model=AIOrganizationProfileResponse)
def patch_profile(request: Request, gmail_account_id: str,
                  payload: AIOrganizationProfilePatch) -> AIOrganizationProfileResponse:
    with _scope(request, gmail_account_id):
        result = legacy.patch_ai_organization_profile(request, payload)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.delete("/v1/gmail-accounts/{gmail_account_id}/ai-organization/data",
               status_code=204)
def delete_data(request: Request, gmail_account_id: str) -> Response:
    with _scope(request, gmail_account_id):
        return legacy.delete_ai_organization_data(request)


@router.get("/v1/gmail-accounts/{gmail_account_id}/ai-organization/progress",
            response_model=AIOrganizationProgressResponse)
def progress(request: Request, gmail_account_id: str) -> AIOrganizationProgressResponse:
    with _scope(request, gmail_account_id):
        result = legacy.ai_organization_progress(request)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.get("/v1/gmail-accounts/{gmail_account_id}/ai-inbox",
            response_model=AIInboxResponse)
def inbox(request: Request, gmail_account_id: str,
          limit: int = Query(default=200, ge=1, le=500),
          matter_status: MatterStatus | None = Query(default=None, alias="status")) -> AIInboxResponse:
    with _scope(request, gmail_account_id):
        result = legacy.ai_inbox(request, limit, matter_status)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.get("/v1/gmail-accounts/{gmail_account_id}/ai-todos",
            response_model=AITodoResponse)
def todos(request: Request, gmail_account_id: str,
          limit: int = Query(default=100, ge=1, le=200)) -> AITodoResponse:
    with _scope(request, gmail_account_id):
        result = legacy.ai_todos(request, limit)
    items = [item.model_copy(update={"gmail_account_id": gmail_account_id}) for item in result.items]
    return result.model_copy(update={"gmail_account_id": gmail_account_id, "items": items})


@router.patch("/v1/gmail-accounts/{gmail_account_id}/ai-todos/{todo_id}",
              response_model=AITodoItem)
def patch_todo(request: Request, gmail_account_id: str, todo_id: str,
               payload: AITodoUpdateRequest) -> AITodoItem:
    with _scope(request, gmail_account_id):
        result = legacy.patch_ai_todo(request, todo_id, payload)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.get("/v1/gmail-accounts/{gmail_account_id}/ai-inbox/search",
            response_model=AIInboxResponse)
def search(request: Request, gmail_account_id: str,
           q: str = Query(min_length=1, max_length=200),
           limit: int = Query(default=200, ge=1, le=500)) -> AIInboxResponse:
    with _scope(request, gmail_account_id):
        result = legacy.search_ai_inbox(request, q, limit)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.get("/v1/gmail-accounts/{gmail_account_id}/matters/{matter_id}",
            response_model=AIMatterDetailResponse)
def matter(request: Request, gmail_account_id: str,
           matter_id: str) -> AIMatterDetailResponse:
    with _scope(request, gmail_account_id):
        result = legacy.matter_detail(request, matter_id)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.get(
    "/v1/gmail-accounts/{gmail_account_id}/matters/{matter_id}/messages/{message_id}/grouping-reason",
    response_model=AIGroupingExplanation,
)
def grouping_reason(request: Request, gmail_account_id: str,
                    matter_id: str, message_id: str) -> AIGroupingExplanation:
    with _scope(request, gmail_account_id):
        return legacy.matter_message_grouping_reason(request, matter_id, message_id)


@router.post("/v1/gmail-accounts/{gmail_account_id}/matter-decisions",
             response_model=MatterDecisionResponse)
def decision(request: Request, gmail_account_id: str,
             payload: MatterDecisionRequest) -> MatterDecisionResponse:
    with _scope(request, gmail_account_id):
        result = legacy.matter_decision(request, payload)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.post("/v1/gmail-accounts/{gmail_account_id}/mailbox/entity-actions",
             response_model=MatterEntityActionResponse)
def entity_action(request: Request, gmail_account_id: str,
                  payload: MatterEntityActionRequest) -> MatterEntityActionResponse:
    with _scope(request, gmail_account_id):
        result = legacy.matter_entity_action(request, payload)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})


@router.post("/v1/gmail-accounts/{gmail_account_id}/matters/{matter_id}/reply",
             response_model=AIMatterReplyResponse)
def matter_reply(request: Request, gmail_account_id: str, matter_id: str,
                 payload: MailReplyRequest) -> AIMatterReplyResponse:
    with _scope(request, gmail_account_id):
        result = legacy.reply_to_matter(request, matter_id, payload)
    return result.model_copy(update={"gmail_account_id": gmail_account_id})
