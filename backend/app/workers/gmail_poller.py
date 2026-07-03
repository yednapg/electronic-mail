from __future__ import annotations

"""Development Gmail sync poller used when Pub/Sub watch delivery is unavailable."""

import argparse
from datetime import datetime, timedelta, timezone
import signal
import time
from uuid import uuid4

from app.core.config import load_settings
from app.db.jobs import enqueue_job, renew_heartbeat
from app.db.mail_groups import get_import_state, list_connected_gmail_user_ids
from app.services.gmail_importer import FIRST_BATCH_SIZE

STOP = False
WATCH_EXPIRY_GRACE = timedelta(minutes=5)
RECENT_DELTA_SYNC_GRACE = timedelta(seconds=25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    args = parser.parse_args()
    settings = load_settings()
    worker_id = f"gmail-poller-{uuid4()}"

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_heartbeat = 0.0
    while not STOP:
        now = time.monotonic()
        if now - last_heartbeat >= args.heartbeat_interval:
            renew_heartbeat(str(settings.database_path), worker_id=worker_id, queues=["gmail_poll"])
            last_heartbeat = now
        poll_once(settings, worker_id=worker_id)
        time.sleep(max(1.0, args.interval))


def poll_once(settings, *, worker_id: str = "gmail-poller") -> int:
    database_url = str(settings.database_path)
    renew_heartbeat(database_url, worker_id=worker_id, queues=["gmail_poll"])
    queued = 0
    for user_id in list_connected_gmail_user_ids(database_url):
        state = get_import_state(database_url, user_id=user_id)
        if state is None or not state.last_history_id:
            enqueue_job(
                database_url,
                kind="gmail_import_batch",
                queue="critical",
                user_id=user_id,
                dedupe_key=f"gmail-poll-import:{user_id}",
                priority=80,
                payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "first_run": state is None},
            )
            queued += 1
            continue
        if _should_poll(settings, state):
            enqueue_job(
                database_url,
                kind="gmail_delta_sync",
                queue="critical",
                user_id=user_id,
                dedupe_key=f"gmail-poll-delta:{user_id}",
                priority=75,
                payload={"user_id": user_id, "batch_size": 100, "source": "poller"},
            )
            queued += 1
    return queued


def _should_poll(settings, state) -> bool:
    if _completed_recently(state):
        return False
    if not settings.gmail_pubsub_topic:
        return True
    if state.gmail_watch_error:
        return True
    if not state.gmail_watch_expiration_at:
        return True
    try:
        expiration = datetime.fromisoformat(state.gmail_watch_expiration_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    return expiration <= datetime.now(timezone.utc) + WATCH_EXPIRY_GRACE


def _completed_recently(state) -> bool:
    if not state.last_import_completed_at:
        return False
    try:
        completed = datetime.fromisoformat(state.last_import_completed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return completed >= datetime.now(timezone.utc) - RECENT_DELTA_SYNC_GRACE


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


if __name__ == "__main__":
    main()
