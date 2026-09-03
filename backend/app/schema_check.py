from __future__ import annotations

"""Bounded startup gate that keeps workers behind the migrated schema."""

import argparse
from contextlib import contextmanager
import sys
import time
from typing import Any, Iterator

from app.core.config import Settings, load_settings
from app.db.repository import ALEMBIC_HEAD_REVISION, connect_bounded_schema_probe


SCHEMA_PROBE_CONNECT_TIMEOUT_SECONDS = 5
SCHEMA_PROBE_LOCK_TIMEOUT_MS = 5_000
SCHEMA_PROBE_STATEMENT_TIMEOUT_MS = 15_000
SCHEMA_PROBE_TRANSACTION_TIMEOUT_MS = 20_000
ALEMBIC_REVISION_PROBE = (
    "SELECT MIN(version_num) FROM alembic_version HAVING COUNT(*) = 1"
)
VECTOR_EXTENSION_PROBE = "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
REQUIRED_SCHEMA_PROBES = (
    (
        "SELECT body_fetch_status, body_fetch_updated_at, render_doc_bytes, "
        "content_revision, attachment_descriptors_json, attachment_descriptors_ready "
        "FROM gmail_messages LIMIT 1"
    ),
    "SELECT 1 FROM mail_groups LIMIT 1",
    "SELECT 1 FROM app_session_snapshots LIMIT 1",
    "SELECT previous_labels_json FROM gmail_pending_thread_actions LIMIT 1",
    "SELECT attachments_json, request_hash FROM gmail_pending_sends LIMIT 1",
    "SELECT 1 FROM gmail_client_drafts LIMIT 1",
    "SELECT login_code_hash, exchange_code_challenge FROM mobile_oauth_handoffs LIMIT 1",
    "SELECT started_epoch FROM oauth_login_sessions LIMIT 1",
    "SELECT intent, initiating_user_id FROM oauth_login_sessions LIMIT 1",
    "SELECT subject_hash, deleted_epoch FROM google_subject_deletion_tombstones LIMIT 1",
    "SELECT active_generation_id, previous_generation_id FROM gmail_thread_order_state LIMIT 1",
    "SELECT generation_id, gmail_thread_id, position FROM gmail_thread_order_entries LIMIT 1",
    (
        "SELECT reconcile_generation, reconcile_cursor, "
        "reconcile_baseline_history_id, reconcile_started_at, last_delta_sync_at, "
        "history_cursor_authoritative, sync_generation, phase, initial_target_count, "
        "initial_metadata_count, initial_body_target_count, initial_body_ready_count, "
        "history_metadata_count, history_body_ready_count, estimated_total_count, "
        "initial_window_complete, history_metadata_complete, history_body_complete, "
        "last_progress_at, attachment_descriptors_complete "
        "FROM gmail_import_state LIMIT 1"
    ),
    "SELECT generation_id, message_id FROM gmail_reconcile_seen LIMIT 1",
    (
        "SELECT generation_id, gmail_thread_id, position, message_count, "
        "metadata_ready_at, body_ready_at "
        "FROM gmail_initial_window_entries LIMIT 1"
    ),
    "SELECT release_sha FROM worker_heartbeats LIMIT 1",
    (
        "SELECT consented_at, enabled, grouping_style, rollout_mode, "
        "active_generation_id, revision FROM matter_profiles LIMIT 1"
    ),
    "SELECT prompt_version, embedding_model, classifier_model, review_model FROM matter_generations LIMIT 1",
    (
        "SELECT content_revision, reference_tokens, embedding, processing_state "
        "FROM message_semantics LIMIT 1"
    ),
    "SELECT extracted_text, status, truncated FROM attachment_text_extractions LIMIT 1",
    (
        "SELECT stable_goal, dynamic_title, summary, status, evidence_message_ids, "
        "confidence_state, revision FROM matters LIMIT 1"
    ),
    "SELECT matter_id, message_id, membership_locked FROM matter_members LIMIT 1",
    (
        "SELECT canonical_key, goal, status, latest_development, revision "
        "FROM matter_subgoals LIMIT 1"
    ),
    "SELECT decision_type, constraints_json, idempotency_key FROM matter_decisions LIMIT 1",
    "SELECT model, latency_ms, estimated_cost_usd, error_code FROM ai_usage_events LIMIT 1",
    (
        "SELECT id, user_id, email, google_sub, state, initial_ready_at "
        "FROM gmail_accounts LIMIT 1"
    ),
    "SELECT primary_gmail_account_id FROM users LIMIT 1",
    (
        "SELECT gmail_account_id, row_counts, identifier_checksums, verified_at "
        "FROM multi_account_migration_audits LIMIT 1"
    ),
    "SELECT gmail_account_id FROM gmail_messages LIMIT 1",
    "SELECT gmail_account_id FROM gmail_import_state LIMIT 1",
    "SELECT gmail_account_id FROM gmail_pending_sends LIMIT 1",
    "SELECT gmail_account_id FROM gmail_client_drafts LIMIT 1",
    "SELECT gmail_account_id FROM matter_profiles LIMIT 1",
    "SELECT gmail_account_id FROM matters LIMIT 1",
    "SELECT gmail_account_id FROM ai_usage_events LIMIT 1",
    "SELECT user_id, gmail_account_id FROM google_oauth_tokens LIMIT 1",
)


