from __future__ import annotations

"""Durable Gmail raw import jobs for the mailbox product path."""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    delete_gmail_messages,
    existing_gmail_message_ids,
    get_import_state,
    list_group_messages,
    list_messages_for_gmail_thread,
    mark_gmail_messages_body_fetch_state,
    mark_import_completed,
    mark_import_error,
    mark_import_started,
    mark_mail_groups_pending,
    prune_empty_mail_groups,
    upsert_gmail_messages,
    user_can_write_gmail,
)
from app.services.email_extraction import has_persisted_renderable_body, parse_gmail_message
from app.services.integrations.google import build_google_service, create_authorized_credentials
from app.services.mailbox_events import MAILBOX_CHANGED, emit_mailbox_event
from app.services.mail_groups import enqueue_projection_refresh, rebuild_touched_mail_groups

FIRST_BATCH_SIZE = 50
BACKFILL_BATCH_SIZE = 100
FIRST_RUN_RECENT_DAYS = 90
FIRST_RUN_RECENT_MAX_MESSAGES = 5000
FIRST_RUN_ALL_MAIL_SEED = "__ALL_MAIL__"
FIRST_RUN_SEED_LABELS = ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH", FIRST_RUN_ALL_MAIL_SEED)
GMAIL_BATCH_GET_SIZE = 50
GMAIL_HISTORY_PAGE_SIZE = 500
GMAIL_HISTORY_TYPES = ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]
GMAIL_METADATA_HEADERS = ["Subject", "From", "To", "Cc", "Bcc", "Date", "Message-ID", "In-Reply-To", "References", "List-ID"]
logger = logging.getLogger(__name__)


def _ai_grouping_enabled(settings: Settings) -> bool:
    return bool(getattr(settings, "ai_grouping_enabled", False))


