from __future__ import annotations

"""Durable Gmail raw import jobs for the mail-groups product path."""

from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.errors import HttpError

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    delete_gmail_messages,
    get_import_state,
    list_group_messages,
    mark_import_completed,
    mark_import_error,
    mark_import_started,
    mark_mail_groups_pending,
    prune_empty_mail_groups,
    upsert_gmail_messages,
    user_can_write_gmail,
)
from app.services.email_extraction import parse_gmail_message
from app.services.integrations.google import build_google_service, create_authorized_credentials
from app.services.mail_groups import enqueue_projection_refresh, rebuild_touched_mail_groups

FIRST_BATCH_SIZE = 50
BACKFILL_BATCH_SIZE = 30
FIRST_RUN_RECENT_DAYS = 30
FIRST_RUN_RECENT_MAX_MESSAGES = FIRST_BATCH_SIZE
GMAIL_BATCH_GET_SIZE = 50
GMAIL_HISTORY_PAGE_SIZE = 500
GMAIL_HISTORY_TYPES = ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]
GMAIL_METADATA_HEADERS = ["Subject", "From", "To", "Cc", "Bcc", "Date", "Message-ID", "In-Reply-To", "References", "List-ID"]


def run_gmail_import_batch(settings: Settings, *, user_id: str, batch_size: int, first_run: bool = False) -> int:
    """Import a bounded newest Gmail batch, then queue AI grouping for visible product output."""
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
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=True,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=next_cursor,
            clear_full_backfill_cursor=next_cursor is None,
        )
        if next_cursor:
            enqueue_job(
                database_url,
                kind="gmail_backfill",
                queue="slow",
                user_id=user_id,
                dedupe_key=f"gmail-backfill:{user_id}",
                priority=80,
                payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE},
            )
        enqueue_job(
            database_url,
            kind="first_run_ai_grouping",
            queue="critical",
            user_id=user_id,
            dedupe_key=f"first-run-ai-grouping:{user_id}",
            priority=95,
            payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE},
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
        hidden_message_ids = [
            message.message_id
            for message in messages
            if "TRASH" in message.label_ids or "SPAM" in message.label_ids
        ]
        if hidden_message_ids:
            hidden_group_ids = delete_gmail_messages(database_url, user_id=user_id, message_ids=hidden_message_ids)
            hidden_empty_group_ids = prune_empty_mail_groups(database_url, user_id=user_id, group_ids=hidden_group_ids)
            hidden_pending_group_ids = mark_mail_groups_pending(
                database_url,
                user_id=user_id,
                group_ids=[group_id for group_id in hidden_group_ids if group_id not in set(hidden_empty_group_ids)],
            )
            deleted_empty_group_ids.extend(hidden_empty_group_ids)
            deleted_pending_group_ids.extend(hidden_pending_group_ids)
            messages = [message for message in messages if message.message_id not in set(hidden_message_ids)]
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
        elif deleted_empty_group_ids:
            enqueue_projection_refresh(settings, user_id=user_id, priority=20)
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
    if state is not None and state.first_batch_imported_at and not page_token:
        return 0

    mark_import_started(database_url, user_id=user_id)
    try:
        listed = _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=page_token)
        messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
        upsert_gmail_messages(database_url, messages)
        touched = rebuild_touched_mail_groups(
            settings,
            user_id=user_id,
            message_ids=[message.message_id for message in messages],
            use_ai=False,
        )
        next_cursor = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=next_cursor,
            clear_full_backfill_cursor=next_cursor is None,
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
        return touched
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def _list_messages(settings: Settings, *, user_id: str, batch_size: int, page_token: str | None) -> dict[str, Any]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    list_args: dict[str, Any] = {
        "userId": "me",
        "maxResults": max(1, min(batch_size, 500)),
        "q": "-in:spam -in:trash",
    }
    if page_token:
        list_args["pageToken"] = page_token
    request = service.users().messages().list(**list_args)
    return request.execute()


def _run_recent_metadata_sync(settings: Settings, *, user_id: str, batch_size: int, can_write_checked: bool = False) -> int:
    database_url = str(settings.database_path)
    if not can_write_checked and not user_can_write_gmail(database_url, user_id=user_id):
        return 0

    mark_import_started(database_url, user_id=user_id)
    try:
        listed = _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=None)
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
        return touched
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=str(exc))
        raise


def run_gmail_body_fetch(settings: Settings, *, user_id: str, group_id: str) -> int:
    """Fetch full bodies for a group when a reader or enrichment path needs them."""
    if not group_id:
        return 0
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_group_messages(database_url, user_id=user_id, group_id=group_id)
    missing = [message.message_id for message in messages if not message.text_body and not message.html_body_sanitized]
    if not missing:
        return 0
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    payloads = _batch_get_message_payloads(service, missing, format="full")
    parsed_messages = [
        GmailMessageRecord(created_at="", updated_at="", **parse_gmail_message(payload, user_id=user_id))
        for payload in payloads.values()
    ]
    upsert_gmail_messages(database_url, parsed_messages)
    rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=[message.message_id for message in parsed_messages], use_ai=False)
    enqueue_projection_refresh(settings, user_id=user_id)
    return len(parsed_messages)


def _hydrate_first_run_recent_window(settings: Settings, *, user_id: str, batch_size: int) -> tuple[list[GmailMessageRecord], str | None, str | None]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_RECENT_DAYS)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    page_token: str | None = None
    messages: list[GmailMessageRecord] = []
    latest_history_id: str | None = None
    next_cursor: str | None = None
    page_size = max(1, min(max(batch_size, FIRST_BATCH_SIZE), 100))
    while len(messages) < FIRST_RUN_RECENT_MAX_MESSAGES:
        listed = _list_messages(settings, user_id=user_id, batch_size=page_size, page_token=page_token)
        page_messages, page_latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed, format="metadata")
        if not page_messages:
            next_cursor = None
            break
        messages.extend(page_messages)
        latest_history_id = _max_history_id(latest_history_id, page_latest_history_id)
        next_cursor = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
        oldest_ms = min((_internal_date_ms(message) for message in page_messages), default=None)
        if next_cursor is None or (oldest_ms is not None and oldest_ms <= cutoff_ms):
            break
        page_token = next_cursor
    return messages[:FIRST_RUN_RECENT_MAX_MESSAGES], latest_history_id, next_cursor


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
        parsed = parse_gmail_message(payload, user_id=user_id)
        latest_history_id = _max_history_id(latest_history_id, parsed.get("history_id"))
        messages.append(GmailMessageRecord(created_at="", updated_at="", **parsed))
    return messages, latest_history_id


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
