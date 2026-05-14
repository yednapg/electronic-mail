from __future__ import annotations

"""Mail group product pipeline: grouping, enrichment, dashboard mapping."""

from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import hashlib
import json
import re
from typing import Any

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    MailGroupRecord,
    count_mail_groups,
    count_mail_groups_by_enrichment_status,
    get_import_state,
    get_mail_group_by_key,
    get_mail_group_detail,
    list_dashboard_mail_groups,
    list_group_messages,
    list_mail_groups,
    list_recent_messages,
    mark_import_completed,
    mark_import_error,
    replace_group_members,
    upsert_mail_group,
    user_can_write_gmail,
)
from app.schemas.domain import (
    AttentionItem,
    AttentionItemDetail,
    DashboardBriefing,
    DashboardProfile,
    DashboardResponse,
    FeedResponse,
    GmailThreadRow,
    GmailThreadSection,
    GoogleAuthState,
    MailboxLabel,
    MailboxResponse,
    MailboxSyncStateResponse,
    ThreadMessage,
    ThreadReaderResponse,
)
from app.services.email_extraction import clean_ai_text, compact_text, sender_domain

FIRST_BATCH_SIZE = 50
BACKFILL_BATCH_SIZE = 30
DASHBOARD_DAYS = 7
ACTION_WORD_RE = re.compile(r"(?i)\b(reply|respond|confirm|approve|review|pay|payment failed|due|overdue|urgent|action required|scheduled|interview|check.?in|delivered|out for delivery|ticket|case|invoice|receipt)\b")
LIFECYCLE_WORD_RE = re.compile(r"(?i)\b(order|shipped|delivered|tracking|invoice|payment|booking|flight|application|ticket|case|request|approved|received)\b")
ALLOWED_ACTION_TYPES = {"pay", "reply", "confirm", "track", "review", "open", "none"}
ALLOWED_TIMING_BANDS = {"now", "today", "later", "hidden"}


def enqueue_first_run(settings: Settings, *, user_id: str) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"first-run:{user_id}",
        priority=100,
        payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "first_run": True},
    )
    return job.id


def enqueue_mailbox_sync(settings: Settings, *, user_id: str) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="default",
        user_id=user_id,
        dedupe_key=f"mailbox-sync:{user_id}",
        priority=20,
        payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE, "first_run": False},
    )
    return job.id


def rebuild_mail_groups(
    settings: Settings,
    *,
    user_id: str,
    limit: int = 500,
    use_ai: bool = True,
    max_ai_groups: int | None = None,
) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_recent_messages(database_url, user_id=user_id, limit=limit)
    candidates = _candidate_groups(messages)
    touched = 0
    ai_groups_processed = 0
    for group_key, grouped_messages in candidates.items():
        latest = max(grouped_messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(grouped_messages)
        existing = get_mail_group_by_key(database_url, user_id=user_id, group_key=group_key)
        if use_ai and existing is not None and existing.generated_from_hash == group_hash and existing.generated_at is not None:
            continue
        if use_ai and max_ai_groups is not None and ai_groups_processed >= max_ai_groups:
            continue
        if not use_ai and existing is not None and existing.generated_from_hash == group_hash and existing.generated_at is not None:
            enrichment = _enrichment_from_existing(existing)
            enrichment["_ai_ready"] = existing.enrichment_status == "ready"
            generated_at = existing.generated_at
        else:
            enrichment = enrich_group(
                settings,
                messages=grouped_messages,
                group_key=group_key,
                generated_from_hash=group_hash,
                use_ai=use_ai,
            )
            generated_at = datetime.now(timezone.utc).isoformat() if enrichment.get("_ai_ready") else None
            if use_ai:
                ai_groups_processed += 1
        ai_ready = bool(enrichment.get("_ai_ready"))
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=enrichment["group_type"],
            ai_title=enrichment["ai_title"],
            ai_summary=enrichment["ai_summary"],
            labels=enrichment["labels"],
            action_needed=bool(enrichment["action_needed"]),
            action_type=str(enrichment["action_type"]),
            priority=int(enrichment["priority"]),
            timing_band=str(enrichment["timing_band"]),
            dashboard_visible=bool(enrichment["dashboard_visible"]) if ai_ready else False,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=generated_at,
            enrichment_status="ready" if ai_ready else "pending",
            membership_source=_member_reason(grouped_messages[0]),
            ai_model=settings.openai_model if ai_ready else None,
            ai_error=str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None,
            ai_generated_at=generated_at,
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, _member_reason(message), 0.9) for message in grouped_messages],
        )
        touched += 1
    return touched