def run_gmail_import_batch(settings: Settings, *, user_id: str, batch_size: int, first_run: bool = False) -> int:
    """Import a bounded newest Gmail batch for the raw mailbox launch path."""
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    if not first_run:
        return _run_recent_metadata_sync(settings, user_id=user_id, batch_size=batch_size, can_write_checked=True)

    mark_import_started(database_url, user_id=user_id)
    try:
        messages, latest_history_id, next_cursor = _hydrate_first_run_recent_window(
            settings,
            user_id=user_id,
            batch_size=batch_size,
        )
        upsert_gmail_messages(database_url, messages)
        backfill_cursor = next_cursor or _encode_full_mailbox_cursor()
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=True,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=backfill_cursor,
            clear_full_backfill_cursor=False,
            full_backfill_started=next_cursor is None,
        )
        enqueue_job(
            database_url,
            kind="gmail_backfill",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-backfill:{user_id}",
            priority=80,
            payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE},
        )
        if _ai_grouping_enabled(settings):
            enqueue_job(
                database_url,
                kind="first_run_ai_grouping",
                queue="critical",
                user_id=user_id,
                dedupe_key=f"first-run-ai-grouping:{user_id}",
                priority=95,
                payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE},
            )
        if messages:
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=MAILBOX_CHANGED,
                mailbox_label="all",
                payload={"source": "first_run", "message_count": len(messages)},
            )
        return len(messages)
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def run_gmail_delta_sync(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int = 100,
    target_history_id: str | None = None,
) -> int:
    """Sync Gmail changes since the saved history cursor without broad mailbox listing."""
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0

    state = get_import_state(database_url, user_id=user_id)
    if state is None or not state.last_history_id:
        return _run_recent_metadata_sync(settings, user_id=user_id, batch_size=batch_size, can_write_checked=True)

    mark_import_started(database_url, user_id=user_id)
    try:
        delta = _list_history_delta(
            settings,
            user_id=user_id,
            start_history_id=state.last_history_id,
            page_size=batch_size,
        )
        latest_history_id = _max_history_id(delta["latest_history_id"], target_history_id)
        deleted_ids = set(delta["deleted_message_ids"])
        message_ids = [message_id for message_id in delta["message_ids"] if message_id not in deleted_ids]

        affected_group_ids = delete_gmail_messages(database_url, user_id=user_id, message_ids=list(deleted_ids))
        deleted_empty_group_ids = prune_empty_mail_groups(database_url, user_id=user_id, group_ids=affected_group_ids)
        deleted_pending_group_ids = mark_mail_groups_pending(
            database_url,
            user_id=user_id,
            group_ids=[group_id for group_id in affected_group_ids if group_id not in set(deleted_empty_group_ids)],
        )

        messages, hydrated_history_id = _hydrate_message_ids(settings, user_id=user_id, message_ids=message_ids, format="metadata")
        latest_history_id = _max_history_id(latest_history_id, hydrated_history_id)
        touched = 0
        if messages:
            upsert_gmail_messages(database_url, messages)
            touched = rebuild_touched_mail_groups(
                settings,
                user_id=user_id,
                message_ids=[message.message_id for message in messages],
                use_ai=False,
            )
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
        )
        if messages or deleted_pending_group_ids:
            _enqueue_enrichment_and_projection(settings, user_id=user_id, priority=60)
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=MAILBOX_CHANGED,
                mailbox_label="all",
                payload={
                    "source": "gmail_delta",
                    "message_count": len(messages),
                    "deleted_count": len(deleted_ids),
                    "pending_group_count": len(deleted_pending_group_ids),
                },
            )
        elif deleted_empty_group_ids:
            if _ai_grouping_enabled(settings):
                enqueue_projection_refresh(settings, user_id=user_id, priority=20)
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=MAILBOX_CHANGED,
                mailbox_label="all",
                payload={"source": "gmail_delta", "deleted_count": len(deleted_ids)},
            )
        logger.info(
            "Gmail delta sync completed user_id=%s imported=%s deleted=%s pending_groups=%s latest_history_id=%s target_history_id=%s",
            user_id,
            len(messages),
            len(deleted_ids),
            len(deleted_pending_group_ids),
            latest_history_id,
            target_history_id,
        )
        return touched + len(deleted_pending_group_ids)
    except HttpError as exc:
        if _is_history_cursor_expired(exc):
            return _run_recent_metadata_sync(settings, user_id=user_id, batch_size=batch_size, can_write_checked=True)
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def run_gmail_backfill(settings: Settings, *, user_id: str, batch_size: int = BACKFILL_BATCH_SIZE) -> int:
    """Continue importing older Gmail using the independent full-backfill cursor."""
    batch_size = max(1, min(batch_size, BACKFILL_BATCH_SIZE))
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0

    state = get_import_state(database_url, user_id=user_id)
    page_token = state.full_backfill_cursor if state else None
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    if state is not None and state.first_batch_imported_at and not page_token and full_backfill_completed_at:
        return 0

    mark_import_started(database_url, user_id=user_id)
    try:
        first_run_cursor = _decode_first_run_cursor(page_token)
        full_mailbox_cursor = None if first_run_cursor is not None else _decode_full_mailbox_cursor(page_token)
        if first_run_cursor is not None:
            messages, latest_history_id, next_cursor = _hydrate_first_run_cursor_page(
                settings,
                user_id=user_id,
                batch_size=batch_size,
                cursor=first_run_cursor,
            )
            full_backfill_started = False
            full_backfill_completed = False
            if next_cursor is None:
                next_cursor = _encode_full_mailbox_cursor()
                full_backfill_started = True
        elif full_mailbox_cursor is not None:
            messages, latest_history_id, next_cursor = _hydrate_full_mailbox_cursor_page(
                settings,
                user_id=user_id,
                batch_size=batch_size,
                cursor=full_mailbox_cursor,
            )
            full_backfill_started = True
            full_backfill_completed = next_cursor is None
        else:
            listed = _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=page_token)
            messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
            next_cursor = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
            full_backfill_started = False
            full_backfill_completed = False
        upsert_gmail_messages(database_url, messages)
        touched = rebuild_touched_mail_groups(
            settings,
            user_id=user_id,
            message_ids=[message.message_id for message in messages],
            use_ai=False,
        )
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=next_cursor,
            clear_full_backfill_cursor=next_cursor is None,
            full_backfill_started=full_backfill_started,
            full_backfill_completed=full_backfill_completed,
        )
        if next_cursor:
            enqueue_job(
                database_url,
                kind="gmail_backfill",
                queue="slow",
                user_id=user_id,
                dedupe_key=f"gmail-backfill:{user_id}:{next_cursor}",
                priority=80,
                payload={"user_id": user_id, "batch_size": batch_size},
            )
        if messages:
            if _ai_grouping_enabled(settings):
                enqueue_job(
                    database_url,
                    kind="mail_group_enrich",
                    queue="default",
                    user_id=user_id,
                    dedupe_key=f"mail-group-enrich:{user_id}",
                    priority=40,
                    payload={"user_id": user_id},
                )
                enqueue_projection_refresh(settings, user_id=user_id, priority=1)
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=MAILBOX_CHANGED,
                mailbox_label="all",
                payload={"source": "gmail_backfill", "message_count": len(messages), "completed": full_backfill_completed},
            )
        return touched
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def _list_messages(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int,
    page_token: str | None,
    label_ids: list[str] | None = None,
) -> dict[str, Any]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    list_args: dict[str, Any] = {
        "userId": "me",
        "maxResults": max(1, min(batch_size, 500)),
        "includeSpamTrash": True,
    }
    if page_token:
        list_args["pageToken"] = page_token
    if label_ids:
        list_args["labelIds"] = label_ids
    request = service.users().messages().list(**list_args)
    return request.execute()