def configure_schema_probe_connection(connection) -> None:
    """Keep startup/readiness schema probes bounded while migrations hold locks."""
    connection.exec_driver_sql(
        f"SET LOCAL lock_timeout = '{SCHEMA_PROBE_LOCK_TIMEOUT_MS}ms'"
    )
    connection.exec_driver_sql(
        f"SET LOCAL statement_timeout = '{SCHEMA_PROBE_STATEMENT_TIMEOUT_MS}ms'"
    )


@contextmanager
def schema_probe_connection(settings: Settings) -> Iterator[Any]:
    """Yield a probe connection whose budgets precede checkout and SQL."""
    with connect_bounded_schema_probe(
        str(settings.database_path),
        connect_timeout_seconds=SCHEMA_PROBE_CONNECT_TIMEOUT_SECONDS,
        lock_timeout_ms=SCHEMA_PROBE_LOCK_TIMEOUT_MS,
        statement_timeout_ms=SCHEMA_PROBE_STATEMENT_TIMEOUT_MS,
        transaction_timeout_ms=SCHEMA_PROBE_TRANSACTION_TIMEOUT_MS,
    ) as connection:
        configure_schema_probe_connection(connection)
        yield connection


def schema_is_current(settings: Settings) -> bool:
    """Return whether Postgres is reachable and stamped at this release head."""
    if settings.database_backend != "postgres":
        return False
    try:
        with schema_probe_connection(settings) as connection:
            revision = connection.exec_driver_sql(ALEMBIC_REVISION_PROBE).scalar()
            if revision != ALEMBIC_HEAD_REVISION:
                return False
            if connection.exec_driver_sql(VECTOR_EXTENSION_PROBE).scalar() != 1:
                return False
            if getattr(settings, "is_production_like", False) is True and (
                getattr(settings, "multi_gmail_writes_enabled", False) is True
                or getattr(settings, "multi_gmail_ai_enabled", False) is True
            ):
                role_bypasses_isolation = connection.exec_driver_sql(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).scalar()
                if bool(role_bypasses_isolation):
                    return False
            for query in REQUIRED_SCHEMA_PROBES:
                connection.exec_driver_sql(query)
    except Exception:
        return False
    return True


def wait_for_current_schema(settings: Settings, *, wait_seconds: float, interval_seconds: float = 2.0) -> bool:
    """Wait for the API migration service without exposing connection details."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        if schema_is_current(settings):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.1, min(interval_seconds, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    args = parser.parse_args()
    settings = load_settings()
    if wait_for_current_schema(settings, wait_seconds=args.wait_seconds):
        return 0
    print(
        f"Database schema is not ready at expected revision {ALEMBIC_HEAD_REVISION}.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
