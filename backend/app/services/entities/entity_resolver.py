from __future__ import annotations

"""Resolve new source records onto existing entities or create fallback entities."""

from app.db.models import StoredEntity
from app.db.repository import (
    append_trace_record,
    attach_record_to_entity,
    create_entity,
    find_entity_by_member_record_id,
    find_entity_by_thread_id,
    get_entity_states,
    list_loaded_entities,
    list_candidate_records,
)
from app.schemas.ai import EntityGroupingRequest, EntityGroupingResponse
from app.schemas.domain import SourceRecord
from app.services.ai.decision import resolve_entity_group


SUBJECT_NOISE_WORDS = {
    "confirmed",
    "confirmation",
    "accepted",
    "waitlist",
    "waitlisted",
    "rsvp",
    "registered",
    "registration",
    "invite",
    "invitation",
    "reminder",
    "update",
    "notification",
    "status",
}

GROUPING_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "your",
    "this",
    "that",
    "have",
    "has",
    "been",
    "will",
    "into",
    "about",
    "card",
    "bank",
    "email",
    "message",
    "request",
    "service",
    "query",
    "concern",
    "assistance",
    "update",
    "registered",
    "registration",
    "acknowledgement",
    "acknowledge",
    "received",
}


def resolve_entity_for_record(
    database_path: str,
    record: SourceRecord,
) -> tuple[StoredEntity, float]:
    """Attach a source record by exact member, thread, heuristic, AI, or fallback creation."""
    trace_input = {
        "source_record_id": record.id,
        "thread_id": record.thread_id,
        "subject": string_value(record.raw_payload, "subject"),
        "sender": string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender"),
    }
    existing_by_member = find_entity_by_member_record_id(database_path, record.id)

    if existing_by_member is not None:
        append_trace_record(
            database_path,
            stage="grouping",
            user_id=record.user_id,
            entity_id=existing_by_member.id,
            source_record_id=record.id,
            trace_id=existing_by_member.id,
            input=trace_input,
            output={"entity_id": existing_by_member.id, "confidence": 1.0, "method": "existing_member"},
        )
        return existing_by_member, 1.0

    if record.thread_id:
        existing_by_thread = find_entity_by_thread_id(database_path, record.thread_id)

        if existing_by_thread is not None:
            attach_record_to_entity(database_path, existing_by_thread.id, record.id)
            append_trace_record(
                database_path,
                stage="grouping",
                user_id=record.user_id,
                entity_id=existing_by_thread.id,
                source_record_id=record.id,
                trace_id=existing_by_thread.id,
                input=trace_input,
                output={"entity_id": existing_by_thread.id, "confidence": 1.0, "method": "thread_match"},
            )
            return existing_by_thread, 1.0

    candidates = find_entity_candidates(database_path, record)
    best_candidate = candidates[0] if candidates else None

    if best_candidate is not None and best_candidate["confidence"] >= 0.7:
        attach_record_to_entity(database_path, best_candidate["entity"].id, record.id)
        append_trace_record(
            database_path,
            stage="grouping",
            user_id=record.user_id,
            entity_id=best_candidate["entity"].id,
            source_record_id=record.id,
            trace_id=best_candidate["entity"].id,
            input=trace_input,
            output={
                "entity_id": best_candidate["entity"].id,
                "confidence": float(best_candidate["confidence"]),
                "method": "heuristic_match",
            },
        )
        return best_candidate["entity"], float(best_candidate["confidence"])

    if candidates:
        ai_resolution = resolve_entity_with_ai(record, candidates)

        if ai_resolution.entity_id is not None and ai_resolution.confidence > 0.8:
            matched_candidate = next(
                (candidate for candidate in candidates if candidate["entity"].id == ai_resolution.entity_id),
                None,
            )

            if matched_candidate is not None:
                attach_record_to_entity(database_path, matched_candidate["entity"].id, record.id)
                append_trace_record(
                    database_path,
                    stage="grouping",
                    user_id=record.user_id,
                    entity_id=matched_candidate["entity"].id,
                    source_record_id=record.id,
                    trace_id=matched_candidate["entity"].id,
                    input=trace_input,
                    output={
                        "entity_id": matched_candidate["entity"].id,
                        "confidence": float(ai_resolution.confidence),
                        "method": "ai_match",
                    },
                )
                return matched_candidate["entity"], float(ai_resolution.confidence)

    entity = create_entity(database_path, f"fallback:{record.id}")
    attach_record_to_entity(database_path, entity.id, record.id)
    append_trace_record(
        database_path,
        stage="grouping",
        user_id=record.user_id,
        entity_id=entity.id,
        source_record_id=record.id,
        trace_id=entity.id,
        input=trace_input,
        output={"entity_id": entity.id, "confidence": 0.0, "method": "fallback_create"},
    )
    return entity, 0.0


