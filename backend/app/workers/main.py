from __future__ import annotations

"""Durable worker CLI for Gmail import and mail-group jobs."""

import argparse
from base64 import urlsafe_b64decode
import json
import signal
import time
from typing import Any
from uuid import uuid4

from app.core.config import load_settings
from app.db.jobs import claim_job, cleanup_old_jobs, complete_job, enqueue_job, fail_job, renew_heartbeat
from app.db.repository import get_user_by_email
from app.services.gmail_importer import run_gmail_backfill, run_gmail_import_batch
from app.services.mail_groups import BACKFILL_BATCH_SIZE, FIRST_BATCH_SIZE, rebuild_mail_groups, run_first_run_ai_grouping

STOP = False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queues", default="critical,default,slow")
    parser.add_argument("--sleep", type=float, default=2.0)
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
        try:
            _run_job(settings, job)
            complete_job(str(settings.database_path), job.id)
        except Exception as exc:
            fail_job(str(settings.database_path), job, f"{type(exc).__name__}: {exc}")
        finally:
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
        return
    if job.kind == "gmail_backfill":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_backfill missing user_id")
        run_gmail_backfill(settings, user_id=user_id, batch_size=int(payload.get("batch_size") or 100))
        return
    if job.kind == "mail_group_candidates":
        if not isinstance(user_id, str):
            raise RuntimeError(f"{job.kind} missing user_id")
        rebuild_mail_groups(settings, user_id=user_id, use_ai=False)
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
        touched = rebuild_mail_groups(settings, user_id=user_id, use_ai=True, max_ai_groups=3)
        if touched >= 3:
            enqueue_job(
                str(settings.database_path),
                kind="mail_group_enrich",
                queue="default",
                user_id=user_id,
                dedupe_key=f"mail-group-enrich:{user_id}:{uuid4()}",
                priority=9,
                payload={"user_id": user_id},
            )
        return
    if job.kind == "first_run_ai_grouping":
        if not isinstance(user_id, str):
            raise RuntimeError("first_run_ai_grouping missing user_id")
        run_first_run_ai_grouping(settings, user_id=user_id, limit=int(payload.get("batch_size") or FIRST_BATCH_SIZE))
        enqueue_job(
            str(settings.database_path),
            kind="gmail_backfill",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-backfill:{user_id}",
            priority=1,
            payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE},
        )
        return
    if job.kind == "first_run_ready_check":
        if not isinstance(user_id, str):
            raise RuntimeError("first_run_ready_check missing user_id")
        run_first_run_ai_grouping(settings, user_id=user_id, limit=int(payload.get("batch_size") or FIRST_BATCH_SIZE))
        return
    if job.kind == "gmail_pubsub_sync":
        resolved_user_id = _user_id_from_pubsub(settings, payload) or user_id
        if not isinstance(resolved_user_id, str):
            return
        run_gmail_import_batch(settings, user_id=resolved_user_id, batch_size=int(payload.get("batch_size") or 30))
        return
    if job.kind == "job_retention_cleanup":
        cleanup_old_jobs(str(settings.database_path))
        return
    raise RuntimeError(f"Unknown job kind {job.kind}")


def _stop(_signum, _frame) -> None:
    global STOP
    STOP = True


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