def _run_recent_metadata_sync(settings: Settings, *, user_id: str, batch_size: int, can_write_checked: bool = False) -> int:
    database_url = str(settings.database_path)
    if not can_write_checked and not user_can_write_gmail(database_url, user_id=user_id):
        return 0

    mark_import_started(database_url, user_id=user_id)
    try:
        listed = _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=None)
        listed = _merge_listed_messages(
            listed,
            _list_draft_messages(settings, user_id=user_id, batch_size=batch_size, page_token=None),
        )
        messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
        upsert_gmail_messages(database_url, messages)
        touched = rebuild_touched_mail_groups(
            settings,
            user_id=user_id,
            message_ids=[message.message_id for message in messages],
            use_ai=False,
        )
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
        )
        if messages:
            _enqueue_enrichment_and_projection(settings, user_id=user_id, priority=40)
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=MAILBOX_CHANGED,
                mailbox_label="all",
                payload={"source": "recent_sync", "message_count": len(messages)},
            )
        return touched
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def run_gmail_body_fetch(settings: Settings, *, user_id: str, group_id: str = "", gmail_thread_id: str = "") -> int:
    """Fetch full bodies for a group when a reader or enrichment path needs them."""
    if not group_id and not gmail_thread_id:
        return 0
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = (
        list_group_messages(database_url, user_id=user_id, group_id=group_id)
        if group_id
        else list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=gmail_thread_id)
    )
    missing = [message.message_id for message in messages if not _has_body_for_reader(message)]
    if not missing:
        return 0
    mark_gmail_messages_body_fetch_state(database_url, user_id=user_id, message_ids=missing, status="pending")
    try:
        credentials = create_authorized_credentials(settings, user_id=user_id)
        if credentials is None:
            raise RuntimeError("Google credentials are not connected")
        service = build_google_service("gmail", "v1", credentials)
        payloads = _batch_get_message_payloads(service, missing, format="full")
        parsed_messages = [
            GmailMessageRecord(
                created_at="",
                updated_at="",
                **parse_gmail_message(
                    payload,
                    user_id=user_id,
                    inline_attachment_resolver=_inline_attachment_resolver(service),
                ),
            )
            for payload in payloads.values()
        ]
    except Exception as exc:
        mark_gmail_messages_body_fetch_state(database_url, user_id=user_id, message_ids=missing, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    upsert_gmail_messages(database_url, parsed_messages)
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[message.message_id for message in parsed_messages], use_ai=False)
    if _ai_grouping_enabled(settings):
        enqueue_projection_refresh(settings, user_id=user_id)
    return len(parsed_messages)


def _has_body_for_reader(message: GmailMessageRecord) -> bool:
    return has_persisted_renderable_body(
        text_body=message.text_body,
        html_body=message.html_body_sanitized,
        html_render_document=message.html_render_document,
        raw_payload=message.raw_payload,
    )


