from __future__ import annotations

"""Final feed projection, filtering, deduplication, and ranking logic."""

from app.db.repository import append_trace_record
from app.schemas.domain import AttentionItem, FeedResponse, PipelineOutput


# Higher priority means a section or item should be surfaced earlier.
SECTION_PRIORITY = {"now": 0, "today": 1, "worth_knowing": 2}
TIMING_SCORE = {"now": 400, "today": 250, "later": 100, "hidden": -1000}
IMPORTANCE_SCORE = {"high": 90, "medium": 50, "low": 10}
CONFIDENCE_SCORE = {"high": 30, "medium": 18, "low": 0}
EFFORT_SCORE = {"quick": 8, "deep": 2}
LIFECYCLE_SCORE = {"active": 12, "scheduled": 8, "resolved": -1000, "suppressed": -1000}
BLOCKING_STATES = {"awaiting_reply", "awaiting_rsvp", "awaiting_payment"}


def build_feed(outputs: list[PipelineOutput], database_path: str | None = None) -> FeedResponse:
    """Project per-entity outputs into sectioned feed arrays."""
    selected_by_entity_id: dict[str, tuple[PipelineOutput, AttentionItem, str]] = {}

    for output in outputs:
        candidate = to_feed_candidate(output)

        if candidate is None:
            continue

        _, attention_item, section = candidate
        existing = selected_by_entity_id.get(attention_item.entity_id)

        if existing is None or should_replace_candidate(existing, candidate):
            selected_by_entity_id[attention_item.entity_id] = candidate

    feed = FeedResponse()

    for output, attention_item, section in selected_by_entity_id.values():
        feed_item = enrich_attention_item(output, attention_item)
        feed.__getattribute__(section).append(feed_item)

    feed.now.sort(key=lambda item: sort_key(selected_by_entity_id[item.entity_id]))
    feed.today.sort(key=lambda item: sort_key(selected_by_entity_id[item.entity_id]))
    feed.worth_knowing.sort(key=lambda item: sort_key(selected_by_entity_id[item.entity_id]))

    if database_path is not None:
        log_feed_ranking(database_path, selected_by_entity_id, feed)

    return feed


def to_feed_candidate(output: PipelineOutput) -> tuple[PipelineOutput, AttentionItem, str] | None:
    """Drop suppressed, hidden, and resolved items before ranking."""
    attention_item = output.attention_item

    if attention_item is None or output.suppressed:
        return None

    lifecycle_state = attention_item.lifecycle_state or output.entity.lifecycle_state

    if lifecycle_state == "resolved" or attention_item.timing_band == "hidden":
        return None
    if attention_item.current_state == "resolved":
        return None

    return output, attention_item, map_timing_band_to_section(attention_item.timing_band)


def map_timing_band_to_section(timing_band: str) -> str:
    """Translate pipeline timing bands into frontend feed sections."""
    if timing_band == "now":
        return "now"
    if timing_band == "today":
        return "today"
    return "worth_knowing"


def should_replace_candidate(
    existing: tuple[PipelineOutput, AttentionItem, str],
    incoming: tuple[PipelineOutput, AttentionItem, str],
) -> bool:
    """Pick the stronger candidate when multiple items claim the same entity."""
    _, _, existing_section = existing
    _, _, incoming_section = incoming
    priority_delta = SECTION_PRIORITY[incoming_section] - SECTION_PRIORITY[existing_section]

    if priority_delta != 0:
        return priority_delta < 0

    return sort_key(incoming) < sort_key(existing)


def sort_key(candidate: tuple[PipelineOutput, AttentionItem, str]) -> tuple[int, int, str]:
    """Sort by descending score, then nearest due date, then stable entity id."""
    output, attention_item, _ = candidate
    score = get_candidate_score(output, attention_item)
    due_timestamp = due_at_timestamp(output.entity.due_at)
    due_sort = due_timestamp if due_timestamp is not None else 2**63 - 1
    return (-score, due_sort, attention_item.entity_id)


def get_candidate_score(output: PipelineOutput, attention_item: AttentionItem) -> int:
    """Blend urgency, importance, confidence, and lifecycle into one ranking score."""
    importance_level = attention_item.importance_level or ("medium" if output.entity.importance else "low")
    lifecycle_state = attention_item.lifecycle_state or output.entity.lifecycle_state
    current_state = attention_item.current_state or output.entity.current_state
    blocking_boost = 20 if current_state in BLOCKING_STATES else 0

    return (
        TIMING_SCORE[attention_item.timing_band]
        + IMPORTANCE_SCORE[importance_level]
        + CONFIDENCE_SCORE[attention_item.action_confidence]
        + EFFORT_SCORE[attention_item.effort_level]
        + LIFECYCLE_SCORE[lifecycle_state]
        + blocking_boost
    )


def enrich_attention_item(output: PipelineOutput, attention_item: AttentionItem) -> AttentionItem:
    """Backfill canonical entity metadata onto a candidate feed item."""
    data = attention_item.model_dump()

    if output.entity.due_at is not None:
        data["due_at"] = output.entity.due_at
    if output.entity.source is not None:
        data["source"] = output.entity.source
    if attention_item.lifecycle_state is None:
        data["lifecycle_state"] = output.entity.lifecycle_state
    if attention_item.current_state is None:
        data["current_state"] = output.entity.current_state

    return AttentionItem.model_validate(data)


def due_at_timestamp(due_at: str | None) -> int | None:
    """Convert a due timestamp into epoch seconds when it is parseable."""
    if due_at is None:
        return None

    try:
        return int(__import__("datetime").datetime.fromisoformat(due_at.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def log_feed_ranking(
    database_path: str,
    selected_by_entity_id: dict[str, tuple[PipelineOutput, AttentionItem, str]],
    feed: FeedResponse,
) -> None:
    """Persist final ranking and section placement for replay/debug purposes."""
    for section_name, items in (
        ("now", feed.now),
        ("today", feed.today),
        ("worth_knowing", feed.worth_knowing),
    ):
        for index, item in enumerate(items):
            output, _, _ = selected_by_entity_id[item.entity_id]
            source_record_id = item.id if item.id != item.entity_id else None
            score = get_candidate_score(output, item)
            append_trace_record(
                database_path,
                stage="ranking",
                user_id=item.user_id,
                entity_id=item.entity_id,
                source_record_id=source_record_id,
                trace_id=item.trace_id,
                input={
                    "timing_band": item.timing_band,
                    "importance_level": item.importance_level,
                    "action_confidence": item.action_confidence,
                    "effort_level": item.effort_level,
                },
                output={
                    "section": section_name,
                    "rank": index,
                    "score": score,
                },
            )
            append_trace_record(
                database_path,
                stage="output",
                user_id=item.user_id,
                entity_id=item.entity_id,
                source_record_id=source_record_id,
                trace_id=item.trace_id,
                input={
                    "section": section_name,
                    "rank": index,
                },
                output={
                    "surfaced": True,
                    "suppressed": False,
                    "section": section_name,
                    "item_id": item.id,
                },
            )
