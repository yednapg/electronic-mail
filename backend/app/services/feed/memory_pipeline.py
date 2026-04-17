from __future__ import annotations

"""Persisted-memory pipeline that hydrates entities and produces the feed."""

from datetime import datetime, timezone

from app.db.models import LoadedEntity
from app.db.repository import (
    append_trace_record,
    get_entity_member_count,
    get_loaded_entity,
    get_source_record_count,
    list_all_loaded_entities,
    list_entities_missing_state_ids,
    list_unlinked_source_records,
    upsert_ai_suggestion,
)
from app.schemas.ai import FeedEntityContextInput, FeedEntityJudgmentOutput
from app.schemas.domain import AttentionItem, FeedResponse, PipelineEntity, PipelineOutput, SourceRecord
from app.services.ai.decision import judge_feed_entities
from app.services.entities.derive_entity_state import derive_and_store_entity_state
from app.services.entities.entity_resolver import get_sender_domain, resolve_entity_for_record
from app.services.feed.build_feed import build_feed


ALLOWED_ACTIONS = {
    "reply",
    "confirm",
    "pay",
    "join",
    "review",
    "send",
    "approve",
    "open",
    "register",
    "track",
    "none",
}


def hydrate_persistent_memory(
    database_path: str,
    source_records: list[SourceRecord] | None = None,
) -> list[str]:
    """Resolve records to entities, backfill missing links, and derive state."""
    touched_entity_ids: set[str] = set()
    ordered_records = sorted(source_records or [], key=lambda record: record.received_at)

    for record in ordered_records:
        entity, _ = resolve_entity_for_record(database_path, record)
        touched_entity_ids.add(entity.id)

    for record in list_unlinked_source_records(database_path):
        source_record = SourceRecord(
            id=record.id,
            user_id="local-user",
            source=record.source,
            thread_id=record.thread_id or "",
            raw_payload=record.raw_payload,
            received_at=record.timestamp,
        )
        entity, _ = resolve_entity_for_record(database_path, source_record)
        touched_entity_ids.add(entity.id)

    for entity_id in list_entities_missing_state_ids(database_path):
        touched_entity_ids.add(entity_id)

    for entity_id in touched_entity_ids:
        loaded = get_loaded_entity(database_path, entity_id)

        if loaded is None:
            continue

        derive_and_store_entity_state(database_path, entity_id, loaded.members)

    total_records = get_source_record_count(database_path)
    total_members = get_entity_member_count(database_path)

    # Every source record should belong to exactly one entity member row.
    if total_records != total_members:
        print("DATA LOSS DETECTED", {"totalRecords": total_records, "totalMembers": total_members})

    return sorted(touched_entity_ids)


def refresh_ai_suggestions_for_entities(database_path: str, entity_ids: list[str]) -> None:
    """Refresh cached AI judgments only for entities changed by the current sync cycle."""
    unique_entity_ids = sorted(set(entity_ids))

    if not unique_entity_ids:
        return

    entities = [entity for entity in list_all_loaded_entities(database_path) if entity.entity.id in unique_entity_ids]
    entities_needing_refresh = [entity for entity in entities if get_usable_suggestion(entity) is None]
    contexts = [context for entity in entities_needing_refresh if (context := to_feed_entity_context(entity)) is not None]

    if not contexts:
        return

    context_by_id = {context.id: context for context in contexts}
    judgments = judge_feed_entities(contexts)
    judgment_by_id = {judgment.id: judgment for judgment in judgments}

    for entity in entities_needing_refresh:
        judgment = judgment_by_id.get(entity.entity.id)

        if judgment is None:
            continue

        normalized = normalize_judgment(entity, judgment)

        if normalized is None:
            continue

        context = context_by_id.get(entity.entity.id)
        if context is not None:
            append_trace_record(
                database_path,
                stage="decision",
                user_id="local-user",
                entity_id=entity.entity.id,
                source_record_id=entity.members[-1].id if entity.members else None,
                trace_id=entity.entity.id,
                input=context.model_dump(),
                output=normalized,
            )

        upsert_ai_suggestion(database_path, entity_id=entity.entity.id, **normalized)