def _hydrate_first_run_recent_window(settings: Settings, *, user_id: str, batch_size: int) -> tuple[list[GmailMessageRecord], str | None, str | None]:
    recent_days = max(1, int(getattr(settings, "gmail_recent_days", FIRST_RUN_RECENT_DAYS) or FIRST_RUN_RECENT_DAYS))
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    messages_by_id: dict[str, GmailMessageRecord] = {}
    latest_history_id: str | None = None
    page_size = max(1, min(max(batch_size, FIRST_BATCH_SIZE), 500))
    next_tokens: dict[str, str | None] = {}
    completed_labels: set[str] = set()
    for label_id in FIRST_RUN_SEED_LABELS:
        listed = _list_first_run_seed_messages(
            settings,
            user_id=user_id,
            batch_size=page_size,
            page_token=None,
            label_id=label_id,
        )
        page_messages, page_latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
        for message in page_messages:
            internal_ms = _internal_date_ms(message)
            if internal_ms is None or internal_ms >= cutoff_ms:
                messages_by_id.setdefault(message.message_id, message)
        latest_history_id = _max_history_id(latest_history_id, page_latest_history_id)
        next_page_token = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
        oldest_ms = min((_internal_date_ms(message) for message in page_messages), default=None)
        if next_page_token is None or not page_messages or (oldest_ms is not None and oldest_ms <= cutoff_ms):
            completed_labels.add(label_id)
        else:
            next_tokens[label_id] = next_page_token

    thread_messages, thread_latest_history_id = _hydrate_thread_metadata_for_messages(
        settings,
        user_id=user_id,
        messages=list(messages_by_id.values()),
    )
    latest_history_id = _max_history_id(latest_history_id, thread_latest_history_id)
    for message in thread_messages:
        messages_by_id.setdefault(message.message_id, message)

    messages = sorted(messages_by_id.values(), key=lambda item: item.internal_date or item.updated_at, reverse=True)
    next_cursor = _encode_first_run_cursor(next_tokens=next_tokens, completed_labels=completed_labels, cutoff_ms=cutoff_ms)
    return messages[:FIRST_RUN_RECENT_MAX_MESSAGES], latest_history_id, next_cursor


def _hydrate_first_run_cursor_page(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int,
    cursor: dict[str, Any],
) -> tuple[list[GmailMessageRecord], str | None, str | None]:
    cutoff_ms = int(cursor.get("cutoff_ms") or 0)
    next_tokens = {str(key): value for key, value in dict(cursor.get("next_tokens") or {}).items() if isinstance(value, str) and value}
    completed_labels = {str(label) for label in list(cursor.get("completed_labels") or [])}
    messages_by_id: dict[str, GmailMessageRecord] = {}
    latest_history_id: str | None = None
    page_size = max(1, min(max(batch_size, BACKFILL_BATCH_SIZE), 500))
    for label_id in FIRST_RUN_SEED_LABELS:
        if label_id in completed_labels:
            continue
        page_token = next_tokens.get(label_id)
        if not page_token:
            completed_labels.add(label_id)
            continue
        listed = _list_first_run_seed_messages(
            settings,
            user_id=user_id,
            batch_size=page_size,
            page_token=page_token,
            label_id=label_id,
        )
        page_messages, page_latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
        for message in page_messages:
            internal_ms = _internal_date_ms(message)
            if internal_ms is None or internal_ms >= cutoff_ms:
                messages_by_id.setdefault(message.message_id, message)
        latest_history_id = _max_history_id(latest_history_id, page_latest_history_id)
        next_page_token = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
        oldest_ms = min((_internal_date_ms(message) for message in page_messages), default=None)
        if next_page_token is None or not page_messages or (oldest_ms is not None and oldest_ms <= cutoff_ms):
            completed_labels.add(label_id)
            next_tokens.pop(label_id, None)
        else:
            next_tokens[label_id] = next_page_token

    thread_messages, thread_latest_history_id = _hydrate_thread_metadata_for_messages(
        settings,
        user_id=user_id,
        messages=list(messages_by_id.values()),
    )
    latest_history_id = _max_history_id(latest_history_id, thread_latest_history_id)
    for message in thread_messages:
        messages_by_id.setdefault(message.message_id, message)
    messages = sorted(messages_by_id.values(), key=lambda item: item.internal_date or item.updated_at, reverse=True)
    next_cursor = _encode_first_run_cursor(next_tokens=next_tokens, completed_labels=completed_labels, cutoff_ms=cutoff_ms)
    return messages[:FIRST_RUN_RECENT_MAX_MESSAGES], latest_history_id, next_cursor


