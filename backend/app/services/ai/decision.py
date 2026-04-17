from __future__ import annotations

"""AI and heuristic decision logic used by both feed and grouping flows."""

import json
import os
from datetime import datetime, timezone

from openai import OpenAI

from app.schemas.ai import (
    CalendarContextInput,
    CalendarContextOutput,
    CalendarContextResponse,
    DashboardBriefingInput,
    DashboardBriefingOutput,
    DashboardBriefingResponse,
    DecideResponse,
    DecisionOutput,
    EntityGroupingRequest,
    EntityGroupingResponse,
    EntityInput,
    FeedEntityContextInput,
    FeedEntityJudgmentOutput,
    FeedEntityJudgmentResponse,
)
from app.schemas.domain import DashboardBriefing, DashboardProfile, FeedResponse

SYSTEM_PROMPT = """You convert normalized Gmail and Calendar entities into decision-ready items.

Rules:
- Only output items that require user attention.
- One item per actionable entity.
- Exactly one primary action per item.
- Use direct action verbs not directly linked to prompts but included in sentence: reply, confirm, pay, join, review, send, approve, open, register, track.
- Do not use vague actions like "handle", "check", or "look into" when a clearer verb exists.
- Do not use marketing words, hype, or repeated urgency.
- Titles must be concise, human-readable, and action-first.
- why_this_is_here must be plain language and must not mention prompts, models, pipelines, or system logic.
- timing_band:
  - now: urgent, blocking, overdue, or due within 24 hours
  - today: same-day importance or near-term action
  - later: future but valid action
  - hidden: no action required
- importance_level reflects user cost of missing the item.
- action_confidence reflects how clear the action is from the entity.
- If an email is informational, completed, a receipt, a confirmation with no next step, a waitlist notice, or a status update, do not include it.
- Prefer suppressing doubtful items over surfacing vague ones.
- Keep the original subject/body meaning intact.

Return strict JSON only:
{
  "items": [
    {
      "id": "string",
      "is_decision": true,
      "title": "string",
      "why_this_is_here": "string",
      "primary_action": "string",
      "timing_band": "now|today|later|hidden",
      "importance_level": "high|medium|low",
      "action_confidence": "high|medium|low"
    }
  ]
}
"""

CALENDAR_CONTEXT_PROMPT = """You write one natural sentence for each calendar item for the main dashboard list.

Rules:
- Keep the output human and natural, not robotic.
- Do not say "calendar item", "event", or "scheduled" unless needed.
- Use the provided day_phrase and time_phrase exactly when they exist.
- Prefer concrete phrasing like "You have a meeting with Michael today at 21:30."
- Keep names and original meaning intact.
- Return one sentence per item.

Return strict JSON only:
{
  "items": [
    {
      "id": "string",
      "why_this_is_here": "string"
    }
  ]
}
"""

FEED_JUDGMENT_PROMPT = """You judge which persisted entities should appear in a personal feed.

Rules:
- Return one judgment max per entity.
- The entity timeline is the memory/context for what has happened so far.
- Repeated reminders and lifecycle updates should collapse into one meaningful item.
- Read the whole timeline together, not just the latest subject line.
- Title must be concise, human-readable, and useful in a personal feed.
- Title must summarize the full entity from the user's perspective in natural language.
- Prefer titles like "Northstar Bank registered your credit card upgrade and limit increase request."
- Avoid generic titles like "Northstar request acknowledged", "Bank update", or a bare copied subject line when the timeline provides richer context.
- When several emails are about the same request, synthesize them into one natural title that reflects the latest meaningful state.
- If an item is informative but still worth surfacing, prefer action = none and keep the title as a natural status sentence.
- Do not force verbs like review, track, join, or open when the timeline does not imply a concrete user action.
- If the timeline shows the bank/vendor has acknowledged the request, registered it, taken it up for review, or promised a response within a few working days, prefer action = none unless the user is explicitly asked to do something.
- If the provider has already completed the work from their side, prefer action = none and summarize the completed status accurately.
- explanation should explain why this matters now.
- action must be one of: reply, confirm, pay, join, review, send, approve, open, register, track, none.
- suggested_timing must be one of: now, today, later, hidden.
- suggested_priority must be an integer from 0 to 100.
- suggested_visibility is whether the item should appear at all.
- Suppress completed or stale informational items when they do not need attention.

Return strict JSON only:
{
  "items": [
    {
      "id": "string",
      "title": "string",
      "explanation": "string",
      "action": "string",
      "suggested_timing": "now|today|later|hidden",
      "suggested_priority": 0,
      "suggested_visibility": true
    }
  ]
}
"""

