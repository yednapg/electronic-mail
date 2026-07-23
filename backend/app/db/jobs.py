from __future__ import annotations

"""Postgres-backed durable background job queue."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.db.repository import get_engine
from app.db.user_mail_guard import user_mail_write_transaction

ACTIVE_STATUSES = {"queued", "running"}
REQUIRED_RUNTIME_QUEUES = ("critical", "reader", "default", "slow", "gmail_poll")
FRESH_WORKER_SECONDS = 120


@dataclass(frozen=True)
class BackgroundJob:
    id: str
    kind: str
    queue: str
    status: str
    user_id: str | None
    dedupe_key: str | None
    priority: int
    payload_version: int
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    run_after: str
    lease_owner: str | None
    lease_expires_at: str | None
    last_error: str | None
    trace_id: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class QueueHealth:
    queue_depth: dict[str, int]
    dead_jobs: int
    stale_running_jobs: int
    oldest_queued_age_seconds: int | None
    workers: list[dict[str, Any]]
    worker_online: bool
    worker_releases_match: bool
    required_queues_ready: bool


def enqueue_job(
    database_url: str,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
    dedupe_key: str | None = None,
    queue: str = "default",
    priority: int = 0,
    max_attempts: int = 5,
    payload_version: int = 1,
    run_after_seconds: int = 0,
    wake_existing: bool = True,
) -> BackgroundJob:
    engine = get_engine(database_url)
    job_id = str(uuid4())
    payload_json = json.dumps(payload or {}, ensure_ascii=True)
    run_after_seconds = max(0, int(run_after_seconds))
    transaction = (
        user_mail_write_transaction(engine, user_id=user_id)
        if user_id is not None
        else engine.begin()
    )
    with transaction as connection:
        if dedupe_key:
            existing = connection.execute(
                text(
                    """
                    SELECT * FROM background_jobs
                    WHERE kind = :kind AND dedupe_key = :dedupe_key AND status IN ('queued', 'running')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"kind": kind, "dedupe_key": dedupe_key},
            ).mappings().first()
            if existing is not None:
                if str(existing["status"]) == "queued":
                    if wake_existing:
                        existing = connection.execute(
                            text(
                                """
                                UPDATE background_jobs
                                SET run_after = LEAST(run_after, now() + (:run_after_seconds * interval '1 second')),
                                    priority = GREATEST(priority, :priority),
                                    updated_at = now()
                                WHERE id = :id
                                RETURNING *
                                """
                            ),
                            {"id": existing["id"], "priority": priority, "run_after_seconds": run_after_seconds},
                        ).mappings().first()
                        if existing is not None:
                            _insert_event(connection, str(existing["id"]), "progress", None, {"reason": "dedupe_woke_queued_job"})
                    elif priority > int(existing["priority"]):
                        existing = connection.execute(
                            text(
                                """
                                UPDATE background_jobs
                                SET priority = :priority,
                                    updated_at = now()
                                WHERE id = :id
                                RETURNING *
                                """
                            ),
                            {"id": existing["id"], "priority": priority},
                        ).mappings().first()
                return _job_from_row(existing)

        row = connection.execute(
            text(
                """
                INSERT INTO background_jobs (
                  id, kind, queue, status, user_id, dedupe_key, priority, payload_version,
                  payload_json, max_attempts, run_after, created_at, updated_at
                ) VALUES (
                  :id, :kind, :queue, 'queued', :user_id, :dedupe_key, :priority, :payload_version,
                  :payload_json, :max_attempts, now() + (:run_after_seconds * interval '1 second'), now(), now()
                )
                ON CONFLICT DO NOTHING
                RETURNING *
                """
            ),
            {
                "id": job_id,
                "kind": kind,
                "queue": queue,
                "user_id": user_id,
                "dedupe_key": dedupe_key,
                "priority": priority,
                "payload_version": payload_version,
                "payload_json": payload_json,
                "max_attempts": max_attempts,
                "run_after_seconds": run_after_seconds,
            },
        ).mappings().first()
        if row is None and dedupe_key:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM background_jobs
                    WHERE kind = :kind AND dedupe_key = :dedupe_key AND status IN ('queued', 'running')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"kind": kind, "dedupe_key": dedupe_key},
            ).mappings().first()
        if row is None:
            raise RuntimeError("Job enqueue failed")
        _insert_event(connection, str(row["id"]), "queued", None, {})
        return _job_from_row(row)


