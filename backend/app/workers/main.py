from __future__ import annotations

"""Durable worker CLI for Gmail import and mail-group jobs."""

import argparse
from base64 import urlsafe_b64decode
from datetime import datetime, timezone
import json
import logging
import signal
import threading
import time
from typing import Any
from uuid import uuid4

from app.core.config import load_settings
from app.core.error_safety import GoogleCredentialsUnavailable, safe_google_error, safe_job_error
from app.core.observability import configure_observability
from app.db.jobs import (
    cancel_claimed_job,
    claim_job,
    cleanup_old_jobs,
    complete_job,
    fail_job,
    renew_heartbeat,
    retry_backoff_seconds,
)
from app.db.repository import get_user_by_email
from app.db.mail_groups import (
    gmail_history_cursor_is_authoritative,
    mark_google_disconnected,
    mark_import_error,
)
from app.db.user_mail_guard import UserMailWorkBlocked
from app.services.gmail_importer import (
    GMAIL_SEARCH_MAX_PAGES,
    refresh_gmail_thread_order,
    run_gmail_backfill,
    run_gmail_body_backfill,
    run_gmail_delta_sync,
    run_gmail_full_reconciliation,
    run_gmail_import_batch,
)
from app.services.gmail_watch import ensure_gmail_watch
from app.services.integrations.google import retry_encrypted_google_token_revocation
from app.services.mailbox_events import DASHBOARD_CHANGED, MAILBOX_CHANGED, MAILBOX_SYNC_PROGRESS, emit_mailbox_event
from app.services.mailbox_actions import rollback_failed_thread_action, run_pending_thread_action
from app.services.mailbox_sends import run_pending_send
from app.services.mailbox_search import run_mailbox_search_hydration
from app.services.mail_groups import (
    MAILBOX_REBUILD_LIMIT,
    enqueue_projection_refresh,
    get_import_state,
    rebuild_mail_groups,
    refresh_app_session_snapshot,
    refresh_visible_mail_projection,
)
from app.workers.retry_policy import gmail_is_authorization_failure, gmail_retry_delay_seconds