def build_feed_from_entities(database_path: str, current_time: str) -> FeedResponse:
    """Convert all loaded entities into pipeline outputs and then section them into a feed."""
    entities = list_all_loaded_entities(database_path)
    outputs = [to_pipeline_output(database_path, entity, current_time) for entity in entities]
    feed = build_feed(outputs, database_path)
    source_records = get_source_record_count(database_path)
    feed_items = len(feed.now) + len(feed.today) + len(feed.worth_knowing)

    # Feed projection should never create more visible items than underlying entities.
    if feed_items > len(entities) or len(entities) > source_records:
        print(
            "COUNT INVARIANT VIOLATION",
            {
                "sourceRecords": source_records,
                "entities": len(entities),
                "feedItems": feed_items,
            },
        )

    print(
        {
            "sourceRecords": source_records,
            "entities": len(entities),
            "feedItems": feed_items,
        }
    )
    return feed


def to_pipeline_output(database_path: str, entity: LoadedEntity, current_time: str) -> PipelineOutput:
    """Turn one loaded entity into either a visible feed item or a suppression result."""
    latest_record = entity.members[-1] if entity.members else None
    state = entity.state
    source_record_id = latest_record.id if latest_record is not None else None

    if latest_record is None or state is None:
        pipeline_entity = to_pipeline_entity(entity, None)
        append_trace_record(
            database_path,
            stage="output",
            user_id="local-user",
            entity_id=pipeline_entity.id,
            source_record_id=source_record_id,
            trace_id=pipeline_entity.id,
            input={"reason": "missing_state"},
            output={"surfaced": False, "suppressed": True, "suppression_reason": "missing_state"},
        )
        return create_suppressed_output(pipeline_entity, "missing_state")

    pipeline_entity = to_pipeline_entity(entity, latest_record.source)
    append_trace_record(
        database_path,
        stage="lifecycle_transition",
        user_id="local-user",
        entity_id=pipeline_entity.id,
        source_record_id=source_record_id,
        trace_id=pipeline_entity.id,
        input={"current_state": state.current_state},
        output={"lifecycle_state": pipeline_entity.lifecycle_state},
    )

    if state.current_state == "resolved":
        append_trace_record(
            database_path,
            stage="output",
            user_id="local-user",
            entity_id=pipeline_entity.id,
            source_record_id=source_record_id,
            trace_id=pipeline_entity.id,
            input={"reason": "resolved"},
            output={"surfaced": False, "suppressed": True, "suppression_reason": "resolved"},
        )
        return create_suppressed_output(pipeline_entity, "resolved")

    suggestion = get_usable_suggestion(entity)

    downgraded_from_hidden = suggestion is not None and (
        not suggestion.suggested_visibility or suggestion.suggested_timing == "hidden"
    )

    attention_item = (
        to_fallback_attention_item(entity, latest_record, current_time)
        if suggestion is None
        else to_suggested_attention_item(
            entity,
            latest_record,
            current_time,
            force_worth_knowing=downgraded_from_hidden,
        )
    )
    append_trace_record(
        database_path,
        stage="action_selection",
        user_id="local-user",
        entity_id=pipeline_entity.id,
        source_record_id=source_record_id,
        trace_id=pipeline_entity.id,
        input={
            "current_state": state.current_state,
            "used_ai_suggestion": suggestion is not None,
        },
        output={
            "need_type": attention_item.need_type,
            "action_type": attention_item.action_type,
            "effort_level": attention_item.effort_level,
            "primary_action": attention_item.primary_action,
            "fallback_action": attention_item.fallback_action,
            "downgraded_from_hidden": downgraded_from_hidden,
        },
    )
    append_trace_record(
        database_path,
        stage="timing",
        user_id="local-user",
        entity_id=pipeline_entity.id,
        source_record_id=source_record_id,
        trace_id=pipeline_entity.id,
        input={
            "due_at": attention_item.due_at,
            "current_state": state.current_state,
        },
        output={
            "timing_band": attention_item.timing_band,
            "importance_level": attention_item.importance_level,
            "action_confidence": attention_item.action_confidence,
        },
    )

    return PipelineOutput(
        entity=pipeline_entity,
        attention_item=attention_item,
        suppressed=False,
        suppression_reason=None,
    )