def normalize_subject(subject: str) -> str:
    """Strip reply prefixes and low-signal lifecycle words before subject matching."""
    normalized = subject.strip().lower()

    while normalized.startswith(("re: ", "fw: ", "fwd: ")):
        if normalized.startswith("re: "):
            normalized = normalized[4:]
        elif normalized.startswith("fw: "):
            normalized = normalized[4:]
        else:
            normalized = normalized[5:]

    tokens = [token for token in _split_tokens(normalized) if token not in SUBJECT_NOISE_WORDS]
    return " ".join(tokens)


def get_sender_domain(sender: str) -> str:
    """Extract an email sender domain for lightweight candidate narrowing."""
    sender = sender.lower()

    if "@" not in sender:
        return ""

    return sender.split("@", 1)[1].split(">", 1)[0].strip()


def find_entity_candidates(database_path: str, record: SourceRecord) -> list[dict[str, object]]:
    """Rank likely entity candidates using subject overlap and sender-domain hints."""
    normalized_subject = normalize_subject(string_value(record.raw_payload, "subject"))
    record_body = string_value(record.raw_payload, "body")
    record_grouping_text = build_grouping_text(
        string_value(record.raw_payload, "subject"),
        record_body,
    )
    sender_domain = get_sender_domain(
        string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender")
    )

    if not normalized_subject and not record_grouping_text:
        return []

    by_entity_id: dict[str, dict[str, object]] = {}

    for candidate_record, entity in list_candidate_records(database_path, sender_domain or None):
        candidate_subject = normalize_subject(candidate_record.subject or "")
        subject_confidence = get_subject_confidence(normalized_subject, candidate_subject)
        support_confidence = get_support_confidence(
            record_grouping_text,
            build_grouping_text(candidate_record.subject or "", payload_string(candidate_record.raw_payload, "body")),
        )

        if subject_confidence <= 0 and support_confidence <= 0:
            continue

        same_sender_domain = bool(sender_domain) and get_sender_domain(candidate_record.sender or "") == sender_domain
        base_confidence = max(subject_confidence, support_confidence)
        confidence = base_confidence if same_sender_domain else max(0.0, base_confidence - 0.2)
        latest_timestamp = candidate_record.timestamp
        existing = by_entity_id.get(entity.id)

        if existing is None or confidence > existing["confidence"] or latest_timestamp > existing["latest_timestamp"]:
            by_entity_id[entity.id] = {
                "entity": entity,
                "confidence": confidence,
                "latest_subject": candidate_record.subject or "Untitled",
                "latest_sender": candidate_record.sender or "",
                "latest_timestamp": latest_timestamp,
                "support_confidence": support_confidence,
            }

    states = get_entity_states(database_path, by_entity_id.keys())
    loaded_by_entity = {
        loaded.entity.id: loaded
        for loaded in list_loaded_entities(database_path, [str(entity_id) for entity_id in by_entity_id.keys()])
    }
    candidates = []

    for candidate in by_entity_id.values():
        entity = candidate["entity"]
        loaded_entity = loaded_by_entity.get(entity.id)
        candidate_summary = build_entity_summary(loaded_entity)
        summary_confidence = get_support_confidence(record_grouping_text, candidate_summary)
        confidence = max(float(candidate["confidence"]), summary_confidence)
        candidates.append(
            {
                "entity": entity,
                "confidence": confidence,
                "latest_subject": candidate["latest_subject"],
                "latest_sender": candidate["latest_sender"],
                "current_state": states.get(entity.id).current_state if entity.id in states else None,
                "summary": candidate_summary,
            }
        )

    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)[:5]