STOP = False
logger = logging.getLogger(__name__)
LOOP_ERROR_BACKOFF_SECONDS = 5.0
_PROGRESSIVE_GMAIL_JOB_KINDS = {
    "gmail_import_batch",
    "gmail_backfill",
    "gmail_full_reconcile",
    "gmail_delta_sync",
    "gmail_pubsub_sync",
    "gmail_body_fetch",
    "gmail_body_backfill",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queues", default="critical,default")
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    args = parser.parse_args()
    queues = [item.strip() for item in args.queues.split(",") if item.strip()]
    worker_id = f"worker-{uuid4()}"
    settings = load_settings()
    configure_observability(settings)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not STOP:
        try:
            worked = _run_worker_cycle(
                settings,
                worker_id=worker_id,
                queues=queues,
                heartbeat_interval=args.heartbeat_interval,
            )
        except Exception as exc:
            logger.warning(
                "worker.loop_error",
                extra={
                    "event_fields": {
                        "event": "worker.loop_error",
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            time.sleep(min(LOOP_ERROR_BACKOFF_SECONDS, max(0.1, args.sleep)))
            continue
        if not worked:
            time.sleep(max(0.1, args.sleep))


def _run_worker_cycle(settings, *, worker_id: str, queues: list[str], heartbeat_interval: float) -> bool:
    """Claim and process at most one job; callers retry transient DB failures."""
    database_url = str(settings.database_path)
    renew_heartbeat(
        database_url,
        worker_id=worker_id,
        queues=queues,
        release_sha=settings.release_sha,
    )
    job = claim_job(database_url, worker_id=worker_id, queues=queues)
    if job is None:
        return False
    queue_wait_ms = _job_queue_wait_ms(
        str(getattr(job, "created_at", "") or ""),
        getattr(job, "started_at", None),
    )
    logger.info(
        "worker.job_started",
        extra={
            "event_fields": {
                "event": "worker.job_started",
                "job_kind": job.kind,
                "queue": getattr(job, "queue", None),
                "attempt_count": job.attempt_count,
                "queue_wait_ms": queue_wait_ms,
            }
        },
    )
    renew_heartbeat(
        database_url,
        worker_id=worker_id,
        queues=queues,
        release_sha=settings.release_sha,
        current_job_id=job.id,
    )
    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_job_heartbeat_loop,
        args=(
            database_url,
            worker_id,
            queues,
            settings.release_sha,
            job.id,
            heartbeat_interval,
            stop_heartbeat,
        ),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        _run_job(settings, job)
        complete_job(database_url, job.id, worker_id=worker_id)
    except UserMailWorkBlocked:
        cancel_claimed_job(database_url, job.id, worker_id=worker_id)
    except GoogleCredentialsUnavailable as exc:
        # Retrying cannot repair revoked or expired credentials. Preserve the
        # local mailbox and wait for the user to complete Google OAuth again.
        _mark_gmail_reauthorization_required(settings, job=job, exc=exc)
        cancel_claimed_job(database_url, job.id, worker_id=worker_id)
    except Exception as exc:
        if job.kind.startswith("gmail_") and gmail_is_authorization_failure(exc):
            _mark_gmail_reauthorization_required(settings, job=job, exc=exc)
            cancel_claimed_job(database_url, job.id, worker_id=worker_id)
            return True
        retry_delay_seconds = gmail_retry_delay_seconds(
            job.kind,
            exc,
            exponential_delay_seconds=retry_backoff_seconds(job.attempt_count),
        )
        failed = fail_job(
            database_url,
            job,
            safe_job_error(exc),
            worker_id=worker_id,
            retry_delay_seconds=retry_delay_seconds,
        )
        action_user_id = job.payload.get("user_id") or job.user_id
        if (
            failed
            and job.attempt_count >= job.max_attempts
            and job.kind == "gmail_thread_action"
            and isinstance(action_user_id, str)
        ):
            rollback_failed_thread_action(
                settings,
                user_id=action_user_id,
                server_action_id=str(job.payload.get("server_action_id") or ""),
            )
        if (
            failed
            and job.attempt_count >= job.max_attempts
            and job.kind in _PROGRESSIVE_GMAIL_JOB_KINDS
            and isinstance(action_user_id, str)
        ):
            _mark_progressive_gmail_job_failed(
                settings,
                user_id=action_user_id,
                job_kind=job.kind,
                error=safe_job_error(exc),
            )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1.0)
        renew_heartbeat(
            database_url,
            worker_id=worker_id,
            queues=queues,
            release_sha=settings.release_sha,
        )
    return True


def _job_queue_wait_ms(created_at: str, started_at: str | None) -> int | None:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        started = (
            datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started_at
            else datetime.now(timezone.utc)
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, int((started - created).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


def _mark_gmail_reauthorization_required(settings, *, job, exc: BaseException) -> None:
    user_id = job.payload.get("user_id") or job.user_id
    if not isinstance(user_id, str):
        return
    database_url = str(settings.database_path)
    error = safe_google_error(exc, operation="mail sync")
    try:
        mark_google_disconnected(database_url, user_id=user_id)
        mark_import_error(database_url, user_id=user_id, error=error)
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_SYNC_PROGRESS,
            payload={
                "source": "gmail_authorization",
                "phase": "failed",
                "reauthorization_required": True,
            },
        )
    except Exception as state_error:
        logger.warning(
            "gmail.authorization_state_failed",
            extra={
                "event_fields": {
                    "event": "gmail.authorization_state_failed",
                    "user_id": user_id,
                    "exception_type": type(state_error).__name__,
                }
            },
        )


def _mark_progressive_gmail_job_failed(
    settings,
    *,
    user_id: str,
    job_kind: str,
    error: str,
) -> None:
    """Expose terminal generation failure without removing committed mail."""
    try:
        mark_import_error(str(settings.database_path), user_id=user_id, error=error)
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_SYNC_PROGRESS,
            payload={
                "source": job_kind,
                "phase": "failed",
                "terminal": True,
            },
        )
    except Exception as state_error:
        logger.warning(
            "gmail.progress_failure_state_failed",
            extra={
                "event_fields": {
                    "event": "gmail.progress_failure_state_failed",
                    "user_id": user_id,
                    "job_kind": job_kind,
                    "exception_type": type(state_error).__name__,
                }
            },
        )


def _run_job(settings, job) -> None:
    payload = job.payload
    user_id = payload.get("user_id") or job.user_id
    if job.payload_version != 1:
        raise RuntimeError(f"Unsupported payload version {job.payload_version}")
    if job.kind == "google_token_revoke":
        retry_encrypted_google_token_revocation(
            settings,
            job_id=job.id,
            subject_hash=str(payload.get("subject_hash") or ""),
            token_json_encrypted=str(payload.get("token_json_encrypted") or ""),
        )
        return
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
    if job.kind == "gmail_full_reconcile":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_full_reconcile missing user_id")
        run_gmail_full_reconciliation(
            settings,
            user_id=user_id,
            batch_size=int(payload.get("batch_size") or 250),
        )
        try:
            refresh_app_session_snapshot(settings, user_id=user_id)
            emit_mailbox_event(
                settings,
                user_id=user_id,
                event_type=DASHBOARD_CHANGED,
                payload={"source": "gmail_full_reconcile"},
            )
        except Exception as exc:
            # Reconciliation may already have atomically published its cursor.
            # A snapshot/event failure must not restart the full remote scan.
            logger.warning(
                "gmail_full_reconcile.post_refresh_failed",
                extra={
                    "event_fields": {
                        "event": "gmail_full_reconcile.post_refresh_failed",
                        "user_id": user_id,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
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
        refresh_app_session_snapshot(settings, user_id=user_id)
        return
    if job.kind == "gmail_body_backfill":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_body_backfill missing user_id")
        run_gmail_body_backfill(
            settings,
            user_id=user_id,
            batch_size=int(payload.get("batch_size") or 25),
            descriptor_after_message_id=(
                str(payload["descriptor_after_message_id"])
                if payload.get("descriptor_after_message_id")
                else None
            ),
            descriptor_backfill_complete=bool(
                payload.get("descriptor_backfill_complete", False)
            ),
        )
        refresh_app_session_snapshot(settings, user_id=user_id)
        return
    if job.kind == "gmail_search_hydrate":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_search_hydrate missing user_id")
        run_mailbox_search_hydration(
            settings,
            user_id=user_id,
            query=str(payload.get("query") or ""),
            label=str(payload.get("label") or "all"),
            limit=int(payload.get("limit") or 100),
            search_key=str(payload.get("search_key") or ""),
            max_pages=int(payload.get("max_pages") or GMAIL_SEARCH_MAX_PAGES),
            response_limit=int(payload.get("response_limit") or 200),
            page_token=payload.get("page_token"),
            continuation_index=payload.get("continuation_index", 0),
            continuation_key=payload.get("continuation_key"),
            continuation_token_hashes=payload.get("continuation_token_hashes"),
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
        return
    if job.kind == "mail_group_enrich":
        # Historical AI-enrichment jobs may survive a deployment. They are a
        # deliberate no-op in the no-AI mailbox release.
        return
    if job.kind == "first_run_ai_grouping":
        return
    if job.kind == "first_run_ready_check":
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
    if job.kind == "gmail_thread_order_refresh":
        if not isinstance(user_id, str):
            raise RuntimeError("gmail_thread_order_refresh missing user_id")
        target_history_id = str(payload.get("target_history_id") or "") or None
        state = get_import_state(str(settings.database_path), user_id=user_id)
        current_history_id = (
            str(state.last_history_id or "")
            if gmail_history_cursor_is_authoritative(state)
            else ""
        )
        if (
            target_history_id
            and target_history_id.isdigit()
            and current_history_id.isdigit()
            and int(target_history_id) < int(current_history_id)
        ):
            return
        refresh_gmail_thread_order(settings, user_id=user_id)
        refresh_app_session_snapshot(settings, user_id=user_id)
        emit_mailbox_event(
            settings,
            user_id=user_id,
            event_type=MAILBOX_CHANGED,
            mailbox_label="all",
            payload={"source": "gmail_thread_order_refresh"},
        )
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
    release_sha: str,
    job_id: str,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(max(1.0, interval_seconds)):
        try:
            renew_heartbeat(
                database_url,
                worker_id=worker_id,
                queues=queues,
                release_sha=release_sha,
                current_job_id=job_id,
            )
        except Exception as exc:
            logger.warning(
                "worker.heartbeat_error",
                extra={
                    "event_fields": {
                        "event": "worker.heartbeat_error",
                        "exception_type": type(exc).__name__,
                    }
                },
            )


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


def _run_cli() -> int:
    try:
        main()
    except Exception as exc:
        logger.error(
            "worker.fatal",
            extra={"event_fields": {"event": "worker.fatal", "exception_type": type(exc).__name__}},
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