def get_job(database_url: str, job_id: str) -> BackgroundJob | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM background_jobs WHERE id = :id"),
            {"id": job_id},
        ).mappings().first()
    return _job_from_row(row) if row is not None else None


def count_active_jobs(database_url: str, *, user_id: str, kinds: list[str] | None = None) -> int:
    sql = """
        SELECT COUNT(*)
        FROM background_jobs
        WHERE user_id = :user_id
          AND status IN ('queued', 'running')
    """
    params: dict[str, Any] = {"user_id": user_id}
    if kinds:
        sql += " AND kind = ANY(:kinds)"
        params["kinds"] = kinds
    with get_engine(database_url).connect() as connection:
        value = connection.execute(text(sql), params).scalar_one()
    return int(value)


def has_active_google_token_revocation(database_url: str, *, subject_hash: str) -> bool:
    """Return whether provider cleanup is still pending for one Google subject."""
    normalized_subject_hash = subject_hash.strip()
    if not normalized_subject_hash:
        raise ValueError("Google subject hash is required")
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM background_jobs
                  WHERE kind = 'google_token_revoke'
                    AND status IN ('queued', 'running')
                    AND payload_json::jsonb ->> 'subject_hash' = :subject_hash
                )
                """
            ),
            {"subject_hash": normalized_subject_hash},
        ).scalar_one()
    return bool(value)


def complete_google_token_revocations_for_subject(database_url: str, *, subject_hash: str) -> int:
    """Resolve every pending grant cleanup after Google confirms subject-wide revocation."""
    normalized_subject_hash = subject_hash.strip()
    if not normalized_subject_hash:
        raise ValueError("Google subject hash is required")
    with get_engine(database_url).begin() as connection:
        completed_ids = connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'succeeded',
                    completed_at = now(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE kind = 'google_token_revoke'
                  AND status IN ('queued', 'running')
                  AND payload_json::jsonb ->> 'subject_hash' = :subject_hash
                RETURNING id
                """
            ),
            {"subject_hash": normalized_subject_hash},
        ).scalars().all()
        for job_id in completed_ids:
            _insert_event(
                connection,
                str(job_id),
                "succeeded",
                None,
                {"reason": "google_subject_revoked"},
            )
    return len(completed_ids)


def complete_google_token_revocation_job(database_url: str, *, job_id: str) -> bool:
    """Resolve one unusable credential without claiming sibling grants were revoked."""
    normalized_job_id = job_id.strip()
    if not normalized_job_id:
        raise ValueError("Google token revocation job ID is required")
    with get_engine(database_url).begin() as connection:
        completed_job_id = connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'succeeded',
                    completed_at = now(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = :job_id
                  AND kind = 'google_token_revoke'
                  AND status IN ('queued', 'running')
                RETURNING id
                """
            ),
            {"job_id": normalized_job_id},
        ).scalar_one_or_none()
        if completed_job_id is None:
            return False
        _insert_event(
            connection,
            str(completed_job_id),
            "succeeded",
            None,
            {"reason": "google_credential_already_invalid"},
        )
    return True


def claim_job(database_url: str, *, worker_id: str, queues: list[str], lease_seconds: int = 300) -> BackgroundJob | None:
    if not queues:
        return None
    with get_engine(database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                WITH candidate AS (
                  SELECT id
                  FROM background_jobs
                  WHERE queue = ANY(:queues)
                    AND (
                      (status = 'queued' AND run_after <= now())
                      OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now())
                    )
                  ORDER BY priority DESC, run_after ASC, created_at ASC
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE background_jobs AS jobs
                SET status = 'running',
                    lease_owner = :worker_id,
                    lease_expires_at = now() + (:lease_seconds * interval '1 second'),
                    started_at = COALESCE(started_at, now()),
                    attempt_count = attempt_count + 1,
                    updated_at = now()
                FROM candidate
                WHERE jobs.id = candidate.id
                RETURNING jobs.*
                """
            ),
            {"queues": queues, "worker_id": worker_id, "lease_seconds": lease_seconds},
        ).mappings().first()
        if row is None:
            return None
        _insert_event(connection, str(row["id"]), "claimed", None, {"worker_id": worker_id})
        return _job_from_row(row)


