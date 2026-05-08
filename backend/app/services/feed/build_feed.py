from __future__ import annotations

"""Final feed projection, filtering, deduplication, and ranking logic."""

from datetime import datetime

from app.db.repository import append_trace_record
from app.schemas.domain import AttentionItem, FeedResponse, PipelineOutput
from app.services.ai.decision import normalize_entity_state


TIMING_ORDER = {"now": 0, "today": 1, "later": 2}
STATE_ORDER = {"open": 0, "waiting": 1, "done": 2}
IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
DEFAULT_ORDER = 3


def build_feed(outputs: list[PipelineOutput], database_path: str | None = None) -> FeedResponse:
    """Project per-entity outputs into sectioned feed arrays."""
    selected_by_entity_id: dict[str, tuple[PipelineOutput, AttentionItem, str]] = {}

    for output in outputs:
        candidate = to_feed_candidate(output)

        if candidate is None:
            continue

        _, attention_item, _ = candidate
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
    """Keep every surfaced entity and map it into a feed section."""
    attention_item = output.attention_item

    if attention_item is None or output.suppressed:
        return None

    timing_band = attention_item.timing_band

    if timing_band == "hidden":
        return None

    return output, attention_item, map_timing_band_to_section(timing_band)


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
    return sort_key(incoming) < sort_key(existing)


def sort_key(candidate: tuple[PipelineOutput, AttentionItem, str]) -> tuple[int, int, int, int, str]:
    """Sort by timing, then state, then importance, then recency."""
    output, attention_item, _ = candidate
    timing_rank = _rank_timing_band(attention_item.timing_band)
    current_state = normalize_entity_state(attention_item.current_state or output.entity.current_state)
    state_rank = STATE_ORDER.get(current_state, DEFAULT_ORDER)
    importance_level = attention_item.importance_level or ("medium" if output.entity.importance else "low")
    importance_rank = IMPORTANCE_ORDER.get(importance_level, DEFAULT_ORDER)
    recency_rank = _timestamp_rank(attention_item.created_at)

    return (timing_rank, state_rank, importance_rank, recency_rank, attention_item.entity_id)


def _rank_timing_band(timing_band: str) -> int:
    return TIMING_ORDER.get(timing_band, DEFAULT_ORDER)


def _timestamp_rank(value: str | None) -> int:
    if value is None:
        return 2**63 - 1

    try:
        return -int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 2**63 - 1


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
        data["current_state"] = normalize_entity_state(output.entity.current_state)

    return AttentionItem.model_validate(data)


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
            rank_key = sort_key(selected_by_entity_id[item.entity_id])
            append_trace_record(
                database_path,
                stage="ranking",
                user_id=item.user_id,
                entity_id=item.entity_id,
                source_record_id=source_record_id,
                trace_id=item.trace_id,
                input={
                    "timing_band": item.timing_band,
                    "current_state": item.current_state,
                    "importance_level": item.importance_level,
                    "created_at": item.created_at,
                },
                output={
                    "section": section_name,
                    "rank": index,
                    "rank_key": rank_key,
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