def run_first_run_ai_grouping(settings: Settings, *, user_id: str, limit: int = FIRST_BATCH_SIZE) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        mark_import_error(database_url, user_id=user_id, error="Gmail is disconnected or data deletion is active.")
        raise RuntimeError("Gmail is disconnected or data deletion is active.")
    messages = list_recent_messages(database_url, user_id=user_id, limit=limit)
    if not messages:
        mark_import_error(database_url, user_id=user_id, error="No Gmail messages were imported for first-run grouping.")
        raise RuntimeError("No Gmail messages were imported for first-run grouping.")
    try:
        grouped_outputs = _ai_batch_group_messages(settings, messages=messages)
        created, visible_created = _store_ai_batch_groups(settings, user_id=user_id, messages=messages, grouped_outputs=grouped_outputs)
        if created < 3 and visible_created < 1:
            raise RuntimeError("AI grouping returned insufficient product-quality groups.")
        mark_import_completed(database_url, user_id=user_id, groups_ready=True, dashboard_ready=True)
        return created
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=f"first_run_ai_grouping failed: {exc}")
        raise


def build_dashboard_response(settings: Settings, *, user_id: str | None, auth: GoogleAuthState, profile: DashboardProfile | None = None) -> DashboardResponse:
    if user_id is None or not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())
    since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    database_url = str(settings.database_path)
    groups = list_dashboard_mail_groups(database_url, user_id=user_id, since_iso=since)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    feed = FeedResponse()
    for group in groups:
        item = _attention_item_from_group(group)
        if group.timing_band == "now":
            feed.now.append(item)
        elif group.timing_band == "today":
            feed.today.append(item)
        else:
            feed.worth_knowing.append(item)
    return DashboardResponse(
        auth=auth,
        profile=profile,
        briefing=DashboardBriefing(
            headline="Your dashboard is ready.",
            brief="Built from grouped Gmail work, not raw inbox noise.",
        ),
        feed=feed,
        runtime_status={
            "feed_source": "ai_mail_groups" if groups else "empty",
            "canonical_ready": bool(status_counts.get("ready", 0)),
            "ai_groups_ready": bool(status_counts.get("ready", 0)),
            "pending_group_count": status_counts.get("pending", 0),
            "ready_group_count": status_counts.get("ready", 0),
            "last_mailbox_sync_at": None,
            "queue_lag_seconds": None,
            "stale_reason": None,
        },
    )


def build_mailbox_response(settings: Settings, *, user_id: str, label: str = "inbox", limit: int = 150, cursor: str | None = None) -> MailboxResponse:
    database_url = str(settings.database_path)
    groups = list_mail_groups(database_url, user_id=user_id, limit=limit)
    mailbox_label = _mailbox_label(label)
    rows = [
        _gmail_row_from_group(group, list_group_messages(database_url, user_id=user_id, group_id=group.id))
        for group in groups
        if _group_matches_label(group, mailbox_label)
    ]
    return MailboxResponse(label=mailbox_label, total_threads=len(rows), next_cursor=None, sections=_bucket_rows(rows))


