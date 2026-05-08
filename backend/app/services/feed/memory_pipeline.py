from __future__ import annotations

"""Persisted-memory pipeline that hydrates entities and produces the feed."""

from datetime import datetime, timezone
import re

from app.db.models import LoadedEntity
from app.db.repository import (
    append_trace_record,
    clear_derived_memory,
    get_entity_member_count,
    get_latest_entity_outcomes,
    get_loaded_entity,
    get_source_record_count,
    list_all_loaded_entities,
    list_loaded_entities,
    list_entities_missing_state_ids,
    list_unlinked_source_records,
    upsert_ai_suggestion,
)
from app.schemas.ai import FeedEntityContextInput, FeedEntityJudgmentOutput
from app.schemas.domain import AttentionItem, FeedResponse, PipelineEntity, PipelineOutput, SourceRecord
from app.services.ai.decision import judge_feed_entities, normalize_entity_state
from app.services.entities.entity_reconciler import reconcile_entities
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

TITLE_CONTEXT_KEYWORDS = {
    "approval",
    "approved",
    "callback",
    "compensation",
    "copy",
    "document",
    "documents",
    "fund",
    "funding",
    "letter",
    "paid",
    "payment",
    "receipt",
    "refund",
    "report",
    "sent",
    "statement",
}
TITLE_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:and|after|but)\b[^.;]{12,120}", re.IGNORECASE),
]
MAX_SUGGESTED_TITLE_CHARS = 120


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

    touched_entity_ids.update(reconcile_entities(database_path))

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


def rebuild_persistent_memory(database_path: str) -> list[str]:
    """Recompute entity memory from already-synced source records."""
    clear_derived_memory(database_path)
    return hydrate_persistent_memory(database_path)


def refresh_ai_suggestions_for_entities(database_path: str, entity_ids: list[str]) -> None:
    """Refresh cached AI judgments only for entities changed by the current sync cycle."""
    unique_entity_ids = sorted(set(entity_ids))

    if not unique_entity_ids:
        return

    entities = list_loaded_entities(database_path, unique_entity_ids)
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
    outcomes = get_latest_entity_outcomes(database_path, user_id="google-dev-user")
    outputs = [to_pipeline_output(database_path, entity, current_time, latest_outcome=outcomes.get(entity.entity.id)) for entity in entities]
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


