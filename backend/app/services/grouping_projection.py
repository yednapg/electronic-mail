from __future__ import annotations

"""Validated final projection for product-visible mailbox grouping."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from email.utils import parseaddr
import hashlib
import re
from typing import Any

from app.db.mail_groups import GmailMessageRecord, MailGroupRecord, VisibleMailGroupUpsert
from app.services.attention_classifier import deterministic_classification
from app.services.email_extraction import compact_text, sender_domain


INBOX_STRICT_WORKFLOWS = {"financial_transfer", "support_case", "billing", "logistics", "account_security", "application"}
EXACT_SIGNAL_PREFIXES = ("order_id:", "ticket_id:", "tracking_id:", "invoice_id:", "booking_id:", "application_id:")
GENERIC_ENTITY_NAMES = {"default user", "support", "noreply", "no-reply", "notification", "notifications", "team", "info", "mail"}
AI_LIFECYCLE_MIN_INBOX_CONFIDENCE = 0.94
AI_LIFECYCLE_ALLOWED_PARTNER_ENTITIES = (
    frozenset({"HDFC Bank", "CCIL FX Retail"}),
)
CONFLICTING_SIGNAL_KEYS = (
    "order_id",
    "ticket_id",
    "tracking_id",
    "invoice_id",
    "booking_id",
    "application_id",
    "case_id",
    "reference_id",
    "transaction_ref",
    "trade_ref",
)


@dataclass(frozen=True)
class ProjectionBuildResult:
    groups: list[VisibleMailGroupUpsert]
    audit_events: list[dict[str, Any]]


@dataclass(frozen=True)
class CandidateDecision:
    accepted: bool
    group_kind: str
    canonical_entity: str
    contact_channel: str | None
    title: str
    summary: str
    workflow_type: str
    confidence: float
    evidence: dict[str, Any]
    reason: str
    score: tuple[int, float, int, str]


def build_inbox_projection(
    *,
    groups: list[MailGroupRecord],
    group_messages: dict[str, list[GmailMessageRecord]],
) -> ProjectionBuildResult:
    candidates: list[tuple[MailGroupRecord, list[GmailMessageRecord], CandidateDecision]] = []
    audit_events: list[dict[str, Any]] = []
    for group in groups:
        messages = group_messages.get(group.id) or []
        decision = evaluate_inbox_candidate(group, messages)
        audit_events.append(
            {
                "source_group_id": group.id,
                "projection_key": _projection_key(group, messages),
                "decision": "accepted" if decision.accepted else "rejected",
                "reason": decision.reason,
                "evidence": decision.evidence,
            }
        )
        if decision.accepted:
            candidates.append((group, messages, decision))

    claimed_message_ids: set[str] = set()
    claimed_projection_keys: set[str] = set()
    visible_groups: list[VisibleMailGroupUpsert] = []
    for group, messages, decision in sorted(candidates, key=lambda item: item[2].score, reverse=True):
        projection_key = _projection_key(group, messages)
        if projection_key in claimed_projection_keys:
            audit_events.append(
                {
                    "source_group_id": group.id,
                    "projection_key": projection_key,
                    "decision": "superseded",
                    "reason": "another stronger visible group already claimed this projection key",
                    "evidence": decision.evidence,
                }
            )
            continue
        if any(message.message_id in claimed_message_ids for message in messages):
            audit_events.append(
                {
                    "source_group_id": group.id,
                    "projection_key": projection_key,
                    "decision": "superseded",
                    "reason": "another stronger visible group already claimed at least one member",
                    "evidence": decision.evidence,
                }
            )
            continue
        claimed_projection_keys.add(projection_key)
        for message in messages:
            claimed_message_ids.add(message.message_id)
        latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
        visible_groups.append(
            VisibleMailGroupUpsert(
                projection_key=projection_key,
                visibility="inbox",
                group_kind=decision.group_kind,
                canonical_entity=decision.canonical_entity,
                contact_channel=decision.contact_channel,
                title=decision.title[:180],
                summary=decision.summary[:1200],
                workflow_type=decision.workflow_type,
                confidence=decision.confidence,
                source_group_id=group.id,
                source="validated_projection",
                evidence=decision.evidence,
                latest_message_at=latest.internal_date or latest.updated_at,
                latest_message_id=latest.message_id,
                members=[(message, "validated_projection", decision.confidence) for message in messages],
            )
        )
    return ProjectionBuildResult(groups=visible_groups, audit_events=audit_events)


def evaluate_inbox_candidate(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> CandidateDecision:
    if len(messages) < 2:
        return _rejected(group, messages, "single-message groups render as normal thread rows")

    thread_ids = _thread_ids(messages)
    if len(thread_ids) < 2:
        return _rejected(group, messages, "single Gmail thread groups render as conversation rows")

    workflow_type = _workflow_type(group, messages)
    if workflow_type not in INBOX_STRICT_WORKFLOWS:
        return _rejected(group, messages, f"{workflow_type} is not allowed as a normal Inbox lifecycle group")

    entity, channel = canonical_entity_for_messages(messages)
    ai_contract = _ai_grouping_contract(group)
    ai_allows_inbox = _ai_contract_allows_inbox(ai_contract, group, messages)
    strong_evidence = _strong_evidence(group, messages)
    if not strong_evidence:
        return _rejected(group, messages, "cross-thread Inbox group has no strong shared reference evidence")

    if _crosses_unrelated_entities(messages, entity):
        return _rejected(group, messages, "cross-thread group crosses unrelated canonical entities")

    if _has_conflicting_extracted_references(messages):
        return _rejected(group, messages, "cross-thread group has conflicting extracted references")

    if group.membership_source in {"ai_batch", "ai_lifecycle"} and not ai_allows_inbox:
        return _rejected(group, messages, "AI output did not explicitly justify this as an Inbox lifecycle group")

    title = _visible_title(group, entity, strong_evidence)
    summary = compact_text(group.ai_summary or f"{entity} updates for {strong_evidence[0]}.") or title
    confidence = max(float(group.classification_confidence or 0), float(ai_contract.get("confidence") or 0), 0.9 if _is_exact_reference_group(group) else 0.82)
    evidence = {
        "canonical_entity": entity,
        "contact_channel": channel,
        "thread_count": len(thread_ids),
        "message_count": len(messages),
        "strong_evidence": strong_evidence,
        "source_group_key": group.group_key,
        "source_membership": group.membership_source,
        "ai_contract": ai_contract,
    }
    exact_score = 100 if _is_exact_reference_group(group) else 80
    return CandidateDecision(
        accepted=True,
        group_kind="lifecycle",
        canonical_entity=entity,
        contact_channel=channel,
        title=title,
        summary=summary,
        workflow_type=workflow_type,
        confidence=min(1.0, confidence),
        evidence=evidence,
        reason="validated lifecycle group",
        score=(exact_score, min(1.0, confidence), -len(messages), group.id),
    )


def canonical_entity_for_messages(messages: list[GmailMessageRecord]) -> tuple[str, str | None]:
    entity_messages = _inbox_visible_messages(messages) or messages
    names = [canonical_entity_for_message(message) for message in entity_messages]
    entities = [name for name, _channel in names if name]
    channels = [channel for _name, channel in names if channel]
    if entities:
        entity = Counter(entities).most_common(1)[0][0]
    else:
        entity = "Unknown Sender"
    channel = Counter(channels).most_common(1)[0][0] if channels else None
    return entity, channel


def canonical_entity_for_message(message: GmailMessageRecord) -> tuple[str, str | None]:
    raw_domain = str(message.extracted_signals.get("sender_domain") or sender_domain(message.sender) or "").lower()
    display_name, email_address = parseaddr(message.sender or "")
    local = email_address.split("@", 1)[0].lower() if "@" in email_address else ""
    text = " ".join([raw_domain, display_name.lower(), local])

    overrides = [
        (("hdfcbank", "hdfc.bank", "hdfcfx", "hdfc"), "HDFC Bank"),
        (("ccilindia", "fxclear", "fxnoreply"), "CCIL FX Retail"),
        (("interactivebrokers", "ibkr"), "Interactive Brokers"),
        (("cityflo",), "Cityflo"),
        (("tatastarbucks", "starbucks"), "Starbucks India"),
        (("google",), "Google"),
        (("sbi",), "SBI"),
        (("hsbc",), "HSBC"),
        (("openai", "chatgpt"), "OpenAI"),
        (("amazon web services", "amazonaws", "aws"), "Amazon Web Services"),
        (("flipkart",), "Flipkart"),
        (("railway",), "Railway"),
    ]
    for needles, entity in overrides:
        if any(needle in text for needle in needles):
            return entity, _contact_channel(display_name, local)

    candidate = compact_text(display_name or "")
    if not candidate or candidate.lower() in GENERIC_ENTITY_NAMES:
        candidate = _entity_from_domain(raw_domain)
    if not candidate:
        candidate = compact_text(email_address or message.sender or "Unknown Sender")
    return candidate, _contact_channel(display_name, local)


def _rejected(group: MailGroupRecord, messages: list[GmailMessageRecord], reason: str) -> CandidateDecision:
    entity, channel = canonical_entity_for_messages(messages) if messages else ("Unknown Sender", None)
    return CandidateDecision(
        accepted=False,
        group_kind="collection",
        canonical_entity=entity,
        contact_channel=channel,
        title=group.ai_title,
        summary=group.ai_summary,
        workflow_type=_workflow_type(group, messages) if messages else group.group_type,
        confidence=float(group.classification_confidence or 0),
        evidence={"source_group_key": group.group_key, "thread_count": len(_thread_ids(messages)), "message_count": len(messages)},
        reason=reason,
        score=(0, 0, 0, group.id),
    )


def _projection_key(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> str:
    reference = "|".join(_strong_evidence(group, messages))
    if reference and _is_exact_reference_group(group):
        digest_source = f"{group.user_id}:{reference}"
    elif reference:
        digest_source = f"{group.user_id}:{group.group_key}:{reference}"
    else:
        digest_source = "\n".join(sorted(message.message_id for message in messages)) or group.group_key
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:24]
    return f"inbox:{digest}"


def _workflow_type(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> str:
    classification = group.classification if isinstance(group.classification, dict) else {}
    family = compact_text(str(classification.get("workflow_family") or group.group_type or ""))
    if family == "dashboard_bundle":
        family = ""
    if not family or family == "other":
        try:
            _facts, fallback = deterministic_classification(messages)
            family = fallback.workflow_family
        except Exception:
            family = "other"
    return family


def _strong_evidence(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> list[str]:
    evidence: list[str] = []
    if _is_exact_reference_group(group):
        evidence.append(group.group_key)
    contract = _ai_grouping_contract(group)
    if _ai_contract_allows_inbox(contract, group, messages):
        strong = contract.get("strong_evidence")
        if isinstance(strong, list):
            evidence.extend(f"ai:{compact_text(str(item))}" for item in strong if compact_text(str(item)))
        elif compact_text(str(strong or "")):
            evidence.append(f"ai:{compact_text(str(strong))}")
    thread_ids = _thread_ids(messages)
    signal_threads: dict[str, set[str]] = defaultdict(set)
    for message in messages:
        thread_id = message.gmail_thread_id or message.message_id
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        for key in CONFLICTING_SIGNAL_KEYS:
            value = compact_text(str(signals.get(key) or ""))
            if value and thread_id:
                signal_threads[f"{key}:{value}"].add(thread_id)
    for signal, covered_threads in signal_threads.items():
        if thread_ids and covered_threads == thread_ids:
            evidence.append(signal)
    headers = [
        compact_text(str(message.headers.get("references") or message.headers.get("References") or message.headers.get("in-reply-to") or message.headers.get("In-Reply-To") or ""))
        for message in messages
        if isinstance(message.headers, dict)
    ]
    shared_header_ids = _shared_header_ids(headers)
    evidence.extend(f"mail-reference:{value}" for value in shared_header_ids)
    return list(dict.fromkeys(evidence))


def _is_exact_reference_group(group: MailGroupRecord) -> bool:
    return group.group_key.startswith(EXACT_SIGNAL_PREFIXES) or group.group_key.startswith("mail-reference:")


def _ai_grouping_contract(group: MailGroupRecord) -> dict[str, Any]:
    classification = group.classification if isinstance(group.classification, dict) else {}
    contract = classification.get("grouping_contract")
    return contract if isinstance(contract, dict) else {}


def _ai_contract_allows_inbox(contract: dict[str, Any], group: MailGroupRecord, messages: list[GmailMessageRecord]) -> bool:
    if not contract:
        return _is_exact_reference_group(group)
    group_kind = compact_text(str(contract.get("group_kind") or "")).lower()
    shared_object = compact_text(str(contract.get("shared_object") or ""))
    should_show = bool(contract.get("should_show_in_inbox"))
    workflow_family = compact_text(str(contract.get("workflow_family") or "")).lower()
    risk_level = compact_text(str(contract.get("risk_level") or "")).lower()
    try:
        confidence = float(contract.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    strong = contract.get("strong_evidence")
    has_strong = bool(strong) if isinstance(strong, list) else bool(compact_text(str(strong or "")))
    has_message_evidence = _contract_has_per_message_evidence(contract, messages)
    if workflow_family in {"marketing", "newsletter"}:
        return False
    return (
        should_show
        and group_kind in {"conversation", "lifecycle"}
        and bool(shared_object)
        and has_strong
        and has_message_evidence
        and risk_level == "low"
        and confidence >= AI_LIFECYCLE_MIN_INBOX_CONFIDENCE
    )


def _crosses_unrelated_entities(messages: list[GmailMessageRecord], accepted_entity: str) -> bool:
    entities = {entity for entity, _channel in (canonical_entity_for_message(message) for message in _inbox_visible_messages(messages)) if entity}
    if len(entities) <= 1:
        return False
    if any(entities <= allowed for allowed in AI_LIFECYCLE_ALLOWED_PARTNER_ENTITIES):
        return False
    return True


def _contract_has_per_message_evidence(contract: dict[str, Any], messages: list[GmailMessageRecord]) -> bool:
    visible_ids = {message.message_id for message in _inbox_visible_messages(messages)}
    if not visible_ids:
        visible_ids = {message.message_id for message in messages}
    raw = contract.get("per_message_evidence")
    if isinstance(raw, dict):
        evidence_ids = {str(message_id) for message_id, evidence in raw.items() if compact_text(str(evidence or ""))}
    elif isinstance(raw, list):
        evidence_ids = {
            str(item.get("message_id") or item.get("id"))
            for item in raw
            if isinstance(item, dict) and compact_text(str(item.get("evidence") or item.get("reason") or ""))
        }
    else:
        evidence_ids = set()
    return bool(visible_ids) and visible_ids <= evidence_ids


def _has_conflicting_extracted_references(messages: list[GmailMessageRecord]) -> bool:
    values_by_key: dict[str, set[str]] = defaultdict(set)
    for message in _inbox_visible_messages(messages):
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        for key in CONFLICTING_SIGNAL_KEYS:
            value = compact_text(str(signals.get(key) or ""))
            if value:
                values_by_key[key].add(value.lower())
    return any(len(values) > 1 for values in values_by_key.values())


def _inbox_visible_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    return [message for message in messages if "SENT" not in {label.upper() for label in message.label_ids}]


def _visible_title(group: MailGroupRecord, entity: str, evidence: list[str]) -> str:
    title = compact_text(group.ai_title or "")
    if title and not _generic_title(title):
        return title
    reference = evidence[0].split(":", 1)[-1] if evidence else ""
    return compact_text(f"{entity} case {reference}".strip()) or entity


def _generic_title(title: str) -> bool:
    return title.lower().strip() in GENERIC_ENTITY_NAMES or title.lower().strip() in {"updates", "support case", "transfer updates"}


def _thread_ids(messages: list[GmailMessageRecord]) -> set[str]:
    return {message.gmail_thread_id or message.message_id for message in messages if message.gmail_thread_id or message.message_id}


def _entity_from_domain(domain: str) -> str:
    if not domain:
        return ""
    parts = [part for part in domain.split(".") if part and part not in {"com", "co", "in", "net", "org", "mail", "email"}]
    if not parts:
        return domain
    return parts[0].replace("-", " ").title()


def _contact_channel(display_name: str, local: str) -> str | None:
    source = compact_text(display_name or local)
    if not source or source.lower() in GENERIC_ENTITY_NAMES:
        return None
    return source[:80]


def _shared_header_ids(headers: list[str]) -> list[str]:
    ids_by_message = [set(re.findall(r"<([^>]+)>", header)) for header in headers if header]
    if len(ids_by_message) < 2:
        return []
    shared = set.intersection(*ids_by_message) if ids_by_message else set()
    return sorted(shared)[:3]
