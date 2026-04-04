from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from openai import OpenAI

from schema import (
    CalendarContextInput,
    CalendarContextOutput,
    CalendarContextResponse,
    DecideResponse,
    DecisionOutput,
    EntityInput,
)

SYSTEM_PROMPT = """You convert normalized Gmail and Calendar entities into decision-ready items.

Rules:
- Only output items that require user attention.
- One item per actionable entity.
- Exactly one primary action per item.
- Use direct action verbs: reply, confirm, pay, join, review, send, approve, open, register, track.
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

MAX_BODY_CHARS = 1800
MAX_SUBJECT_CHARS = 180
MAX_SUMMARY_CHARS = 280
MAX_SENDER_CHARS = 120
MAX_PARTICIPANTS = 8
MAX_BATCH_JSON_CHARS = 18000


def decide_entities(entities: list[EntityInput]) -> list[DecisionOutput]:
    if not entities:
        return []

    if _has_llm_config():
        return _decide_with_llm(entities)

    return _decide_with_heuristics(entities)


def _has_llm_config() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _decide_with_llm(entities: list[EntityInput]) -> list[DecisionOutput]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-5.4")
    items: list[DecisionOutput] = []

    for batch in _chunk_json_payloads(
        [_compact_entity_for_llm(entity) for entity in entities],
        key="entities",
    ):
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
                        f"{json.dumps(batch, ensure_ascii=True)}"
                    ),
                },
            ],
        )

        content = completion.choices[0].message.content or '{"items":[]}'
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
    if not items:
        return []

    if _has_llm_config():
        return _describe_calendar_context_with_llm(items)

    return [_describe_calendar_context_heuristically(item) for item in items]


def _decide_with_heuristics(entities: list[EntityInput]) -> list[DecisionOutput]:
    items: list[DecisionOutput] = []

    for entity in entities:
        item = _decide_entity_heuristically(entity)

        if item is None:
            continue

        items.append(item)

    return items


def _decide_entity_heuristically(entity: EntityInput) -> DecisionOutput | None:
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
                        f"{json.dumps(batch, ensure_ascii=True)}"
                    ),
                },
            ],
        )

        content = completion.choices[0].message.content or '{"items":[]}'
        parsed = CalendarContextResponse.model_validate(json.loads(content))
        outputs.extend(parsed.items)

    return outputs


def _describe_calendar_context_heuristically(
    item: CalendarContextInput,
) -> CalendarContextOutput:
    subject = _compact(item.subject)
    lead = f"You have {_with_meeting_article(subject)}" if _looks_like_meeting(subject) else f"You have {subject}"

    if item.time_phrase:
        sentence = f"{lead} {item.day_phrase} at {item.time_phrase}."
    else:
        sentence = f"{lead} {item.day_phrase}."

    return CalendarContextOutput(id=item.id, why_this_is_here=sentence)


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
