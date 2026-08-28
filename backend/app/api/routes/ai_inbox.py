from __future__ import annotations

"""Authenticated API for the isolated AI Inbox projection."""

from datetime import datetime, timezone
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.config import load_settings
from app.db.ai_inbox import (
    active_member_snapshot,
    apply_user_decision,
    count_generation_jobs,
    delete_ai_data,
    generation_progress,
    get_generation,
    get_matter,
    get_profile,
    latest_shadow_generation,
    list_ai_inbox,
    promote_generation,
    record_shadow_evaluation,
    start_shadow_generation,
    update_profile,
)
from app.db.mail_groups import list_messages_by_ids
from app.schemas.ai_inbox import (
    AIGroupingExplanation,
    AIInboxResponse,
    AIMatterDetailResponse,
    AIMatterReplyResponse,
    AIOrganizationProfilePatch,
    AIOrganizationProfileResponse,
    AIOrganizationProgressResponse,
    AIShadowGenerationRequest,
    AIShadowGenerationResponse,
    AIShadowEvaluationRequest,
    AIShadowEvaluationResponse,
    AIShadowPromotionRequest,
    MatterDecisionRequest,
    MatterDecisionResponse,
    MatterEntityActionRequest,
    MatterEntityActionResponse,
)
from app.schemas.domain import MailReplyRequest
from app.services.ai_inbox import (
    build_grouping_explanation,
    build_matter_detail,
    enqueue_generation_bootstrap,
    enqueue_message_organization,
    shadow_promotion_gate_failures,
)
from app.services.ai_inbox_actions import enqueue_matter_action
from app.services.auth import require_current_user
from app.services.mailbox_events import AI_INBOX_CHANGED, AI_PROFILE_CHANGED, emit_mailbox_event
from app.services.mailbox_sends import send_reply


router = APIRouter(tags=["ai-inbox"])
settings = load_settings()
logger = logging.getLogger(__name__)


def _local_shadow_generation_id(user_id: str) -> str | None:
    """Expose a shadow projection in the normal AI Inbox tab only for explicit local testing."""
    if settings.app_env != "local" or not settings.ai_inbox_local_shadow_preview:
        return None
    profile = get_profile(str(settings.database_path), user_id=user_id)
    if str(profile.get("rollout_mode") or "disabled") not in {"shadow", "preview"}:
        return None
    generation = latest_shadow_generation(str(settings.database_path), user_id=user_id)
    return str(generation["id"]) if generation is not None else None


def _profile_response(profile: dict[str, object]) -> AIOrganizationProfileResponse:
    return AIOrganizationProfileResponse(
        consented=bool(profile.get("consented_at")),
        enabled=bool(profile.get("enabled")),
        available=bool(settings.ai_inbox_enabled and settings.ai_inbox_configured),
        grouping_style=str(profile.get("grouping_style") or "focused"),
        rollout_mode=str(profile.get("rollout_mode") or "disabled"),
        active_generation_id=str(profile["active_generation_id"]) if profile.get("active_generation_id") else None,
        revision=int(profile.get("revision") or 0),
    )


