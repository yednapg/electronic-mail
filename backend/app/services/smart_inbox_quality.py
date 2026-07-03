from __future__ import annotations

"""Developer audit helpers for smart-inbox grouping and title quality."""

from dataclasses import dataclass, field
import re
from typing import Any

from app.schemas.domain import GmailThreadRow, MailboxResponse, SmartInboxResponse, SmartInboxRow


VAGUE_TITLE_WORDS = {
    "alert",
    "alerts",
    "email",
    "emails",
    "message",
    "messages",
    "notification",
    "notifications",
    "notice",
    "notices",
    "update",
    "updates",
}
GENERIC_TITLE_WORDS = VAGUE_TITLE_WORDS | {
    "account",
    "activity",
    "details",
    "information",
    "latest",
    "new",
    "regarding",
    "request",
    "status",
}
STRONG_GROUPING_METADATA_KEYS = {"reference", "topic", "workflow", "contract", "grouping_contract", "evidence", "conversation"}
SENDER_STREAM_SOURCES = {"provider_stream", "sender_domain", "sender", "newsletter"}


@dataclass(frozen=True)
class SmartInboxQualityIssue:
    """One audit issue for a visible inbox row."""

    code: str
    severity: str
    row_id: str
    title: str | None
    sender: str | None
    message_count: int
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SmartInboxQualityReport:
    """Aggregate quality signal for a rendered mailbox response."""

    total_rows: int
    grouped_rows: int
    ai_ready_rows: int
    issues: list[SmartInboxQualityIssue]

    @property
    def blocking_issue_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "blocker")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")


def audit_smart_inbox_quality(mailbox: MailboxResponse) -> SmartInboxQualityReport:
    """Flag rows that are risky for the task-based inbox contract."""
    rows = [row for section in mailbox.sections for row in section.rows]
    issues: list[SmartInboxQualityIssue] = []

    for row in rows:
        issues.extend(_audit_row(row))

    return SmartInboxQualityReport(
        total_rows=len(rows),
        grouped_rows=sum(1 for row in rows if row.message_count > 1),
        ai_ready_rows=sum(1 for row in rows if row.presentation_status == "ai_ready"),
        issues=issues,
    )


def audit_smart_inbox_response_quality(smart_inbox: SmartInboxResponse) -> SmartInboxQualityReport:
    """Flag blockers in the app-facing smart inbox payload."""
    rows = [row for section in smart_inbox.sections for row in section.rows]
    issues: list[SmartInboxQualityIssue] = []

    for row in rows:
        issues.extend(_audit_smart_row(row))

    return SmartInboxQualityReport(
        total_rows=len(rows),
        grouped_rows=sum(1 for row in rows if _smart_row_message_count(row) > 1),
        ai_ready_rows=sum(1 for row in rows if row.readiness == "ready"),
        issues=issues,
    )


def smart_inbox_response_is_product_ready(smart_inbox: SmartInboxResponse) -> bool:
    """Return true only when the rendered smart inbox is fully ready to enter."""
    report = audit_smart_inbox_response_quality(smart_inbox)
    if report.total_rows == 0 or report.blocking_issue_count > 0:
        return False
    return bool(
        smart_inbox.ready_count >= report.total_rows
        and smart_inbox.partial_count == 0
        and smart_inbox.failed_count == 0
        and report.ai_ready_rows >= report.total_rows
    )


