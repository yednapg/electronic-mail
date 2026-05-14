from __future__ import annotations

"""Resolve new source records onto existing entities or create fallback entities."""

import html
import os
import re

from app.db.models import StoredEntity
from app.db.repository import (
    append_trace_record,
    attach_record_to_entity,
    attach_thread_to_entity,
    create_entity,
    find_entity_by_member_record_id,
    find_entity_by_thread_id,
    get_entity_states,
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
    "dear",
    "customer",
    "thank",
    "thanks",
    "writing",
    "system",
    "generated",
    "response",
    "regards",
    "services",
    "working",
    "days",
    "further",
    "regarding",
    "continuation",
    "continue",
    "inform",
    "informed",
    "please",
    "kindly",
    "shared",
    "final",
    "interim",
    "review",
    "appropriate",
    "email",
    "correspondence",
    "reference",
    "number",
    "unique",
    "support",
    "customerservice",
}

GENERIC_SENDER_LABELS = {
    "co",
    "com",
    "in",
    "info",
    "io",
    "mail",
    "mailer",
    "net",
    "no",
    "noreply",
    "notifications",
    "notify",
    "org",
    "qmailer",
    "reply",
    "smtp",
    "support",
}

LOW_SIGNAL_ORGANIZATIONS = {
    "gmail",
    "googlemail",
    "rameshpandey",
    "rbi",
}

