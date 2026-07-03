from __future__ import annotations

"""Hybrid Gmail attention classification.

This module is the boundary between raw mail enrichment and dashboard/to-do
policy. It keeps text heuristics centralized and makes final ranking decisions
deterministic even when AI provides structured intent hints.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from app.db.mail_groups import GmailMessageRecord
from app.services.email_extraction import compact_text, sender_domain
from app.services.mail_group_config import MAILBOX_TASK_REFERENCE_SIGNALS


CLASSIFICATION_VERSION = "attention_classifier_v1"

ACTION_TYPES = {"pay", "reply", "confirm", "track", "review", "open", "none"}
TIMING_BANDS = {"now", "today", "later", "hidden"}

REFERENCE_SIGNAL_NAMES = list(MAILBOX_TASK_REFERENCE_SIGNALS)

FAMILY_PATTERNS: dict[str, re.Pattern[str]] = {
    "financial_transfer": re.compile(r"(?i)\b(fx[- ]?retail|forex|wire transfer|international wire|outward remittance|remittance|swift payment|trade confirmation|trade summary)\b"),
    "support_case": re.compile(r"(?i)\b(service request|support case|case reference|ticket|grievance|complaint|dispute|inquiry|query)\b"),
    "application": re.compile(r"(?i)\b(application|admission|reconsideration|financial aid|estimated costs)\b"),
    "billing": re.compile(r"(?i)\b(invoice|statement|payment|billing|subscription|renewal|card consent)\b"),
    "account_security": re.compile(r"(?i)\b(sign[ -]?in|login|security alert|verification code|otp|account access|password)\b"),
    "logistics": re.compile(r"(?i)\b(order|shipment|shipped|delivered|out for delivery|tracking|awb|booking|reservation|ride|trip|bus)\b"),
    "marketing": re.compile(r"(?i)\b(sale|offer|discount|cashback|voucher|promotion|newsletter|webinar|digest|unsubscribe)\b"),
}

ACTION_REQUIRED_RE = re.compile(
    r"(?i)\b("
    r"action required|requires? your action|please (?:reply|respond|confirm|approve|review|pay|submit|provide|upload)|"
    r"you (?:must|need to|should) (?:reply|respond|confirm|approve|review|pay|submit|provide|upload)|"
    r"payment failed|overdue|due today|due by|pending user|awaiting user|verify your|complete your"
    r")\b"
)
TERMINAL_RE = re.compile(r"(?i)\b(processed|resolved|completed|closed|settled|approved|delivered|sent successfully|made available|received and is now available)\b")
WAITING_RE = re.compile(r"(?i)\b(acknowledged|registered|we'?ll respond|will respond|working on it|under review|in progress|pending review|expected update|resolution by)\b")
STALE_SECURITY_RE = re.compile(r"(?i)\b(sign[ -]?in|login|verification code|otp|security alert)\b")
REFERENCE_RE = re.compile(r"(?i)\b(?:case reference(?: no\.?| number)?|reference(?: no\.?| number)?|service request|ticket|case|trade no|trade number|application id|dispute|order|invoice)[:#\s.-]*([a-z]{0,8}\d[a-z0-9-]{4,})\b")
DISPUTE_REFERENCE_RE = re.compile(
    r"(?ix)\bdispute(?:\s*(?:id|number|no\.?|\#)\s*[:\#.-]?|\s+(?:acknowledgement|status\s+update|interim\s+update)\s*[-:])\s*([a-z]{0,8}\d[a-z0-9-]{4,})\b"
)
MONEY_RE = re.compile(r"(?i)(?:₹|rs\.?|inr|usd|\$|eur|gbp)\s?[0-9][0-9,]*(?:\.[0-9]{1,2})?")
DATE_HINT_RE = re.compile(r"(?i)\b(?:by|before|on|due)\s+([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\b")
REJECTION_RE = re.compile(r"(?i)\b(rejected|not selected|not be selected|unable to offer|regret to inform|declined|unsuccessful)\b")
CANCELLED_RE = re.compile(r"(?i)\b(cancelled|canceled)\b")
MODIFIED_RE = re.compile(r"(?i)\b(modified|changed|rescheduled)\b")
REPLY_RE = re.compile(r"(?i)\b(re:|reply|replied|responded|response)\b")
CONFIRMED_RE = re.compile(r"(?i)\b(confirmed|confirmation|approved)\b")

POLICY_WEIGHTS = {
    "urgent_action": 92,
    "normal_action": 74,
    "waiting_followup": 48,
    "terminal_awareness": 34,
    "important_awareness": 28,
    "low_signal": 8,
}

DOMAIN_STOP_WORDS = {"bank", "co", "com", "edu", "in", "net", "org", "support", "mail", "email", "no-reply", "noreply"}
DOMAIN_SUFFIX_STOP_WORDS = {"bank", "mail", "email", "support"}


@dataclass(frozen=True)
class WorkflowFacts:
    provider: str
    domains: list[str]
    reference_id: str | None
    latest_at: str | None
    unread: bool
    sent: bool
    has_attachment: bool
    has_money: bool
    has_due_hint: bool
    labels: list[str]
    text: str


@dataclass(frozen=True)
class WorkflowClassification:
    workflow_family: str
    workflow_state: str
    requires_user_action: bool
    terminal_state: bool
    urgency: str
    due_at: str | None
    confidence: float
    reason: str
    supersedes: list[str] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "workflow_family": self.workflow_family,
            "workflow_state": self.workflow_state,
            "requires_user_action": self.requires_user_action,
            "terminal_state": self.terminal_state,
            "urgency": self.urgency,
            "due_at": self.due_at,
            "confidence": self.confidence,
            "reason": self.reason,
            "supersedes": self.supersedes,
            "source_message_ids": self.source_message_ids,
        }


@dataclass(frozen=True)
class AttentionPolicy:
    action_needed: bool
    action_type: str
    priority: int
    timing_band: str
    dashboard_visible: bool
    ranking_reason: str
    suppression_reason: str | None


def deterministic_classification(messages: list[GmailMessageRecord]) -> tuple[WorkflowFacts, WorkflowClassification]:
    facts = extract_workflow_facts(messages)
    family = classify_family(facts.text)
    terminal = bool(TERMINAL_RE.search(facts.text))
    requires_action = bool(ACTION_REQUIRED_RE.search(facts.text)) and not terminal
    waiting = bool(WAITING_RE.search(facts.text)) and not terminal and not requires_action
    workflow_state = "needs_user_action" if requires_action else "resolved" if terminal else "waiting" if waiting else "informational"
    urgency = "high" if requires_action and facts.unread else "medium" if requires_action else "low"
    due_at = extract_due_at(facts.text)
    reason = _classification_reason(family=family, state=workflow_state, facts=facts)
    confidence = 0.78 if family != "other" or requires_action or terminal else 0.52
    return facts, WorkflowClassification(
        workflow_family=family,
        workflow_state=workflow_state,
        requires_user_action=requires_action,
        terminal_state=terminal,
        urgency=urgency,
        due_at=due_at,
        confidence=confidence,
        reason=reason,
        source_message_ids=[message.message_id for message in messages],
    )


def classify_with_ai_output(messages: list[GmailMessageRecord], ai_output: dict[str, Any] | None) -> tuple[WorkflowFacts, WorkflowClassification]:
    facts, fallback = deterministic_classification(messages)
    if not isinstance(ai_output, dict):
        return facts, fallback

    family = _safe_family(ai_output.get("workflow_family") or ai_output.get("group_type"), fallback.workflow_family)
    state = _safe_state(ai_output.get("workflow_state"), fallback.workflow_state)
    terminal = _bool_or_default(ai_output.get("terminal_state"), fallback.terminal_state)
    requires_action = _bool_or_default(ai_output.get("requires_user_action") if "requires_user_action" in ai_output else ai_output.get("action_needed"), fallback.requires_user_action)
    if terminal:
        requires_action = False
    urgency = _safe_urgency(ai_output.get("urgency"), fallback.urgency)
    due_at = compact_text(str(ai_output.get("due_at") or fallback.due_at or "")) or None
    confidence = _float_between(ai_output.get("confidence"), fallback.confidence)
    reason = compact_text(str(ai_output.get("reason") or fallback.reason))[:500] or fallback.reason
    supersedes = _string_list(ai_output.get("supersedes"))[:12]
    return facts, WorkflowClassification(
        workflow_family=family,
        workflow_state=state,
        requires_user_action=requires_action,
        terminal_state=terminal,
        urgency=urgency,
        due_at=due_at,
        confidence=confidence,
        reason=reason,
        supersedes=supersedes,
        source_message_ids=[message.message_id for message in messages],
    )


def apply_attention_policy(classification: WorkflowClassification, facts: WorkflowFacts) -> AttentionPolicy:
    if classification.workflow_family in {"marketing", "newsletter"}:
        return AttentionPolicy(False, "none", 0, "hidden", False, "marketing/newsletter suppressed", "low-signal marketing or newsletter")

    if _stale_security_awareness(classification, facts):
        return AttentionPolicy(False, "none", POLICY_WEIGHTS["low_signal"], "hidden", False, "stale security alert suppressed", "stale security or verification notice")

    if classification.terminal_state:
        visible = classification.workflow_family in {"financial_transfer", "support_case", "application", "billing", "logistics", "account_security"}
        timing = "later" if visible else "hidden"
        return AttentionPolicy(
            False,
            "open",
            POLICY_WEIGHTS["terminal_awareness"],
            timing,
            visible,
            f"{classification.workflow_family} reached terminal state",
            None if visible else "terminal low-signal update",
        )

    if classification.requires_user_action:
        action_type = _policy_action_type(classification, facts)
        urgent = classification.urgency == "high" or bool(classification.due_at)
        timing = "now" if urgent else "today"
        return AttentionPolicy(
            True,
            action_type,
            POLICY_WEIGHTS["urgent_action"] if urgent else POLICY_WEIGHTS["normal_action"],
            timing,
            True,
            f"{classification.workflow_family} requires user action",
            None,
        )

    if classification.workflow_state == "waiting":
        return AttentionPolicy(
            False,
            "open",
            POLICY_WEIGHTS["waiting_followup"],
            "today",
            True,
            f"{classification.workflow_family} is waiting for an external update",
            None,
        )

    if classification.workflow_family in {"application", "billing", "account_security", "financial_transfer", "support_case"}:
        return AttentionPolicy(
            False,
            "open",
            POLICY_WEIGHTS["important_awareness"],
            "later",
            True,
            f"{classification.workflow_family} is useful awareness",
            None,
        )

    return AttentionPolicy(False, "none", POLICY_WEIGHTS["low_signal"], "hidden", False, "low-signal informational email", "not actionable or useful enough for dashboard")


def attention_enrichment_payload(
    *,
    messages: list[GmailMessageRecord],
    group_key: str,
    ai_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts, classification = classify_with_ai_output(messages, ai_output)
    policy = apply_attention_policy(classification, facts)
    latest = latest_message(messages)
    summary_hint = compact_text(str((ai_output or {}).get("ai_summary") or ""))
    title = _quality_title(
        ai_output=(ai_output or {}).get("ai_title"),
        message_title=latest.ai_title,
        subject=latest.subject,
        classification=classification,
        facts=facts,
        context=summary_hint,
    )[:180]
    summary = compact_text(str(summary_hint or latest.snippet or latest.text_body or classification.reason or title))[:1200]
    labels = sorted(set(_string_list((ai_output or {}).get("labels")) + facts.labels + [classification.workflow_family, classification.workflow_state]))[:16]
    return {
        "group_type": classification.workflow_family,
        "ai_title": title or _fallback_title(classification, facts),
        "ai_summary": summary or classification.reason,
        "labels": labels,
        "action_needed": policy.action_needed,
        "action_type": policy.action_type,
        "priority": policy.priority,
        "timing_band": policy.timing_band,
        "dashboard_visible": policy.dashboard_visible,
        "message_titles": (ai_output or {}).get("message_titles", {}),
        "classification_version": CLASSIFICATION_VERSION,
        "classification_json": {
            **classification.to_json(),
            "facts": facts_to_json(facts),
            "group_key": group_key,
            "grouping_contract": _grouping_contract(ai_output),
        },
        "classification_confidence": classification.confidence,
        "ranking_reason": policy.ranking_reason,
        "suppression_reason": policy.suppression_reason,
    }


def workflow_cluster_key(group_type: str, classification_json: dict[str, Any], _latest_at: str | None) -> str | None:
    classification = classification_json if isinstance(classification_json, dict) else {}
    facts = classification.get("facts") if isinstance(classification.get("facts"), dict) else {}
    family = str(classification.get("workflow_family") or group_type or "other")
    if family in {"other", "marketing", "newsletter"}:
        return None
    provider = str(facts.get("provider") or "unknown")
    reference = str(classification.get("reference_id") or facts.get("reference_id") or "").strip()
    if reference:
        return f"ref:{_slug(provider)}:{_slug(reference)}"
    return None


def _grouping_contract(ai_output: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ai_output, dict):
        return {}
    member_ids = ai_output.get("member_ids") or ai_output.get("member_message_ids")
    return {
        "group_kind": compact_text(str(ai_output.get("group_kind") or ""))[:80],
        "canonical_entity": compact_text(str(ai_output.get("canonical_entity") or ""))[:180],
        "shared_object": compact_text(str(ai_output.get("shared_object") or ""))[:240],
        "workflow_family": compact_text(str(ai_output.get("workflow_family") or ai_output.get("group_type") or ""))[:80],
        "member_ids": _string_list(member_ids)[:80],
        "context_sent_ids": _string_list(ai_output.get("context_sent_ids"))[:80],
        "strong_evidence": _string_list(ai_output.get("strong_evidence"))[:12],
        "weak_evidence": _string_list(ai_output.get("weak_evidence"))[:12],
        "per_message_evidence": _per_message_evidence(ai_output.get("per_message_evidence")),
        "excluded_ids": _string_list(ai_output.get("excluded_ids") or ai_output.get("excluded_message_ids"))[:50],
        "risk_level": compact_text(str(ai_output.get("risk_level") or ""))[:40],
        "should_show_in_inbox": _bool_or_default(ai_output.get("should_show_in_inbox"), False),
        "should_show_in_dashboard": _bool_or_default(ai_output.get("should_show_in_dashboard"), False),
        "confidence": _float_between(ai_output.get("confidence"), 0.0),
    }


def _per_message_evidence(value: Any) -> dict[str, str]:
    evidence: dict[str, str] = {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                items.append((item.get("message_id") or item.get("id"), item.get("evidence") or item.get("reason")))
    else:
        return evidence
    for raw_message_id, raw_reason in items:
        message_id = compact_text(str(raw_message_id or ""))
        reason = compact_text(str(raw_reason or ""))
        if message_id and reason:
            evidence[message_id[:160]] = reason[:500]
    return evidence


def is_terminal_classification(classification_json: dict[str, Any]) -> bool:
    return bool(classification_json.get("terminal_state"))


def is_status_noise_classification(classification_json: dict[str, Any]) -> bool:
    state = str(classification_json.get("workflow_state") or "")
    return state in {"waiting", "informational"} and not bool(classification_json.get("requires_user_action"))


def facts_to_json(facts: WorkflowFacts) -> dict[str, Any]:
    return {
        "provider": facts.provider,
        "domains": facts.domains,
        "reference_id": facts.reference_id,
        "latest_at": facts.latest_at,
        "unread": facts.unread,
        "sent": facts.sent,
        "has_attachment": facts.has_attachment,
        "has_money": facts.has_money,
        "has_due_hint": facts.has_due_hint,
        "labels": facts.labels,
    }


def extract_workflow_facts(messages: list[GmailMessageRecord]) -> WorkflowFacts:
    latest = latest_message(messages)
    text = _group_text(messages)
    labels = sorted({label.upper() for message in messages for label in message.label_ids})
    domains = sorted(
        {
            domain
            for message in messages
            if (domain := str(message.extracted_signals.get("sender_domain") or sender_domain(message.sender)).lower())
        }
    )
    return WorkflowFacts(
        provider=_provider_root(domains),
        domains=domains,
        reference_id=_reference_id(messages, text),
        latest_at=latest.internal_date or latest.updated_at,
        unread="UNREAD" in labels,
        sent="SENT" in labels,
        has_attachment=any(_message_has_attachment(message) for message in messages),
        has_money=bool(MONEY_RE.search(text)),
        has_due_hint=bool(DATE_HINT_RE.search(text)),
        labels=labels,
        text=text,
    )


def latest_message(messages: list[GmailMessageRecord]) -> GmailMessageRecord:
    if not messages:
        raise ValueError("Cannot classify an empty message group.")
    return max(messages, key=lambda item: item.internal_date or item.updated_at)


def classify_family(text: str) -> str:
    for family, pattern in FAMILY_PATTERNS.items():
        if pattern.search(text):
            return "newsletter" if family == "marketing" and "newsletter" in text.lower() else family
    return "conversation" if _conversation_text(text) else "other"


def extract_due_at(text: str) -> str | None:
    match = DATE_HINT_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace("/", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _classification_reason(*, family: str, state: str, facts: WorkflowFacts) -> str:
    bits = [family.replace("_", " "), state.replace("_", " ")]
    if facts.reference_id:
        bits.append(f"reference {facts.reference_id}")
    if facts.unread:
        bits.append("unread")
    return "; ".join(bits)


def _policy_action_type(classification: WorkflowClassification, facts: WorkflowFacts) -> str:
    text = _action_intent_text(facts.text)
    if classification.workflow_family == "billing" or re.search(r"\b(?:pay|payment|invoice|overdue)\b", text):
        return "pay"
    if re.search(r"\b(?:reply|respond)\b", text):
        return "reply"
    if re.search(r"\b(?:confirm|approve|rsvp|verify)\b", text):
        return "confirm"
    if classification.workflow_family == "logistics":
        return "track"
    return "review"


def _action_intent_text(value: str) -> str:
    text = value.lower()
    text = re.sub(r"\b[\w.+%-]+@[\w.-]+\.[a-z]{2,}\b", " ", text)
    text = re.sub(r"\b(?:do[-_ ]?not[-_ ]?reply|donotreply|no[-_ ]?reply|noreply)\b", " ", text)
    return text


def _stale_security_awareness(classification: WorkflowClassification, facts: WorkflowFacts) -> bool:
    if classification.requires_user_action or classification.workflow_family != "account_security":
        return False
    if not STALE_SECURITY_RE.search(facts.text):
        return False
    latest = _parse_iso(facts.latest_at)
    if latest is None:
        return False
    return datetime.now(timezone.utc) - latest > timedelta(days=2)


def _group_text(messages: list[GmailMessageRecord]) -> str:
    chunks: list[str] = []
    for message in messages:
        chunks.extend([
            message.subject or "",
            message.ai_title or "",
            message.sender or "",
            message.snippet or "",
            message.text_body or "",
            json.dumps(message.extracted_signals, ensure_ascii=True),
        ])
    return compact_text(" ".join(chunks))[:12000]


def _reference_id(messages: list[GmailMessageRecord], text: str) -> str | None:
    for message in messages:
        for signal_name in REFERENCE_SIGNAL_NAMES:
            value = message.extracted_signals.get(signal_name)
            if isinstance(value, str) and value.strip():
                return f"{signal_name}:{_slug(value)}"
    dispute_match = DISPUTE_REFERENCE_RE.search(text)
    if dispute_match:
        return f"dispute_id:{_slug(dispute_match.group(1))}"
    match = REFERENCE_RE.search(text)
    if match:
        return f"ref:{_slug(match.group(1))}"
    return None


def _provider_root(domains: list[str]) -> str:
    roots = sorted({root for domain in domains if (root := _domain_root(domain))})
    if not roots:
        return "unknown"
    if len(roots) == 1:
        return roots[0]
    return "multi-" + hashlib.sha1("|".join(roots).encode("utf-8")).hexdigest()[:12]


def _domain_root(domain: str) -> str | None:
    parts = [
        part
        for part in re.findall(r"[a-z0-9]+", domain.lower())
        if len(part) > 1 and part not in DOMAIN_STOP_WORDS
    ]
    if not parts:
        return None
    root = parts[0]
    for suffix in DOMAIN_SUFFIX_STOP_WORDS:
        if root.endswith(suffix) and len(root) > len(suffix) + 2:
            root = root[: -len(suffix)]
            break
    return _slug(root)


def _message_has_attachment(message: GmailMessageRecord) -> bool:
    def walk(part: dict[str, Any]) -> bool:
        if part.get("filename") or (isinstance(part.get("body"), dict) and part["body"].get("attachmentId")):
            return True
        return any(walk(child) for child in part.get("parts", []) if isinstance(child, dict))

    payload = message.raw_payload.get("payload") if isinstance(message.raw_payload, dict) else None
    return walk(payload) if isinstance(payload, dict) else False


def _conversation_text(text: str) -> bool:
    return bool(re.search(r"(?i)\b(re:|fwd?:|reply|respond|wrote:|from:|sent:|to:)\b", text))


def _safe_family(value: Any, fallback: str) -> str:
    normalized = compact_text(str(value or fallback)).lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in {*FAMILY_PATTERNS.keys(), "newsletter", "conversation", "other"} else fallback


def _safe_state(value: Any, fallback: str) -> str:
    normalized = compact_text(str(value or fallback)).lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized in {"needs_user_action", "waiting", "resolved", "informational"} else fallback


def _safe_urgency(value: Any, fallback: str) -> str:
    normalized = compact_text(str(value or fallback)).lower()
    return normalized if normalized in {"high", "medium", "low"} else fallback


def _bool_or_default(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return fallback


def _float_between(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [compact_text(str(item))[:80] for item in value if compact_text(str(item))]


def _quality_title(
    *,
    ai_output: Any,
    message_title: str | None,
    subject: str | None,
    classification: WorkflowClassification,
    facts: WorkflowFacts,
    context: str | None = None,
) -> str:
    context_text = compact_text(" ".join(value for value in [facts.text, context or ""] if value))
    fallback = polish_inbox_title(_fallback_title(classification, facts, context_text=context_text))
    for value in [ai_output, message_title, subject]:
        title = compact_text(str(value or ""))
        if not title:
            continue
        stateful = polish_inbox_title(_stateful_title(title, classification=classification, facts=facts, context_text=context_text))
        if not _generic_title(title, classification=classification, facts=facts):
            return stateful
        if stateful != title and _title_has_provider_and_object_or_reference(stateful, classification=classification, facts=facts):
            return stateful
    return fallback


def polish_inbox_title(title: str) -> str:
    """Remove stacked workflow states from titles before they reach Inbox UI."""
    cleaned = compact_text(title).strip(" -–—:|.,")
    if not cleaned:
        return ""
    replacements = [
        (r"\bpending\s+response\s+resolved\b", "resolved"),
        (r"\bawaiting\s+response\s+resolved\b", "resolved"),
        (r"\bunder\s+review\s+resolved\b", "resolved"),
        (r"\banswered\s+reply\b", "answered"),
        (r"\breplied\s+reply\b", "replied"),
        (r"\bresponded\s+reply\b", "responded"),
        (r"\b(successful|successfully|succeeded)\s+confirmed\b", "confirmed"),
        (r"\b(ready|available|received|processed|completed|delivered|approved)\s+confirmed\b", r"\1"),
        (r"\b(credited\s+and\s+debited)\s+confirmed\b", r"\1"),
        (r"\b(credited|debited)\s+confirmed\b", r"\1"),
        (r"\bneeds\s+(.{1,64}?)\s+action\s+required\b", r"needs \1"),
        (r"\bneeded\s+action\s+required\b", "needed"),
        (r"\brequires?\s+action\s+action\s+required\b", "action required"),
    ]
    polished = cleaned
    for pattern, replacement in replacements:
        polished = re.sub(pattern, replacement, polished, flags=re.IGNORECASE)
    if re.search(r"\b(?:admission|application|i-20|student|reconsideration)\b", polished, flags=re.IGNORECASE):
        polished = re.sub(r"\s+\barriving\b", "", polished, flags=re.IGNORECASE)
    return compact_text(polished).strip(" -–—:|.,")


def _generic_title(title: str, *, classification: WorkflowClassification, facts: WorkflowFacts) -> bool:
    normalized = _slug(title).replace("-", " ")
    family = classification.workflow_family.replace("_", " ")
    state = classification.workflow_state.replace("_", " ")
    generic_titles = {
        "application update",
        "status update",
        "status updates",
        "case update",
        "case updates",
        "service request",
        "support update",
        "support updates",
        "important update",
        "important updates",
        "order status",
        "order update",
        "order updates",
        "account update",
        "account updates",
        "update",
        "updates",
        "notification",
        "notice",
        "message",
        "new message",
        family,
        state,
        f"{family} update",
        f"{family} updates",
        f"{family} status",
    }
    if normalized in generic_titles:
        return True
    provider_titles = {_slug(_provider_title(facts)).replace("-", " "), _slug(facts.provider).replace("-", " ")}
    provider_titles.discard("")
    provider_tail_titles = {
        "updates",
        "update",
        "messages",
        "message",
        "notifications",
        "notification",
        "notices",
        "notice",
        "emails",
        "email",
        "support",
        "support case",
        "case update",
        "application update",
        "order update",
        "order updates",
        "account update",
        "service request update",
        "service request updates",
    }
    return any(normalized == f"{provider} {tail}" for provider in provider_titles for tail in provider_tail_titles)


def _stateful_title(
    title: str,
    *,
    classification: WorkflowClassification,
    facts: WorkflowFacts,
    context_text: str,
) -> str:
    cleaned = polish_inbox_title(title)
    if not cleaned:
        return _fallback_title(classification, facts, context_text=context_text)
    outcome = _title_outcome(classification, context_text or facts.text)
    if not outcome or _title_has_concrete_state(cleaned, outcome):
        return cleaned
    replacement = outcome
    substitutions = [
        r"\bstatus\s+updates?\b$",
        r"\bupdates?\b$",
        r"\bstatus\b$",
        r"\bnotices?\b$",
        r"\bnotifications?\b$",
        r"\bmessages?\b$",
        r"\bpending\s+response\b$",
        r"\bawaiting\s+response\b$",
        r"\bunder\s+review\b$",
        r"\bwaiting\b$",
    ]
    for pattern in substitutions:
        updated = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE).strip()
        if updated != cleaned:
            return polish_inbox_title(updated)
    if len(cleaned) + len(replacement) + 1 <= 180:
        return polish_inbox_title(f"{cleaned} {replacement}")
    return cleaned


def _title_has_concrete_state(title: str, outcome: str) -> bool:
    normalized = _slug(title).replace("-", " ")
    outcome_tokens = [token for token in _slug(outcome).split("-") if token]
    if outcome_tokens and all(token in normalized.split() for token in outcome_tokens):
        return True
    if outcome == "action required" and re.search(r"\b(?:needs?|needed|required|requires? action)\b", normalized):
        return True
    if outcome == "confirmed" and re.search(r"\b(?:successful|successfully|succeeded|ready|available|received|credited|debited|approved)\b", normalized):
        return True
    if outcome == "reply" and re.search(r"\b(?:reply|replied|responded|response|answered)\b", normalized):
        return True
    if outcome in {"waiting", "awaiting response", "under review", "acknowledged"} and re.search(
        r"\b(?:pending response|awaiting response|under review|waiting|acknowledged|registered)\b",
        normalized,
    ):
        return True
    state_markers = {
        "acknowledged",
        "action required",
        "approved",
        "arriving",
        "awaiting response",
        "cancelled",
        "canceled",
        "closed",
        "completed",
        "confirmed",
        "declined",
        "delayed",
        "delivered",
        "modified",
        "not selected",
        "out for delivery",
        "processed",
        "ready",
        "received",
        "rejected",
        "resolved",
        "shipped",
        "successful",
        "under review",
        "unsuccessful",
        "waiting",
    }
    return any(marker in normalized for marker in state_markers)


def _title_has_provider_and_object_or_reference(
    title: str,
    *,
    classification: WorkflowClassification,
    facts: WorkflowFacts,
) -> bool:
    normalized = _slug(title).replace("-", " ")
    title_tokens = set(normalized.split())
    if facts.reference_id and ":" in facts.reference_id:
        _signal, raw_value = facts.reference_id.split(":", 1)
        reference_tokens = set(re.findall(r"[a-z0-9]+", raw_value.lower()))
        if reference_tokens and reference_tokens <= title_tokens:
            return True
    provider_values = {_provider_title(facts), facts.provider}
    provider_tokens = {
        token
        for value in provider_values
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in DOMAIN_STOP_WORDS
    }
    if not provider_tokens & title_tokens:
        return False
    object_tokens_by_family = {
        "account_security": {"account", "access", "login", "password", "security", "sign", "signin", "verification"},
        "application": {"admission", "application", "cost", "costs", "financial", "reconsideration"},
        "billing": {"billing", "card", "invoice", "payment", "renewal", "statement", "subscription"},
        "financial_transfer": {"fx", "remittance", "swift", "trade", "transfer", "wire"},
        "logistics": {"booking", "delivery", "order", "reservation", "shipment", "tracking", "trip"},
        "support_case": {"case", "complaint", "dispute", "grievance", "query", "request", "service", "ticket"},
    }
    object_tokens = object_tokens_by_family.get(classification.workflow_family, set())
    return bool(object_tokens & title_tokens)


def _fallback_title(
    classification: WorkflowClassification,
    facts: WorkflowFacts,
    *,
    context_text: str | None = None,
) -> str:
    family = classification.workflow_family.replace("_", " ")
    provider = _provider_title(facts)
    text = context_text or facts.text
    topic = _title_topic(classification.workflow_family, text)
    reference = _title_reference_fragment(classification.workflow_family, facts.reference_id)
    if reference and reference.lower() not in topic.lower():
        topic = reference
    outcome = _title_outcome(classification, text)
    parts = [provider, topic or family]
    if outcome:
        parts.append(outcome)
    return compact_text(" ".join(part for part in parts if part)).strip()


def _provider_title(facts: WorkflowFacts) -> str:
    provider = facts.provider.replace("-", " ").strip()
    if not provider:
        return "Unknown"
    words = re.findall(r"[a-z0-9]+", provider.lower())
    return " ".join(word.upper() if word.isalpha() and len(word) <= 4 else word.title() for word in words) or "Unknown"


def _title_topic(family: str, text: str) -> str:
    lowered = text.lower()
    if family == "financial_transfer":
        if "remittance" in lowered:
            return "remittance"
        if "fx" in lowered or "trade" in lowered:
            return "trade"
        return "wire transfer" if "wire" in lowered else "transfer"
    if family == "support_case":
        return "case"
    if family == "application":
        if "reconsideration" in lowered:
            return "reconsideration"
        if "financial aid" in lowered or "estimated cost" in lowered or "estimated costs" in lowered:
            return "financial aid"
        return "application"
    if family == "logistics":
        if "order" in lowered:
            return "order"
        if any(token in lowered for token in ["trip", "ride", "bus", "reservation", "booking"]):
            return "trip"
        return "delivery"
    if family == "billing":
        if "invoice" in lowered:
            return "invoice"
        if "statement" in lowered:
            return "statement"
        return "billing"
    if family == "account_security":
        return "security"
    return family.replace("_", " ")


def _title_outcome(classification: WorkflowClassification, text: str) -> str:
    lowered = text.lower()
    logistics_context = classification.workflow_family == "logistics" or re.search(
        r"(?i)\b(order|delivery|shipment|shipping|tracking|package|parcel|booking|reservation|ride|trip|bus)\b",
        text,
    )
    if logistics_context:
        if "out for delivery" in lowered:
            return "out for delivery"
        if re.search(r"(?i)\b(arriv(?:es|ing)|expected delivery|delivery expected)\b", text):
            return "arriving"
        if "delayed" in lowered:
            return "delayed"
        if "shipped" in lowered:
            return "shipped"
        if "delivered" in lowered:
            return "delivered"
    if classification.workflow_family == "application" and REJECTION_RE.search(text):
        return "rejection"
    if classification.workflow_family == "application" and "reconsideration" in lowered and REPLY_RE.search(text):
        return "reply"
    if CANCELLED_RE.search(text):
        return "cancelled"
    if MODIFIED_RE.search(text):
        return "modified"
    if "processed" in lowered or "completed" in lowered or "settled" in lowered:
        return "processed"
    if "closed" in lowered or "resolved" in lowered:
        return "resolved"
    if CONFIRMED_RE.search(text):
        return "confirmed"
    if classification.workflow_state == "needs_user_action":
        return "action required"
    if classification.workflow_state == "waiting" and classification.workflow_family in {"support_case", "financial_transfer"}:
        if re.search(r"(?i)\b(acknowledged|registered|received your|has been received)\b", text):
            return "acknowledged"
        if re.search(r"(?i)\b(under review|in progress|working on it|checking)\b", text):
            return "under review"
        if re.search(r"(?i)\b(will respond|we'?ll respond|resolution by|expected update)\b", text):
            return "awaiting response"
        return "waiting"
    return ""


def _title_reference_fragment(family: str, reference_id: str | None) -> str:
    if not reference_id or ":" not in reference_id:
        return ""
    signal_name, raw_value = reference_id.split(":", 1)
    value = raw_value.strip("-")
    if not value:
        return ""
    display_value = value.upper() if re.search(r"[a-z]", value) and re.search(r"\d", value) else value
    label_by_signal = {
        "application_id": "application",
        "booking_id": "booking",
        "dispute_id": "dispute",
        "invoice_id": "invoice",
        "order_id": "order",
        "repair_id": "repair",
        "ticket_id": "case",
        "trade_id": "trade",
        "tracking_id": "tracking",
    }
    label = label_by_signal.get(signal_name)
    if label:
        return f"{label} {display_value}"
    if signal_name == "ref":
        if family == "support_case":
            return f"case {display_value}"
        if family == "financial_transfer":
            return f"transfer {display_value}"
    return ""


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"