DASHBOARD_BRIEFING_PROMPT = """You write the top briefing for a personal dashboard.

Rules:
- Use the exact numeric counts provided. Never invent or change counts.
- headline must be a short greeting sentence.
- If you can infer a first name confidently from the account_email, use it in headline as "Good morning, TestUser."
- If the name is not clear, omit the name and keep the greeting natural.
- brief must be one concise natural-language paragraph.
- brief should summarize meetings, tasks, replies, payments, and how free the rest of the day looks.
- Use the provided free_after_label exactly when you mention free time.
- Keep the tone calm, practical, and personal.
- Do not mention prompts, JSON, models, or system behavior.
- Do not use markdown.

Return strict JSON only:
{
  "items": [
    {
      "display_name": "string|null",
      "headline": "string",
      "brief": "string"
    }
  ]
}
"""

ENTITY_GROUPING_PROMPT = """You decide whether a new email belongs to an existing entity.

Rules:
- Prefer exact semantic grouping over loose similarity.
- Repeated reminders and lifecycle updates should attach to the same entity.
- If uncertain, return entity_id = null.
- confidence must be between 0 and 1.

Return strict JSON only:
{
  "entity_id": "string|null",
  "confidence": 0.0
}
"""

MAX_BODY_CHARS = 1800
MAX_SUBJECT_CHARS = 180
MAX_SUMMARY_CHARS = 280
MAX_SENDER_CHARS = 120
MAX_PARTICIPANTS = 8
MAX_BATCH_JSON_CHARS = 18000


