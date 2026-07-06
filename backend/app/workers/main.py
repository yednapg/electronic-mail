from __future__ import annotations

"""Durable worker CLI for Gmail import and mail-group jobs."""

import argparse
from base64 import urlsafe_b64decode
import json
import signal
import threading
import time
from typing import Any
from uuid import uuid4

from app.core.config import load_settings
from app.db.jobs import claim_job, cleanup_old_jobs, complete_job, enqueue_job, fail_job, renew_heartbeat
from app.db.repository import get_user_by_email
from app.services.gmail_importer import run_gmail_backfill, run_gmail_delta_sync, run_gmail_import_batch
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mailbox_events import DASHBOARD_CHANGED, emit_mailbox_event
from app.services.mailbox_actions import run_pending_thread_action
from app.services.mailbox_sends import run_pending_send
from app.services.mail_groups import (
    FIRST_BATCH_SIZE,
    MAILBOX_REBUILD_LIMIT,
    MAIL_GROUP_ENRICH_BATCH_SIZE,
    enqueue_projection_refresh,
    enrich_pending_mail_groups,
    rebuild_mail_groups,
    refresh_app_session_snapshot,
    refresh_visible_mail_projection,
    run_first_run_ai_grouping,
)

STOP = False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queues", default="critical,default")
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    args = parser.parse_args()
    queues = [item.strip() for item in args.queues.split(",") if item.strip()]
    worker_id = f"worker-{uuid4()}"
    settings = load_settings()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not STOP:
        renew_heartbeat(str(settings.database_path), worker_id=worker_id, queues=queues)
        job = claim_job(str(settings.database_path), worker_id=worker_id, queues=queues)
        if job is None:
            time.sleep(args.sleep)
            continue
        renew_heartbeat(str(settings.database_path), worker_id=worker_id, queues=queues, current_job_id=job.id)
        stop_heartbeat = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_job_heartbeat_loop,
            args=(str(settings.database_path), worker_id, queues, job.id, args.heartbeat_interval, stop_heartbeat),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            _run_job(settings, job)
            complete_job(str(settings.database_path), job.id)
        except Exception as exc:
            fail_job(str(settings.database_path), job, f"{type(exc).__name__}: {exc}")
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1.0)
            renew_heartbeat(str(settings.database_path), worker_id=worker_id, queues=queues)


