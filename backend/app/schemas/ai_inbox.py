from __future__ import annotations

"""Public contracts for the isolated, backend-owned AI Inbox projection."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.domain import GmailThreadAction, MailReplyRequest, MailSendResponse, ThreadMessage


MatterStatus = Literal[
    "needs_you",
    "waiting_on_others",
    "in_progress",
    "completed",
    "partially_completed",
    "failed",
    "cancelled",
    "informational",
]
MatterConfidenceState = Literal["automatic", "provisional", "confirmed"]
GroupingStyle = Literal["focused", "broader"]
RolloutMode = Literal["disabled", "shadow", "preview", "live"]


class AIOrganizationProfileResponse(BaseModel):
    consented: bool
    enabled: bool
    available: bool
    grouping_style: GroupingStyle = "focused"
    rollout_mode: RolloutMode = "disabled"
    active_generation_id: str | None = None
    revision: int = 0


class AIOrganizationProfilePatch(BaseModel):
    consent: bool | None = None
    enabled: bool | None = None
    grouping_style: GroupingStyle | None = None
    rebuild_existing: bool = False


class AIShadowGenerationRequest(BaseModel):
    grouping_style: GroupingStyle = "focused"


class AIShadowGenerationResponse(BaseModel):
    generation_id: str
    status: Literal["shadow", "active"]


class AIShadowPromotionRequest(BaseModel):
    confirm_evaluation_gates: bool = False


class AIShadowEvaluationRequest(BaseModel):
    reviewed_messages: int = Field(ge=1)
    explicit_reference_candidate_recall: float = Field(ge=0, le=1)
    automatic_merge_precision: float = Field(ge=0, le=1)
    false_automatic_merges: int = Field(ge=0)
    unsupported_outcome_headlines: int = Field(ge=0)
    confirmed_membership_moves: int = Field(ge=0)
    unsolicited_gmail_mutations: int = Field(ge=0)
    normal_inbox_regressions: int = Field(ge=0)


class AIShadowEvaluationResponse(BaseModel):
    generation_id: str
    passes: bool
    failures: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)


class AIMatterRow(BaseModel):
    id: str
    kind: Literal["matter"] = "matter"
    title: str
    summary: str
    status: MatterStatus
    confidence_state: MatterConfidenceState
    confidence: float
    latest_message_at: str
    message_count: int
    unread: bool = False
    starred: bool = False
    participants: list[str] = Field(default_factory=list)
    counterpart_entities: list[str] = Field(default_factory=list, max_length=4)
    evidence_message_ids: list[str] = Field(default_factory=list)
    revision: int
    open_subgoal_count: int = 0
    review_count: int = 0
    matching_message_ids: list[str] = Field(default_factory=list)


class AIOrganizingRow(BaseModel):
    id: str
    kind: Literal["organizing"] = "organizing"
    gmail_thread_id: str
    title: str
    sender: str | None = None
    counterpart_entities: list[str] = Field(default_factory=list, max_length=4)
    snippet: str | None = None
    latest_message_at: str
    message_count: int = 1
    state: Literal["organizing", "failed"] = "organizing"
    error: str | None = None
    matching_message_ids: list[str] = Field(default_factory=list)


class AIInboxResponse(BaseModel):
    profile: AIOrganizationProfileResponse
    generation_id: str | None = None
    revision: str
    stale: bool = False
    stale_reason: str | None = None
    matters: list[AIMatterRow] = Field(default_factory=list)
    organizing: list[AIOrganizingRow] = Field(default_factory=list)
    generated_at: str


class AIGroupingExplanation(BaseModel):
    message_id: str
    event_role: str
    verdict: Literal[
        "strong_continuation",
        "possible_continuation",
        "different_matter",
    ]
    explanation: str
    supporting_factors: list[str] = Field(default_factory=list)
    conflicting_factors: list[str] = Field(default_factory=list)
    evidence_message_ids: list[str] = Field(default_factory=list)


class AIReviewProposal(AIGroupingExplanation):
    id: str
    subject: str
    sender: str | None = None
    occurred_at: str
    recommended_action: Literal["add_to_matter", "keep_separate"]


class AIMatterDetailResponse(BaseModel):
    id: str
    title: str
    stable_goal: str
    summary: str
    status: MatterStatus
    confidence_state: MatterConfidenceState
    confidence: float
    evidence_message_ids: list[str] = Field(default_factory=list)
    revision: int
    latest_replyable_message_id: str | None = None
    total_messages: int
    matter_message_ids: list[str] = Field(default_factory=list)
    review_proposals: list[AIReviewProposal] = Field(default_factory=list)
    messages: list[ThreadMessage] = Field(default_factory=list)


class MatterDecisionRequest(BaseModel):
    client_decision_id: str = Field(min_length=1, max_length=128)
    decision: Literal["confirm", "separate", "merge", "move"]
    matter_id: str
    target_matter_id: str | None = None
    message_ids: list[str] = Field(default_factory=list, max_length=500)
    expected_revision: int


class MatterDecisionResponse(BaseModel):
    client_decision_id: str
    decision_id: str
    matter_ids: list[str]
    revision: str
    state: Literal["applied", "conflict"]


class MatterEntityActionRequest(BaseModel):
    client_action_id: str = Field(min_length=1, max_length=128)
    matter_id: str
    action: GmailThreadAction
    expected_revision: int
    confirm_multi_thread_trash: bool = False


class MatterEntityActionResponse(BaseModel):
    client_action_id: str
    matter_id: str
    action: GmailThreadAction
    target_message_ids: list[str]
    affected_thread_count: int
    state: Literal["queued", "applied"]


class AIOrganizationProgressResponse(BaseModel):
    generation_id: str | None = None
    phase: Literal["disabled", "organizing_recent", "organizing_history", "ready", "failed"]
    recent_total: int = 0
    recent_processed: int = 0
    history_total: int = 0
    history_processed: int = 0
    last_error: str | None = None


class AIMatterReplyResponse(MailSendResponse):
    matter_id: str


__all__ = [
    "AIInboxResponse",
    "AIMatterDetailResponse",
    "AIGroupingExplanation",
    "AIReviewProposal",
    "AIMatterReplyResponse",
    "AIOrganizationProfilePatch",
    "AIOrganizationProfileResponse",
    "AIOrganizationProgressResponse",
    "AIShadowGenerationRequest",
    "AIShadowGenerationResponse",
    "AIShadowPromotionRequest",
    "AIShadowEvaluationRequest",
    "AIShadowEvaluationResponse",
    "MatterDecisionRequest",
    "MatterDecisionResponse",
    "MatterEntityActionRequest",
    "MatterEntityActionResponse",
    "MailReplyRequest",
]