def _openai_debug_enabled() -> bool:
    """Gate verbose terminal logging for live OpenAI requests behind an env flag."""
    return os.getenv("OPENAI_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}


def _log_openai_exchange(*, label: str, model: str, payload: dict[str, object], content: str) -> None:
    """Print the compact request payload and raw model JSON to the backend terminal."""
    if not _openai_debug_enabled():
        return

    print(
        json.dumps(
            {
                "openai_debug": True,
                "label": label,
                "model": model,
                "payload": payload,
                "response": content,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


def decide_entities(entities: list[EntityInput]) -> list[DecisionOutput]:
    """Return actionable decisions for normalized source entities."""
    if not entities:
        return []

    if _has_llm_config():
        return _decide_with_llm(entities)

    if _llm_required():
        _raise_missing_llm()

    return _decide_with_heuristics(entities)


def _has_llm_config() -> bool:
    """Use the LLM path only when an API key is configured."""
    return bool(os.getenv("OPENAI_API_KEY"))


def _llm_required() -> bool:
    """Allow runtime to fail fast instead of silently falling back when OpenAI is mandatory."""
    return os.getenv("OPENAI_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"}


def _raise_missing_llm() -> None:
    """Surface OpenAI misconfiguration clearly in environments that require it."""
    raise RuntimeError("OPENAI_API_KEY is required because OPENAI_REQUIRED is enabled.")


def _decide_with_llm(entities: list[EntityInput]) -> list[DecisionOutput]:
    """Batch entity decisioning through the configured chat model."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    items: list[DecisionOutput] = []

    for batch in _chunk_json_payloads(
        [_compact_entity_for_llm(entity) for entity in entities],
        key="entities",
    ):
        user_payload = {
            "entities": batch["entities"],
        }
        completion = client.chat.completions.create(
            model=model,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Generate decision-ready items for these entities. "
                        "Only include entities that require action.\n\n"
                        f"{json.dumps(user_payload, ensure_ascii=True)}"
                    ),
                },
            ],
        )

        content = completion.choices[0].message.content or '{"items":[]}'
        _log_openai_exchange(
            label="decide_entities",
            model=model,
            payload=user_payload,
            content=content,
        )
        parsed = DecideResponse.model_validate(json.loads(content))
        items.extend(
            item
            for item in parsed.items
            if item.is_decision and item.timing_band != "hidden"
        )

    return items


def describe_calendar_context(
    items: list[CalendarContextInput],
) -> list[CalendarContextOutput]:
    """Generate dashboard copy for calendar items."""
    if not items:
        return []

    if _has_llm_config():
        return _describe_calendar_context_with_llm(items)

    if _llm_required():
        _raise_missing_llm()

    return [_describe_calendar_context_heuristically(item) for item in items]


def judge_feed_entities(
    entities: list[FeedEntityContextInput],
) -> list[FeedEntityJudgmentOutput]:
    """Judge whether persisted entities should surface in the feed."""
    if not entities:
        return []

    if _has_llm_config():
        return _judge_feed_entities_with_llm(entities)

    if _llm_required():
        _raise_missing_llm()

    return [_judge_feed_entity_heuristically(entity) for entity in entities]


def resolve_entity_group(request: EntityGroupingRequest) -> EntityGroupingResponse:
    """Choose whether a new record belongs to an existing entity."""
    if not request.candidates:
        return EntityGroupingResponse(entity_id=None, confidence=0.0)

    if _has_llm_config():
        return _resolve_entity_group_with_llm(request)

    if _llm_required():
        _raise_missing_llm()

    return _resolve_entity_group_heuristically(request)


def generate_dashboard_briefing(
    feed: FeedResponse,
    profile: DashboardProfile | None = None,
) -> DashboardBriefing:
    """Return the generated dashboard headline and summary paragraph."""
    briefing_input = _build_dashboard_briefing_input(feed, profile)

    if _has_llm_config():
        return _generate_dashboard_briefing_with_llm(briefing_input)

    if _llm_required():
        _raise_missing_llm()

    return _generate_dashboard_briefing_heuristically(briefing_input)


def _decide_with_heuristics(entities: list[EntityInput]) -> list[DecisionOutput]:
    """Fallback decisioning path used when no LLM is configured."""
    items: list[DecisionOutput] = []

    for entity in entities:
        item = _decide_entity_heuristically(entity)

        if item is None:
            continue

        items.append(item)

    return items


def _decide_entity_heuristically(entity: EntityInput) -> DecisionOutput | None:
    """Infer one decision item from simple keyword and due-date signals."""
    text = " ".join(
        part
        for part in [entity.subject, entity.body, entity.thread_summary or ""]
        if part
    ).lower()

    if _has_any(
        text,
        [
            "processed successfully",
            "no payment due",
            "completed",
            "resolved",
            "you're confirmed",
            "you are confirmed",
            "accepted",
            "waitlist",
            "receipt for your donation",
            "receipt",
        ],
    ):
        return None

    action = _infer_action(entity, text)

    if action is None:
        return None

    timing_band = _infer_timing_band(entity)
    if timing_band == "hidden":
        return None

    importance_level = _infer_importance(entity, text, timing_band)
    action_confidence = _infer_confidence(text, action)

    return DecisionOutput(
        id=entity.id,
        is_decision=True,
        title=_build_title(entity, action),
        why_this_is_here=_build_reason(entity, action, timing_band),
        primary_action=action,
        timing_band=timing_band,
        importance_level=importance_level,
        action_confidence=action_confidence,
    )


def _describe_calendar_context_with_llm(
    items: list[CalendarContextInput],
) -> list[CalendarContextOutput]:
    """Batch calendar phrasing through the configured model."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    outputs: list[CalendarContextOutput] = []

    compact_items = [
        {
            "id": item.id,
            "subject": _compact(item.subject),
            "participants": item.participants[:MAX_PARTICIPANTS],
            "timing_band": item.timing_band,
            "day_phrase": item.day_phrase,
            "time_phrase": item.time_phrase,
        }
        for item in items
    ]

    for batch in _chunk_json_payloads(compact_items, key="items"):
        user_payload = {
            "items": batch["items"],
        }
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": CALENDAR_CONTEXT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Write one dashboard sentence for each calendar item.\n\n"
                        f"{json.dumps(user_payload, ensure_ascii=True)}"
                    ),
                },
            ],
        )

        content = completion.choices[0].message.content or '{"items":[]}'
        _log_openai_exchange(
            label="calendar_context",
            model=model,
            payload=user_payload,
            content=content,
        )
        parsed = CalendarContextResponse.model_validate(json.loads(content))
        outputs.extend(parsed.items)

    return outputs