LABELED_REFERENCE_PATTERN = re.compile(
    r"(?i)\b(?:ticket|case(?:\s*id)?|complaint(?:\s*(?:no|number))?|ref(?:erence)?(?:\s*(?:no|number))?|application(?:\s*number)?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]{5,})"
)
COMPOSITE_REFERENCE_PATTERN = re.compile(
    r"\b(?:[A-Z]{1,}(?:[-/][A-Z0-9]{2,})+|[A-Z]{1,}[A-Z0-9]{7,})\b"
)
HTML_BREAK_PATTERN = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>|</li>|</h[1-6]>")
HTML_BLOCK_PATTERN = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
HTML_TAG_PATTERN = re.compile(r"(?s)<[^>]+>")
QUOTE_BOUNDARY_PATTERNS = [
    re.compile(r"(?i)^on .+ wrote:$"),
    re.compile(r"(?i)^wrote:$"),
    re.compile(r"(?i)^-+\s*original message\s*-+$"),
    re.compile(r"(?i)^from:\s"),
    re.compile(r"(?i)^sent:\s"),
    re.compile(r"(?i)^to:\s"),
    re.compile(r"(?i)^cc:\s"),
    re.compile(r"(?i)^subject:\s"),
]
NAMED_MARKER_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]{2,}\b")
LOW_SIGNAL_MARKERS = {
    "case",
    "cms",
    "dear",
    "id",
    "ifsc",
    "inr",
    "pan",
    "ref",
    "rbi",
    "reg",
    "upi",
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
    normalized_subject = normalize_subject(string_value(record.raw_payload, "subject"))
    existing_by_member = find_entity_by_member_record_id(database_path, record.id, user_id=record.user_id)

    if existing_by_member is not None:
        attach_record_with_thread_membership(database_path, existing_by_member.id, record)
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

    existing_by_thread = (
        find_entity_by_thread_id(database_path, record.source, record.thread_id, user_id=record.user_id)
        if record.thread_id
        else None
    )

    if existing_by_thread is not None:
        attach_record_with_thread_membership(database_path, existing_by_thread.id, record)
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

    if candidates:
        if is_reference_only_subject(normalized_subject):
            anchored_candidates = [
                candidate
                for candidate in candidates
                if candidate.get("shared_reference_ids") or candidate.get("shared_named_markers")
            ]
            shared_ref_candidates = [candidate for candidate in anchored_candidates if candidate.get("shared_reference_ids")]
            named_candidates = [candidate for candidate in anchored_candidates if candidate.get("shared_named_markers")]

            if len(shared_ref_candidates) != 1 and len(named_candidates) != 1:
                candidates = []

        if not candidates:
            entity = create_entity(database_path, build_entity_seed_key(record), user_id=record.user_id)
            attach_record_with_thread_membership(database_path, entity.id, record)
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

        deterministic_candidate = choose_deterministic_candidate(candidates)

        if deterministic_candidate is not None:
            matched_entity = deterministic_candidate["entity"]
            attach_record_with_thread_membership(database_path, matched_entity.id, record)
            append_trace_record(
                database_path,
                stage="grouping",
                user_id=record.user_id,
                entity_id=matched_entity.id,
                source_record_id=record.id,
                trace_id=matched_entity.id,
                input=trace_input,
                output={
                    "entity_id": matched_entity.id,
                    "confidence": float(deterministic_candidate["confidence"]),
                    "method": "heuristic_match",
                },
            )
            return matched_entity, float(deterministic_candidate["confidence"])

        if should_use_ai_grouping():
            ai_resolution = resolve_entity_with_ai(record, candidates)

            if ai_resolution.entity_id is not None and ai_resolution.confidence > 0.8:
                matched_candidate = next(
                    (candidate for candidate in candidates if candidate["entity"].id == ai_resolution.entity_id),
                    None,
                )

                if matched_candidate is not None:
                    attach_record_with_thread_membership(database_path, matched_candidate["entity"].id, record)
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

    entity = create_entity(database_path, build_entity_seed_key(record), user_id=record.user_id)
    attach_record_with_thread_membership(database_path, entity.id, record)
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


def get_sender_organization(sender: str) -> str:
    """Extract the most useful organization token from an email sender domain."""
    domain = get_sender_domain(sender)

    if not domain:
        return ""

    labels = [label for label in domain.split(".") if label]

    for label in reversed(labels):
        if label not in GENERIC_SENDER_LABELS:
            return label

    return labels[0] if labels else ""


def find_entity_candidates(database_path: str, record: SourceRecord) -> list[dict[str, object]]:
    """Rank likely entity candidates using subject, issue-marker, and sender-domain hints."""
    normalized_subject = normalize_subject(string_value(record.raw_payload, "subject"))
    record_body = grouping_body(string_value(record.raw_payload, "body"))
    record_issue_markers = extract_issue_markers(
        string_value(record.raw_payload, "subject"),
        record_body,
    )
    record_reference_ids = extract_message_reference_ids(
        string_value(record.raw_payload, "subject"),
        record_body,
    )
    sender_domain = get_sender_domain(
        string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender")
    )
    sender_organization = get_sender_organization(
        string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender")
    )
    record_named_markers = extract_named_markers(
        string_value(record.raw_payload, "subject"),
        record_body,
        sender_organization,
    )
    record_grouping_text = build_grouping_text(
        string_value(record.raw_payload, "subject"),
        record_body,
    )

    if not normalized_subject and not record_grouping_text:
        return []

    by_entity_id: dict[str, dict[str, object]] = {}

    for candidate_record, entity in list_candidate_records(database_path, sender_domain or None, user_id=record.user_id):
        candidate_subject = normalize_subject(candidate_record.subject or "")
        candidate_body = grouping_body(payload_string(candidate_record.raw_payload, "body"))
        candidate_issue_markers = extract_issue_markers(candidate_record.subject or "", candidate_body)
        candidate_reference_ids = extract_message_reference_ids(candidate_record.subject or "", candidate_body)
        candidate_sender_organization = get_sender_organization(candidate_record.sender or "")
        candidate_named_markers = extract_named_markers(
            candidate_record.subject or "",
            candidate_body,
            candidate_sender_organization,
        )
        shared_reference_ids = record_reference_ids & candidate_reference_ids
        shared_issue_markers = record_issue_markers & candidate_issue_markers
        shared_named_markers = record_named_markers & candidate_named_markers
        both_low_signal_senders = (
            sender_organization in LOW_SIGNAL_ORGANIZATIONS
            and candidate_sender_organization in LOW_SIGNAL_ORGANIZATIONS
        )

        subject_confidence = get_subject_confidence(normalized_subject, candidate_subject)
        support_confidence = get_support_confidence(
            record_grouping_text,
            build_grouping_text(candidate_record.subject or "", candidate_body),
        )
        sender_affinity = get_sender_affinity(
            sender_domain,
            sender_organization,
            get_sender_domain(candidate_record.sender or ""),
            candidate_sender_organization,
        )
        meaningful_sender_match = has_meaningful_sender_match(
            sender_domain,
            sender_organization,
            get_sender_domain(candidate_record.sender or ""),
            candidate_sender_organization,
        )

        if (
            not shared_reference_ids
            and not meaningful_sender_match
            and not shared_named_markers
            and subject_confidence < 0.62
            and support_confidence < 0.62
            and len(shared_issue_markers) < 2
        ):
            continue
        if (
            sender_organization
            and sender_organization not in LOW_SIGNAL_ORGANIZATIONS
            and sender_organization != candidate_sender_organization
            and sender_organization not in candidate_named_markers
            and not shared_reference_ids
        ):
            continue
        if (
            sender_organization in LOW_SIGNAL_ORGANIZATIONS
            and not shared_reference_ids
            and not meaningful_sender_match
            and not shared_named_markers
            and support_confidence < 0.72
            and len(shared_issue_markers) < 3
        ):
            continue
        if both_low_signal_senders and not shared_reference_ids and not shared_named_markers:
            continue

        base_confidence = max(subject_confidence, support_confidence)
        if shared_reference_ids:
            base_confidence = max(base_confidence, 0.86)
        elif shared_named_markers:
            base_confidence = max(base_confidence, 0.72)
        elif shared_issue_markers:
            base_confidence = max(base_confidence, 0.6)

        confidence = min(0.99, base_confidence * sender_affinity if base_confidence < 0.9 else base_confidence)
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
                "shared_reference_ids": sorted(shared_reference_ids),
                "shared_issue_markers": sorted(shared_issue_markers),
                "shared_named_markers": sorted(shared_named_markers),
                "candidate_named_markers": sorted(candidate_named_markers),
            }

    states = get_entity_states(database_path, by_entity_id.keys())
    candidates = []

    for candidate in by_entity_id.values():
        entity = candidate["entity"]
        candidate_summary = str(candidate["latest_subject"])
        candidate_summary_issue_markers = extract_issue_markers(candidate_summary, "")
        candidate_summary_reference_ids = extract_reference_ids(candidate_summary)
        shared_summary_issue_markers = record_issue_markers & candidate_summary_issue_markers
        shared_summary_reference_ids = record_reference_ids & candidate_summary_reference_ids

        summary_confidence = get_support_confidence(record_grouping_text, candidate_summary)
        confidence = max(float(candidate["confidence"]), summary_confidence)
        if shared_summary_reference_ids:
            confidence = max(confidence, 0.87)
        elif candidate.get("shared_named_markers"):
            confidence = max(confidence, 0.74)
        elif shared_summary_issue_markers:
            confidence = max(confidence, 0.65)
        candidates.append(
            {
                "entity": entity,
                "confidence": confidence,
                "latest_subject": candidate["latest_subject"],
                "latest_sender": candidate["latest_sender"],
                "current_state": states.get(entity.id).current_state if entity.id in states else None,
                "summary": candidate_summary,
                "shared_reference_ids": sorted(
                    set(candidate.get("shared_reference_ids", [])) | shared_summary_reference_ids
                ),
                "shared_named_markers": sorted(candidate.get("shared_named_markers", [])),
                "candidate_named_markers": sorted(candidate.get("candidate_named_markers", [])),
            }
        )

    return sorted(candidates, key=lambda item: item["confidence"], reverse=True)[:5]