def _encode_first_run_cursor(*, next_tokens: dict[str, str | None], completed_labels: set[str], cutoff_ms: int) -> str | None:
    active_tokens = {label: token for label, token in next_tokens.items() if token and label not in completed_labels}
    if not active_tokens:
        return None
    payload = {
        "type": "first_run_90d",
        "next_tokens": active_tokens,
        "completed_labels": sorted(completed_labels),
        "cutoff_ms": cutoff_ms,
    }
    return _encode_cursor_payload(payload)


def _encode_full_mailbox_cursor(
    *,
    page_token: str | None = None,
    all_mail_completed: bool = False,
    draft_page_token: str | None = None,
    drafts_completed: bool = False,
) -> str:
    payload = {
        "type": "full_mailbox",
        "page_token": page_token,
        "all_mail_completed": all_mail_completed,
        "draft_page_token": draft_page_token,
        "drafts_completed": drafts_completed,
    }
    return _encode_cursor_payload(payload)


def _encode_cursor_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _list_first_run_seed_messages(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int,
    page_token: str | None,
    label_id: str,
) -> dict[str, Any]:
    if label_id == "DRAFT":
        return _list_draft_messages(settings, user_id=user_id, batch_size=batch_size, page_token=page_token)
    return _list_messages(
        settings,
        user_id=user_id,
        batch_size=batch_size,
        page_token=page_token,
        label_ids=_label_ids_for_first_run_seed(label_id),
    )


def _label_ids_for_first_run_seed(label_id: str) -> list[str] | None:
    return None if label_id == FIRST_RUN_ALL_MAIL_SEED else [label_id]


def _list_draft_messages(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int,
    page_token: str | None,
) -> dict[str, Any]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    list_args: dict[str, Any] = {
        "userId": "me",
        "maxResults": max(1, min(batch_size, 500)),
        "includeSpamTrash": True,
    }
    if page_token:
        list_args["pageToken"] = page_token
    response = service.users().drafts().list(**list_args).execute()
    messages: list[dict[str, str]] = []
    for draft in response.get("drafts", []) if isinstance(response.get("drafts"), list) else []:
        if not isinstance(draft, dict):
            continue
        message = draft.get("message")
        if isinstance(message, dict) and message.get("id"):
            messages.append({"id": str(message["id"])})
    listed: dict[str, Any] = {"messages": messages}
    if isinstance(response.get("nextPageToken"), str):
        listed["nextPageToken"] = response["nextPageToken"]
    return listed


def _merge_listed_messages(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    seen: set[str] = set()
    for listed in (primary, secondary):
        for item in listed.get("messages", []) if isinstance(listed.get("messages"), list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            message_id = str(item["id"])
            if message_id in seen:
                continue
            seen.add(message_id)
            messages.append({"id": message_id})
    merged = dict(primary)
    merged["messages"] = messages
    return merged


def _decode_first_run_cursor(cursor: str | None) -> dict[str, Any] | None:
    payload = _decode_cursor_payload(cursor)
    if not isinstance(payload, dict) or payload.get("type") != "first_run_90d":
        return None
    return payload


def _decode_full_mailbox_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return {
            "type": "full_mailbox",
            "page_token": None,
            "all_mail_completed": False,
            "draft_page_token": None,
            "drafts_completed": False,
        }
    payload = _decode_cursor_payload(cursor)
    if not isinstance(payload, dict) or payload.get("type") != "full_mailbox":
        return None
    return payload


def _decode_cursor_payload(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{cursor}{padding}".encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _hydrate_full_mailbox_cursor_page(
    settings: Settings,
    *,
    user_id: str,
    batch_size: int,
    cursor: dict[str, Any],
) -> tuple[list[GmailMessageRecord], str | None, str | None]:
    page_token = str(cursor.get("page_token") or "") or None
    all_mail_completed = bool(cursor.get("all_mail_completed"))
    draft_page_token = str(cursor.get("draft_page_token") or "") or None
    drafts_completed = bool(cursor.get("drafts_completed"))
    listed = {"messages": []} if all_mail_completed else _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=page_token)
    draft_listed = {"messages": []}
    if not drafts_completed:
        draft_listed = _list_draft_messages(settings, user_id=user_id, batch_size=batch_size, page_token=draft_page_token)
    merged = _merge_listed_messages(listed, draft_listed)
    raw_items = merged.get("messages") if isinstance(merged.get("messages"), list) else []
    listed_ids = [str(item.get("id")) for item in raw_items if isinstance(item, dict) and item.get("id")]
    existing_ids = existing_gmail_message_ids(str(settings.database_path), user_id=user_id, message_ids=listed_ids)
    missing_listed = {"messages": [{"id": message_id} for message_id in listed_ids if message_id not in existing_ids]}
    messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=missing_listed, format="metadata")
    next_page_token = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
    next_all_mail_completed = all_mail_completed or next_page_token is None
    next_draft_page_token = draft_listed.get("nextPageToken") if isinstance(draft_listed.get("nextPageToken"), str) else None
    next_drafts_completed = drafts_completed or next_draft_page_token is None
    next_cursor = (
        _encode_full_mailbox_cursor(
            page_token=next_page_token,
            all_mail_completed=next_all_mail_completed,
            draft_page_token=next_draft_page_token,
            drafts_completed=next_drafts_completed,
        )
        if not next_all_mail_completed or not next_drafts_completed
        else None
    )
    return messages, latest_history_id, next_cursor


