from __future__ import annotations

"""Development Gmail sync poller used when Pub/Sub watch delivery is unavailable."""

import argparse
from datetime import datetime, timedelta, timezone
import logging
import signal
import time
from typing import Any
from uuid import uuid4

from app.core.config import load_settings
from app.core.observability import configure_observability
from app.db.jobs import count_active_jobs, enqueue_job, renew_heartbeat
from app.db.mail_groups import (
    get_import_state,
    gmail_history_cursor_is_authoritative,
    list_connected_gmail_user_ids,
)
from app.db.user_mail_guard import UserMailWorkBlocked
from app.services.integrations.google import check_user_google_credentials
from app.services.mail_groups import ensure_background_import_work

STOP = False
logger = logging.getLogger(__name__)
WATCH_EXPIRY_GRACE = timedelta(minutes=5)
RECENT_DELTA_SYNC_GRACE = timedelta(seconds=25)
PUBSUB_RECOVERY_SYNC_GRACE = timedelta(minutes=5)
RETENTION_CLEANUP_INTERVAL_SECONDS = 60 * 60
LOOP_ERROR_BACKOFF_SECONDS = 5.0
GOOGLE_REAUTH_RECHECK_SECONDS = 120.0
_GOOGLE_REAUTH_RECHECK_AT: dict[str, float] = {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    args = parser.parse_args()
    settings = load_settings()
    configure_observability(settings)
    worker_id = f"gmail-poller-{uuid4()}"

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_heartbeat = 0.0
    last_cleanup = 0.0
    while not STOP:
        try:
            now = time.monotonic()
            if now - last_heartbeat >= args.heartbeat_interval:
                renew_heartbeat(
                    str(settings.database_path),
                    worker_id=worker_id,
                    queues=["gmail_poll"],
                    release_sha=settings.release_sha,
                )
                last_heartbeat = now
            if now - last_cleanup >= RETENTION_CLEANUP_INTERVAL_SECONDS:
                _enqueue_retention_cleanup(settings)
                last_cleanup = now
            poll_once(settings, worker_id=worker_id)
        except Exception as exc:
            logger.warning(
                "gmail_poller.loop_error",
                extra={
                    "event_fields": {
                        "event": "gmail_poller.loop_error",
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            time.sleep(min(LOOP_ERROR_BACKOFF_SECONDS, max(1.0, args.interval)))
            continue
        time.sleep(max(1.0, args.interval))


def poll_once(settings, *, worker_id: str = "gmail-poller") -> int:
    database_url = str(settings.database_path)
    renew_heartbeat(
        database_url,
        worker_id=worker_id,
        queues=["gmail_poll"],
        release_sha=settings.release_sha,
    )
    queued = 0
    for user_id in list_connected_gmail_user_ids(database_url):
        if not _credentials_available(settings, user_id=user_id):
            continue
        try:
            ensure_background_import_work(settings, user_id=user_id)
        except UserMailWorkBlocked:
            continue
        state = get_import_state(database_url, user_id=user_id)
        if state is not None and getattr(state, "reconcile_generation", None):
            if not count_active_jobs(
                database_url,
                user_id=user_id,
                kinds=["gmail_full_reconcile"],
            ):
                try:
                    enqueue_job(
                        database_url,
                        kind="gmail_full_reconcile",
                        queue="slow",
                        user_id=user_id,
                        dedupe_key=(
                            f"gmail-full-reconcile:{user_id}:resume:"
                            f"{state.reconcile_generation}"
                        ),
                        priority=90,
                        payload={"user_id": user_id, "batch_size": 250},
                    )
                except UserMailWorkBlocked:
                    continue
                queued += 1
            continue
        if not gmail_history_cursor_is_authoritative(state):
            try:
                enqueue_job(
                    database_url,
                    kind="gmail_import_batch",
                    queue="critical",
                    user_id=user_id,
                    dedupe_key=f"gmail-poll-import:{user_id}",
                    priority=80,
                    payload={"user_id": user_id, "batch_size": 50, "first_run": state is None},
                )
            except UserMailWorkBlocked:
                continue
            queued += 1
            continue
        if _should_poll(settings, state):
            try:
                enqueue_job(
                    database_url,
                    kind="gmail_delta_sync",
                    queue="critical",
                    user_id=user_id,
                    dedupe_key=f"gmail-poll-delta:{user_id}",
                    priority=75,
                    payload={"user_id": user_id, "batch_size": 100, "source": "poller"},
                )
            except UserMailWorkBlocked:
                continue
            queued += 1
    return queued


def _credentials_available(settings: Any, *, user_id: str) -> bool:
    """Avoid retry storms while a stored Google credential needs reauthentication."""
    now = time.monotonic()
    if now < _GOOGLE_REAUTH_RECHECK_AT.get(user_id, 0.0):
        return False
    try:
        status = check_user_google_credentials(settings, user_id=user_id, refresh_expired=True)
    except UserMailWorkBlocked:
        return False
    if status.connected:
        _GOOGLE_REAUTH_RECHECK_AT.pop(user_id, None)
        return True
    _GOOGLE_REAUTH_RECHECK_AT[user_id] = now + GOOGLE_REAUTH_RECHECK_SECONDS
    logger.info(
        "gmail_poller.reauth_required",
        extra={
            "event_fields": {
                "event": "gmail_poller.reauth_required",
                "user_id": user_id,
                "reauth_required": bool(status.reauth_required or status.has_stored_tokens),
            }
        },
    )
    return False


def _should_poll(settings, state) -> bool:
    grace = RECENT_DELTA_SYNC_GRACE
    if not settings.gmail_pubsub_topic:
        return not _completed_recently(state, grace=grace)
    if not state.gmail_watch_error and state.gmail_watch_expiration_at:
        try:
            expiration = datetime.fromisoformat(state.gmail_watch_expiration_at.replace("Z", "+00:00"))
        except ValueError:
            expiration = None
        if expiration is not None:
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
            if expiration > datetime.now(timezone.utc) + WATCH_EXPIRY_GRACE:
                # Pub/Sub is the fast path, but a bounded periodic delta is
                # still required to recover silently dropped notifications.
                grace = PUBSUB_RECOVERY_SYNC_GRACE
    return not _completed_recently(state, grace=grace)


def _completed_recently(state, *, grace: timedelta = RECENT_DELTA_SYNC_GRACE) -> bool:
    # Backfill/reconciliation page completion must not suppress recovery deltas.
    if not getattr(state, "last_delta_sync_at", None):
        return False
    try:
        completed = datetime.fromisoformat(state.last_delta_sync_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return completed >= datetime.now(timezone.utc) - grace


def _enqueue_retention_cleanup(settings) -> None:
    enqueue_job(
        str(settings.database_path),
        kind="job_retention_cleanup",
        queue="slow",
        dedupe_key="job-retention-cleanup",
        priority=-10,
        payload={},
    )


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def _run_cli() -> int:
    try:
        main()
    except Exception as exc:
        logger.error(
            "gmail_poller.fatal",
            extra={"event_fields": {"event": "gmail_poller.fatal", "exception_type": type(exc).__name__}},
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
