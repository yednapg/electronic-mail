from __future__ import annotations

"""Bounded startup gate that keeps workers behind the migrated schema."""

import argparse
import sys
import time

from app.core.config import Settings, load_settings
from app.db.repository import ALEMBIC_HEAD_REVISION, get_engine


def schema_is_current(settings: Settings) -> bool:
    """Return whether Postgres is reachable and stamped at this release head."""
    if settings.database_backend != "postgres":
        return False
    try:
        with get_engine(str(settings.database_path)).connect() as connection:
            revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version LIMIT 1").scalar()
            if revision != ALEMBIC_HEAD_REVISION:
                return False
            connection.exec_driver_sql("SELECT attachments_json FROM gmail_pending_sends LIMIT 1")
            connection.exec_driver_sql("SELECT 1 FROM gmail_client_drafts LIMIT 1")
            connection.exec_driver_sql(
                "SELECT login_code_hash, exchange_code_challenge FROM mobile_oauth_handoffs LIMIT 1"
            )
            connection.exec_driver_sql("SELECT started_epoch FROM oauth_login_sessions LIMIT 1")
            connection.exec_driver_sql(
                "SELECT subject_hash, deleted_epoch FROM google_subject_deletion_tombstones LIMIT 1"
            )
            connection.exec_driver_sql(
                "SELECT active_generation_id, previous_generation_id FROM gmail_thread_order_state LIMIT 1"
            )
            connection.exec_driver_sql(
                "SELECT generation_id, gmail_thread_id, position FROM gmail_thread_order_entries LIMIT 1"
            )
            connection.exec_driver_sql("SELECT release_sha FROM worker_heartbeats LIMIT 1")
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