def renew_heartbeat(
    database_url: str,
    *,
    worker_id: str,
    queues: list[str],
    release_sha: str,
    current_job_id: str | None = None,
    lease_seconds: int = 300,
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO worker_heartbeats (worker_id, queues_json, current_job_id, release_sha, last_seen_at)
                VALUES (:worker_id, :queues_json, :current_job_id, :release_sha, now())
                ON CONFLICT (worker_id) DO UPDATE SET
                  queues_json = excluded.queues_json,
                  current_job_id = excluded.current_job_id,
                  release_sha = excluded.release_sha,
                  last_seen_at = now()
                """
            ),
            {
                "worker_id": worker_id,
                "queues_json": json.dumps(queues),
                "current_job_id": current_job_id,
                "release_sha": release_sha,
            },
        )
        if current_job_id is not None:
            connection.execute(
                text(
                    """
                    UPDATE background_jobs
                    SET lease_expires_at = now() + (:lease_seconds * interval '1 second'),
                        updated_at = now()
                    WHERE id = :id
                      AND status = 'running'
                      AND lease_owner = :worker_id
                    """
                ),
                {"id": current_job_id, "worker_id": worker_id, "lease_seconds": lease_seconds},
            )


def complete_job(database_url: str, job_id: str, *, worker_id: str) -> bool:
    with get_engine(database_url).begin() as connection:
        completed_job_id = connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'succeeded', completed_at = now(), lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE id = :id
                  AND status = 'running'
                  AND lease_owner = :worker_id
                RETURNING id
                """
            ),
            {"id": job_id, "worker_id": worker_id},
        ).scalar_one_or_none()
        if completed_job_id is None:
            return False
        _insert_event(connection, job_id, "succeeded", None, {})
    return True


def fail_job(database_url: str, job: BackgroundJob, error: str, *, worker_id: str) -> bool:
    final = job.attempt_count >= job.max_attempts
    delay_seconds = _backoff_seconds(job.attempt_count)
    with get_engine(database_url).begin() as connection:
        if final:
            failed_job_id = connection.execute(
                text(
                    """
                    UPDATE background_jobs
                    SET status = 'dead', last_error = :error, completed_at = now(), lease_owner = NULL,
                        lease_expires_at = NULL, updated_at = now()
                    WHERE id = :id
                      AND status = 'running'
                      AND lease_owner = :worker_id
                    RETURNING id
                    """
                ),
                {"id": job.id, "error": error[:4000], "worker_id": worker_id},
            ).scalar_one_or_none()
            if failed_job_id is None:
                return False
            _insert_event(connection, job.id, "dead", error, {})
        else:
            failed_job_id = connection.execute(
                text(
                    """
                    UPDATE background_jobs
                    SET status = 'queued', last_error = :error, run_after = now() + (:delay_seconds * interval '1 second'),
                        lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                    WHERE id = :id
                      AND status = 'running'
                      AND lease_owner = :worker_id
                    RETURNING id
                    """
                ),
                {
                    "id": job.id,
                    "error": error[:4000],
                    "delay_seconds": delay_seconds,
                    "worker_id": worker_id,
                },
            ).scalar_one_or_none()
            if failed_job_id is None:
                return False
            _insert_event(connection, job.id, "retry", error, {"delay_seconds": delay_seconds})
    return True