def choose_deterministic_candidate(candidates: list[dict[str, object]]) -> dict[str, object] | None:
    """Pick only high-confidence cross-thread matches without blocking on AI."""
    if not candidates:
        return None

    top = candidates[0]
    top_confidence = float(top.get("confidence") or 0.0)
    second_confidence = float(candidates[1].get("confidence") or 0.0) if len(candidates) > 1 else 0.0
    margin = top_confidence - second_confidence
    has_shared_reference = bool(top.get("shared_reference_ids"))
    has_shared_named_marker = bool(top.get("shared_named_markers"))

    if has_shared_reference and top_confidence >= 0.86 and (len(candidates) == 1 or margin >= 0.05):
        return top

    if has_shared_named_marker and top_confidence >= 0.9 and (len(candidates) == 1 or margin >= 0.1):
        return top

    if top_confidence >= 0.94 and (len(candidates) == 1 or margin >= 0.12):
        return top

    return None


def should_use_ai_grouping() -> bool:
    """Keep first-run hydration fast unless AI grouping is explicitly enabled."""
    return os.getenv("OPENAI_ENTITY_GROUPING", "").strip().lower() in {"1", "true", "yes", "on"}


def get_subject_confidence(left: str, right: str) -> float:
    """Score subject similarity with exact, substring, and token-overlap matches."""
    if not left or not right:
        return 0.0

    left_refs = extract_reference_ids(left)
    right_refs = extract_reference_ids(right)

    if left_refs and right_refs and not (left_refs & right_refs):
        left = strip_reference_only_subject(left)
        right = strip_reference_only_subject(right)

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
        snippet=grouping_body(string_value(record.raw_payload, "body")),
        reference_ids=sorted(
            extract_message_reference_ids(
                string_value(record.raw_payload, "subject"),
                string_value(record.raw_payload, "body"),
            )
        ),
        named_markers=sorted(
            extract_named_markers(
                string_value(record.raw_payload, "subject"),
                string_value(record.raw_payload, "body"),
                get_sender_organization(string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender")),
            )
        ),
        candidates=[
            {
                "entity_id": candidate["entity"].id,
                "canonical_key": candidate["entity"].canonical_key,
                "latest_subject": candidate["latest_subject"],
                "latest_sender": candidate["latest_sender"] or None,
                "current_state": candidate["current_state"],
                "summary": str(candidate.get("summary") or f"{candidate['latest_subject']} {(candidate['current_state'] or '')}".strip()),
                "reference_ids": sorted(candidate.get("shared_reference_ids", [])),
                "named_markers": sorted(candidate.get("candidate_named_markers", [])),
            }
            for candidate in candidates[:5]
        ],
    )
    return resolve_entity_group(request)