def get_subject_confidence(left: str, right: str) -> float:
    """Score subject similarity with exact, substring, and token-overlap matches."""
    if not left or not right:
        return 0.0
    if left == right:
        return 0.95
    if left in right or right in left:
        return 0.82

    left_tokens = {token for token in left.split() if len(token) > 2}
    right_tokens = {token for token in right.split() if len(token) > 2}
    shared_tokens = left_tokens & right_tokens

    if not shared_tokens:
        return 0.0

    overlap = len(shared_tokens) / max(len(left_tokens), len(right_tokens), 1)

    if overlap >= 0.6:
        return 0.78
    if overlap >= 0.34:
        return 0.62
    return 0.4


def resolve_entity_with_ai(
    record: SourceRecord,
    candidates: list[dict[str, object]],
) -> EntityGroupingResponse:
    """Ask the AI grouping layer only after the cheap deterministic checks fail."""
    request = EntityGroupingRequest(
        subject=string_value(record.raw_payload, "subject"),
        snippet=string_value(record.raw_payload, "body"),
        candidates=[
            {
                "entity_id": candidate["entity"].id,
                "canonical_key": candidate["entity"].canonical_key,
                "latest_subject": candidate["latest_subject"],
                "latest_sender": candidate["latest_sender"] or None,
                "current_state": candidate["current_state"],
                "summary": str(candidate.get("summary") or f"{candidate['latest_subject']} {(candidate['current_state'] or '')}".strip()),
            }
            for candidate in candidates[:5]
        ],
    )
    return resolve_entity_group(request)


def string_value(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _split_tokens(value: str) -> list[str]:
    cleaned = "".join(character if character.isalnum() or character.isspace() else " " for character in value)
    return [token for token in cleaned.split() if token]


def payload_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def build_grouping_text(subject: str, body: str) -> str:
    """Combine subject/body into one normalized text source for grouping signals."""
    return " ".join(part for part in [normalize_subject(subject), body.strip().lower()] if part).strip()


def get_support_confidence(left: str, right: str) -> float:
    """Use token and phrase overlap to attach vague follow-ups to the right open thread."""
    if not left or not right:
        return 0.0

    left_signals = extract_grouping_signals(left)
    right_signals = extract_grouping_signals(right)

    if not left_signals or not right_signals:
        return 0.0

    shared = left_signals & right_signals

    if not shared:
        return 0.0

    overlap = len(shared) / max(min(len(left_signals), len(right_signals)), 1)

    if overlap >= 0.6:
        return 0.9
    if overlap >= 0.34:
        return 0.78
    return 0.55


def extract_grouping_signals(text: str) -> set[str]:
    """Extract stable topical tokens and short phrases from support-style email copy."""
    tokens = [token for token in _split_tokens(text.lower()) if len(token) > 2]
    filtered_tokens = [token for token in tokens if token not in GROUPING_STOP_WORDS]
    signals = set(filtered_tokens)

    for index in range(len(filtered_tokens) - 1):
        left = filtered_tokens[index]
        right = filtered_tokens[index + 1]
        signals.add(f"{left} {right}")

    return signals


def build_entity_summary(loaded_entity) -> str:
    """Summarize recent member subjects/bodies so grouping sees more than one latest subject."""
    if loaded_entity is None:
        return ""

    parts: list[str] = []

    for record in loaded_entity.members[-3:]:
        subject = record.subject or payload_string(record.raw_payload, "subject")
        body = payload_string(record.raw_payload, "body")
        parts.append(build_grouping_text(subject or "", body))

    if loaded_entity.state is not None:
        parts.append(loaded_entity.state.current_state)

    return " ".join(part for part in parts if part).strip()