def build_mailbox_sync_state(settings: Settings, *, user_id: str) -> MailboxSyncStateResponse:
    state = get_import_state(str(settings.database_path), user_id=user_id)
    connected = user_can_write_gmail(str(settings.database_path), user_id=user_id)
    return MailboxSyncStateResponse(
        connected=connected,
        last_history_id=state.last_history_id if state else None,
        last_full_sync_at=state.last_import_completed_at if state else None,
        watch_expiration_at=None,
        last_sync_started_at=state.last_import_started_at if state else None,
        last_sync_completed_at=state.last_import_completed_at if state else None,
        last_sync_error=state.last_sync_error if state else None,
        total_threads=count_mail_groups(str(settings.database_path), user_id=user_id),
    )


def build_group_detail_response(settings: Settings, *, user_id: str, group_id: str, limit: int = 50, offset: int = 0) -> ThreadReaderResponse | None:
    detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=group_id)
    if detail is None:
        return None
    messages = detail.messages[offset : offset + limit]
    return ThreadReaderResponse(
        entity_id=detail.group.id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=detail.group.id,
        subject=detail.group.ai_title,
        total_messages=len(detail.messages),
        limit=limit,
        offset=offset,
        has_more=offset + len(messages) < len(detail.messages),
        messages=[_thread_message_from_gmail(message) for message in messages],
    )


def enrich_group(
    settings: Settings,
    *,
    messages: list[GmailMessageRecord],
    group_key: str,
    generated_from_hash: str,
    use_ai: bool = True,
) -> dict[str, Any]:
    fallback = _fallback_enrichment(messages, group_key)
    if not use_ai or not settings.openai_configured:
        return fallback
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=8.0)
        prompt = {
            "task": "Turn candidate Gmail messages into one real-life mail group.",
            "rules": [
                "Use the candidate only if the messages represent the same real-life thing.",
                "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
                "Do not invent pay, reply, or confirm actions from boilerplate words.",
                "Return a concise human title, not an email subject with Re/Fwd prefixes.",
                "Return a summary that explains what happened and what the user should know.",
            ],
            "required_json_fields": ["group_type", "ai_title", "ai_summary", "labels", "action_needed", "action_type", "priority", "timing_band", "dashboard_visible"],
            "allowed_action_type": sorted(ALLOWED_ACTION_TYPES),
            "allowed_timing_band": sorted(ALLOWED_TIMING_BANDS),
            "messages": [
                {
                    "subject": message.subject,
                    "sender": message.sender,
                    "date": message.internal_date,
                    "snippet": message.snippet,
                    "text": (message.text_body or "")[:2500],
                    "signals": message.extracted_signals,
                }
                for message in messages[:12]
            ],
        }
        response = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": "Return strict JSON only. Be concise. Dashboard should show only actionable or very useful recent groups."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            text={"format": {"type": "json_object"}},
            store=False,
        )
        text_output = getattr(response, "output_text", "") or ""
        parsed = json.loads(text_output)
        enriched = _normalize_enrichment(parsed, fallback)
        enriched["labels"] = sorted(set(enriched["labels"]) | {label for message in messages for label in message.label_ids})
        enriched["_ai_ready"] = True
        return enriched
    except Exception:
        fallback["_fallback"] = True
        fallback["_ai_error"] = "per-group AI enrichment failed"
        return fallback