def _hydrate_thread_metadata_for_messages(
    settings: Settings,
    *,
    user_id: str,
    messages: list[GmailMessageRecord],
) -> tuple[list[GmailMessageRecord], str | None]:
    thread_ids = [thread_id for thread_id in dict.fromkeys(message.gmail_thread_id for message in messages) if thread_id]
    if not thread_ids:
        return [], None
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    hydrated: dict[str, GmailMessageRecord] = {}
    latest_history_id: str | None = None
    for thread_id in thread_ids:
        try:
            payload = (
                service.users()
                .threads()
                .get(userId="me", id=thread_id, format="metadata", metadataHeaders=GMAIL_METADATA_HEADERS)
                .execute()
            )
        except Exception:
            continue
        raw_messages = payload.get("messages") if isinstance(payload, dict) and isinstance(payload.get("messages"), list) else []
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict) or not raw_message.get("id"):
                continue
            parsed = parse_gmail_message(raw_message, user_id=user_id)
            latest_history_id = _max_history_id(latest_history_id, parsed.get("history_id"))
            hydrated[str(raw_message["id"])] = GmailMessageRecord(created_at="", updated_at="", **parsed)
    return list(hydrated.values()), latest_history_id


def _hydrate_messages(
    settings: Settings,
    *,
    user_id: str,
    listed: dict[str, Any],
    format: str = "metadata",
) -> tuple[list[GmailMessageRecord], str | None]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    raw_items = listed.get("messages") if isinstance(listed.get("messages"), list) else []
    message_ids = [str(item.get("id")) for item in raw_items if isinstance(item, dict) and item.get("id")]
    payloads = _batch_get_message_payloads(service, message_ids, format=format)
    messages: list[GmailMessageRecord] = []
    latest_history_id: str | None = None
    for message_id in message_ids:
        payload = payloads.get(message_id)
        if payload is None:
            continue
        parsed = parse_gmail_message(
            payload,
            user_id=user_id,
            inline_attachment_resolver=_inline_attachment_resolver(service) if format == "full" else None,
        )
        latest_history_id = _max_history_id(latest_history_id, parsed.get("history_id"))
        messages.append(GmailMessageRecord(created_at="", updated_at="", **parsed))
    return messages, latest_history_id


def _inline_attachment_resolver(service: Any):
    cache: dict[tuple[str, str], str | None] = {}

    def resolve(message_id: str, attachment_id: str) -> str | None:
        key = (message_id, attachment_id)
        if key in cache:
            return cache[key]
        try:
            response = service.users().messages().attachments().get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ).execute()
            data = response.get("data") if isinstance(response, dict) else None
            cache[key] = data if isinstance(data, str) and data else None
        except Exception:
            cache[key] = None
        return cache[key]

    return resolve


def _hydrate_message_ids(
    settings: Settings,
    *,
    user_id: str,
    message_ids: list[str],
    format: str = "metadata",
) -> tuple[list[GmailMessageRecord], str | None]:
    listed = {"messages": [{"id": message_id} for message_id in list(dict.fromkeys(message_ids))]}
    return _hydrate_messages(settings, user_id=user_id, listed=listed, format=format)


