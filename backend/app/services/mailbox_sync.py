from __future__ import annotations

"""Mailbox sync orchestration for Gmail-first UI and async dashboard derivation."""

from base64 import urlsafe_b64decode
from datetime import datetime, timezone
import json
from typing import Any

from app.core.config import Settings
from app.db.repository import (
    DEFAULT_USER_ID,
    get_gmail_sync_state,
    get_user_by_email,
    list_google_oauth_token_user_ids,
    update_gmail_sync_run_state,
    upsert_gmail_sync_state,
    utc_now_iso,
)
from app.schemas.domain import MailboxSyncStateResponse, SourceRecord
from app.services.dashboard import load_dashboard_briefing_cache, save_dashboard_briefing_cache
from app.services.dashboard import is_legacy_local_default_user, stored_user_profile
from app.services.ai.decision import generate_dashboard_briefing, sanitize_dashboard_briefing
from app.services.feed.memory_pipeline import (
    build_feed_from_projection_cache,
    build_feed_from_entities,
    hydrate_persistent_memory,
    list_entity_ids_needing_ai_refresh,
    list_stale_feed_projection_entity_ids,
    refresh_ai_suggestions_for_entities,
    refresh_feed_projections_for_entities,
)
from app.services.integrations.google import (
    build_google_service,
    create_authorized_credentials,
    fetch_google_source_records,
    fetch_google_account_profile,
    load_google_account_profile,
)
from app.services.mailbox import build_mailbox_sync_state_response
from app.services.source_record_summaries import refresh_source_record_summaries


def queueable_mailbox_sync(
    settings: Settings,
    *,
    user_id: str = DEFAULT_USER_ID,
    sync_scope: str | None = None,
    force_full_sync: bool = False,
    derive_work: bool = True,
) -> MailboxSyncStateResponse:
    """Run one Gmail mailbox sync and refresh dashboard work cache in the background."""
    database_path = str(settings.database_path)
    effective_user_id = None if is_legacy_local_default_user(settings, user_id) else user_id
    started_at = utc_now_iso()
    update_gmail_sync_run_state(database_path, user_id=user_id, started_at=started_at, error_message=None)
    try:
        records = fetch_google_source_records(
            settings,
            user_id=effective_user_id,
            collect_records=False,
            sync_scope=sync_scope,
            force_full_sync=force_full_sync,
        )
        if derive_work and records:
            derive_work_from_source_records(settings, records, user_id=user_id)
        update_gmail_sync_run_state(
            database_path,
            user_id=user_id,
            completed_at=utc_now_iso(),
            error_message=None,
        )
    except Exception as exc:
        update_gmail_sync_run_state(
            database_path,
            user_id=user_id,
            completed_at=utc_now_iso(),
            error_message=str(exc),
        )
        raise

    return build_mailbox_sync_state_response(settings, user_id=user_id)


def derive_work_from_source_records(settings: Settings, records: list[SourceRecord], *, user_id: str = DEFAULT_USER_ID) -> None:
    """Refresh dashboard work projections from changed mailbox records asynchronously."""
    database_path = str(settings.database_path)
    source_record_ids = [record.id for record in records]
    refresh_source_record_summaries(database_path, source_record_ids, user_id=user_id)
    changed_entity_ids = hydrate_persistent_memory(database_path, records, include_unlinked=True, user_id=user_id)
    refresh_entity_ids = sorted(
        set(changed_entity_ids)
        | set(list_entity_ids_needing_ai_refresh(database_path, user_id=user_id))
        | set(list_stale_feed_projection_entity_ids(database_path, user_id=user_id))
    )
    if refresh_entity_ids:
        refresh_ai_suggestions_for_entities(database_path, refresh_entity_ids, user_id=user_id)
        refresh_feed_projections_for_entities(
            database_path,
            refresh_entity_ids,
            datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
        )

    feed = build_feed_from_projection_cache(database_path, user_id=user_id) or build_feed_from_entities(
        database_path,
        datetime.now(timezone.utc).isoformat(),
        user_id=user_id,
        record_trace=False,
    )
    profile = load_google_account_profile() if is_legacy_local_default_user(settings, user_id) else None
    profile = profile or stored_user_profile(settings, user_id=user_id)
    profile = profile or fetch_google_account_profile(settings, user_id=None if is_legacy_local_default_user(settings, user_id) else user_id)
    briefing = load_dashboard_briefing_cache(settings, user_id=user_id)
    if briefing is None or refresh_entity_ids:
        briefing = generate_dashboard_briefing(feed, profile)
    save_dashboard_briefing_cache(settings, sanitize_dashboard_briefing(briefing, feed, profile), user_id=user_id)