def create_suppressed_output(entity: PipelineEntity, reason: str) -> PipelineOutput:
    """Create a standard suppressed pipeline output."""
    return PipelineOutput(entity=entity, attention_item=None, suppressed=True, suppression_reason=reason)


def to_pipeline_entity(entity: LoadedEntity, source: str | None) -> PipelineEntity:
    """Adapt the persistence-layer entity into the pipeline entity schema."""
    current_state = entity.state.current_state if entity.state is not None else "resolved"
    due_at = entity.state.due_at if entity.state is not None else None

    return PipelineEntity(
        id=entity.entity.id,
        group_id=entity.entity.id,
        user_id="local-user",
        source=source or "gmail",
        current_state=current_state,
        due_at=due_at,
        importance=current_state in {"pending_deadline", "awaiting_reply"},
        lifecycle_state=to_lifecycle_state(current_state),
        created_at=entity.entity.created_at,
        updated_at=entity.entity.updated_at,
    )


def to_feed_entity_context(entity: LoadedEntity) -> FeedEntityContextInput | None:
    """Build the compact memory context sent to the AI feed-judgment layer."""
    if entity.state is None or not entity.members:
        return None

    latest_record = entity.members[-1]

    return FeedEntityContextInput(
        id=entity.entity.id,
        source=latest_record.source,
        current_state=entity.state.current_state,
        due_at=entity.state.due_at,
        latest_subject=payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled",
        entity_summary=build_entity_summary(entity),
        latest_sender=latest_record.sender,
        latest_timestamp=latest_record.timestamp,
        participants=sorted(
            {
                participant
                for record in entity.members
                for participant in collect_participants(record.raw_payload)
            }
        ),
        sender_domains=sorted(
            {
                domain
                for record in entity.members
                if (domain := get_sender_domain(record.sender or ""))
            }
        ),
        record_count=len(entity.members),
        reminder_count=count_reminder_records(entity.members),
        lifecycle_hints=derive_lifecycle_hints(entity.members),
        timeline=[
            {
                "id": record.id,
                "source": record.source,
                "subject": payload_string(record.raw_payload, "subject") or record.subject or "Untitled",
                "sender": record.sender,
                "timestamp": record.timestamp,
                "body_snippet": payload_string(record.raw_payload, "body"),
                "thread_id": record.thread_id,
            }
            for record in entity.members
        ],
    )


def build_entity_summary(entity: LoadedEntity) -> str:
    """Summarize the recent entity lifecycle so the model can title the whole thread naturally."""
    fragments: list[str] = []

    for record in entity.members[-4:]:
        subject = payload_string(record.raw_payload, "subject") or record.subject or ""
        body = payload_string(record.raw_payload, "body")
        fragment = " ".join(part for part in [subject, body] if part).strip()

        if fragment:
            fragments.append(fragment)

    if entity.state is not None:
        fragments.append(f"Current state: {entity.state.current_state}.")

    summary = " | ".join(fragments)
    return summary[:900].strip()


def normalize_judgment(entity: LoadedEntity, judgment: FeedEntityJudgmentOutput) -> dict[str, object] | None:
    """Validate and normalize AI judgment fields before writing them to SQLite."""
    title = normalize_text_field(judgment.title)
    explanation = normalize_text_field(judgment.explanation)

    if not title or not explanation:
        return None

    return {
        "title": title,
        "explanation": explanation,
        "action": normalize_action(judgment.action, entity.state.current_state if entity.state else "open"),
        "suggested_timing": normalize_timing_band(
            judgment.suggested_timing,
            entity,
        ),
        "suggested_priority": clamp_priority(judgment.suggested_priority),
        "suggested_visibility": judgment.suggested_visibility,
        "model": "ai-judgment",
        "generated_from_updated_at": get_entity_context_updated_at(entity),
    }


def get_usable_suggestion(entity: LoadedEntity):
    """Ignore cached AI suggestions once entity context has moved past them."""
    if entity.ai_suggestion is None:
        return None

    if entity.ai_suggestion.generated_from_updated_at < get_entity_context_updated_at(entity):
        return None

    return entity.ai_suggestion