def _audit_row(row: GmailThreadRow) -> list[SmartInboxQualityIssue]:
    issues: list[SmartInboxQualityIssue] = []
    title = _display_title(row)

    if row.presentation_status != "ai_ready":
        issues.append(
            _issue(
                "title_not_ai_ready",
                "blocker",
                row,
                f"row presentation_status is {row.presentation_status}",
            )
        )

    if _title_is_vague(title, row.sender):
        issues.append(
            _issue(
                "vague_title",
                "blocker",
                row,
                "title does not explain the current state of the task",
            )
        )

    if _title_has_stacked_state(title):
        issues.append(
            _issue(
                "stacked_state_title",
                "blocker",
                row,
                "title combines incompatible or duplicate workflow states",
            )
        )

    if row.message_count > 1:
        strong_evidence = _has_strong_grouping_evidence(row.grouping_metadata)
        if not strong_evidence:
            issues.append(
                _issue(
                    "group_without_task_evidence",
                    "blocker",
                    row,
                    "grouped row has no reference/topic/workflow evidence",
                    {"grouping_metadata": row.grouping_metadata},
                )
            )
        if _looks_like_sender_stream(row.grouping_metadata):
            issues.append(
                _issue(
                    "sender_stream_group",
                    "blocker",
                    row,
                    "grouping metadata looks sender-based instead of task-based",
                    {"grouping_metadata": row.grouping_metadata},
                )
            )
        if _child_titles_look_unrelated(row):
            issues.append(
                _issue(
                    "mixed_child_tasks",
                    "blocker",
                    row,
                    "children have low title overlap and may represent different tasks",
                    {"child_titles": [_child_title(child) for child in row.children]},
                )
            )

    return issues


def _audit_smart_row(row: SmartInboxRow) -> list[SmartInboxQualityIssue]:
    issues: list[SmartInboxQualityIssue] = []
    title = _compact(row.title)

    if row.readiness != "ready":
        issues.append(
            _smart_issue(
                "row_not_ready",
                "blocker",
                row,
                f"smart row readiness is {row.readiness}",
            )
        )

    if _title_is_vague(title, row.primary_sender):
        issues.append(
            _smart_issue(
                "vague_title",
                "blocker",
                row,
                "title does not explain the current state of the task",
            )
        )

    if _title_has_stacked_state(title):
        issues.append(
            _smart_issue(
                "stacked_state_title",
                "blocker",
                row,
                "title combines incompatible or duplicate workflow states",
            )
        )

    if _smart_row_crosses_threads(row):
        if not _smart_row_has_strong_grouping_evidence(row.grouping_reason):
            issues.append(
                _smart_issue(
                    "group_without_task_evidence",
                    "blocker",
                    row,
                    "cross-thread smart row has no concrete object, reference, or lifecycle evidence",
                    {"grouping_reason": row.grouping_reason},
                )
            )
        if _looks_like_sender_stream(_smart_grouping_metadata(row.grouping_reason)):
            issues.append(
                _smart_issue(
                    "sender_stream_group",
                    "blocker",
                    row,
                    "grouping metadata looks sender-based instead of task-based",
                    {"grouping_reason": row.grouping_reason},
                )
            )

    return issues