def _ai_batch_group_messages(settings: Settings, *, messages: list[GmailMessageRecord]) -> list[dict[str, Any]]:
    if not settings.openai_configured:
        raise RuntimeError("OpenAI is not configured for first-run AI grouping.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, timeout=45.0)
    prompt = {
        "task": "Create product-visible mail groups from recent Gmail messages.",
        "rules": [
            "You are creating product-visible mail groups, not summarizing individual emails.",
            "Group emails by the real-life thing they represent.",
            "Use Gmail threadId as one signal, not the final grouping.",
            "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
            "Do not invent pay, reply, or confirm actions from boilerplate words.",
            "Return concise human titles, not email subjects with Re/Fwd prefixes.",
            "Return summaries that explain what happened and what the user should know.",
            "A message can appear in only one output group.",
        ],
        "allowed_action_type": sorted(ALLOWED_ACTION_TYPES),
        "allowed_timing_band": sorted(ALLOWED_TIMING_BANDS),
        "dashboard_visibility_rule": "true only if action_needed is true or the recent awareness is genuinely important.",
        "candidate_context": _candidate_context_for_ai(messages),
        "messages": [_message_for_ai(message) for message in messages],
        "output_schema": {
            "groups": [
                {
                    "client_group_key": "stable short key",
                    "group_type": "conversation | support_case | order_lifecycle | billing | travel | application | newsletter | marketing | account_lifecycle | other",
                    "ai_title": "human product title",
                    "ai_summary": "one or two useful sentences",
                    "labels": ["short", "labels"],
                    "action_needed": False,
                    "action_type": "pay | reply | confirm | track | review | open | none",
                    "priority": 0,
                    "timing_band": "now | today | later | hidden",
                    "dashboard_visible": False,
                    "member_message_ids": ["gmail-message-id"],
                }
            ]
        },
    }
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "Return strict JSON only. Do not include markdown. "
                    "Every member_message_id must come from the provided input."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
        ],
        text={"format": {"type": "json_object"}},
        store=False,
    )
    parsed = json.loads(getattr(response, "output_text", "") or "{}")
    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise RuntimeError("AI grouping response did not contain a groups array.")
    return [group for group in groups if isinstance(group, dict)]


def _store_ai_batch_groups(
    settings: Settings,
    *,
    user_id: str,
    messages: list[GmailMessageRecord],
    grouped_outputs: list[dict[str, Any]],
) -> tuple[int, int]:
    database_url = str(settings.database_path)
    by_id = {message.message_id: message for message in messages}
    used_ids: set[str] = set()
    created = 0
    visible_created = 0
    for index, output in enumerate(grouped_outputs):
        member_ids = []
        raw_member_ids = output.get("member_message_ids", [])
        if not isinstance(raw_member_ids, list):
            continue
        for item in raw_member_ids:
            message_id = str(item)
            if message_id in by_id and message_id not in used_ids and message_id not in member_ids:
                member_ids.append(message_id)
        if not member_ids:
            continue
        title = compact_text(str(output.get("ai_title") or ""))
        summary = compact_text(str(output.get("ai_summary") or ""))
        if not title or not summary:
            continue
        used_ids.update(member_ids)
        members = [by_id[message_id] for message_id in member_ids]
        latest = max(members, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(members)
        group_key = _ai_group_key(output.get("client_group_key"), members, index)
        action_type = _normalize_action_type(output.get("action_type"))
        timing_band = _normalize_timing_band(output.get("timing_band"))
        labels = _normalize_labels(output.get("labels"), members)
        dashboard_visible = bool(output.get("dashboard_visible")) and bool(title and summary)
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=compact_text(str(output.get("group_type") or "other"))[:80] or "other",
            ai_title=title[:180],
            ai_summary=summary[:1200],
            labels=labels,
            action_needed=bool(output.get("action_needed")),
            action_type=action_type,
            priority=max(0, min(100, _int_or_default(output.get("priority"), 0))),
            timing_band=timing_band,
            dashboard_visible=dashboard_visible,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=datetime.now(timezone.utc).isoformat(),
            enrichment_status="ready",
            membership_source="ai_batch",
            ai_model=settings.openai_model,
            ai_error=None,
            ai_generated_at=datetime.now(timezone.utc).isoformat(),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, "ai_batch", 0.95) for message in members],
        )
        created += 1
        if dashboard_visible:
            visible_created += 1
    return created, visible_created


def _message_for_ai(message: GmailMessageRecord) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "gmail_thread_id": message.gmail_thread_id,
        "subject": message.subject,
        "sender": message.sender,
        "recipients": message.recipients,
        "date": message.internal_date,
        "snippet": message.snippet,
        "text": clean_ai_text(message.text_body or message.snippet or "", max_chars=2000),
        "signals": message.extracted_signals,
        "labels": message.label_ids,
    }