def _list_history_delta(
    settings: Settings,
    *,
    user_id: str,
    start_history_id: str,
    page_size: int,
) -> dict[str, Any]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    page_token: str | None = None
    message_ids: list[str] = []
    deleted_message_ids: list[str] = []
    latest_history_id: str | None = None
    while True:
        request_args: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": GMAIL_HISTORY_TYPES,
            "maxResults": max(1, min(page_size, GMAIL_HISTORY_PAGE_SIZE)),
        }
        if page_token:
            request_args["pageToken"] = page_token
        response = service.users().history().list(**request_args).execute()
        latest_history_id = _max_history_id(latest_history_id, response.get("historyId"))
        for history_item in response.get("history", []) if isinstance(response.get("history"), list) else []:
            if not isinstance(history_item, dict):
                continue
            latest_history_id = _max_history_id(latest_history_id, history_item.get("id"))
            message_ids.extend(_history_messages(history_item, keys=["messages", "messagesAdded", "labelsAdded", "labelsRemoved"]))
            deleted_message_ids.extend(_history_messages(history_item, keys=["messagesDeleted"]))
        page_token = response.get("nextPageToken") if isinstance(response.get("nextPageToken"), str) else None
        if not page_token:
            break
    deleted_set = set(deleted_message_ids)
    return {
        "message_ids": [message_id for message_id in list(dict.fromkeys(message_ids)) if message_id not in deleted_set],
        "deleted_message_ids": list(dict.fromkeys(deleted_message_ids)),
        "latest_history_id": latest_history_id,
    }


def _history_messages(history_item: dict[str, Any], *, keys: list[str]) -> list[str]:
    message_ids: list[str] = []
    for key in keys:
        values = history_item.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            if key == "messages":
                message = value
            else:
                message = value.get("message") if isinstance(value.get("message"), dict) else None
            if isinstance(message, dict) and message.get("id"):
                message_ids.append(str(message["id"]))
    return message_ids


def _batch_get_message_payloads(service: Any, message_ids: list[str], *, format: str) -> dict[str, dict[str, Any]]:
    if not message_ids:
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    missing_ids = list(dict.fromkeys(message_ids))
    try:
        for offset in range(0, len(missing_ids), GMAIL_BATCH_GET_SIZE):
            batch_ids = missing_ids[offset : offset + GMAIL_BATCH_GET_SIZE]

            def callback(request_id: str, response: Any, exception: Exception | None) -> None:
                if exception is None and isinstance(response, dict):
                    payloads[request_id] = response

            batch = service.new_batch_http_request(callback=callback)
            for message_id in batch_ids:
                request_args: dict[str, Any] = {"userId": "me", "id": message_id, "format": format}
                if format == "metadata":
                    request_args["metadataHeaders"] = GMAIL_METADATA_HEADERS
                batch.add(
                    service.users().messages().get(**request_args),
                    request_id=message_id,
                )
            batch.execute()
    except Exception:
        payloads = {}

    missing = [message_id for message_id in missing_ids if message_id not in payloads]
    for message_id in missing:
        request_args = {"userId": "me", "id": message_id, "format": format}
        if format == "metadata":
            request_args["metadataHeaders"] = GMAIL_METADATA_HEADERS
        payload = service.users().messages().get(**request_args).execute()
        if isinstance(payload, dict):
            payloads[message_id] = payload
    return payloads


def _enqueue_enrichment_and_projection(settings: Settings, *, user_id: str, priority: int) -> None:
    if not _ai_grouping_enabled(settings):
        return
    enqueue_job(
        str(settings.database_path),
        kind="mail_group_enrich",
        queue="default",
        user_id=user_id,
        dedupe_key=f"mail-group-enrich:{user_id}",
        priority=priority,
        payload={"user_id": user_id},
    )
    enqueue_projection_refresh(settings, user_id=user_id, priority=20)


def _is_history_cursor_expired(exc: HttpError) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) == 404
    except (TypeError, ValueError):
        return False


def _internal_date_ms(message: GmailMessageRecord) -> int | None:
    if not message.internal_date:
        return None
    try:
        parsed = datetime.fromisoformat(message.internal_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _max_history_id(left: str | None, right: object) -> str | None:
    if right is None:
        return left
    if left is None:
        return str(right)
    try:
        return str(max(int(left), int(str(right))))
    except ValueError:
        return str(right)