def _issue(
    code: str,
    severity: str,
    row: GmailThreadRow,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> SmartInboxQualityIssue:
    return SmartInboxQualityIssue(
        code=code,
        severity=severity,
        row_id=row.thread_id,
        title=_display_title(row),
        sender=row.sender,
        message_count=row.message_count,
        reason=reason,
        metadata=metadata or {},
    )


def _smart_issue(
    code: str,
    severity: str,
    row: SmartInboxRow,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> SmartInboxQualityIssue:
    return SmartInboxQualityIssue(
        code=code,
        severity=severity,
        row_id=row.id,
        title=_compact(row.title),
        sender=row.primary_sender,
        message_count=_smart_row_message_count(row),
        reason=reason,
        metadata=metadata or {},
    )


def _display_title(row: GmailThreadRow) -> str | None:
    return _compact(row.title or row.ai_title or row.latest_subject)


def _title_is_vague(title: str | None, sender: str | None) -> bool:
    if not title:
        return True
    title_tokens = _meaningful_tokens(title)
    if len(title_tokens) < 3:
        return True
    if title_tokens and title_tokens.issubset(GENERIC_TITLE_WORDS):
        return True
    sender_tokens = _meaningful_tokens(sender or "")
    if sender_tokens and title_tokens.issubset(sender_tokens | GENERIC_TITLE_WORDS):
        return True
    return False


def _title_has_stacked_state(title: str | None) -> bool:
    normalized = _compact(title or "").lower()
    if not normalized:
        return False
    stacked_patterns = [
        r"\bpending\s+response\s+resolved\b",
        r"\bawaiting\s+response\s+resolved\b",
        r"\bunder\s+review\s+resolved\b",
        r"\banswered\s+reply\b",
        r"\breplied\s+reply\b",
        r"\b(?:successful|successfully|succeeded)\s+confirmed\b",
        r"\b(?:ready|available|received|processed|completed|delivered|approved)\s+confirmed\b",
        r"\bcredited\s+and\s+debited\s+confirmed\b",
        r"\b(?:credited|debited)\s+confirmed\b",
        r"\bneeds\s+.{1,64}?\s+action\s+required\b",
        r"\bneeded\s+action\s+required\b",
    ]
    if any(re.search(pattern, normalized) for pattern in stacked_patterns):
        return True
    return bool(re.search(r"\b(?:admission|application|i-20|student|reconsideration)\b.*\barriving\b", normalized))


def _has_strong_grouping_evidence(metadata: dict[str, Any]) -> bool:
    if not metadata:
        return False
    for key in STRONG_GROUPING_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, dict) and _metadata_has_content(value):
            return True
        if isinstance(value, str) and value.strip():
            return True
    source = metadata.get("source")
    if isinstance(source, str) and "reference" in source.lower():
        return True
    return False


def _smart_row_has_strong_grouping_evidence(reason: dict[str, Any]) -> bool:
    if not reason:
        return False
    if _metadata_has_content(reason.get("evidence")):
        return True
    canonical_key = reason.get("canonical_key")
    if isinstance(canonical_key, str) and canonical_key.strip():
        return True
    metadata = _smart_grouping_metadata(reason)
    if _has_strong_grouping_evidence(metadata):
        return True
    evidence = metadata.get("evidence")
    if _metadata_has_content(evidence):
        return True
    contract = metadata.get("grouping_contract")
    if isinstance(contract, dict) and _metadata_has_content(contract.get("shared_object")) and _metadata_has_content(contract.get("strong_evidence")):
        return True
    if isinstance(evidence, dict):
        contract = evidence.get("ai_contract")
        if isinstance(contract, dict) and _metadata_has_content(contract.get("shared_object")) and _metadata_has_content(contract.get("strong_evidence")):
            return True
        strong = evidence.get("strong_evidence")
        if _metadata_has_content(strong):
            return True
    return False


def _smart_grouping_metadata(reason: dict[str, Any]) -> dict[str, Any]:
    metadata = reason.get("metadata")
    return metadata if isinstance(metadata, dict) else reason


def _metadata_has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_metadata_has_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_metadata_has_content(item) for item in value)
    return bool(value)


def _looks_like_sender_stream(metadata: dict[str, Any]) -> bool:
    source = metadata.get("source")
    if isinstance(source, str) and source.lower() in SENDER_STREAM_SOURCES:
        return True
    cluster_key = metadata.get("cluster_key")
    return isinstance(cluster_key, str) and any(part in cluster_key.lower() for part in ("provider-stream", "sender:"))


def _smart_row_message_count(row: SmartInboxRow) -> int:
    return max(1, len(row.source_message_ids), len(row.source_thread_ids))


def _smart_row_crosses_threads(row: SmartInboxRow) -> bool:
    return len({thread_id for thread_id in row.source_thread_ids if thread_id}) > 1


def _child_titles_look_unrelated(row: GmailThreadRow) -> bool:
    if len(row.children) < 2:
        return False
    child_token_sets = [_meaningful_tokens(_child_title(child)) for child in row.children]
    child_token_sets = [tokens for tokens in child_token_sets if tokens]
    if len(child_token_sets) < 2:
        return False
    shared = set.intersection(*child_token_sets)
    union = set.union(*child_token_sets)
    return bool(union) and len(shared) / len(union) < 0.12


def _child_title(child) -> str:
    return _compact(child.ai_title or child.subject or child.snippet or "") or ""


def _meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "from", "your", "you"}
    }


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    compacted = " ".join(value.split())
    return compacted or None
