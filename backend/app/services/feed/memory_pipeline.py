from __future__ import annotations

"""Persisted-memory pipeline that hydrates entities and produces the feed."""

from collections.abc import Callable
from datetime import datetime, timezone
import re

from app.db.models import LoadedEntity
from app.db.repository import (
    DEFAULT_USER_ID,
    append_trace_record,
    clear_derived_memory,
    get_feed_projection_count,
    get_entity_member_count,
    get_latest_entity_outcomes,
    get_source_record_count,
    list_entity_member_source_records_lightweight,
    list_feed_projection_payloads,
    list_all_loaded_entities,
    list_loaded_entities,
    list_entities_missing_state_ids,
    list_unlinked_source_records,
    upsert_ai_suggestion,
    upsert_feed_projection,
)
from app.schemas.ai import FeedEntityContextInput, FeedEntityJudgmentOutput
from app.schemas.domain import AttentionItem, AttentionItemDetail, FeedResponse, PipelineEntity, PipelineOutput, SourceRecord
from app.services.ai.decision import judge_feed_entities, normalize_entity_state
from app.services.copy_quality import humanize_account_copy, infer_account_emails_from_records
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
AI_JUDGMENT_MODEL = "ai-judgment-v3"
PERSISTENT_SUGGESTION_MODELS = {AI_JUDGMENT_MODEL, "manual-task"}
FEED_PROJECTION_VERSION = "feed-projection-v4"
RECENT_ACTION_WINDOW_DAYS = 90
CONCRETE_ACTIONS = {"reply", "confirm", "pay", "join", "review", "send", "approve", "register", "track"}
URL_PATTERN = re.compile(r"https?://[^\s<>)\"']+", re.IGNORECASE)
ACTION_LANGUAGE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bplease\s+(?:reply|respond|review|confirm|approve|send|share|upload|pay|complete|submit)\b",
        r"\b(?:reply|respond|review|confirm|approve|send|share|upload|pay|complete|submit)\s+(?:by|before|today|within)\b",
        r"\b(?:can|could|would)\s+you\s+(?:reply|respond|review|confirm|approve|send|share|upload|pay|complete|submit|provide)\b",
        r"\b(?:need|needs|needed|requires|requesting)\s+(?:your\s+)?(?:reply|response|review|confirmation|approval|payment|documents?|details?|input)\b",
        r"\b(?:action required|required action|payment due|bill due|due today|overdue|expires today)\b",
        r"\b(?:kindly|please)\s+(?:provide|submit|send|share|confirm|make payment)\b",
    ]
]
ACTION_INFERENCE_PATTERNS = [
    ("pay", re.compile(r"\b(?:pay|payment due|bill due|invoice due|make payment|autopay)\b", re.IGNORECASE)),
    ("reply", re.compile(r"\b(?:reply|respond|response needed|get back to us)\b", re.IGNORECASE)),
    ("confirm", re.compile(r"\b(?:confirm|rsvp|accept or decline|accept|decline)\b", re.IGNORECASE)),
    ("approve", re.compile(r"\b(?:approve|approval required|approval needed)\b", re.IGNORECASE)),
    ("send", re.compile(r"\b(?:send|share|upload|submit|provide)\s+(?:the\s+)?(?:document|documents|file|files|details|information|screenshot|form)\b", re.IGNORECASE)),
    ("review", re.compile(r"\b(?:review|check|verify)\b", re.IGNORECASE)),
    ("register", re.compile(r"\b(?:register|sign up|complete registration)\b", re.IGNORECASE)),
    ("join", re.compile(r"\b(?:join|attend)\b", re.IGNORECASE)),
    ("track", re.compile(r"\b(?:track|check status|view status)\b", re.IGNORECASE)),
]
PASSIVE_STATUS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:processed|processed successfully|completed|closed|resolved|delivered|shipped)\b",
        r"\b(?:confirmed|accepted|approved|registered|acknowledged|received)\b",
        r"\b(?:receipt|statement|intimation|confirmation|notification|update)\b",
        r"\b(?:no action required|no further action|for your information|fyi)\b",
    ]
]
IMPORTANT_STATUS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:itr|income tax|tax return|intimation)\b",
        r"\b(?:refund|payment|bill|statement|approval|approved|processed|completed|closed)\b",
        r"\b(?:account|card|bank|rbi|zerodha|demat|application)\b",
    ]
]
URGENT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:urgent|immediately|asap|overdue|expires today|due today|by today|within 24 hours)\b",
        r"\bsecurity\b",
    ]
]


