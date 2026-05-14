from __future__ import annotations

"""Fast first-run dashboard feed built directly from recent Gmail thread projections."""

from datetime import datetime, timedelta, timezone
import re

from app.db.models import StoredGmailThreadProjection
from app.db.repository import DEFAULT_USER_ID, list_recent_gmail_thread_projections, utc_now_iso
from app.schemas.domain import AttentionItem, AttentionItemDetail, FeedResponse

FIRST_RUN_DASHBOARD_DAYS = 7
FIRST_RUN_DASHBOARD_LIMIT = 80
ACTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(action required|required action|urgent|asap|overdue|due today|expires today)\b",
        r"\b(please|kindly)\s+(review|reply|respond|confirm|approve|send|share|upload|submit|pay|complete)\b",
        r"\b(reply|respond|review|confirm|approve|send|share|upload|submit|pay|complete)\s+(by|before|today|within)\b",
        r"\b(payment due|bill due|invoice|application update|security alert)\b",
    ]
]
WORTH_KNOWING_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(statement|receipt|confirmation|confirmed|approved|processed|completed|welcome|security alert)\b",
        r"\b(account|application|domain|cloudflare|supabase|openai|zerodha|bank)\b",
    ]
]


def build_fast_dashboard_feed_from_gmail_threads(
    database_path: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    current_time: str | None = None,
    days: int = FIRST_RUN_DASHBOARD_DAYS,
) -> FeedResponse | None:
    """Build a bounded dashboard feed from recent Gmail thread projections."""
    now = _parse_datetime(current_time) if current_time else datetime.now(timezone.utc)
    since_iso = (now - timedelta(days=days)).isoformat()
    projections = list_recent_gmail_thread_projections(
        database_path,
        user_id=user_id,
        since_iso=since_iso,
        label="inbox",
        limit=FIRST_RUN_DASHBOARD_LIMIT,
    )
    if not projections:
        return None

    now_items: list[AttentionItem] = []
    today_items: list[AttentionItem] = []
    worth_knowing_items: list[AttentionItem] = []

    for projection in projections:
        bucket = _bucket_projection(projection, now)
        item = _to_attention_item(projection, user_id=user_id, timing_band=bucket)
        if bucket == "now":
            now_items.append(item)
        elif bucket == "today":
            today_items.append(item)
        elif bucket == "later":
            worth_knowing_items.append(item)

    return FeedResponse(
        now=now_items[:5],
        today=today_items[:8],
        worth_knowing=worth_knowing_items[:5],
    )


def count_fast_dashboard_items(feed: FeedResponse | None) -> int:
    """Return total visible fast-dashboard item count."""
    if feed is None:
        return 0
    return len(feed.now) + len(feed.today) + len(feed.worth_knowing)


def _bucket_projection(projection: StoredGmailThreadProjection, now: datetime) -> str:
    text = _projection_text(projection)
    received_at = _parse_datetime(projection.latest_received_at)
    age_hours = (now - received_at).total_seconds() / 3600
    is_important = "IMPORTANT" in projection.label_ids

    if projection.unread and (is_important or _matches(ACTION_PATTERNS, text) or age_hours <= 24):
        return "now"
    if is_important or _matches(ACTION_PATTERNS, text) or age_hours <= 72:
        return "today"
    if _matches(WORTH_KNOWING_PATTERNS, text):
        return "later"
    return "hidden"


def _to_attention_item(projection: StoredGmailThreadProjection, *, user_id: str, timing_band: str) -> AttentionItem:
    subject = (projection.latest_subject or "Gmail thread").strip() or "Gmail thread"
    sender = (projection.latest_sender or "Gmail").strip() or "Gmail"
    snippet = (projection.snippet or "").strip()
    needs_action = timing_band in {"now", "today"}
    created_at = projection.latest_received_at or projection.updated_at or utc_now_iso()
    action = _primary_action(subject, snippet) if needs_action else "Open thread"

    return AttentionItem(
        id=f"fast-gmail:{projection.thread_id}",
        entity_id=f"gmail-thread:{projection.thread_id}",
        user_id=user_id,
        need_type="decision" if needs_action else "awareness",
        action_type="external" if needs_action else "none",
        effort_level="quick",
        timing_band=timing_band,
        action_confidence="medium" if needs_action else "low",
        primary_action=action,
        fallback_action="open",
        title=_title_for_thread(subject, needs_action=needs_action),
        why_this_is_here=_why_this_is_here(projection, timing_band),
        detail=AttentionItemDetail(
            body=[line for line in [f"From {sender}", snippet] if line],
            action_label="Open thread",
            source_label="Gmail",
        ),
        importance_level="high" if timing_band == "now" else "medium" if timing_band == "today" else "low",
        lifecycle_state="active" if needs_action else "scheduled",
        current_state="open" if needs_action else "waiting",
        source="gmail",
        gmail_thread_id=projection.thread_id,
        gmail_thread_action="mark_read" if projection.unread else None,
        trace_id=f"fast-gmail:{projection.thread_id}",
        created_at=created_at,
    )


def _projection_text(projection: StoredGmailThreadProjection) -> str:
    return " ".join(
        part
        for part in [projection.latest_subject or "", projection.latest_sender or "", projection.snippet or "", " ".join(projection.label_ids)]
        if part
    )


def _matches(patterns: list[re.Pattern[str]], value: str) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _primary_action(subject: str, snippet: str) -> str:
    text = f"{subject} {snippet}".lower()
    if "pay" in text or "payment" in text or "bill" in text or "invoice" in text:
        return "Check payment"
    if "confirm" in text or "rsvp" in text:
        return "Confirm"
    if "reply" in text or "respond" in text:
        return "Reply"
    if "review" in text or "application" in text:
        return "Review"
    return "Open thread"


def _title_for_thread(subject: str, *, needs_action: bool) -> str:
    if needs_action:
        return subject if _starts_with_action(subject) else f"Review {subject}"
    return subject


def _starts_with_action(value: str) -> bool:
    return value.strip().lower().startswith(("review", "reply", "confirm", "pay", "send", "submit", "open"))


def _why_this_is_here(projection: StoredGmailThreadProjection, timing_band: str) -> str:
    if timing_band == "now":
        return "Recent unread or important Gmail thread that may need attention."
    if timing_band == "today":
        return "Recent Gmail thread with signals that it may need a decision or follow-up."
    return "Recent Gmail thread worth knowing before you start work."


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