def build_entity_seed_key(record: SourceRecord) -> str:
    """Choose a deterministic canonical key for a brand-new entity."""
    if record.source == "gmail" and record.thread_id:
        return f"gmail-thread:{record.thread_id}"
    return f"fallback:{record.id}"


def attach_record_with_thread_membership(database_path: str, entity_id: str, record: SourceRecord) -> None:
    """Attach one record and its source-scoped thread membership when present."""
    attach_record_to_entity(database_path, entity_id, record.id)

    if record.thread_id:
        attach_thread_to_entity(database_path, entity_id, record.source, record.thread_id, user_id=record.user_id)


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
    cleaned_body = grouping_body(body)
    return " ".join(part for part in [normalize_subject(subject), cleaned_body.lower()] if part).strip()


def get_sender_affinity(
    sender_domain: str,
    sender_organization: str,
    candidate_domain: str,
    candidate_organization: str,
) -> float:
    """Prefer same sender host, then same organization, then looser semantic matches."""
    if sender_domain and candidate_domain and sender_domain == candidate_domain:
        if sender_organization in LOW_SIGNAL_ORGANIZATIONS:
            return 0.78
        return 1.0
    if sender_organization and candidate_organization and sender_organization == candidate_organization:
        if sender_organization in LOW_SIGNAL_ORGANIZATIONS:
            return 0.76
        return 0.95
    return 0.7


def has_meaningful_sender_match(
    sender_domain: str,
    sender_organization: str,
    candidate_domain: str,
    candidate_organization: str,
) -> bool:
    """Treat exact sender similarity as useful only when the mailbox is not generic."""
    if (
        sender_domain
        and candidate_domain
        and sender_domain == candidate_domain
        and sender_organization not in LOW_SIGNAL_ORGANIZATIONS
    ):
        return True

    return (
        sender_organization
        and candidate_organization
        and sender_organization == candidate_organization
        and sender_organization not in LOW_SIGNAL_ORGANIZATIONS
    )


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
    representative_records: list[object] = []
    seen_threads: set[str] = set()

    for record in loaded_entity.members:
        thread_key = record.thread_id or record.id

        if thread_key in seen_threads:
            continue

        seen_threads.add(thread_key)
        representative_records.append(record)

    if len(representative_records) > 4:
        representative_records = representative_records[:2] + representative_records[-2:]

    for record in representative_records:
        subject = record.subject or payload_string(record.raw_payload, "subject")
        body = grouping_body(payload_string(record.raw_payload, "body"))
        parts.append(build_grouping_text(subject or "", body))

    if loaded_entity.state is not None:
        parts.append(loaded_entity.state.current_state)

    return " ".join(part for part in parts if part).strip()