def to_suggested_attention_item(
    entity: LoadedEntity,
    latest_record,
    current_time: str,
    *,
    force_worth_knowing: bool = False,
) -> AttentionItem:
    """Build a feed item from the cached AI suggestion when it is still valid."""
    state = entity.state
    suggestion = entity.ai_suggestion
    assert state is not None
    assert suggestion is not None
    primary_action = normalize_action(suggestion.action, state.current_state)
    timing_band = (
        "later"
        if force_worth_knowing
        else normalize_timing_band(suggestion.suggested_timing, entity, current_time, latest_record.source)
    )
    importance_level = (
        "low" if force_worth_knowing else to_importance_level(suggestion.suggested_priority, state.current_state)
    )
    action_confidence = (
        "low"
        if force_worth_knowing and primary_action == "none"
        else to_action_confidence(state.current_state, primary_action, suggestion.suggested_priority)
    )

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id="local-user",
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=timing_band,
        action_confidence=action_confidence,
        primary_action=primary_action,
        fallback_action="open",
        title=suggestion.title,
        why_this_is_here=suggestion.explanation,
        due_at=state.due_at,
        importance_level=importance_level,
        lifecycle_state=to_lifecycle_state(state.current_state),
        current_state=state.current_state,
        source=latest_record.source,
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def to_fallback_attention_item(entity: LoadedEntity, latest_record, current_time: str) -> AttentionItem:
    """Produce a deterministic fallback item when AI judgment is missing or stale."""
    state = entity.state
    assert state is not None
    primary_action = to_fallback_action(state.current_state)
    latest_title = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled"

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id="local-user",
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=derive_fallback_timing_band(state.current_state, state.due_at, current_time, latest_record.source),
        action_confidence=to_action_confidence(state.current_state, primary_action, 50),
        primary_action=primary_action,
        fallback_action="open",
        title=f"Open: {latest_title}",
        why_this_is_here=to_fallback_explanation(state.current_state, latest_title),
        due_at=state.due_at,
        importance_level=to_importance_level(50, state.current_state),
        lifecycle_state=to_lifecycle_state(state.current_state),
        current_state=state.current_state,
        source=latest_record.source,
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def collect_participants(raw_payload: dict[str, object]) -> list[str]:
    """Read participant-like arrays from either calendar or email payload fields."""
    values = raw_payload.get("participants") or raw_payload.get("attendees")

    if not isinstance(values, list):
        return []

    return [str(value).strip() for value in values if isinstance(value, str) and value.strip()]


def payload_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def count_reminder_records(records) -> int:
    """Count reminder-like records to give the AI more timeline context."""
    count = 0

    for record in records:
        subject = (payload_string(record.raw_payload, "subject") or record.subject or "").lower()
        body = (payload_string(record.raw_payload, "body") or "").lower()

        if any(token in f"{subject} {body}" for token in ["reminder", "expires", "deadline", "due"]):
            count += 1

    return count


def derive_lifecycle_hints(records) -> list[str]:
    """Extract coarse lifecycle tokens from the full entity history."""
    text = " ".join(
        f"{payload_string(record.raw_payload, 'subject') or record.subject or ''} {payload_string(record.raw_payload, 'body') or ''}".lower()
        for record in records
    )
    hints = [
        hint
        for hint in ["registered", "waitlist", "accepted", "rsvp", "confirmed", "received", "processing", "shipped", "delivered"]
        if hint in text
    ]
    if any(token in text for token in ["acknowledge receipt", "request has been registered", "system generated response"]):
        hints.append("acknowledged")
    if any(
        token in text
        for token in [
            "under review",
            "taken up for",
            "appropriate review",
            "interim response",
            "we shall respond by",
            "respond within 2 working days",
            "request has been raised",
            "activation will be completed",
        ]
    ):
        hints.append("under_review")
    if any(
        token in text
        for token in [
            "successfully reversed",
            "final response has been shared",
            "completed from our end",
            "processed successfully",
        ]
    ):
        hints.append("provider_done")
    return sorted(set(hints))


def normalize_action(action: str, current_state: str) -> str:
    """Clamp AI actions to the supported action vocabulary."""
    normalized = action.strip().lower()
    return normalized if normalized in ALLOWED_ACTIONS else to_fallback_action(current_state)