def cancel_claimed_job(database_url: str, job_id: str, *, worker_id: str) -> bool:
    """Cancel work that reached a durable user-mail guard after it was claimed."""
    with get_engine(database_url).begin() as connection:
        cancelled_job_id = connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'cancelled', completed_at = now(), lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = now()
                WHERE id = :id
                  AND status = 'running'
                  AND lease_owner = :worker_id
                RETURNING id
                """
            ),
            {"id": job_id, "worker_id": worker_id},
        ).scalar_one_or_none()
        if cancelled_job_id is None:
            return False
        _insert_event(connection, job_id, "cancelled", None, {"reason": "user_mail_guard"})
    return True


def cancel_user_jobs(database_url: str, *, user_id: str) -> int:
    with get_engine(database_url).begin() as connection:
        result = connection.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'cancelled', completed_at = now(), lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE user_id = :user_id AND status IN ('queued', 'running')
                """
            ),
            {"user_id": user_id},
        )
    return int(result.rowcount or 0)


def get_queue_health(database_url: str, *, expected_release_sha: str) -> QueueHealth:
    with get_engine(database_url).connect() as connection:
        depths = connection.execute(
            text("SELECT queue, COUNT(*) AS count FROM background_jobs WHERE status = 'queued' GROUP BY queue")
        ).mappings().all()
        dead_jobs = connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE status = 'dead'")).scalar_one()
        stale_running = connection.execute(
            text("SELECT COUNT(*) FROM background_jobs WHERE status = 'running' AND lease_expires_at < now()")
        ).scalar_one()
        oldest_age = connection.execute(
            text("SELECT EXTRACT(EPOCH FROM (now() - MIN(created_at))) FROM background_jobs WHERE status = 'queued'")
        ).scalar()
        workers = connection.execute(
            text(
                """
                SELECT
                  worker_id,
                  queues_json,
                  current_job_id,
                  release_sha,
                  last_seen_at,
                  EXTRACT(EPOCH FROM (now() - last_seen_at)) AS age_seconds
                FROM worker_heartbeats
                ORDER BY last_seen_at DESC
                """
            )
        ).mappings().all()
    worker_payloads = [
        {
            "worker_id": row["worker_id"],
            "queues": json.loads(row["queues_json"] or "[]"),
            "current_job_id": row["current_job_id"],
            "release_sha": row["release_sha"],
            "last_seen_at": _iso(row["last_seen_at"]),
            "age_seconds": int(row["age_seconds"] or 0),
            "fresh": int(row["age_seconds"] or 0) <= FRESH_WORKER_SECONDS,
            "release_matches_expected": row["release_sha"] == expected_release_sha,
        }
        for row in workers
    ]
    fresh_workers = [worker for worker in worker_payloads if worker["fresh"]]
    matching_fresh_queues = {
        queue
        for worker in fresh_workers
        if worker["release_matches_expected"]
        for queue in worker["queues"]
    }
    return QueueHealth(
        queue_depth={str(row["queue"]): int(row["count"]) for row in depths},
        dead_jobs=int(dead_jobs or 0),
        stale_running_jobs=int(stale_running or 0),
        oldest_queued_age_seconds=int(oldest_age) if oldest_age is not None else None,
        workers=worker_payloads,
        worker_online=any(worker["queues"] for worker in fresh_workers),
        worker_releases_match=bool(fresh_workers)
        and all(worker["release_matches_expected"] for worker in fresh_workers),
        required_queues_ready=all(queue in matching_fresh_queues for queue in REQUIRED_RUNTIME_QUEUES),
    )


