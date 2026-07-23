from __future__ import annotations

"""Gmail push-watch registration and renewal."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.core.config import Settings
from app.core.error_safety import safe_google_error
from app.db.jobs import enqueue_job
from app.db.mail_groups import (
    get_import_state,
    mark_gmail_watch_error,
    mark_gmail_watch_started,
)
from app.db.user_mail_guard import shared_user_mail_lock
from app.services.integrations.google import start_gmail_watch


@dataclass(frozen=True)
class GmailWatchResult:
    status: Literal["not_configured", "active", "started", "failed"]
    history_id: str | None = None
    expiration_at: str | None = None
    error: str | None = None


def ensure_gmail_watch(settings: Settings, *, user_id: str, force: bool = False) -> GmailWatchResult:
    """Register or renew Gmail push notifications when Pub/Sub is configured."""
    if not settings.gmail_pubsub_topic:
        return GmailWatchResult(status="not_configured")

    database_url = str(settings.database_path)
    with shared_user_mail_lock(database_url, user_id=user_id):
        state = get_import_state(database_url, user_id=user_id)
        if not force and state is not None and _watch_is_fresh(state.gmail_watch_expiration_at, settings.gmail_watch_renewal_hours):
            return GmailWatchResult(
                status="active",
                history_id=state.gmail_watch_history_id,
                expiration_at=state.gmail_watch_expiration_at,
            )

        try:
            response = start_gmail_watch(settings, user_id=user_id)
        except Exception as exc:
            error = safe_google_error(exc, operation="mail sync registration")
            mark_gmail_watch_error(database_url, user_id=user_id, error=error)
            return GmailWatchResult(status="failed", error=error)

        history_id = str(response.get("historyId") or "") or None
        expiration_at = _expiration_from_gmail_response(response.get("expiration"))
        mark_gmail_watch_started(
            database_url,
            user_id=user_id,
            history_id=history_id,
            expiration_at=expiration_at,
        )
        _enqueue_watch_renewal(settings, user_id=user_id, expiration_at=expiration_at)
        return GmailWatchResult(status="started", history_id=history_id, expiration_at=expiration_at)


def _watch_is_fresh(expiration_at: str | None, renewal_hours: int) -> bool:
    expiration = _parse_iso(expiration_at)
    if expiration is None:
        return False
    renewal_window = timedelta(hours=max(1, renewal_hours))
    return expiration - datetime.now(timezone.utc) > renewal_window


def _expiration_from_gmail_response(value: object) -> str | None:
    if value is None:
        return None
    try:
        milliseconds = int(str(value))
    except ValueError:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _enqueue_watch_renewal(settings: Settings, *, user_id: str, expiration_at: str | None) -> None:
    delay_seconds = max(1, int(settings.gmail_watch_renewal_hours) * 60 * 60)
    expiration = _parse_iso(expiration_at)
    if expiration is not None:
        seconds_until_expiration = int((expiration - datetime.now(timezone.utc)).total_seconds())
        delay_seconds = max(60 * 60, min(delay_seconds, seconds_until_expiration - 60 * 60))
    enqueue_job(
        str(settings.database_path),
        kind="gmail_watch_renewal",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"gmail-watch-renewal:{user_id}",
        payload={"user_id": user_id},
        priority=90,
        run_after_seconds=delay_seconds,
    )