def normalize_text_field(value: str) -> str:
    return " ".join(value.split()).strip()


def to_fallback_action(current_state: str) -> str:
    """Map derived state to the safest default action when AI is absent."""
    if current_state == "awaiting_reply":
        return "reply"
    if current_state == "pending_deadline":
        return "review"
    if current_state == "scheduled":
        return "none"
    return "open"


def normalize_timing_band(
    suggested_timing: str,
    entity: LoadedEntity,
    current_time: str | None = None,
    source: str | None = None,
) -> str:
    """Keep AI timing suggestions within the system's feed invariants."""
    normalized = suggested_timing.strip().lower()
    state = entity.state
    assert state is not None
    fallback = derive_fallback_timing_band(
        state.current_state,
        state.due_at,
        current_time or datetime.now(timezone.utc).isoformat(),
        source or (entity.members[0].source if entity.members else "gmail"),
    )

    if normalized not in {"now", "today", "later", "hidden"}:
        return fallback
    if normalized == "hidden":
        return fallback
    if state.current_state == "awaiting_reply" and normalized == "later":
        return "today"
    if state.due_at is not None:
        due_at = parse_iso(state.due_at)
        reference = parse_iso(current_time or datetime.now(timezone.utc).isoformat())

        if due_at is not None and reference is not None and due_at <= reference + 24 * 60 * 60 and normalized == "later":
            return "today"

    return normalized


def clamp_priority(priority) -> int:
    if not isinstance(priority, (int, float)):
        return 50
    return max(0, min(100, round(priority)))


def derive_fallback_timing_band(current_state: str, due_at: str | None, current_time: str, source: str) -> str:
    """Derive a timing band from due date proximity and source defaults."""
    if due_at is None:
        return "today" if source == "calendar" or current_state == "awaiting_reply" else "later"

    due_timestamp = parse_iso(due_at)
    current_timestamp = parse_iso(current_time)

    if due_timestamp is None or current_timestamp is None:
        return "today"

    delta_seconds = due_timestamp - current_timestamp

    if delta_seconds <= 24 * 60 * 60:
        return "now"
    if delta_seconds <= 3 * 24 * 60 * 60:
        return "today"
    return "later"


def to_fallback_explanation(current_state: str, title: str) -> str:
    """Generate rule-based explanation copy when no AI explanation exists."""
    if current_state == "pending_deadline":
        return f"This still has an upcoming deadline for {title}."
    if current_state == "awaiting_reply":
        return f"This thread is waiting on your reply about {title}."
    if current_state == "scheduled":
        return f"This is still relevant on your schedule for {title}."
    if current_state in {"resolved", "delivered"}:
        return f"This was updated recently for {title}."
    return f"This still matters for {title}."


def to_action_type(primary_action: str) -> str:
    if primary_action in {"reply", "confirm"}:
        return "inline"
    if primary_action == "none":
        return "none"
    return "external"


def to_effort_level(primary_action: str) -> str:
    if primary_action in {"reply", "confirm", "pay", "none"}:
        return "quick"
    return "deep"


def to_importance_level(priority: int, current_state: str) -> str:
    if current_state == "pending_deadline" or priority >= 80:
        return "high"
    if current_state == "awaiting_reply" or priority >= 50:
        return "medium"
    return "low"


def to_action_confidence(current_state: str, primary_action: str, priority: int) -> str:
    if current_state == "awaiting_reply" or primary_action == "reply":
        return "high"
    if priority >= 60:
        return "medium"
    return "low"


def to_lifecycle_state(current_state: str) -> str:
    if current_state == "scheduled":
        return "scheduled"
    if current_state == "resolved":
        return "resolved"
    return "active"


def get_entity_context_updated_at(entity: LoadedEntity) -> str:
    """Return the freshest timestamp that should invalidate cached AI output."""
    timestamps = [entity.entity.updated_at]

    if entity.state is not None:
        timestamps.append(entity.state.updated_at)
    if entity.members:
        timestamps.append(entity.members[-1].timestamp)

    return max(timestamps)


def parse_iso(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