def ensure_gmail_watch(settings: Settings, *, user_id: str = DEFAULT_USER_ID) -> MailboxSyncStateResponse:
    """Register or renew a Gmail watch and persist its history cursor/expiration."""
    if not settings.gmail_pubsub_topic:
        return build_mailbox_sync_state_response(settings, user_id=user_id)

    existing = get_gmail_sync_state(str(settings.database_path), user_id)
    if existing is not None and not should_renew_gmail_watch(existing.watch_expiration_at, settings.gmail_watch_renewal_hours):
        return build_mailbox_sync_state_response(settings, user_id=user_id)

    effective_user_id = None if is_legacy_local_default_user(settings, user_id) else user_id
    credentials = create_authorized_credentials(settings, user_id=effective_user_id)
    if credentials is None:
        return build_mailbox_sync_state_response(settings, user_id=user_id)

    gmail_service = build_google_service("gmail", "v1", credentials)
    response = (
        gmail_service.users()
        .watch(
            userId="me",
            body={
                "topicName": settings.gmail_pubsub_topic,
            },
        )
        .execute()
    )
    history_id = str(response.get("historyId")) if response.get("historyId") is not None else None
    expiration = to_watch_expiration_iso(response.get("expiration"))
    upsert_gmail_sync_state(
        str(settings.database_path),
        user_id=user_id,
        last_history_id=history_id or (existing.last_history_id if existing is not None else None),
        last_full_sync_at=existing.last_full_sync_at if existing is not None else None,
        watch_expiration_at=expiration,
        last_sync_started_at=existing.last_sync_started_at if existing is not None else None,
        last_sync_completed_at=existing.last_sync_completed_at if existing is not None else None,
        last_sync_error=None,
    )
    return build_mailbox_sync_state_response(settings, user_id=user_id)


def should_renew_gmail_watch(expiration_at: str | None, renewal_hours: int) -> bool:
    """Return true when the Gmail watch is missing or close enough to expiration."""
    if expiration_at is None:
        return True
    try:
        expiration = datetime.fromisoformat(expiration_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    now = datetime.now(timezone.utc)
    remaining_seconds = (expiration.astimezone(timezone.utc) - now).total_seconds()
    return remaining_seconds <= max(1, renewal_hours) * 60 * 60


def handle_pubsub_notification(settings: Settings, pubsub_message: dict[str, Any]) -> MailboxSyncStateResponse:
    """Handle one Cloud Pub/Sub notification by catching up from the local history cursor."""
    notification = decode_pubsub_notification(pubsub_message)
    user = get_user_by_email(str(settings.database_path), notification["emailAddress"].strip().lower())
    if user is None:
        raise ValueError(f"No beta user found for Gmail Pub/Sub email {notification['emailAddress']}")
    return queueable_mailbox_sync(settings, user_id=user.id, derive_work=True)


def decode_pubsub_notification(pubsub_message: dict[str, Any]) -> dict[str, str]:
    """Decode Gmail Pub/Sub message data into emailAddress/historyId fields."""
    data = pubsub_message.get("data")
    if not isinstance(data, str) or not data.strip():
        raise ValueError("Pub/Sub message is missing data")
    try:
        padding = "=" * (-len(data) % 4)
        decoded = urlsafe_b64decode(f"{data}{padding}".encode("utf-8")).decode("utf-8")
    except Exception:
        decoded = data
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("Pub/Sub message data is not an object")
    email_address = payload.get("emailAddress")
    history_id = payload.get("historyId")
    if not isinstance(email_address, str) or not isinstance(history_id, str):
        raise ValueError("Pub/Sub message is missing Gmail history metadata")
    return {"emailAddress": email_address, "historyId": history_id}


def pull_pubsub_notifications(settings: Settings, *, max_messages: int = 10) -> int:
    """Production worker entrypoint for pulling Gmail Pub/Sub notifications."""
    if not settings.gmail_pubsub_subscription:
        return 0

    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    response = subscriber.pull(
        request={
            "subscription": settings.gmail_pubsub_subscription,
            "max_messages": max(1, max_messages),
        }
    )
    ack_ids: list[str] = []
    for message in response.received_messages:
        handle_pubsub_notification(settings, {"data": message.message.data.decode("utf-8")})
        ack_ids.append(message.ack_id)

    if ack_ids:
        subscriber.acknowledge(
            request={
                "subscription": settings.gmail_pubsub_subscription,
                "ack_ids": ack_ids,
            }
        )
    return len(ack_ids)


def renew_expiring_gmail_watches(settings: Settings) -> int:
    """Renew Gmail watches for every connected beta user that is close to expiration."""
    renewed = 0
    for user_id in list_google_oauth_token_user_ids(str(settings.database_path)):
        before = get_gmail_sync_state(str(settings.database_path), user_id)
        if before is not None and not should_renew_gmail_watch(before.watch_expiration_at, settings.gmail_watch_renewal_hours):
            continue
        ensure_gmail_watch(settings, user_id=user_id)
        renewed += 1
    return renewed


def to_watch_expiration_iso(value: object) -> str | None:
    """Convert Gmail watch expiration milliseconds to an ISO timestamp."""
    if value is None:
        return None
    try:
        milliseconds = int(str(value))
    except ValueError:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