def cleanup_old_jobs(database_url: str, *, succeeded_days: int = 30, dead_days: int = 90) -> int:
    """Delete expired queue, authentication, event, and idempotency records."""
    with get_engine(database_url).begin() as connection:
        expired_runtime_rows = 0
        for statement in (
            "DELETE FROM oauth_login_sessions WHERE expires_at < now()",
            "DELETE FROM mobile_login_codes WHERE expires_at < now()",
            "DELETE FROM mobile_oauth_handoffs WHERE expires_at < now()",
            """
            DELETE FROM app_sessions
            WHERE expires_at < now() - interval '7 days'
               OR (revoked_at IS NOT NULL AND revoked_at < now() - interval '7 days')
            """,
            "DELETE FROM mailbox_events WHERE created_at < now() - interval '7 days'",
            "DELETE FROM worker_heartbeats WHERE last_seen_at < now() - interval '7 days'",
            """
            DELETE FROM gmail_pending_thread_actions
            WHERE (state = 'applied' AND updated_at < now() - interval '30 days')
               OR (state = 'failed' AND updated_at < now() - interval '90 days')
            """,
            """
            DELETE FROM gmail_pending_sends
            WHERE state IN ('sent', 'failed')
              AND updated_at < now() - interval '90 days'
            """,
            """
            DELETE FROM gmail_client_drafts
            WHERE state IN ('sent', 'deleted')
              AND updated_at < now() - interval '30 days'
            """,
            """
            DELETE FROM gmail_reconcile_seen AS seen
            WHERE NOT EXISTS (
              SELECT 1
              FROM gmail_import_state AS state
              WHERE state.user_id = seen.user_id
                AND state.reconcile_generation = seen.generation_id
            )
            """,
        ):
            result = connection.execute(text(statement))
            expired_runtime_rows += int(result.rowcount or 0)
        event_result = connection.execute(
            text(
                """
                DELETE FROM background_job_events
                WHERE job_id IN (
                  SELECT id
                  FROM background_jobs
                  WHERE (
                    status IN ('succeeded', 'cancelled')
                    AND completed_at IS NOT NULL
                    AND completed_at < now() - (:succeeded_days * interval '1 day')
                  ) OR (
                    status = 'dead'
                    AND completed_at IS NOT NULL
                    AND completed_at < now() - (:dead_days * interval '1 day')
                  )
                )
                """
            ),
            {"succeeded_days": succeeded_days, "dead_days": dead_days},
        )
        job_result = connection.execute(
            text(
                """
                DELETE FROM background_jobs
                WHERE (
                  status IN ('succeeded', 'cancelled')
                  AND completed_at IS NOT NULL
                  AND completed_at < now() - (:succeeded_days * interval '1 day')
                ) OR (
                  status = 'dead'
                  AND completed_at IS NOT NULL
                  AND completed_at < now() - (:dead_days * interval '1 day')
                )
                """
            ),
            {"succeeded_days": succeeded_days, "dead_days": dead_days},
        )
    return expired_runtime_rows + int(event_result.rowcount or 0) + int(job_result.rowcount or 0)


def _insert_event(connection, job_id: str, event_type: str, message: str | None, metadata: dict[str, Any]) -> None:
    connection.execute(
        text(
            """
            INSERT INTO background_job_events (id, job_id, event_type, message, metadata_json, created_at)
            VALUES (:id, :job_id, :event_type, :message, :metadata_json, now())
            """
        ),
        {
            "id": str(uuid4()),
            "job_id": job_id,
            "event_type": event_type,
            "message": message,
            "metadata_json": json.dumps(metadata, ensure_ascii=True),
        },
    )


def _job_from_row(row) -> BackgroundJob:
    return BackgroundJob(
        id=str(row["id"]),
        kind=str(row["kind"]),
        queue=str(row["queue"]),
        status=str(row["status"]),
        user_id=str(row["user_id"]) if row["user_id"] is not None else None,
        dedupe_key=str(row["dedupe_key"]) if row["dedupe_key"] is not None else None,
        priority=int(row["priority"]),
        payload_version=int(row["payload_version"]),
        payload=json.loads(row["payload_json"] or "{}"),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        run_after=_iso(row["run_after"]),
        lease_owner=str(row["lease_owner"]) if row["lease_owner"] is not None else None,
        lease_expires_at=_iso(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None,
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        trace_id=str(row["trace_id"]) if row["trace_id"] is not None else None,
        created_at=_iso(row["created_at"]),
        started_at=_iso(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=_iso(row["completed_at"]) if row["completed_at"] is not None else None,
        updated_at=_iso(row["updated_at"]),
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _backoff_seconds(attempt_count: int) -> int:
    return [0, 30, 120, 600, 3600][min(max(attempt_count, 0), 4)]