def _describe_calendar_context_heuristically(
    item: CalendarContextInput,
) -> CalendarContextOutput:
    """Generate simple natural phrasing for calendar rows without an LLM."""
    subject = _compact(item.subject)
    lead = f"You have {_with_meeting_article(subject)}" if _looks_like_meeting(subject) else f"You have {subject}"

    if item.time_phrase:
        sentence = f"{lead} {item.day_phrase} at {item.time_phrase}."
    else:
        sentence = f"{lead} {item.day_phrase}."

    return CalendarContextOutput(id=item.id, why_this_is_here=sentence)


def _judge_feed_entities_with_llm(
    entities: list[FeedEntityContextInput],
) -> list[FeedEntityJudgmentOutput]:
    """Batch persisted entity judgment through the configured model."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    outputs: list[FeedEntityJudgmentOutput] = []

    compact_entities = [
        {
            "id": entity.id,
            "source": entity.source,
            "current_state": entity.current_state,
            "due_at": entity.due_at,
            "latest_subject": _compact(entity.latest_subject),
            "entity_summary": _truncate_text(entity.entity_summary, 900),
            "latest_sender": _truncate_text(entity.latest_sender or "", MAX_SENDER_CHARS) or None,
            "latest_timestamp": entity.latest_timestamp,
            "participants": entity.participants[:MAX_PARTICIPANTS],
            "sender_domains": entity.sender_domains[:MAX_PARTICIPANTS],
            "record_count": entity.record_count,
            "reminder_count": entity.reminder_count,
            "lifecycle_hints": entity.lifecycle_hints[:MAX_PARTICIPANTS],
            "timeline": [
                {
                    "id": item.id,
                    "source": item.source,
                    "subject": _compact(item.subject),
                    "sender": _truncate_text(item.sender or "", MAX_SENDER_CHARS) or None,
                    "timestamp": item.timestamp,
                    "body_snippet": _truncate_text(item.body_snippet or "", 420) or None,
                    "thread_id": item.thread_id,
                }
                for item in entity.timeline
            ],
        }
        for entity in entities
    ]

    for batch in _chunk_json_payloads(compact_entities, key="entities"):
        user_payload = {
            "entities": batch["entities"],
        }
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": FEED_JUDGMENT_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Judge these feed entities from memory context. "
                        "Read each timeline from oldest to newest and write the title as one natural summary of the full thread from the user's point of view. "
                        "Return judgment signals only; the API will validate, override if needed, and compute final ranking.\n\n"
                        f"{json.dumps(user_payload, ensure_ascii=True)}"
                    ),
                },
            ],
        )

        content = completion.choices[0].message.content or '{"items":[]}'
        _log_openai_exchange(
            label="judge_feed_entities",
            model=model,
            payload=user_payload,
            content=content,
        )
        parsed = FeedEntityJudgmentResponse.model_validate(json.loads(content))
        outputs.extend(parsed.items)

    return outputs


def _judge_feed_entity_heuristically(
    entity: FeedEntityContextInput,
) -> FeedEntityJudgmentOutput:
    """Fallback feed judgment using only derived state and memory metadata."""
    focus = _derive_feed_focus(entity)
    suggested_timing = _infer_feed_timing(entity)
    suggested_priority = _infer_feed_priority(entity)
    suggested_visibility = suggested_timing != "hidden"
    action = _infer_feed_action(entity)
    title = _build_feed_title(focus, action)
    explanation = _build_feed_explanation(focus, entity.current_state)

    return FeedEntityJudgmentOutput(
        id=entity.id,
        title=title,
        explanation=explanation,
        action=action,
        suggested_timing=suggested_timing,
        suggested_priority=suggested_priority,
        suggested_visibility=suggested_visibility,
    )


def _generate_dashboard_briefing_with_llm(
    briefing_input: DashboardBriefingInput,
) -> DashboardBriefing:
    """Generate the dashboard top summary through the configured model."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    user_payload = briefing_input.model_dump()
    completion = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": DASHBOARD_BRIEFING_PROMPT},
            {
                "role": "user",
                "content": (
                    "Write the dashboard greeting and summary from this mailbox context.\n\n"
                    f"{json.dumps(user_payload, ensure_ascii=True)}"
                ),
            },
        ],
    )

    content = completion.choices[0].message.content or '{"items":[]}'
    _log_openai_exchange(
        label="dashboard_briefing",
        model=model,
        payload=user_payload,
        content=content,
    )
    parsed = DashboardBriefingResponse.model_validate(json.loads(content))
    generated = parsed.items[0] if parsed.items else _build_dashboard_briefing_fallback_output(briefing_input)

    headline = generated.headline.strip() or _build_dashboard_briefing_fallback_output(briefing_input).headline
    brief = generated.brief.strip() or _build_dashboard_briefing_fallback_output(briefing_input).brief
    return DashboardBriefing(headline=headline, brief=brief)