def to_pipeline_output(database_path: str, entity: LoadedEntity, current_time: str, latest_outcome=None) -> PipelineOutput:
    """Turn one loaded entity into a visible feed item whenever enough context exists."""
    latest_record = entity.members[-1] if entity.members else None
    state = entity.state
    source_record_id = latest_record.id if latest_record is not None else None

    if latest_record is None:
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

    if state is None:
        pipeline_entity = to_pipeline_entity(entity, latest_record.source)
        attention_item = create_missing_state_attention_item(entity, latest_record)
        append_trace_record(
            database_path,
            stage="action_selection",
            user_id="local-user",
            entity_id=pipeline_entity.id,
            source_record_id=source_record_id,
            trace_id=pipeline_entity.id,
            input={
                "current_state": "open",
                "used_ai_suggestion": False,
                "reason": "missing_state_fallback",
            },
            output={
                "need_type": attention_item.need_type,
                "action_type": attention_item.action_type,
                "effort_level": attention_item.effort_level,
                "primary_action": attention_item.primary_action,
                "fallback_action": attention_item.fallback_action,
                "downgraded_from_hidden": False,
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
                "current_state": "open",
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

    pipeline_entity = to_pipeline_entity(entity, latest_record.source)
    normalized_state = normalize_entity_state(state.current_state)
    if latest_outcome is not None:
        if latest_outcome.outcome_type == "complete":
            pipeline_entity = pipeline_entity.model_copy(update={"current_state": "done", "lifecycle_state": "resolved"})
            return create_suppressed_output(pipeline_entity, "completed_by_user")
        if latest_outcome.outcome_type == "dismiss":
            pipeline_entity = pipeline_entity.model_copy(update={"lifecycle_state": "suppressed"})
            return create_suppressed_output(pipeline_entity, "dismissed_by_user")
        if latest_outcome.outcome_type == "snooze":
            if latest_outcome.snooze_until is None or latest_outcome.snooze_until > current_time:
                pipeline_entity = pipeline_entity.model_copy(
                    update={"due_at": latest_outcome.snooze_until, "lifecycle_state": "scheduled"}
                )
                return create_suppressed_output(pipeline_entity, "snoozed_by_user")

    if normalized_state == "done":
        pipeline_entity = pipeline_entity.model_copy(update={"lifecycle_state": "resolved"})
        return create_suppressed_output(pipeline_entity, "resolved_state")

    suggestion = get_usable_suggestion(entity)
    force_worth_knowing = normalized_state == "done" or (
        suggestion is not None and (
        not suggestion.suggested_visibility or suggestion.suggested_timing == "hidden"
        )
    )
    append_trace_record(
        database_path,
        stage="lifecycle_transition",
        user_id="local-user",
        entity_id=pipeline_entity.id,
        source_record_id=source_record_id,
        trace_id=pipeline_entity.id,
        input={"current_state": normalized_state},
        output={"lifecycle_state": pipeline_entity.lifecycle_state},
    )

    attention_item = (
        to_fallback_attention_item(entity, latest_record, current_time)
        if suggestion is None
        else to_suggested_attention_item(
            entity,
            latest_record,
            current_time,
            force_worth_knowing=force_worth_knowing,
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
            "current_state": normalized_state,
            "used_ai_suggestion": suggestion is not None,
        },
        output={
            "need_type": attention_item.need_type,
            "action_type": attention_item.action_type,
            "effort_level": attention_item.effort_level,
            "primary_action": attention_item.primary_action,
            "fallback_action": attention_item.fallback_action,
            "downgraded_from_hidden": force_worth_knowing,
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
            "current_state": normalized_state,
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


def create_missing_state_attention_item(entity: LoadedEntity, latest_record) -> AttentionItem:
    """Surface entities even when state derivation has not completed yet."""
    latest_title = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled"

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id="local-user",
        need_type="awareness",
        action_type="external",
        effort_level="quick",
        timing_band="later",
        action_confidence="low",
        primary_action="open",
        fallback_action="open",
        title=latest_title,
        why_this_is_here="This item was synced but its state is still being derived, so it is surfaced to avoid hiding it.",
        due_at=None,
        importance_level="low",
        lifecycle_state="active",
        current_state="open",
        source=latest_record.source,
        gmail_thread_id=resolve_gmail_thread_id(entity, latest_record.source),
        gmail_thread_action=resolve_gmail_thread_action(entity, latest_record),
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def to_pipeline_entity(entity: LoadedEntity, source: str | None) -> PipelineEntity:
    """Adapt the persistence-layer entity into the pipeline entity schema."""
    current_state = normalize_entity_state(entity.state.current_state if entity.state is not None else None)
    due_at = entity.state.due_at if entity.state is not None else None

    return PipelineEntity(
        id=entity.entity.id,
        group_id=entity.entity.id,
        user_id="local-user",
        source=source or "gmail",
        current_state=current_state,
        due_at=due_at,
        importance=current_state == "waiting",
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
        current_state=normalize_entity_state(entity.state.current_state),
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
        fragments.append(f"Current state: {normalize_entity_state(entity.state.current_state)}.")

    summary = " | ".join(fragments)
    return summary[:900].strip()


def normalize_judgment(entity: LoadedEntity, judgment: FeedEntityJudgmentOutput) -> dict[str, object] | None:
    """Validate and normalize AI judgment fields before writing them to SQLite."""
    title = enrich_title_with_explanation(
        normalize_text_field(judgment.title),
        normalize_text_field(judgment.explanation),
    )
    explanation = normalize_text_field(judgment.explanation)

    if not title or not explanation:
        return None

    return {
        "title": title,
        "explanation": explanation,
        "action": normalize_action(judgment.action, normalize_entity_state(entity.state.current_state if entity.state else None)),
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
    normalized_state = normalize_entity_state(state.current_state)
    primary_action = normalize_action(suggestion.action, normalized_state)
    timing_band = (
        "later"
        if force_worth_knowing
        else normalize_timing_band(suggestion.suggested_timing, entity, current_time, latest_record.source)
    )
    importance_level = (
        "low" if force_worth_knowing else to_importance_level(suggestion.suggested_priority, normalized_state)
    )
    action_confidence = (
        "low"
        if force_worth_knowing and primary_action == "none"
        else to_action_confidence(normalized_state, primary_action, suggestion.suggested_priority)
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
        lifecycle_state=to_lifecycle_state(normalized_state),
        current_state=normalized_state,
        source=latest_record.source,
        gmail_thread_id=resolve_gmail_thread_id(entity, latest_record.source),
        gmail_thread_action=resolve_gmail_thread_action(entity, latest_record),
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def to_fallback_attention_item(entity: LoadedEntity, latest_record, current_time: str) -> AttentionItem:
    """Produce a deterministic fallback item when AI judgment is missing or stale."""
    state = entity.state
    assert state is not None
    normalized_state = normalize_entity_state(state.current_state)
    primary_action = to_fallback_action(normalized_state)
    latest_title = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled"

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id="local-user",
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=derive_fallback_timing_band(normalized_state, state.due_at, current_time, latest_record.source),
        action_confidence=to_action_confidence(normalized_state, primary_action, 50),
        primary_action=primary_action,
        fallback_action="open",
        title=latest_title if normalized_state == "waiting" else f"Open: {latest_title}",
        why_this_is_here=to_fallback_explanation(normalized_state, latest_title),
        due_at=state.due_at,
        importance_level=to_importance_level(50, normalized_state),
        lifecycle_state=to_lifecycle_state(normalized_state),
        current_state=normalized_state,
        source=latest_record.source,
        gmail_thread_id=resolve_gmail_thread_id(entity, latest_record.source),
        gmail_thread_action=resolve_gmail_thread_action(entity, latest_record),
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def collect_participants(raw_payload: dict[str, object]) -> list[str]:
    """Read participant-like arrays from either calendar or email payload fields."""
    values = raw_payload.get("participants") or raw_payload.get("attendees")

    if not isinstance(values, list):
        return []

    return [str(value).strip() for value in values if isinstance(value, str) and value.strip()]


def resolve_gmail_thread_id(entity: LoadedEntity, source: str | None) -> str | None:
    """Return a concrete Gmail thread id only when the entity maps to exactly one Gmail thread."""
    if source != "gmail":
        return None

    thread_ids = sorted(
        {
            membership.thread_id
            for membership in entity.thread_memberships
            if membership.source == "gmail" and membership.thread_id
        }
    )

    if len(thread_ids) != 1:
        return None

    return thread_ids[0]


def resolve_gmail_thread_action(_entity: LoadedEntity, _latest_record) -> str | None:
    """Keep Gmail mutations out of the compact backend feed projection."""
    return None


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


def enrich_title_with_explanation(title: str, explanation: str) -> str:
    """Carry over one missing high-value clause from the explanation when the title is too narrow."""
    if not title or not explanation:
        return title

    lower_title = title.lower()

    for pattern in TITLE_CONTEXT_PATTERNS:
        for match in pattern.finditer(explanation):
            clause = normalize_text_field(match.group(0)).rstrip(".,;: ")
            clause = re.split(r",|;|\bso\b", clause, maxsplit=1, flags=re.IGNORECASE)[0].rstrip(".,;: ")
            lower_clause = clause.lower()

            if lower_clause in lower_title:
                continue
            if not any(keyword in lower_clause for keyword in TITLE_CONTEXT_KEYWORDS):
                continue

            candidate = normalize_text_field(f"{title.rstrip('.')} {clause}").rstrip(".")

            if len(candidate) <= MAX_SUGGESTED_TITLE_CHARS:
                return f"{candidate}."

    return title


def to_fallback_action(current_state: str) -> str:
    """Map derived state to the safest default action when AI is absent."""
    normalized_state = normalize_entity_state(current_state)

    if normalized_state in {"waiting", "done"}:
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
    current_state = normalize_entity_state(state.current_state)
    fallback = derive_fallback_timing_band(
        current_state,
        state.due_at,
        current_time or datetime.now(timezone.utc).isoformat(),
        source or (entity.members[0].source if entity.members else "gmail"),
    )

    if normalized not in {"now", "today", "later", "hidden"}:
        return fallback
    if normalized == "hidden":
        return fallback
    if current_state == "waiting" and normalized == "later":
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
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "done":
        return "later"

    if due_at is None:
        return "today" if source == "calendar" or normalized_state == "waiting" else "later"

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
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "waiting":
        return f"This is still waiting on the other side for {title}."
    if normalized_state == "done":
        return f"This was already completed for {title}."
    return f"This still needs attention for {title}."


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
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "waiting" or priority >= 80:
        return "high"
    if normalized_state == "open" or priority >= 50:
        return "medium"
    return "low"


def to_action_confidence(current_state: str, primary_action: str, priority: int) -> str:
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "waiting" or primary_action == "reply":
        return "high"
    if priority >= 60:
        return "medium"
    return "low"


def to_lifecycle_state(current_state: str) -> str:
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "done":
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
