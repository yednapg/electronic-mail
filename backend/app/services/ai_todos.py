from __future__ import annotations

"""Strict task eligibility and human-readable projection for AI Inbox matters."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.db.ai_inbox import get_matter, get_matter_context
from app.db.ai_todos import pending_todo_matter_ids, write_todo_projection
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    list_messages_by_ids,
    list_messages_for_gmail_thread,
)
from app.services.ai_inbox import (
    _enforce_budget,
    _model_text,
    _provider_client,
    _structured_response,
)
from app.services.mailbox_events import AI_INBOX_CHANGED, emit_mailbox_event


TODO_PROMPT_VERSION = "todo-eligibility-v3"
AUTO_TODO_CONFIDENCE = 0.85
WORTH_KNOWING_CONFIDENCE = 0.75
UNDATED_TODO_MAX_AGE = timedelta(days=14)
WORTH_KNOWING_MAX_AGE = timedelta(days=14)

TodoDisplayKind = Literal["todo", "worth_knowing", "hidden"]
TodoActionType = Literal[
    "reply", "upload", "pay", "confirm", "review", "track", "register",
    "schedule", "send", "sign", "submit", "call", "attend", "renew",
    "cancel", "open", "other", "none",
]
TodoRequirement = Literal[
    "required", "committed", "necessary", "optional", "informational", "waiting"
]
TodoUrgency = Literal["now", "today", "upcoming", "unscheduled"]


class TodoEligibilityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_subgoal_id: str | None = None
    display_kind: TodoDisplayKind
    title: str | None = Field(default=None, max_length=100)
    detail: str | None = Field(default=None, max_length=300)
    owner: Literal["user", "other", "unknown"]
    action_type: TodoActionType
    requirement: TodoRequirement
    due_at: str | None = None
    due_date_source: Literal["explicit", "none"]
    urgency: TodoUrgency
    confidence: float = Field(ge=0, le=1)
    evidence_message_ids: list[str] = Field(default_factory=list, max_length=12)
    evidence_text: str | None = Field(default=None, max_length=500)
    exclusion_reason: str | None = Field(default=None, max_length=500)


class TodoEligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[TodoEligibilityDecision] = Field(default_factory=list, max_length=12)


TODO_ELIGIBILITY_INSTRUCTIONS = (
    "Determine whether each supplied source action contains a concrete unresolved action owned by the mailbox user. "
    "Your job is not to summarize the matter. Return exactly one decision for every supplied source_key and use only "
    "supplied source keys, subgoal IDs, and message IDs. Classify as todo only when the mailbox user clearly owns the "
    "next action, the action is specific enough to express as a verb and an object, it is unresolved, direct evidence "
    "exists, and it is a requirement, prior commitment, or necessary next step. Optional opportunities, marketing, "
    "newsletters, webinars, surveys, optional registrations, documents merely being available, matters waiting on "
    "someone else, completed or superseded work, and vague suggestions must not become todos. A later sent message that "
    "answers or performs the request means it is resolved. A passed deadline means the action is expired, not overdue. "
    "Old undated requests are stale and must be hidden rather than carried forward forever. Classify only materially "
    "useful non-actionable status changes as worth_knowing. Optional opportunities, surveys, giveaways, promotions, and "
    "generic invitations are low-value and must be hidden, not worth_knowing. Classify all other irrelevant items as hidden. "
    "A todo title must begin with a clear action verb, describe one action, use natural everyday English, contain no more "
    "than ten words, and avoid bureaucratic phrases such as remains pending, needs confirmation, has been made available, "
    "or requires action. A worth-knowing title must be one short factual sentence fragment, not an instruction. Detail "
    "must be at most one plain-English sentence. The payload includes the current time. Use a due date whenever an exact "
    "deadline is explicit in the supplied evidence; never infer or invent a date. Use urgency now only for imminent, "
    "security-critical, travel-critical, or blocking actions; today only for explicit today deadlines. Otherwise use "
    "upcoming when an explicit future due date exists and "
    "unscheduled when it does not. Evidence must quote or closely paraphrase the direct support and cite its message IDs."
)


def enqueue_todo_extraction(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    matter_id: str,
    priority: int = 45,
) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="ai_todo_extract",
        queue="ai",
        user_id=user_id,
        dedupe_key=f"ai-todo:{user_id}:{generation_id}:{matter_id}",
        priority=priority,
        max_attempts=4,
        payload={
            "user_id": user_id,
            "generation_id": generation_id,
            "matter_id": matter_id,
        },
    )
    return job.id


def ensure_todo_extractions(settings: Settings, *, user_id: str, limit: int = 24) -> int:
    pending = pending_todo_matter_ids(
        str(settings.database_path),
        user_id=user_id,
        limit=limit,
    )
    for row in pending:
        enqueue_todo_extraction(
            settings,
            user_id=user_id,
            generation_id=str(row["generation_id"]),
            matter_id=str(row["id"]),
            priority=50,
        )
    return len(pending)


def extract_todos_for_matter(
    settings: Settings,
    *,
    user_id: str,
    generation_id: str,
    matter_id: str,
) -> None:
    result = get_matter(
        str(settings.database_path),
        user_id=user_id,
        generation_id=generation_id,
        matter_id=matter_id,
    )
    if result is None:
        return
    matter, matter_message_ids = result
    matter_revision = int(matter["revision"])
    context = get_matter_context(
        str(settings.database_path),
        user_id=user_id,
        generation_id=generation_id,
        matter_id=matter_id,
        include_events=False,
        include_review_proposals=False,
    )
    active_subgoals = [
        subgoal for subgoal in context["subgoals"]
        if str(subgoal["status"]) == "needs_you"
    ]
    source_actions = [
        {
            "source_key": f"subgoal:{subgoal['id']}",
            "source_subgoal_id": str(subgoal["id"]),
            "goal": str(subgoal["goal"]),
            "latest_development": str(subgoal["latest_development"]),
            "evidence_message_ids": _string_list(subgoal.get("evidence_message_ids"))[:12],
        }
        for subgoal in active_subgoals
    ]
    if not source_actions and str(matter["status"]) == "needs_you":
        source_actions = [{
            "source_key": "matter",
            "source_subgoal_id": None,
            "goal": str(matter["stable_goal"]),
            "latest_development": str(matter["summary"]),
            "evidence_message_ids": _string_list(matter.get("evidence_message_ids"))[:12],
        }]

    if str(matter["status"]) != "needs_you" or not source_actions:
        write_todo_projection(
            str(settings.database_path),
            user_id=user_id,
            generation_id=generation_id,
            matter_id=matter_id,
            matter_revision=matter_revision,
            decisions=[],
            model=settings.ai_inbox_classifier_model,
            prompt_version=TODO_PROMPT_VERSION,
        )
        return

    evidence_ids = list(dict.fromkeys(
        message_id
        for action in source_actions
        for message_id in action["evidence_message_ids"]
    ))
    if not evidence_ids:
        evidence_ids = matter_message_ids[-4:]
    seed_messages = list_messages_by_ids(
        str(settings.database_path),
        user_id=user_id,
        message_ids=list(dict.fromkeys([*evidence_ids, *matter_message_ids]))[:100],
    )
    messages = _thread_context_messages(
        settings,
        user_id=user_id,
        seed_messages=seed_messages,
    )
    now = datetime.now(timezone.utc)
    evidence_id_set = set(evidence_ids)
    selected_messages = [
        message for message in messages
        if message.message_id in evidence_id_set
    ]
    selected_ids = {message.message_id for message in selected_messages}
    for message in reversed(messages):
        if message.message_id not in selected_ids:
            selected_messages.append(message)
            selected_ids.add(message.message_id)
        if len(selected_messages) >= 24:
            break
    selected_messages.sort(key=_message_datetime)
    message_documents = []
    for message in selected_messages:
        cleaned, _references = _model_text(settings, message)
        message_documents.append({
            "message_id": message.message_id,
            "date": message.internal_date or message.updated_at,
            "direction": "sent" if "SENT" in message.label_ids else "received",
            "subject": message.subject,
            "content": cleaned[:2400],
        })

    _enforce_budget(settings, user_id=user_id)
    client = _provider_client(settings)
    extraction = _structured_response(
        settings,
        client=client,
        user_id=user_id,
        generation_id=generation_id,
        message_id=None,
        operation="todo_eligibility",
        model=settings.ai_inbox_classifier_model,
        reasoning_effort="low",
        instructions=TODO_ELIGIBILITY_INSTRUCTIONS,
        payload={
            "current_time": now.isoformat(),
            "matter": {
                "id": matter_id,
                "goal": str(matter["stable_goal"]),
                "title": str(matter["dynamic_title"]),
                "summary": str(matter["summary"]),
                "status": str(matter["status"]),
            },
            "source_actions": source_actions,
            "messages": message_documents,
        },
        output_type=TodoEligibilityResult,
        max_output_tokens=1800,
    )
    decisions = _validated_decisions(
        extraction,
        source_actions=source_actions,
        allowed_message_ids={message["message_id"] for message in message_documents},
        messages=messages,
        now=now,
    )
    write_todo_projection(
        str(settings.database_path),
        user_id=user_id,
        generation_id=generation_id,
        matter_id=matter_id,
        matter_revision=matter_revision,
        decisions=decisions,
        model=settings.ai_inbox_classifier_model,
        prompt_version=TODO_PROMPT_VERSION,
    )
    emit_mailbox_event(
        settings,
        user_id=user_id,
        event_type=AI_INBOX_CHANGED,
        payload={"source": "ai_todos_changed", "matter_id": matter_id},
    )


def _validated_decisions(
    result: TodoEligibilityResult,
    *,
    source_actions: list[dict[str, Any]],
    allowed_message_ids: set[str],
    messages: list[GmailMessageRecord] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = now or datetime.now(timezone.utc)
    message_by_id = {message.message_id: message for message in messages or []}
    sources = {str(action["source_key"]): action for action in source_actions}
    by_key = {
        decision.source_key: decision
        for decision in result.decisions
        if decision.source_key in sources
    }
    validated: list[dict[str, Any]] = []
    for source_key, source in sources.items():
        decision = by_key.get(source_key)
        if decision is None:
            validated.append(_hidden_decision(source, "The classifier returned no decision."))
            continue
        evidence_ids = [
            value for value in dict.fromkeys(decision.evidence_message_ids)
            if value in allowed_message_ids
        ]
        display_kind: TodoDisplayKind = decision.display_kind
        exclusion_reason = decision.exclusion_reason
        title = _clean_title(decision.title)
        detail = _clean_sentence(decision.detail, 300)
        evidence_text = _clean_sentence(decision.evidence_text, 500)
        due_datetime = _explicit_due_datetime(decision.due_at, decision.due_date_source)
        due_at = due_datetime.isoformat() if due_datetime else None
        activity = _lifecycle_activity(evidence_ids, message_by_id)

        if display_kind == "todo":
            valid_todo = (
                decision.owner == "user"
                and decision.requirement in {"required", "committed", "necessary"}
                and decision.action_type not in {"none"}
                and decision.confidence >= AUTO_TODO_CONFIDENCE
                and bool(evidence_ids)
                and bool(title)
                and _looks_like_action_title(title, decision.action_type)
            )
            if not valid_todo:
                display_kind = "hidden"
                exclusion_reason = "The candidate did not pass the strict automatic to-do gates."
            elif activity.sent_after_evidence:
                display_kind = "hidden"
                exclusion_reason = "A later sent message indicates that this request was answered."
            elif due_datetime is not None and due_datetime < current_time:
                display_kind = "hidden"
                exclusion_reason = "The explicit deadline has passed."
            elif due_datetime is None and _is_stale(
                activity.latest_evidence_at,
                now=current_time,
                maximum_age=UNDATED_TODO_MAX_AGE,
            ):
                display_kind = "hidden"
                exclusion_reason = "The undated request is too old to remain an active to-do."
        elif display_kind == "worth_knowing":
            if decision.requirement not in {"informational", "waiting"}:
                display_kind = "hidden"
                exclusion_reason = "Optional opportunities are not important status updates."
            elif decision.confidence < WORTH_KNOWING_CONFIDENCE or not title or not evidence_ids:
                display_kind = "hidden"
                exclusion_reason = "The update was not confident or well evidenced enough to show."
            elif _is_stale(
                activity.latest_evidence_at,
                now=current_time,
                maximum_age=WORTH_KNOWING_MAX_AGE,
            ):
                display_kind = "hidden"
                exclusion_reason = "The update is no longer timely enough to show."

        urgency = _current_urgency(
            due_at=due_datetime,
            latest_evidence_at=activity.latest_evidence_at,
            proposed=decision.urgency,
            now=current_time,
        ) if display_kind == "todo" else "unscheduled"

        validated.append({
            "source_key": source_key,
            "source_subgoal_id": source.get("source_subgoal_id"),
            "display_kind": display_kind,
            "title": title,
            "detail": detail,
            "owner": decision.owner,
            "action_type": decision.action_type if display_kind == "todo" else "open",
            "requirement": decision.requirement,
            "due_at": due_at,
            "due_date_source": "explicit" if due_at else "none",
            "urgency": urgency,
            "confidence": decision.confidence,
            "evidence_message_ids": evidence_ids,
            "evidence_text": evidence_text,
            "exclusion_reason": exclusion_reason,
        })
    return validated


def _hidden_decision(source: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source_key": str(source["source_key"]),
        "source_subgoal_id": source.get("source_subgoal_id"),
        "display_kind": "hidden",
        "title": None,
        "detail": None,
        "owner": "unknown",
        "action_type": "none",
        "requirement": "informational",
        "due_at": None,
        "due_date_source": "none",
        "urgency": "unscheduled",
        "confidence": 0.0,
        "evidence_message_ids": [],
        "evidence_text": None,
        "exclusion_reason": reason,
    }


def _clean_title(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip().rstrip(".")
    if not cleaned:
        return None
    words = cleaned.split()
    return " ".join(words[:10])[:100]


def _clean_sentence(value: str | None, limit: int) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    return cleaned[:limit] or None


def _explicit_due_datetime(value: str | None, source: str) -> datetime | None:
    if source != "explicit" or not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@dataclass(frozen=True)
class _LifecycleActivity:
    latest_evidence_at: datetime | None
    sent_after_evidence: bool


def _thread_context_messages(
    settings: Settings,
    *,
    user_id: str,
    seed_messages: list[GmailMessageRecord],
) -> list[GmailMessageRecord]:
    by_id = {message.message_id: message for message in seed_messages}
    thread_ids = list(dict.fromkeys(
        message.gmail_thread_id or message.message_id
        for message in seed_messages
    ))
    for thread_id in thread_ids[:12]:
        for message in list_messages_for_gmail_thread(
            str(settings.database_path),
            user_id=user_id,
            gmail_thread_id=thread_id,
            maximum_messages=100,
            maximum_stored_bytes=2_000_000,
        ):
            by_id[message.message_id] = message
    return sorted(by_id.values(), key=_message_datetime)


def _lifecycle_activity(
    evidence_ids: list[str],
    message_by_id: dict[str, GmailMessageRecord],
) -> _LifecycleActivity:
    evidence_messages = [
        message_by_id[message_id]
        for message_id in evidence_ids
        if message_id in message_by_id
    ]
    received_evidence = [
        message for message in evidence_messages
        if "SENT" not in message.label_ids
    ]
    if not received_evidence:
        return _LifecycleActivity(latest_evidence_at=None, sent_after_evidence=False)
    latest_evidence_at = max(_message_datetime(message) for message in received_evidence)
    evidence_threads = {
        message.gmail_thread_id or message.message_id
        for message in received_evidence
    }
    sent_after_evidence = any(
        "SENT" in message.label_ids
        and (message.gmail_thread_id or message.message_id) in evidence_threads
        and _message_datetime(message) > latest_evidence_at
        for message in message_by_id.values()
    )
    return _LifecycleActivity(
        latest_evidence_at=latest_evidence_at,
        sent_after_evidence=sent_after_evidence,
    )


def _message_datetime(message: GmailMessageRecord) -> datetime:
    value = message.internal_date or message.updated_at
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_stale(
    latest_evidence_at: datetime | None,
    *,
    now: datetime,
    maximum_age: timedelta,
) -> bool:
    return latest_evidence_at is None or latest_evidence_at < now - maximum_age


def _current_urgency(
    *,
    due_at: datetime | None,
    latest_evidence_at: datetime | None,
    proposed: TodoUrgency,
    now: datetime,
) -> TodoUrgency:
    if due_at is not None:
        local_now = now.astimezone(due_at.tzinfo)
        if due_at <= now + timedelta(hours=2):
            return "now"
        if due_at.date() == local_now.date():
            return "today"
        return "upcoming"
    if (
        proposed == "now"
        and latest_evidence_at is not None
        and latest_evidence_at >= now - timedelta(hours=24)
    ):
        return "now"
    return "unscheduled"


def _looks_like_action_title(title: str, action_type: str) -> bool:
    first = re.sub(r"[^a-z]", "", title.split(maxsplit=1)[0].casefold())
    expected = {
        "reply": {"reply", "respond"},
        "upload": {"upload", "add"},
        "pay": {"pay"},
        "confirm": {"confirm", "rsvp", "verify"},
        "review": {"review", "check", "approve"},
        "track": {"track", "check"},
        "register": {"register", "enroll"},
        "schedule": {"schedule", "book"},
        "send": {"send", "email"},
        "sign": {"sign", "accept"},
        "submit": {"submit", "provide"},
        "call": {"call", "contact"},
        "attend": {"attend", "join"},
        "renew": {"renew"},
        "cancel": {"cancel"},
        "open": {"open"},
        "other": {
            "complete", "finish", "choose", "update", "create", "download",
            "move", "remove", "set", "select", "request", "resolve",
        },
    }
    return first in expected.get(action_type, set())


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            import json
            value = json.loads(value)
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
