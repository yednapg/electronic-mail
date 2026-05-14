from __future__ import annotations

"""Durable Gmail raw import jobs for the mail-groups product path."""

from typing import Any

from app.core.config import Settings
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    GmailMessageRecord,
    get_import_state,
    mark_import_completed,
    mark_import_error,
    mark_import_started,
    upsert_gmail_messages,
    user_can_write_gmail,
)
from app.services.email_extraction import parse_gmail_message
from app.services.integrations.google import build_google_service, create_authorized_credentials
from app.services.mail_groups import rebuild_mail_groups

FIRST_BATCH_SIZE = 50
BACKFILL_BATCH_SIZE = 30


def run_gmail_import_batch(settings: Settings, *, user_id: str, batch_size: int, first_run: bool = False) -> int:
    """Import a bounded newest Gmail batch, then queue AI grouping for visible product output."""
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0

    mark_import_started(database_url, user_id=user_id)
    try:
        listed = _list_messages(settings, user_id=user_id, batch_size=batch_size, page_token=None)
        messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed)
        upsert_gmail_messages(database_url, messages)
        if first_run:
            mark_import_completed(
                database_url,
                user_id=user_id,
                first_batch=True,
                groups_ready=False,
                dashboard_ready=False,
                last_history_id=latest_history_id,
                full_backfill_cursor=listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None,
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

        touched = rebuild_mail_groups(settings, user_id=user_id, limit=500, use_ai=False)
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None,
        )
        if messages:
            enqueue_job(
                database_url,
                kind="mail_group_enrich",
                queue="default",
                user_id=user_id,
                dedupe_key=f"mail-group-enrich:{user_id}",
                priority=10,
                payload={"user_id": user_id},
            )
        return touched
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
        messages, latest_history_id = _hydrate_messages(settings, user_id=user_id, listed=listed)
        upsert_gmail_messages(database_url, messages)
        touched = rebuild_mail_groups(settings, user_id=user_id, limit=1000, use_ai=False)
        next_cursor = listed.get("nextPageToken") if isinstance(listed.get("nextPageToken"), str) else None
        mark_import_completed(
            database_url,
            user_id=user_id,
            first_batch=False,
            groups_ready=False,
            dashboard_ready=False,
            last_history_id=latest_history_id,
            full_backfill_cursor=next_cursor,
        )
        if next_cursor:
            enqueue_job(
                database_url,
                kind="gmail_backfill",
                queue="slow",
                user_id=user_id,
                dedupe_key=f"gmail-backfill:{user_id}:{next_cursor}",
                priority=1,
                payload={"user_id": user_id, "batch_size": batch_size},
            )
        if messages:
            enqueue_job(
                database_url,
                kind="mail_group_enrich",
                queue="default",
                user_id=user_id,
                dedupe_key=f"mail-group-enrich:{user_id}",
                priority=5,
                payload={"user_id": user_id},
            )
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


def _hydrate_messages(settings: Settings, *, user_id: str, listed: dict[str, Any]) -> tuple[list[GmailMessageRecord], str | None]:
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    service = build_google_service("gmail", "v1", credentials)
    messages: list[GmailMessageRecord] = []
    latest_history_id: str | None = None
    raw_items = listed.get("messages") if isinstance(listed.get("messages"), list) else []
    for item in raw_items:
        message_id = item.get("id") if isinstance(item, dict) else None
        if not message_id:
            continue
        payload = service.users().messages().get(userId="me", id=str(message_id), format="full").execute()
        parsed = parse_gmail_message(payload, user_id=user_id)
        latest_history_id = _max_history_id(latest_history_id, parsed.get("history_id"))
        messages.append(GmailMessageRecord(created_at="", updated_at="", **parsed))
    return messages, latest_history_id


def _max_history_id(left: str | None, right: object) -> str | None:
    if right is None:
        return left
    if left is None:
        return str(right)
    try:
        return str(max(int(left), int(str(right))))
    except ValueError:
        return str(right)