def hydrate_persistent_memory(
    database_path: str,
    source_records: list[SourceRecord] | None = None,
    *,
    include_unlinked: bool = False,
    user_id: str = DEFAULT_USER_ID,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Resolve records to entities, backfill missing links, and derive state."""
    touched_entity_ids: set[str] = set()
    explicit_source_records = source_records is not None
    ordered_records = sorted(source_records or [], key=lambda record: record.received_at)
    unlinked_records = list(list_unlinked_source_records(database_path, user_id=user_id)) if include_unlinked or not explicit_source_records else []
    total_records_to_link = len(ordered_records) + len(unlinked_records)
    linked_records = 0

    def report_progress() -> None:
        if progress_callback is not None:
            progress_callback(linked_records, total_records_to_link)

    for record in ordered_records:
        entity, _ = resolve_entity_for_record(database_path, record)
        touched_entity_ids.add(entity.id)
        linked_records += 1
        if linked_records % 5 == 0 or linked_records == total_records_to_link:
            report_progress()

    if include_unlinked or not explicit_source_records:
        for record in unlinked_records:
            source_record = SourceRecord(
                id=record.id,
                user_id=user_id,
                source=record.source,
                thread_id=record.thread_id or "",
                raw_payload=record.raw_payload,
                received_at=record.timestamp,
            )
            entity, _ = resolve_entity_for_record(database_path, source_record)
            touched_entity_ids.add(entity.id)
            linked_records += 1
            if linked_records % 5 == 0 or linked_records == total_records_to_link:
                report_progress()

        for entity_id in list_entities_missing_state_ids(database_path, user_id=user_id):
            touched_entity_ids.add(entity_id)

        touched_entity_ids.update(reconcile_entities(database_path, user_id=user_id))

    state_entity_ids = sorted(touched_entity_ids)
    total_state_work = total_records_to_link + len(state_entity_ids)
    for index, entity_id in enumerate(state_entity_ids, start=1):
        members = list_entity_member_source_records_lightweight(database_path, entity_id, user_id=user_id)

        if not members:
            continue

        derive_and_store_entity_state(database_path, entity_id, members, user_id=user_id)
        if progress_callback is not None and (index % 5 == 0 or index == len(state_entity_ids)):
            progress_callback(linked_records + index, total_state_work)

    report_progress()

    total_records = get_source_record_count(database_path, user_id=user_id)
    total_members = get_entity_member_count(database_path)

    # Every source record should belong to exactly one entity member row.
    if total_records != total_members:
        print("DATA LOSS DETECTED", {"totalRecords": total_records, "totalMembers": total_members})

    return sorted(touched_entity_ids)


def rebuild_persistent_memory(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Recompute entity memory from already-synced source records."""
    clear_derived_memory(database_path, user_id=user_id)
    return hydrate_persistent_memory(database_path, user_id=user_id)


def refresh_ai_suggestions_for_entities(
    database_path: str,
    entity_ids: list[str],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    """Refresh cached AI judgments only for entities changed by the current sync cycle."""
    unique_entity_ids = sorted(set(entity_ids))

    if not unique_entity_ids:
        return

    entities = list_loaded_entities(database_path, unique_entity_ids, user_id=user_id)
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
                user_id=user_id,
                entity_id=entity.entity.id,
                source_record_id=entity.members[-1].id if entity.members else None,
                trace_id=entity.entity.id,
                input=context.model_dump(),
                output=normalized,
            )

        upsert_ai_suggestion(database_path, entity_id=entity.entity.id, **normalized)


def refresh_feed_projections_for_entities(
    database_path: str,
    entity_ids: list[str],
    current_time: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    record_trace: bool = True,
) -> None:
    """Refresh cached feed projections only for entities touched by the current change."""
    unique_entity_ids = sorted(set(entity_ids))

    if not unique_entity_ids:
        return

    entities = list_loaded_entities(database_path, unique_entity_ids, user_id=user_id)
    outcomes = get_latest_entity_outcomes(database_path, user_id=user_id)

    for entity in entities:
        output = to_pipeline_output(
            database_path,
            entity,
            current_time,
            latest_outcome=outcomes.get(entity.entity.id),
            user_id=user_id,
            record_trace=record_trace,
        )
        upsert_feed_projection(
            database_path,
            user_id=user_id,
            entity_id=entity.entity.id,
            pipeline_output=versioned_feed_projection_payload(output),
        )


def build_feed_from_projection_cache(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> FeedResponse | None:
    """Build the feed from persisted per-entity projections when the cache is populated."""
    if get_feed_projection_count(database_path, user_id=user_id) == 0:
        return None

    payloads = list_feed_projection_payloads(database_path, user_id=user_id)
    if not payloads or any(not is_current_feed_projection_payload(payload) for payload in payloads):
        return None

    outputs = [PipelineOutput.model_validate(payload) for payload in payloads]
    return build_feed(outputs)


def versioned_feed_projection_payload(output: PipelineOutput) -> dict[str, object]:
    payload = output.model_dump()
    payload["projection_version"] = FEED_PROJECTION_VERSION
    return payload


def is_current_feed_projection_payload(payload: dict[str, object]) -> bool:
    return payload.get("projection_version") == FEED_PROJECTION_VERSION


def list_stale_feed_projection_entity_ids(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Return entities whose cached feed projection predates the current user-facing copy contract."""
    stale_entity_ids: set[str] = set()

    for payload in list_feed_projection_payloads(database_path, user_id=user_id):
        if is_current_feed_projection_payload(payload):
            continue

        entity = payload.get("entity")
        if isinstance(entity, dict):
            entity_id = entity.get("id")
            if isinstance(entity_id, str) and entity_id:
                stale_entity_ids.add(entity_id)
                continue

        attention_item = payload.get("attention_item")
        if isinstance(attention_item, dict):
            entity_id = attention_item.get("entity_id")
            if isinstance(entity_id, str) and entity_id:
                stale_entity_ids.add(entity_id)

    return sorted(stale_entity_ids)


def list_entity_ids_needing_ai_refresh(database_path: str, *, user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Return persisted entities whose AI judgment cache is missing or from an old copy model."""
    return sorted(
        entity.entity.id
        for entity in list_all_loaded_entities(database_path, user_id=user_id)
        if entity.state is not None and get_usable_suggestion(entity) is None
    )


def build_feed_from_entities(
    database_path: str,
    current_time: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    record_trace: bool = True,
) -> FeedResponse:
    """Convert all loaded entities into pipeline outputs and then section them into a feed."""
    entities = list_all_loaded_entities(database_path, user_id=user_id)
    outcomes = get_latest_entity_outcomes(database_path, user_id=user_id)
    outputs = [
        to_pipeline_output(
            database_path,
            entity,
            current_time,
            latest_outcome=outcomes.get(entity.entity.id),
            user_id=user_id,
            record_trace=record_trace,
        )
        for entity in entities
    ]
    feed = build_feed(outputs, database_path if record_trace else None)
    source_records = get_source_record_count(database_path, user_id=user_id)
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


def to_pipeline_output(
    database_path: str,
    entity: LoadedEntity,
    current_time: str,
    latest_outcome=None,
    *,
    user_id: str = DEFAULT_USER_ID,
    record_trace: bool = True,
) -> PipelineOutput:
    """Turn one loaded entity into a visible feed item whenever enough context exists."""
    latest_record = entity.members[-1] if entity.members else None
    state = entity.state
    source_record_id = latest_record.id if latest_record is not None else None

    if latest_record is None:
        pipeline_entity = to_pipeline_entity(entity, None, user_id=user_id)
        if record_trace:
            append_trace_record(
                database_path,
                stage="output",
                user_id=user_id,
                entity_id=pipeline_entity.id,
                source_record_id=source_record_id,
                trace_id=pipeline_entity.id,
                input={"reason": "missing_state"},
                output={"surfaced": False, "suppressed": True, "suppression_reason": "missing_state"},
            )
        return create_suppressed_output(pipeline_entity, "missing_state")

    if state is None:
        pipeline_entity = to_pipeline_entity(entity, latest_record.source, user_id=user_id)
        attention_item = create_missing_state_attention_item(entity, latest_record, current_time, user_id=user_id)
        if should_suppress_attention_item(entity, latest_record, attention_item, current_time):
            return create_suppressed_output(pipeline_entity, "missing_state_not_actionable")
        if record_trace:
            append_trace_record(
                database_path,
                stage="action_selection",
                user_id=user_id,
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
                user_id=user_id,
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

    pipeline_entity = to_pipeline_entity(entity, latest_record.source, user_id=user_id)
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
        if should_surface_worth_knowing_status(entity, latest_record, current_time):
            return PipelineOutput(
                entity=pipeline_entity,
                attention_item=create_status_attention_item(entity, latest_record, normalized_state, user_id=user_id),
                suppressed=False,
                suppression_reason=None,
            )
        return create_suppressed_output(pipeline_entity, "resolved_state")

    suggestion = get_usable_suggestion(entity)
    if suggestion is not None and (not suggestion.suggested_visibility or suggestion.suggested_timing == "hidden"):
        if should_surface_worth_knowing_status(entity, latest_record, current_time):
            return PipelineOutput(
                entity=pipeline_entity,
                attention_item=create_status_attention_item(entity, latest_record, normalized_state, user_id=user_id),
                suppressed=False,
                suppression_reason=None,
            )
        return create_suppressed_output(pipeline_entity, "hidden_by_feed_judgment")

    force_worth_knowing = False
    if record_trace:
        append_trace_record(
            database_path,
            stage="lifecycle_transition",
            user_id=user_id,
            entity_id=pipeline_entity.id,
            source_record_id=source_record_id,
            trace_id=pipeline_entity.id,
            input={"current_state": normalized_state},
            output={"lifecycle_state": pipeline_entity.lifecycle_state},
        )

    attention_item = (
        to_fallback_attention_item(entity, latest_record, current_time, user_id=user_id)
        if suggestion is None
        else to_suggested_attention_item(
            entity,
            latest_record,
            current_time,
            user_id=user_id,
            force_worth_knowing=force_worth_knowing,
        )
    )
    if should_suppress_attention_item(entity, latest_record, attention_item, current_time):
        return create_suppressed_output(pipeline_entity, "not_actionable_or_worth_knowing")

    if record_trace:
        append_trace_record(
            database_path,
            stage="action_selection",
            user_id=user_id,
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
            user_id=user_id,
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


def create_status_attention_item(
    entity: LoadedEntity,
    latest_record,
    current_state: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> AttentionItem:
    """Surface important passive status as Worth Knowing instead of a to-do."""
    title = compact_dashboard_title(status_title(entity, latest_record), "none", latest_record)
    detail = build_attention_detail_for_entity(entity, latest_record, "none", current_state)

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id=user_id,
        need_type="awareness",
        action_type="none",
        effort_level="quick",
        timing_band="later",
        action_confidence="low",
        primary_action="none",
        fallback_action="open",
        title=title,
        why_this_is_here=detail.body[0] if detail.body else title,
        detail=detail,
        due_at=None,
        importance_level="low",
        lifecycle_state="active",
        current_state=normalize_entity_state(current_state),
        source=latest_record.source,
        gmail_thread_id=resolve_gmail_thread_id(entity, latest_record.source),
        gmail_thread_action=None,
        trace_id=entity.entity.id,
        created_at=latest_record.timestamp,
    )


def create_missing_state_attention_item(
    entity: LoadedEntity,
    latest_record,
    current_time: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> AttentionItem:
    """Surface entities even when state derivation has not completed yet."""
    latest_title = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled"
    primary_action = refine_primary_action(entity, latest_record, "open", current_time)
    timing_band = enforce_dashboard_timing(entity, latest_record, primary_action, "later", current_time)
    detail = build_attention_detail_for_entity(entity, latest_record, primary_action, "open")

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id=user_id,
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=timing_band,
        action_confidence="low" if primary_action == "none" else "medium",
        primary_action=primary_action,
        fallback_action="open",
        title=compact_dashboard_title(latest_title, primary_action, latest_record),
        why_this_is_here=detail.body[0] if detail.body else record_detail_sentence(latest_record),
        detail=detail,
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


def to_pipeline_entity(entity: LoadedEntity, source: str | None, *, user_id: str = DEFAULT_USER_ID) -> PipelineEntity:
    """Adapt the persistence-layer entity into the pipeline entity schema."""
    current_state = normalize_entity_state(entity.state.current_state if entity.state is not None else None)
    due_at = entity.state.due_at if entity.state is not None else None

    return PipelineEntity(
        id=entity.entity.id,
        group_id=entity.entity.id,
        user_id=user_id,
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
        "model": AI_JUDGMENT_MODEL,
        "generated_from_updated_at": get_entity_context_updated_at(entity),
    }


def get_usable_suggestion(entity: LoadedEntity):
    """Ignore cached AI suggestions once entity context has moved past them."""
    if entity.ai_suggestion is None:
        return None

    if entity.ai_suggestion.model not in PERSISTENT_SUGGESTION_MODELS:
        return None

    if entity.ai_suggestion.generated_from_updated_at < get_entity_context_updated_at(entity):
        return None

    return entity.ai_suggestion


def to_suggested_attention_item(
    entity: LoadedEntity,
    latest_record,
    current_time: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    force_worth_knowing: bool = False,
) -> AttentionItem:
    """Build a feed item from the cached AI suggestion when it is still valid."""
    state = entity.state
    suggestion = entity.ai_suggestion
    assert state is not None
    assert suggestion is not None
    normalized_state = normalize_entity_state(state.current_state)
    primary_action = normalize_action(suggestion.action, normalized_state)
    primary_action = refine_primary_action(entity, latest_record, primary_action, current_time)
    account_emails = infer_account_emails_from_records(entity.members)
    timing_band = (
        "later"
        if force_worth_knowing
        else normalize_timing_band(suggestion.suggested_timing, entity, current_time, latest_record.source)
    )
    timing_band = enforce_dashboard_timing(
        entity,
        latest_record,
        primary_action,
        timing_band,
        current_time,
    )
    importance_level = (
        "low" if force_worth_knowing else to_importance_level(suggestion.suggested_priority, normalized_state)
    )
    if primary_action == "none" or timing_band == "later":
        importance_level = "low" if primary_action == "none" else importance_level
    action_confidence = (
        "low"
        if force_worth_knowing and primary_action == "none"
        else to_action_confidence(normalized_state, primary_action, suggestion.suggested_priority)
    )
    title = humanize_account_copy(suggestion.title, record=latest_record, account_emails=account_emails) or suggestion.title
    explanation = (
        humanize_account_copy(
            suggestion.explanation,
            record=latest_record,
            account_emails=account_emails,
        )
        or suggestion.explanation
    )
    if is_synthetic_explanation(explanation):
        explanation = record_detail_sentence(latest_record)
    detail = build_attention_detail_for_entity(entity, latest_record, primary_action, normalized_state, explanation)

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id=user_id,
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=timing_band,
        action_confidence=action_confidence,
        primary_action=primary_action,
        fallback_action="open",
        title=compact_dashboard_title(title, primary_action, latest_record),
        why_this_is_here=detail.body[0] if detail.body else explanation,
        detail=detail,
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


def to_fallback_attention_item(
    entity: LoadedEntity,
    latest_record,
    current_time: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> AttentionItem:
    """Produce a deterministic fallback item when AI judgment is missing or stale."""
    state = entity.state
    assert state is not None
    normalized_state = normalize_entity_state(state.current_state)
    primary_action = to_fallback_action(normalized_state)
    primary_action = refine_primary_action(entity, latest_record, primary_action, current_time)
    latest_title = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Untitled"
    latest_title = (
        humanize_account_copy(
            latest_title,
            record=latest_record,
            account_emails=infer_account_emails_from_records(entity.members),
        )
        or latest_title
    )

    timing_band = derive_fallback_timing_band(normalized_state, state.due_at, current_time, latest_record.source)
    timing_band = enforce_dashboard_timing(entity, latest_record, primary_action, timing_band, current_time)
    detail = build_attention_detail_for_entity(entity, latest_record, primary_action, normalized_state)

    return AttentionItem(
        id=entity.entity.id,
        entity_id=entity.entity.id,
        user_id=user_id,
        need_type="awareness" if primary_action == "none" else "decision",
        action_type=to_action_type(primary_action),
        effort_level=to_effort_level(primary_action),
        timing_band=timing_band,
        action_confidence=to_action_confidence(normalized_state, primary_action, 50),
        primary_action=primary_action,
        fallback_action="open",
        title=compact_dashboard_title(latest_title, primary_action, latest_record),
        why_this_is_here=detail.body[0] if detail.body else to_fallback_explanation(normalized_state, latest_title),
        detail=detail,
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


def refine_primary_action(entity: LoadedEntity, latest_record, primary_action: str, current_time: str) -> str:
    """Keep only concrete email actions in the to-do sections."""
    if latest_record.source != "gmail":
        return primary_action

    text = entity_text(entity)

    if is_passive_status_text(text):
        return "none"

    if not is_recent_record(latest_record.timestamp, current_time):
        return "none"

    inferred_action = infer_concrete_action(text)
    if inferred_action is not None:
        return inferred_action

    if primary_action == "pay" and ("pay" in text or "payment" in text or "bill" in text or extract_action_url(entity, "pay")):
        return "pay"

    if primary_action in CONCRETE_ACTIONS and has_action_required_language(text):
        return primary_action

    if primary_action == "reply" and re.search(r"\b(?:reply|respond|response needed)\b", text, re.IGNORECASE):
        return "reply"

    if primary_action == "confirm" and re.search(r"\b(?:confirm|rsvp|accept|decline)\b", text, re.IGNORECASE):
        return "confirm"

    return "none"


def infer_concrete_action(text: str) -> str | None:
    """Infer the action the user must perform from actual email wording."""
    if not has_action_required_language(text):
        return None

    for action, pattern in ACTION_INFERENCE_PATTERNS:
        if pattern.search(text):
            return action

    return "review"


def enforce_dashboard_timing(
    entity: LoadedEntity,
    latest_record,
    primary_action: str,
    timing_band: str,
    current_time: str,
) -> str:
    """Apply product-level bucket semantics after action selection."""
    if latest_record.source != "gmail":
        return timing_band

    if primary_action == "none":
        return "later"

    if not is_recent_record(latest_record.timestamp, current_time):
        return "later"

    text = entity_text(entity)
    due_at = entity.state.due_at if entity.state is not None else None
    due_delta = seconds_until(due_at, current_time)

    if due_delta is not None:
        if due_delta <= 4 * 60 * 60:
            return "now"
        if due_delta <= 24 * 60 * 60:
            return "today"
        return "later"

    if any(pattern.search(text) for pattern in URGENT_PATTERNS):
        return "now"

    return "today" if timing_band in {"now", "today"} else "later"


def should_surface_worth_knowing_status(entity: LoadedEntity, latest_record, current_time: str) -> bool:
    """Keep recent important passive statuses visible without turning them into work."""
    if latest_record.source != "gmail" or not is_recent_record(latest_record.timestamp, current_time):
        return False

    text = entity_text(entity)
    return is_passive_status_text(text) and any(pattern.search(text) for pattern in IMPORTANT_STATUS_PATTERNS)


def should_suppress_attention_item(
    entity: LoadedEntity,
    latest_record,
    attention_item: AttentionItem,
    current_time: str,
) -> bool:
    """Drop Gmail rows that are neither a concrete task nor important context."""
    if latest_record.source != "gmail":
        return False

    if attention_item.primary_action != "none":
        return False

    return not should_surface_worth_knowing_status(entity, latest_record, current_time)


def is_recent_record(timestamp: str, current_time: str, *, days: int = RECENT_ACTION_WINDOW_DAYS) -> bool:
    record_time = parse_datetime(timestamp)
    reference = parse_datetime(current_time)

    if record_time is None or reference is None:
        return False

    return 0 <= (reference - record_time).total_seconds() <= days * 24 * 60 * 60


def seconds_until(due_at: str | None, current_time: str) -> float | None:
    if due_at is None:
        return None

    due_time = parse_datetime(due_at)
    reference = parse_datetime(current_time)

    if due_time is None or reference is None:
        return None

    return (due_time - reference).total_seconds()


def parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def entity_text(entity: LoadedEntity) -> str:
    return " ".join(
        f"{payload_string(record.raw_payload, 'subject') or record.subject or ''} {payload_string(record.raw_payload, 'body') or ''}"
        for record in entity.members
    ).lower()


def is_passive_status_text(text: str) -> bool:
    if has_action_required_language(text):
        return False

    return any(pattern.search(text) for pattern in PASSIVE_STATUS_PATTERNS)


def has_action_required_language(text: str) -> bool:
    return any(pattern.search(text) for pattern in ACTION_LANGUAGE_PATTERNS)


def build_attention_detail_for_entity(
    entity: LoadedEntity,
    latest_record,
    primary_action: str,
    current_state: str,
    explanation: str | None = None,
) -> AttentionItemDetail:
    """Build task details from actual email content and source coverage."""
    action_url = extract_action_url(entity, primary_action)
    body = detail_body_lines(entity, latest_record, explanation)

    return AttentionItemDetail(
        body=body,
        action_label=detail_action_label(primary_action, current_state, action_url),
        action_url=action_url,
        source_label=detail_source_label(entity, latest_record.source),
    )


def detail_body_lines(entity: LoadedEntity, latest_record, explanation: str | None) -> list[str]:
    lines: list[str] = []
    clean_explanation = normalize_detail_sentence(explanation or "")

    if clean_explanation and not is_synthetic_explanation(clean_explanation):
        lines.append(clean_explanation)

    latest_sentence = record_detail_sentence(latest_record)
    if latest_sentence and latest_sentence not in lines:
        lines.append(latest_sentence)

    if len(entity.members) > 1:
        previous = record_detail_sentence(entity.members[-2])
        if previous and previous not in lines:
            lines.append(previous)

    return lines[:2] or ["Open the source email for the full context."]


def record_detail_sentence(record) -> str:
    subject = payload_string(record.raw_payload, "subject") or record.subject or "Email"
    body = payload_string(record.raw_payload, "body") or payload_string(record.raw_payload, "snippet") or ""
    text = normalize_detail_sentence(first_meaningful_sentence(body))

    if text:
        return text

    return normalize_detail_sentence(subject)


def first_meaningful_sentence(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    for part in parts:
        candidate = part.strip()
        if len(candidate) >= 20 and not candidate.lower().startswith(("dear ", "hello ", "hi ")):
            return candidate

    return parts[0].strip() if parts else cleaned


def normalize_detail_sentence(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    cleaned = cleaned.replace("This is still waiting on the other side for ", "")
    cleaned = cleaned.replace("This still needs attention for ", "")
    cleaned = cleaned.replace("This still needs a decision for ", "")
    cleaned = cleaned.rstrip(" .")

    if len(cleaned) > 220:
        cleaned = f"{cleaned[:217].rstrip()}..."

    if cleaned and cleaned[-1] not in ".!?":
        cleaned = f"{cleaned}."

    return cleaned


def is_synthetic_explanation(value: str) -> bool:
    lowered = value.lower()
    return any(
        phrase in lowered
        for phrase in [
            "waiting on the other side",
            "still needs attention",
            "still needs a decision",
            "part of your mailbox work",
        ]
    )


def detail_action_label(primary_action: str, current_state: str, action_url: str | None) -> str:
    if primary_action == "pay":
        return "Pay bill" if action_url else "Pay"
    if primary_action == "reply":
        return "Reply"
    if primary_action == "confirm":
        return "Confirm"
    if primary_action == "track":
        return "Track status"
    if primary_action == "review":
        return "Review"
    if primary_action == "send":
        return "Send"
    if primary_action == "approve":
        return "Approve"
    if primary_action == "register":
        return "Register"
    if primary_action == "join":
        return "Join"
    if primary_action == "open":
        return "Read"
    if normalize_entity_state(current_state) == "waiting":
        return "No action needed right now"
    return "No action needed"


def detail_source_label(entity: LoadedEntity, source: str | None) -> str:
    if source == "calendar":
        return "Calendar"
    if source == "manual":
        return "Manual"

    count = sum(1 for record in entity.members if record.source == "gmail")
    return f"Gmail · {count} {'email' if count == 1 else 'emails'}"


def extract_action_url(entity: LoadedEntity, primary_action: str) -> str | None:
    urls: list[str] = []
    for record in reversed(entity.members):
        body = payload_string(record.raw_payload, "body") or ""
        subject = payload_string(record.raw_payload, "subject") or record.subject or ""
        urls.extend(extract_urls(f"{subject} {body}"))

    if not urls:
        return None

    if primary_action == "pay":
        for url in urls:
            if any(token in url.lower() for token in ["pay", "payment", "bill", "invoice"]):
                return url

    return urls[0]


def extract_urls(value: str) -> list[str]:
    urls: list[str] = []
    for match in URL_PATTERN.finditer(value):
        url = match.group(0).rstrip(".,;:!?)\"]}'")
        if url not in urls:
            urls.append(url)
    return urls


def compact_dashboard_title(title: str, primary_action: str, latest_record) -> str:
    subject = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or title
    candidate = normalize_text_field(title or subject)
    candidate = re.sub(r"^(?:open|review|track)\s*[:\-]?\s+", "", candidate, flags=re.IGNORECASE)

    if len(candidate) > 96 or candidate.lower().startswith(("re:", "fw:", "fwd:")):
        candidate = normalize_text_field(subject)

    if primary_action == "pay" and not re.match(r"^pay\b", candidate, flags=re.IGNORECASE):
        candidate = f"Pay {candidate}"
    elif primary_action == "reply" and not re.match(r"^reply\b", candidate, flags=re.IGNORECASE):
        candidate = f"Reply to {candidate}"

    if len(candidate) > 96:
        candidate = f"{candidate[:93].rstrip()}..."

    return candidate.rstrip(" .")


def status_title(entity: LoadedEntity, latest_record) -> str:
    subject = payload_string(latest_record.raw_payload, "subject") or latest_record.subject or "Mailbox update"
    body = payload_string(latest_record.raw_payload, "body") or ""
    text = f"{subject} {body}".lower()

    if "itr" in text or "income tax" in text:
        return "Your ITR intimation was processed"
    if "refund" in text:
        return "Refund update received"
    if "approved" in text or "approval" in text:
        return "Approval update received"
    if "closed" in text or "completed" in text:
        return "Request completion update received"

    return subject


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
        return "hidden"
    if current_state == "waiting":
        return "later"
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
        return "today" if source == "calendar" else "later"

    due_timestamp = parse_iso(due_at)
    current_timestamp = parse_iso(current_time)

    if due_timestamp is None or current_timestamp is None:
        return "today"

    delta_seconds = due_timestamp - current_timestamp

    if delta_seconds <= 4 * 60 * 60:
        return "now"
    if delta_seconds <= 24 * 60 * 60:
        return "today"
    return "later"


def to_fallback_explanation(current_state: str, title: str) -> str:
    """Generate rule-based explanation copy when no AI explanation exists."""
    normalized_state = normalize_entity_state(current_state)

    if normalized_state == "waiting":
        return f"Latest email says no action is needed yet for {title}."
    if normalized_state == "done":
        return f"Latest email says {title} is complete."
    return f"Latest email asks you to act on {title}."


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

    if priority >= 80:
        return "high"
    if normalized_state == "open" or priority >= 50:
        return "medium"
    return "low"


def to_action_confidence(current_state: str, primary_action: str, priority: int) -> str:
    normalized_state = normalize_entity_state(current_state)

    if primary_action == "none":
        return "low"
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