def _candidate_context_for_ai(messages: list[GmailMessageRecord]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "gmail_thread_clusters": defaultdict(list),
        "subject_domain_clusters": defaultdict(list),
        "strict_signal_clusters": defaultdict(list),
    }
    for message in messages:
        if message.gmail_thread_id:
            context["gmail_thread_clusters"][message.gmail_thread_id].append(message.message_id)
        signals = message.extracted_signals
        domain = str(signals.get("sender_domain") or sender_domain(message.sender))
        subject = str(signals.get("normalized_subject") or message.subject or "")[:120]
        if subject:
            context["subject_domain_clusters"][f"{domain}:{subject}"].append(message.message_id)
        for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                context["strict_signal_clusters"][f"{signal_name}:{domain}:{value}"].append(message.message_id)
    return {
        key: {cluster_key: ids for cluster_key, ids in value.items() if len(ids) > 1}
        for key, value in context.items()
    }


def _ai_group_key(_client_group_key: Any, members: list[GmailMessageRecord], _index: int) -> str:
    member_hash = hashlib.sha256("\n".join(sorted(message.message_id for message in members)).encode("utf-8")).hexdigest()[:24]
    return f"ai:{member_hash}"


def _normalize_action_type(value: Any) -> str:
    action_type = compact_text(str(value or "none")).lower()
    return action_type if action_type in ALLOWED_ACTION_TYPES else "open"


def _normalize_timing_band(value: Any) -> str:
    timing_band = compact_text(str(value or "later")).lower()
    return timing_band if timing_band in ALLOWED_TIMING_BANDS else "later"


def _normalize_labels(value: Any, members: list[GmailMessageRecord]) -> list[str]:
    output = [str(label).strip()[:60] for label in value if str(label).strip()] if isinstance(value, list) else []
    gmail_labels = [label for message in members for label in message.label_ids]
    return sorted(set(output + gmail_labels))[:16]


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _enrichment_from_existing(group: MailGroupRecord) -> dict[str, Any]:
    return {
        "group_type": group.group_type,
        "ai_title": group.ai_title,
        "ai_summary": group.ai_summary,
        "labels": group.labels,
        "action_needed": group.action_needed,
        "action_type": group.action_type,
        "priority": group.priority,
        "timing_band": group.timing_band,
        "dashboard_visible": group.dashboard_visible,
    }


def _candidate_groups(messages: list[GmailMessageRecord]) -> dict[str, list[GmailMessageRecord]]:
    groups: dict[str, list[GmailMessageRecord]] = defaultdict(list)
    for message in messages:
        signals = message.extracted_signals
        domain = str(signals.get("sender_domain") or sender_domain(message.sender))
        for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                groups[f"{signal_name}:{domain}:{value}"].append(message)
                break
        else:
            if message.gmail_thread_id:
                groups[f"gmail-thread:{message.gmail_thread_id}"].append(message)
            else:
                subject = str(signals.get("normalized_subject") or message.subject or message.message_id)[:120]
                groups[f"subject-domain:{domain}:{subject}"].append(message)
    return dict(groups)


def _fallback_enrichment(messages: list[GmailMessageRecord], group_key: str) -> dict[str, Any]:
    latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
    text = " ".join([latest.subject or "", latest.snippet or "", latest.text_body or ""])
    lifecycle = bool(LIFECYCLE_WORD_RE.search(text))
    action = bool(ACTION_WORD_RE.search(text))
    group_type = _group_type_from_key(group_key, lifecycle=lifecycle)
    gmail_labels = sorted({label for message in messages for label in message.label_ids})
    labels = [group_type, *gmail_labels]
    if action:
        labels.append("needs_action")
    return {
        "group_type": group_type,
        "ai_title": _fallback_title(latest, group_type),
        "ai_summary": compact_text(latest.snippet or latest.text_body or latest.subject or "Gmail update")[:500],
        "labels": labels,
        "action_needed": action,
        "action_type": _action_type(text),
        "priority": 80 if action else 30 if lifecycle else 10,
        "timing_band": "now" if action and "UNREAD" in latest.label_ids else "today" if action else "later",
        "dashboard_visible": action or lifecycle,
    }