def _require_ops_admin(request: Request):
    user = require_current_user(settings, request)
    admin_emails = {
        email.strip().lower()
        for email in os.getenv("OPS_ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    if not admin_emails or user.email.lower() not in admin_emails:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ops admin access required",
        )
    return user


@router.get("/v1/ai-organization/profile", response_model=AIOrganizationProfileResponse)
def ai_organization_profile(request: Request) -> AIOrganizationProfileResponse:
    user = require_current_user(settings, request)
    return _profile_response(get_profile(str(settings.database_path), user_id=user.id))


@router.patch("/v1/ai-organization/profile", response_model=AIOrganizationProfileResponse)
def patch_ai_organization_profile(
    request: Request,
    payload: AIOrganizationProfilePatch,
) -> AIOrganizationProfileResponse:
    user = require_current_user(settings, request)
    if payload.enabled and not (settings.ai_inbox_enabled and settings.ai_inbox_configured):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI Inbox is not available on this deployment")
    try:
        profile, generation_id = update_profile(
            settings,
            user_id=user.id,
            consent=payload.consent,
            enabled=payload.enabled,
            grouping_style=payload.grouping_style,
            rebuild_existing=payload.rebuild_existing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if generation_id:
        enqueue_generation_bootstrap(
            settings,
            user_id=user.id,
            generation_id=generation_id,
            chronological_all=True,
        )
    emit_mailbox_event(
        settings,
        user_id=user.id,
        event_type=AI_PROFILE_CHANGED,
        payload={"generation_id": generation_id, "enabled": bool(profile.get("enabled"))},
    )
    return _profile_response(profile)


@router.delete("/v1/ai-organization/data", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_organization_data(request: Request) -> Response:
    user = require_current_user(settings, request)
    delete_ai_data(str(settings.database_path), user_id=user.id)
    emit_mailbox_event(settings, user_id=user.id, event_type=AI_PROFILE_CHANGED, payload={"deleted": True})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/ai-organization/progress", response_model=AIOrganizationProgressResponse)
def ai_organization_progress(request: Request) -> AIOrganizationProgressResponse:
    user = require_current_user(settings, request)
    return AIOrganizationProgressResponse(**generation_progress(str(settings.database_path), user_id=user.id))


def _inbox_response(
    *,
    user_id: str,
    query: str | None,
    limit: int,
    generation_id: str | None = None,
) -> AIInboxResponse:
    projection = list_ai_inbox(
        str(settings.database_path),
        user_id=user_id,
        search_query=query,
        limit=limit,
        generation_id_override=generation_id,
    )
    return AIInboxResponse(
        profile=_profile_response(projection["profile"]),
        generation_id=projection["generation_id"],
        revision=str(projection["revision"]),
        matters=projection["matters"],
        organizing=projection["organizing"],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/v1/ai-inbox", response_model=AIInboxResponse)
def ai_inbox(request: Request, limit: int = Query(default=200, ge=1, le=500)) -> AIInboxResponse:
    user = require_current_user(settings, request)
    return _inbox_response(
        user_id=user.id,
        query=None,
        limit=limit,
        generation_id=_local_shadow_generation_id(user.id),
    )


@router.get("/v1/ai-inbox/search", response_model=AIInboxResponse)
def search_ai_inbox(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
) -> AIInboxResponse:
    user = require_current_user(settings, request)
    return _inbox_response(
        user_id=user.id,
        query=q.strip(),
        limit=limit,
        generation_id=_local_shadow_generation_id(user.id),
    )


@router.get("/v1/ai-inbox/shadow-preview", response_model=AIInboxResponse)
def shadow_ai_inbox_preview(request: Request, limit: int = Query(default=200, ge=1, le=500)) -> AIInboxResponse:
    user = _require_ops_admin(request)
    profile = get_profile(str(settings.database_path), user_id=user.id)
    if str(profile.get("rollout_mode") or "disabled") not in {"shadow", "preview"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shadow preview is not enabled")
    generation = latest_shadow_generation(str(settings.database_path), user_id=user.id)
    if generation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shadow preview is not available")
    return _inbox_response(
        user_id=user.id,
        query=None,
        limit=limit,
        generation_id=str(generation["id"]),
    )


@router.post(
    "/v1/ai-organization/shadow-generations",
    response_model=AIShadowGenerationResponse,
)
def create_shadow_generation(
    request: Request,
    payload: AIShadowGenerationRequest,
) -> AIShadowGenerationResponse:
    user = _require_ops_admin(request)
    if not (settings.ai_inbox_enabled and settings.ai_inbox_configured):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Inbox is not available on this deployment",
        )
    try:
        generation_id = start_shadow_generation(
            settings,
            user_id=user.id,
            grouping_style=payload.grouping_style,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    enqueue_generation_bootstrap(
        settings,
        user_id=user.id,
        generation_id=generation_id,
        include_history=True,
        chronological_all=True,
    )
    emit_mailbox_event(
        settings,
        user_id=user.id,
        event_type=AI_PROFILE_CHANGED,
        payload={"generation_id": generation_id, "rollout_mode": "shadow"},
    )
    return AIShadowGenerationResponse(generation_id=generation_id, status="shadow")


@router.get(
    "/v1/ai-inbox/shadow-preview/matters/{matter_id}",
    response_model=AIMatterDetailResponse,
)
def shadow_matter_detail(
    request: Request,
    matter_id: str,
    generation_id: str = Query(min_length=1, max_length=128),
) -> AIMatterDetailResponse:
    user = _require_ops_admin(request)
    detail = build_matter_detail(
        settings,
        user_id=user.id,
        matter_id=matter_id,
        generation_id=generation_id,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shadow matter not found")
    return detail


@router.post(
    "/v1/ai-organization/shadow-generations/{generation_id}/evaluation",
    response_model=AIShadowEvaluationResponse,
)
def evaluate_shadow_generation(
    request: Request,
    generation_id: str,
    payload: AIShadowEvaluationRequest,
) -> AIShadowEvaluationResponse:
    user = _require_ops_admin(request)
    try:
        metrics = record_shadow_evaluation(
            str(settings.database_path),
            user_id=user.id,
            generation_id=generation_id,
            quality_metrics=payload.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    failures = shadow_promotion_gate_failures(metrics)
    if float(metrics.get("cost_per_1000_messages") or 0) >= 25.0:
        logger.warning(
            "ai_inbox.shadow_cost_alert",
            extra={
                "event_fields": {
                    "generation_id": generation_id,
                    "cost_per_1000_messages": metrics["cost_per_1000_messages"],
                }
            },
        )
    return AIShadowEvaluationResponse(
        generation_id=generation_id,
        passes=not failures,
        failures=failures,
        metrics=metrics,
    )


@router.post(
    "/v1/ai-organization/shadow-generations/{generation_id}/promote",
    response_model=AIShadowGenerationResponse,
)
def promote_shadow_generation(
    request: Request,
    generation_id: str,
    payload: AIShadowPromotionRequest,
) -> AIShadowGenerationResponse:
    user = _require_ops_admin(request)
    if not payload.confirm_evaluation_gates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Explicit confirmation of the shadow evaluation gates is required",
        )
    generation = get_generation(
        str(settings.database_path), user_id=user.id, generation_id=generation_id
    )
    if generation is None or str(generation["status"]) != "shadow":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shadow generation not found")
    if generation.get("completed_at") is None or count_generation_jobs(
        str(settings.database_path), user_id=user.id, generation_id=generation_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Shadow generation is still processing",
        )
    metrics = generation.get("stats_json")
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    failures = shadow_promotion_gate_failures(metrics if isinstance(metrics, dict) else {})
    if failures:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "promotion_gates_failed", "failures": failures},
        )
    promote_generation(
        str(settings.database_path), user_id=user.id, generation_id=generation_id
    )
    # Reconcile arrivals since the preview and begin old history only after the
    # reviewed 30-day generation becomes active.
    enqueue_generation_bootstrap(
        settings,
        user_id=user.id,
        generation_id=generation_id,
        include_history=True,
    )
    emit_mailbox_event(
        settings,
        user_id=user.id,
        event_type=AI_PROFILE_CHANGED,
        payload={"generation_id": generation_id, "rollout_mode": "live"},
    )
    return AIShadowGenerationResponse(generation_id=generation_id, status="active")


@router.get("/v1/matters/{matter_id}", response_model=AIMatterDetailResponse)
def matter_detail(request: Request, matter_id: str) -> AIMatterDetailResponse:
    user = require_current_user(settings, request)
    detail = build_matter_detail(settings, user_id=user.id, matter_id=matter_id)
    if detail is None:
        preview_generation_id = _local_shadow_generation_id(user.id)
        if preview_generation_id:
            detail = build_matter_detail(
                settings,
                user_id=user.id,
                matter_id=matter_id,
                generation_id=preview_generation_id,
            )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found")
    return detail


@router.get(
    "/v1/matters/{matter_id}/messages/{message_id}/grouping-reason",
    response_model=AIGroupingExplanation,
)
def matter_message_grouping_reason(
    request: Request,
    matter_id: str,
    message_id: str,
) -> AIGroupingExplanation:
    user = require_current_user(settings, request)
    explanation = build_grouping_explanation(
        settings,
        user_id=user.id,
        matter_id=matter_id,
        message_id=message_id,
    )
    if explanation is None:
        preview_generation_id = _local_shadow_generation_id(user.id)
        if preview_generation_id:
            explanation = build_grouping_explanation(
                settings,
                user_id=user.id,
                matter_id=matter_id,
                message_id=message_id,
                generation_id=preview_generation_id,
            )
    if explanation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grouping explanation not found",
        )
    return explanation


@router.post("/v1/matter-decisions", response_model=MatterDecisionResponse)
def matter_decision(request: Request, payload: MatterDecisionRequest) -> MatterDecisionResponse:
    user = require_current_user(settings, request)
    preview_generation_id = _local_shadow_generation_id(user.id)
    decision_generation_id = None
    if preview_generation_id and get_matter(
        str(settings.database_path),
        user_id=user.id,
        matter_id=payload.matter_id,
        generation_id=preview_generation_id,
    ):
        decision_generation_id = preview_generation_id
    try:
        result = apply_user_decision(
            str(settings.database_path), user_id=user.id,
            client_decision_id=payload.client_decision_id, decision=payload.decision,
            matter_id=payload.matter_id, target_matter_id=payload.target_matter_id,
            message_ids=payload.message_ids, expected_revision=payload.expected_revision,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if result["state"] == "conflict":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Matter changed; refresh before applying this decision")
    if payload.decision == "separate":
        for message_id in result.get("message_ids", []):
            messages = list_messages_by_ids(str(settings.database_path), user_id=user.id, message_ids=[message_id])
            if messages:
                enqueue_message_organization(
                    settings, user_id=user.id, generation_id=decision_generation_id,
                    message_id=message_id,
                    content_revision=messages[0].content_revision, priority=110,
                )
    emit_mailbox_event(settings, user_id=user.id, event_type=AI_INBOX_CHANGED, payload={"matter_ids": result["matter_ids"], "source": "user_decision"})
    return MatterDecisionResponse(
        client_decision_id=payload.client_decision_id,
        decision_id=str(result["decision_id"]), matter_ids=result["matter_ids"],
        revision=str(result.get("revision") or ""), state="applied",
    )


@router.post("/v1/mailbox/entity-actions", response_model=MatterEntityActionResponse)
def matter_entity_action(request: Request, payload: MatterEntityActionRequest) -> MatterEntityActionResponse:
    user = require_current_user(settings, request)
    snapshot = active_member_snapshot(str(settings.database_path), user_id=user.id, matter_id=payload.matter_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found")
    revision = int(snapshot["matter"]["revision"])
    if revision != payload.expected_revision:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Matter changed; refresh before applying this action")
    thread_count = len(snapshot["thread_ids"])
    if payload.action in {"move_trash", "delete_forever"} and thread_count > 1 and not payload.confirm_multi_thread_trash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "multi_thread_confirmation_required", "affected_thread_count": thread_count, "affected_message_count": len(snapshot["message_ids"])},
        )
    enqueue_matter_action(
        settings, user_id=user.id, client_action_id=payload.client_action_id,
        matter_id=payload.matter_id, action=payload.action, message_ids=snapshot["message_ids"],
    )
    return MatterEntityActionResponse(
        client_action_id=payload.client_action_id, matter_id=payload.matter_id,
        action=payload.action, target_message_ids=snapshot["message_ids"],
        affected_thread_count=thread_count, state="queued",
    )


@router.post("/v1/matters/{matter_id}/reply", response_model=AIMatterReplyResponse)
def reply_to_matter(
    request: Request,
    matter_id: str,
    payload: MailReplyRequest,
) -> AIMatterReplyResponse:
    user = require_current_user(settings, request)
    detail = build_matter_detail(settings, user_id=user.id, matter_id=matter_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found")
    source_message_id = payload.source_message_id or detail.latest_replyable_message_id
    if source_message_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This matter has no replyable received message")
    if source_message_id not in {message.id for message in detail.messages}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected source message does not belong to this matter",
        )
    message = list_messages_by_ids(str(settings.database_path), user_id=user.id, message_ids=[source_message_id])
    if not message or not message[0].gmail_thread_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The selected message cannot be replied to")
    resolved = payload.model_copy(update={"source_message_id": source_message_id})
    response = send_reply(
        settings, user_id=user.id, mailbox_thread_id=str(message[0].gmail_thread_id), request=resolved
    )
    return AIMatterReplyResponse(matter_id=matter_id, **response.model_dump())