def _run_job(settings, job) -> None:
    payload = job.payload
    user_id = payload.get("user_id") or job.user_id
    if job.payload_version != 1:
        raise RuntimeError(f"Unsupported payload version {job.payload_version}")
    if job.kind == "gmail_import_batch":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_import_batch missing user_id")
        run_gmail_import_batch(
            settings,
            user_id=user_id,
            batch_size=int(payload.get("batch_size") or 30),
            first_run=bool(payload.get("first_run")),
        )
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "gmail_import_batch"})
        return
    if job.kind == "gmail_backfill":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_backfill missing user_id")
        run_gmail_backfill(settings, user_id=user_id, batch_size=int(payload.get("batch_size") or 100))
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "gmail_backfill"})
        return
    if job.kind == "gmail_delta_sync":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_delta_sync missing user_id")
        run_gmail_delta_sync(
            settings,
            user_id=user_id,
            batch_size=int(payload.get("batch_size") or 100),
            target_history_id=str(payload.get("target_history_id") or "") or None,
        )
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "gmail_delta_sync"})
        return
    if job.kind == "gmail_body_fetch":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_body_fetch missing user_id")
        from app.services.gmail_importer import run_gmail_body_fetch

        run_gmail_body_fetch(
            settings,
            user_id=user_id,
            group_id=str(payload.get("group_id") or ""),
            gmail_thread_id=str(payload.get("gmail_thread_id") or ""),
        )
        return
    if job.kind == "gmail_thread_action":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_thread_action missing user_id")
        run_pending_thread_action(settings, user_id=user_id, server_action_id=str(payload.get("server_action_id") or ""))
        return
    if job.kind == "gmail_send_message":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_send_message missing user_id")
        run_pending_send(settings, user_id=user_id, server_send_id=str(payload.get("server_send_id") or ""))
        return
    if job.kind == "mail_group_candidates":
        if not isinstance(user_id, str):
            raise RuntimeError(f"{job.kind} missing user_id")
        rebuild_mail_groups(settings, user_id=user_id, limit=MAILBOX_REBUILD_LIMIT, use_ai=False)
        enqueue_projection_refresh(settings, user_id=user_id)
        enqueue_job(
            str(settings.database_path),
            kind="mail_group_enrich",
            queue="default",
            user_id=user_id,
            dedupe_key=f"mail-group-enrich:{user_id}",
            priority=10,
            payload={"user_id": user_id},
        )
        return
    if job.kind == "mail_group_enrich":
        if not isinstance(user_id, str):
            raise RuntimeError(f"{job.kind} missing user_id")
        preferred_group_ids = payload.get("preferred_group_ids")
        touched = enrich_pending_mail_groups(
            settings,
            user_id=user_id,
            limit=MAIL_GROUP_ENRICH_BATCH_SIZE,
            preferred_group_ids=[str(group_id) for group_id in preferred_group_ids] if isinstance(preferred_group_ids, list) else None,
        )
        enqueue_projection_refresh(settings, user_id=user_id, priority=20)
        if touched >= MAIL_GROUP_ENRICH_BATCH_SIZE:
            enqueue_job(
                str(settings.database_path),
                kind="mail_group_enrich",
                queue="default",
                user_id=user_id,
                dedupe_key=f"mail-group-enrich:{user_id}:{uuid4()}",
                priority=40,
                payload={"user_id": user_id},
            )
        return
    if job.kind == "first_run_ai_grouping":
        if not isinstance(user_id, str):
            raise RuntimeError("first_run_ai_grouping missing user_id")
        run_first_run_ai_grouping(settings, user_id=user_id, limit=int(payload.get("batch_size") or FIRST_BATCH_SIZE))
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "first_run_ai_grouping"})
        return
    if job.kind == "first_run_ready_check":
        if not isinstance(user_id, str):
            raise RuntimeError("first_run_ready_check missing user_id")
        run_first_run_ai_grouping(settings, user_id=user_id, limit=int(payload.get("batch_size") or FIRST_BATCH_SIZE))
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "first_run_ready_check"})
        return
    if job.kind == "gmail_pubsub_sync":
        resolved_user_id = _user_id_from_pubsub(settings, payload) or user_id
        if not isinstance(resolved_user_id, str):
            return
        run_gmail_delta_sync(
            settings,
            user_id=resolved_user_id,
            batch_size=int(payload.get("batch_size") or 100),
            target_history_id=str(payload.get("history_id") or "") or None,
        )
        refresh_app_session_snapshot(settings, user_id=resolved_user_id)
        emit_mailbox_event(settings, user_id=resolved_user_id, event_type=DASHBOARD_CHANGED, payload={"source": "gmail_pubsub_sync"})
        return
    if job.kind == "gmail_watch_renewal":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_watch_renewal missing user_id")
        ensure_gmail_watch(settings, user_id=user_id, force=True)
        return
    if job.kind == "projection_refresh":
        if not isinstance(user_id, str):
            raise RuntimeError("projection_refresh missing user_id")
        refresh_visible_mail_projection(settings, user_id=user_id)
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(settings, user_id=user_id, event_type=DASHBOARD_CHANGED, payload={"source": "projection_refresh"})
        return
    if job.kind == "job_retention_cleanup":
        cleanup_old_jobs(str(settings.database_path))
        return
    raise RuntimeError(f"Unknown job kind {job.kind}")


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def _job_heartbeat_loop(
    database_url: str,
    worker_id: str,
    queues: list[str],
    job_id: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(max(1.0, interval_seconds)):
        renew_heartbeat(database_url, worker_id=worker_id, queues=queues, current_job_id=job_id)


def _user_id_from_pubsub(settings, payload: dict[str, Any]) -> str | None:
    email_address = str(payload.get("email_address") or "").strip().lower()
    envelope = payload.get("pubsub")
    message = envelope.get("message") if isinstance(envelope, dict) and isinstance(envelope.get("message"), dict) else envelope
    data = message.get("data") if isinstance(message, dict) else None
    if not email_address and isinstance(data, str):
        try:
            padding = "=" * (-len(data) % 4)
            decoded = json.loads(urlsafe_b64decode(f"{data}{padding}".encode()).decode())
            email_address = str(decoded.get("emailAddress") or "").strip().lower()
        except Exception:
            email_address = ""
    if not email_address:
        return None
    user = get_user_by_email(str(settings.database_path), email_address)
    return user.id if user is not None else None


if __name__ == "__main__":
    main()