def _normalize_enrichment(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    timing = str(parsed.get("timing_band") or fallback["timing_band"])
    if timing not in ALLOWED_TIMING_BANDS:
        timing = fallback["timing_band"]
    action_type = _normalize_action_type(parsed.get("action_type") or fallback["action_type"])
    return {
        "group_type": str(parsed.get("group_type") or fallback["group_type"])[:80],
        "ai_title": compact_text(str(parsed.get("ai_title") or fallback["ai_title"]))[:180],
        "ai_summary": compact_text(str(parsed.get("ai_summary") or fallback["ai_summary"]))[:1200],
        "labels": [str(label)[:60] for label in parsed.get("labels", fallback["labels"]) if str(label).strip()][:10]
        if isinstance(parsed.get("labels", fallback["labels"]), list)
        else fallback["labels"],
        "action_needed": bool(parsed.get("action_needed", fallback["action_needed"])),
        "action_type": action_type,
        "priority": max(0, min(100, int(parsed.get("priority", fallback["priority"])))),
        "timing_band": timing,
        "dashboard_visible": bool(parsed.get("dashboard_visible", fallback["dashboard_visible"])),
    }


def _group_hash(messages: list[GmailMessageRecord]) -> str:
    parts = [f"{message.message_id}:{message.history_id}:{message.body_hash}:{message.internal_date}" for message in sorted(messages, key=lambda item: item.message_id)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _member_reason(message: GmailMessageRecord) -> str:
    if message.gmail_thread_id:
        return "gmail_thread"
    signals = message.extracted_signals
    for key in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
        if signals.get(key):
            return key
    return "subject_domain"


def _group_type_from_key(group_key: str, *, lifecycle: bool) -> str:
    if group_key.startswith("order_id") or group_key.startswith("tracking_id"):
        return "order_lifecycle"
    if group_key.startswith("ticket_id"):
        return "support_case"
    if group_key.startswith("invoice_id"):
        return "billing"
    if group_key.startswith("booking_id"):
        return "travel"
    if group_key.startswith("application_id"):
        return "application"
    if lifecycle:
        return "lifecycle"
    return "conversation"


def _fallback_title(message: GmailMessageRecord, group_type: str) -> str:
    subject = compact_text(message.subject or "")
    if subject:
        return subject
    domain = sender_domain(message.sender)
    return f"{group_type.replace('_', ' ').title()} from {domain}"


def _action_type(text: str) -> str:
    lowered = text.lower()
    if "pay" in lowered or "payment" in lowered or "invoice" in lowered:
        return "pay"
    if "confirm" in lowered or "rsvp" in lowered:
        return "confirm"
    if "reply" in lowered or "respond" in lowered:
        return "reply"
    if "approve" in lowered:
        return "confirm"
    if "track" in lowered or "delivered" in lowered:
        return "track"
    return "open"


def _attention_item_from_group(group: MailGroupRecord) -> AttentionItem:
    action_type = "external" if group.action_needed else "none"
    return AttentionItem(
        id=f"mail-group:{group.id}",
        entity_id=group.id,
        user_id=group.user_id,
        need_type="decision" if group.action_needed else "awareness",
        action_type=action_type,
        effort_level="quick",
        timing_band=group.timing_band if group.timing_band in {"now", "today", "later", "hidden"} else "later",
        action_confidence="medium",
        primary_action=group.action_type or "open",
        fallback_action="open",
        title=group.ai_title,
        why_this_is_here=group.ai_summary,
        detail=AttentionItemDetail(
            body=[group.ai_summary],
            action_label="Open group",
            source_label="Gmail",
        ),
        importance_level="high" if group.priority >= 70 else "medium" if group.priority >= 35 else "low",
        lifecycle_state="active" if group.action_needed else "scheduled",
        current_state="open" if group.action_needed else "waiting",
        source="gmail",
        gmail_thread_id=group.id,
        gmail_thread_action=None,
        trace_id=f"mail-group:{group.id}",
        created_at=group.created_at,
    )


def _gmail_row_from_group(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> GmailThreadRow:
    latest_message = max(messages, key=lambda item: item.internal_date or item.updated_at) if messages else None
    participants = _participants(messages)
    return GmailThreadRow(
        thread_id=group.id,
        entity_id=group.id,
        latest_source_record_id=group.latest_message_id or group.id,
        latest_received_at=group.latest_message_at or group.updated_at,
        latest_subject=group.ai_title,
        latest_sender=latest_message.sender if latest_message else None,
        participants=participants,
        message_count=max(1, len(messages)),
        summary=group.ai_summary,
        snippet=group.ai_summary,
        label_ids=group.labels,
        unread="unread" in {label.lower() for label in group.labels},
        current_state="open" if group.action_needed else "waiting",
        lifecycle_state="active" if group.action_needed else "scheduled",
        lifecycle_updates=[
            {
                "source_record_id": message.message_id,
                "received_at": message.internal_date or message.updated_at,
                "subject": message.subject or group.ai_title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in messages[-3:]
        ],
        enrichment_status=group.enrichment_status,
    )


def _participants(messages: list[GmailMessageRecord]) -> list[str]:
    seen: set[str] = set()
    participants: list[str] = []
    for message in messages:
        sender = message.sender or ""
        name, address = parseaddr(sender)
        value = compact_text(name or address or sender)
        if value and value.lower() not in seen:
            seen.add(value.lower())
            participants.append(value)
    return participants[:6]


def _thread_message_from_gmail(message: GmailMessageRecord) -> ThreadMessage:
    return ThreadMessage(
        id=message.message_id,
        source="gmail",
        thread_id=message.gmail_thread_id,
        from_address=message.sender,
        to=message.recipients.get("to") if isinstance(message.recipients.get("to"), str) else None,
        cc=message.recipients.get("cc") if isinstance(message.recipients.get("cc"), str) else None,
        bcc=message.recipients.get("bcc") if isinstance(message.recipients.get("bcc"), str) else None,
        subject=message.subject,
        body=message.text_body or message.snippet or "",
        html_body=message.html_body_sanitized,
        snippet=message.snippet,
        label_ids=message.label_ids,
        received_at=message.internal_date or message.updated_at,
    )


def _bucket_rows(rows: list[GmailThreadRow]) -> list[GmailThreadSection]:
    reference = datetime.now().astimezone().date()
    buckets: OrderedDict[str, tuple[str, list[GmailThreadRow]]] = OrderedDict()
    for row in rows:
        date = _parse_date(row.latest_received_at).date()
        age = (reference - date).days
        if age <= 0:
            key, title = "today", "Today"
        elif age == 1:
            key, title = "yesterday", "Yesterday"
        elif age <= 7:
            key, title = "last-seven-days", "Last seven days"
        else:
            key, title = f"month-{date.year}-{date.month}", date.strftime("%B %Y")
        if key not in buckets:
            buckets[key] = (title, [])
        buckets[key][1].append(row)
    return [GmailThreadSection(id=key, title=title, rows=items) for key, (title, items) in buckets.items()]


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _mailbox_label(label: str) -> MailboxLabel:
    normalized = label.strip().lower()
    return normalized if normalized in {"inbox", "sent", "drafts", "trash", "archive", "all"} else "inbox"  # type: ignore[return-value]


def _group_matches_label(group: MailGroupRecord, label: str) -> bool:
    labels = {item.upper() for item in group.labels}
    if label == "all":
        return True
    if label == "inbox":
        return "INBOX" in labels or not ({"SENT", "TRASH", "DRAFT"} & labels)
    if label == "sent":
        return "SENT" in labels
    if label == "trash":
        return "TRASH" in labels
    if label == "drafts":
        return "DRAFT" in labels
    if label == "archive":
        return "INBOX" not in labels and "TRASH" not in labels
    return True