def _generate_dashboard_briefing_heuristically(
    briefing_input: DashboardBriefingInput,
) -> DashboardBriefing:
    """Fallback dashboard briefing when no LLM is configured."""
    output = _build_dashboard_briefing_fallback_output(briefing_input)
    return DashboardBriefing(headline=output.headline, brief=output.brief)


def _resolve_entity_group_with_llm(request: EntityGroupingRequest) -> EntityGroupingResponse:
    """Ask the model whether a new record belongs to an existing entity."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    user_payload = {
        "subject": _compact(request.subject),
        "snippet": _truncate_text(request.snippet, 220),
        "candidates": [
            {
                "entity_id": candidate.entity_id,
                "canonical_key": candidate.canonical_key,
                "latest_subject": _compact(candidate.latest_subject),
                "latest_sender": _truncate_text(candidate.latest_sender or "", MAX_SENDER_CHARS) or None,
                "current_state": candidate.current_state,
                "summary": _truncate_text(candidate.summary, 220),
            }
            for candidate in request.candidates[:5]
        ],
    }
    completion = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ENTITY_GROUPING_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=True),
            },
        ],
    )

    content = completion.choices[0].message.content or '{"entity_id": null, "confidence": 0.0}'
    _log_openai_exchange(
        label="resolve_entity_group",
        model=model,
        payload=user_payload,
        content=content,
    )
    return EntityGroupingResponse.model_validate(json.loads(content))


def _resolve_entity_group_heuristically(request: EntityGroupingRequest) -> EntityGroupingResponse:
    """Fallback grouping based on subject and snippet overlap."""
    normalized_subject = _normalize_grouping_text(request.subject)
    normalized_snippet = _normalize_grouping_text(request.snippet)
    best_entity_id: str | None = None
    best_confidence = 0.0

    for candidate in request.candidates[:5]:
        candidate_text = _normalize_grouping_text(
            f"{candidate.latest_subject} {candidate.summary} {candidate.latest_sender or ''}"
        )
        confidence = max(
            _grouping_overlap_confidence(normalized_subject, _normalize_grouping_text(candidate.latest_subject)),
            _grouping_overlap_confidence(
                f"{normalized_subject} {normalized_snippet}".strip(),
                candidate_text,
            ),
        )

        if confidence > best_confidence:
            best_confidence = confidence
            best_entity_id = candidate.entity_id

    if best_confidence > 0.8 and best_entity_id is not None:
        return EntityGroupingResponse(entity_id=best_entity_id, confidence=best_confidence)

    return EntityGroupingResponse(entity_id=None, confidence=best_confidence)


def _infer_action(entity: EntityInput, text: str) -> str | None:
    if entity.source == "calendar" and _has_any(text, ["rsvp", "accept", "decline", "invite"]):
        return "confirm"
    if _has_any(text, ["rsvp", "accept or decline", "calendar invite", "invited you"]):
        return "confirm"
    if _has_any(text, ["please reply", "reply", "respond", "let me know", "can you send"]):
        return "reply"
    if _has_any(text, ["invoice", "bill", "amount due", "payment due"]):
        return "pay"
    if _has_any(text, ["review", "questionnaire", "pull request", "pr", "doc review"]):
        return "review"
    if _has_any(text, ["register", "sign up"]):
        return "register"
    if _has_any(text, ["join", "attend", "meeting link"]):
        return "join"
    if _has_any(text, ["approve", "approval"]):
        return "approve"
    if _has_any(text, ["send", "share"]):
        return "send"

    return None


def _infer_timing_band(entity: EntityInput) -> str:
    if entity.due_at is None:
        return "today" if entity.source == "calendar" else "later"

    due_at = _parse_iso(entity.due_at)
    reference = _parse_iso(entity.timestamp)

    if due_at is None or reference is None:
        return "later"

    delta_seconds = (due_at - reference).total_seconds()

    if delta_seconds <= 24 * 60 * 60:
        return "now"
    if delta_seconds <= 3 * 24 * 60 * 60:
        return "today"
    return "later"


def _infer_importance(entity: EntityInput, text: str, timing_band: str) -> str:
    if timing_band == "now":
        return "high"

    if entity.source == "calendar" or _has_any(text, ["invoice", "payment", "deadline", "security", "legal"]):
        return "high"

    if _has_any(text, ["reply", "respond", "review", "approve"]):
        return "medium"

    return "low"


def _infer_confidence(text: str, action: str) -> str:
    if action in {"reply", "confirm", "pay"}:
        return "high"

    if _has_any(text, ["please", "need", "required", "must"]):
        return "medium"

    return "low"


def _build_title(entity: EntityInput, action: str) -> str:
    subject = _compact(entity.subject)

    if action == "reply":
        return f"Reply about {subject}"
    if action == "confirm":
        return f"Confirm {subject}"
    if action == "pay":
        return f"Pay {subject}"
    if action == "review":
        return f"Review {subject}"
    if action == "register":
        return f"Register for {subject}"
    if action == "join":
        return f"Join {subject}"
    if action == "send":
        return f"Send {subject}"
    if action == "approve":
        return f"Approve {subject}"

    return f"Open {subject}"


def _build_reason(entity: EntityInput, action: str, timing_band: str) -> str:
    subject = _compact(entity.subject)

    if action == "confirm":
        return f"This invite needs your response for {subject}."
    if action == "reply":
        return f"This message is waiting on your reply about {subject}."
    if action == "pay":
        return f"This payment needs attention for {subject}."
    if action == "review":
        return f"This needs your review for {subject}."
    if action == "register":
        return f"This requires registration for {subject}."
    if timing_band == "now":
        return f"This needs attention soon for {subject}."

    return f"This still needs a decision for {subject}."


def _compact(value: str) -> str:
    compacted = " ".join(value.split()).strip()

    if len(compacted) <= 72:
        return compacted

    return f"{compacted[:69].rstrip()}..."


def _compact_entity_for_llm(entity: EntityInput) -> dict[str, object]:
    return {
        "id": entity.id,
        "source": entity.source,
        "subject": _truncate_text(entity.subject, MAX_SUBJECT_CHARS),
        "body": _truncate_text(entity.body, MAX_BODY_CHARS),
        "sender": _truncate_text(entity.sender, MAX_SENDER_CHARS),
        "participants": entity.participants[:MAX_PARTICIPANTS],
        "timestamp": entity.timestamp,
        "due_at": entity.due_at,
        "thread_summary": _truncate_text(entity.thread_summary or "", MAX_SUMMARY_CHARS) or None,
    }


def _truncate_text(value: str, limit: int) -> str:
    compacted = " ".join(value.split()).strip()

    if len(compacted) <= limit:
        return compacted

    return f"{compacted[:limit - 3].rstrip()}..."


def _chunk_json_payloads(
    items: list[dict[str, object]],
    *,
    key: str,
) -> list[dict[str, list[dict[str, object]]]]:
    batches: list[dict[str, list[dict[str, object]]]] = []
    current_batch: list[dict[str, object]] = []
    current_size = len(key) + 16

    for item in items:
        item_size = len(json.dumps(item, ensure_ascii=True))

        if current_batch and current_size + item_size > MAX_BATCH_JSON_CHARS:
            batches.append({key: current_batch})
            current_batch = []
            current_size = len(key) + 16

        current_batch.append(item)
        current_size += item_size

    if current_batch:
        batches.append({key: current_batch})

    return batches


def _parse_iso(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _looks_like_meeting(subject: str) -> bool:
    return bool(
        subject
        and (
            "meeting" in subject.lower()
            or "call" in subject.lower()
            or "sync" in subject.lower()
        )
    )


def _with_meeting_article(subject: str) -> str:
    if subject.lower().startswith(("a ", "an ", "the ")):
        return subject

    if _looks_like_meeting(subject):
        return f"a {subject[:1].lower()}{subject[1:]}"

    return subject


def _infer_feed_action(entity: FeedEntityContextInput) -> str:
    if entity.current_state == "awaiting_reply":
        return "reply"
    if entity.current_state == "pending_deadline":
        return "review"
    if entity.current_state == "scheduled":
        return "none"

    return "open"


def _infer_feed_timing(entity: FeedEntityContextInput) -> str:
    if entity.current_state == "resolved":
        return "hidden"

    if entity.due_at is None:
        return "today" if entity.source == "calendar" else "later"

    due_at = _parse_iso(entity.due_at)
    reference = _parse_iso(entity.latest_timestamp)

    if due_at is None or reference is None:
        return "later"

    delta_seconds = (due_at - reference).total_seconds()

    if delta_seconds <= 24 * 60 * 60:
        return "now"
    if delta_seconds <= 3 * 24 * 60 * 60:
        return "today"
    return "later"


def _infer_feed_priority(entity: FeedEntityContextInput) -> int:
    if entity.current_state == "pending_deadline":
        return 90
    if entity.current_state == "awaiting_reply":
        return 75
    if entity.current_state == "scheduled":
        return 55

    return 40


def _build_feed_title(subject: str, action: str) -> str:
    if action == "reply":
        return f"Reply about {subject}"
    if action == "review":
        return f"Review {subject}"
    if action == "register":
        return f"Register for {subject}"
    if action == "confirm":
        return f"Confirm {subject}"
    if action == "none":
        return subject

    return f"Open {subject}"


def _build_feed_explanation(subject: str, current_state: str) -> str:
    if current_state == "pending_deadline":
        return f"This still has an upcoming deadline for {subject}."
    if current_state == "awaiting_reply":
        return f"This thread is waiting on your reply about {subject}."
    if current_state == "scheduled":
        return f"This is still relevant on your schedule for {subject}."
    if current_state == "resolved":
        return f"This was updated recently for {subject}."

    return f"This still matters for {subject}."


def _derive_feed_focus(entity: FeedEntityContextInput) -> str:
    """Prefer a synthesized entity summary over the last subject line for fallback copy."""
    summary = _truncate_text(entity.entity_summary or "", 120)

    if summary:
        for segment in summary.split("|"):
            candidate = segment.strip().rstrip(".")
            if len(candidate) >= 18:
                return candidate

    return _compact(entity.latest_subject)


def _build_dashboard_briefing_input(
    feed: FeedResponse,
    profile: DashboardProfile | None,
) -> DashboardBriefingInput:
    """Compact the current feed into one summarization payload."""
    visible_items = [*feed.now, *feed.today, *feed.worth_knowing]
    agenda_items = [item for item in visible_items if item.source == "calendar"]
    free_after_label = _compute_free_after_label(agenda_items)

    return DashboardBriefingInput(
        current_time=datetime.now().astimezone().isoformat(),
        account_email=profile.email if profile is not None else None,
        meeting_count=len(agenda_items),
        task_count=sum(1 for item in visible_items if item.primary_action in _TASK_ACTIONS),
        reply_count=sum(1 for item in visible_items if item.primary_action == "reply"),
        payment_count=sum(1 for item in visible_items if item.primary_action == "pay"),
        free_after_label=free_after_label,
        items=[
            {
                "title": _truncate_text(item.title, 120),
                "why_this_is_here": _truncate_text(item.why_this_is_here, 180),
                "source": item.source,
                "timing_band": item.timing_band,
                "primary_action": item.primary_action,
                "due_at": item.due_at,
            }
            for item in visible_items[:8]
        ],
    )


def _build_dashboard_briefing_fallback_output(
    briefing_input: DashboardBriefingInput,
) -> DashboardBriefingOutput:
    """Assemble a deterministic summary when the model is unavailable."""
    greeting = _greeting_for_current_time(briefing_input.current_time)
    display_name = _infer_display_name_from_email(briefing_input.account_email)
    headline = f"{greeting}, {display_name}." if display_name else f"{greeting}."

    brief = (
        f"You have {briefing_input.meeting_count} meetings, {briefing_input.task_count} tasks, "
        f"{briefing_input.reply_count} emails to reply to, and "
        f"{briefing_input.payment_count} card payments due today. "
        f"You're mostly free after {briefing_input.free_after_label}."
    )

    return DashboardBriefingOutput(
        display_name=display_name,
        headline=headline,
        brief=brief,
    )


def _compute_free_after_label(items: list) -> str:
    """Pick the latest visible calendar time for the free-time summary."""
    latest_due_at: str | None = None

    for item in items:
        if item.due_at is None:
            continue

        parsed_due_at = _parse_iso(item.due_at)
        if parsed_due_at is None:
            continue

        if latest_due_at is None or parsed_due_at > _parse_iso(latest_due_at):
            latest_due_at = item.due_at

    if latest_due_at is None:
        return "the rest of the day"

    parsed = _parse_iso(latest_due_at)
    if parsed is None:
        return "the rest of the day"

    return parsed.astimezone().strftime("%H:%M")


def _greeting_for_current_time(current_time: str) -> str:
    """Return a time-of-day greeting from an ISO timestamp."""
    parsed = _parse_iso(current_time)
    hour = parsed.astimezone().hour if parsed is not None else datetime.now().hour

    if hour >= 12 and hour < 18:
        return "Good afternoon"
    if hour >= 18:
        return "Good evening"
    return "Good morning"


def _infer_display_name_from_email(account_email: str | None) -> str | None:
    """Derive a first-name-like label from the mailbox email when possible."""
    if not account_email or "@" not in account_email:
        return None

    local_part = account_email.split("@", 1)[0].strip().lower()
    if not local_part:
        return None

    for separator in (".", "_", "-"):
        if separator in local_part:
            local_part = local_part.split(separator, 1)[0]
            break

    if not local_part.isalpha() or len(local_part) < 2:
        return None

    return local_part[:1].upper() + local_part[1:]


_TASK_ACTIONS = {"open", "track", "confirm", "review", "join", "send", "approve", "register"}


def _normalize_grouping_text(value: str) -> str:
    return " ".join(value.lower().split())


def _grouping_overlap_confidence(left: str, right: str) -> float:
    left_tokens = {token for token in _normalize_grouping_text(left).split(" ") if len(token) > 2}
    right_tokens = {token for token in _normalize_grouping_text(right).split(" ") if len(token) > 2}

    if not left_tokens or not right_tokens:
        return 0.0

    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))

    if overlap >= 0.8:
        return 0.92
    if overlap >= 0.6:
        return 0.84
    if overlap >= 0.4:
        return 0.68

    return 0.0