def extract_issue_markers(subject: str, body: str) -> set[str]:
    """Extract generic issue markers from meaningful token phrases in the email itself."""
    subject_tokens = [token for token in _split_tokens(normalize_subject(subject)) if is_issue_token(token)]
    body_tokens = [token for token in _split_tokens(grouping_body(body).lower()) if is_issue_token(token)]
    markers: set[str] = set()

    for phrase in extract_phrase_markers(subject_tokens):
        markers.add(phrase)

    for phrase in extract_phrase_markers(body_tokens[:80]):
        markers.add(phrase)

    return markers


def is_issue_token(token: str) -> bool:
    """Keep only content-bearing tokens that can describe a concrete issue."""
    return len(token) > 2 and token not in GROUPING_STOP_WORDS and not token.isdigit()


def extract_phrase_markers(tokens: list[str]) -> set[str]:
    """Build short n-gram markers from normalized content tokens."""
    markers: set[str] = set()

    for size in (2, 3):
        for index in range(len(tokens) - size + 1):
            phrase_tokens = tokens[index : index + size]
            markers.add(" ".join(phrase_tokens))

    return markers


def extract_reference_ids(text: str) -> set[str]:
    """Extract bank/service reference ids that should keep separate cases apart."""
    references: set[str] = set()

    for match in LABELED_REFERENCE_PATTERN.findall(text):
        normalized = normalize_reference_id(match)
        if normalized:
            references.add(normalized)

    for match in COMPOSITE_REFERENCE_PATTERN.findall(text.upper()):
        normalized = normalize_reference_id(match)
        if normalized:
            references.add(normalized)

    return references


def extract_message_reference_ids(subject: str, body: str) -> set[str]:
    """Extract references only from the visible message content, not quoted history."""
    return extract_reference_ids(" ".join(part for part in [subject, grouping_body(body)] if part))


def normalize_reference_id(value: str) -> str:
    """Normalize structured complaint/reference ids into stable comparison keys."""
    normalized = value.strip().strip("()[]{}<>,.;:'\"").upper()

    while normalized.endswith(("/", "-")):
        normalized = normalized[:-1]

    digits = sum(character.isdigit() for character in normalized)

    if digits == 0:
        return ""
    if normalized.isdigit() and len(normalized) < 8:
        return ""
    if digits < 5 and len(normalized) < 8:
        return ""

    return normalized


def extract_named_markers(subject: str, body: str, sender_organization: str = "") -> set[str]:
    """Capture organization-like anchors from the visible message content."""
    text = "\n".join(part for part in [subject, grouping_body(body)] if part)
    markers = {
        match.group(0).lower()
        for match in NAMED_MARKER_PATTERN.finditer(text)
        if match.group(0).lower() not in LOW_SIGNAL_MARKERS and not match.group(0).isdigit()
    }

    if sender_organization and sender_organization not in LOW_SIGNAL_ORGANIZATIONS:
        markers.add(sender_organization)

    return markers


def strip_reference_only_subject(value: str) -> str:
    """Drop bare case-id scaffolding so different complaint IDs do not look similar."""
    tokens = [
        token
        for token in _split_tokens(value.lower())
        if token not in {"case", "id", "ticket", "complaint", "ref", "reference", "number"}
        and not token.isdigit()
    ]
    return " ".join(tokens)


def is_reference_only_subject(value: str) -> bool:
    """Return whether the subject is mostly a bare case/reference identifier."""
    return not strip_reference_only_subject(value)


def grouping_body(body: str) -> str:
    """Reduce an email body to the visible top section that is useful for grouping."""
    if not body:
        return ""

    text = html.unescape(body)
    text = HTML_BLOCK_PATTERN.sub(" ", text)
    text = HTML_BREAK_PATTERN.sub("\n", text)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = text.replace("\r", "\n")

    lines: list[str] = []
    total_chars = 0

    for raw_line in text.split("\n"):
        line = " ".join(raw_line.split()).strip()

        if not line:
            continue
        if line.startswith(">") or any(pattern.search(line) for pattern in QUOTE_BOUNDARY_PATTERNS):
            break

        lines.append(line)
        total_chars += len(line)

        if total_chars >= 1600 or len(lines) >= 40:
            break

    return "\n".join(lines)
