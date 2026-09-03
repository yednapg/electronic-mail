from __future__ import annotations

"""Read-only release gate for the multi-Gmail ownership migration."""

import json
import sys

from app.core.config import load_settings
from app.db.repository import ALEMBIC_HEAD_REVISION, connect


MAILBOX_TABLES = (
    "google_oauth_tokens",
    "gmail_messages",
    "mail_groups",
    "mail_group_members",
    "gmail_import_state",
    "background_jobs",
    "app_session_snapshots",
    "manual_tasks",
    "entity_outcomes",
    "gmail_pending_thread_actions",
    "gmail_pending_sends",
    "mailbox_events",
    "visible_mail_groups",
    "visible_mail_group_members",
    "grouping_decision_audit",
    "gmail_client_drafts",
    "gmail_thread_order_state",
    "gmail_thread_order_entries",
    "gmail_reconcile_seen",
    "gmail_initial_window_entries",
    "google_contact_avatar_cache",
    "matter_generations",
    "matter_profiles",
    "message_semantics",
    "attachment_text_extractions",
    "matters",
    "matter_members",
    "matter_decisions",
    "ai_usage_events",
    "matter_subgoals",
)


def run_check(database_url: str) -> dict[str, object]:
    failures: list[str] = []
    counts: dict[str, int] = {}
    with connect(database_url) as connection:
        revision_row = connection.execute(
            "SELECT MIN(version_num) AS revision FROM alembic_version HAVING COUNT(*) = 1"
        ).fetchone()
        revision = str(revision_row["revision"]) if revision_row is not None else None
        if revision != ALEMBIC_HEAD_REVISION:
            failures.append(
                f"schema revision is {revision or 'missing'}, expected {ALEMBIC_HEAD_REVISION}"
            )

        user_row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        account_row = connection.execute(
            "SELECT COUNT(*) AS count FROM gmail_accounts"
        ).fetchone()
        audit_row = connection.execute(
            "SELECT COUNT(*) AS count FROM multi_account_migration_audits"
        ).fetchone()
        counts["users"] = int(user_row["count"] if user_row else 0)
        counts["gmail_accounts"] = int(account_row["count"] if account_row else 0)
        counts["migration_audits"] = int(audit_row["count"] if audit_row else 0)

        invalid_primary = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM users u
            LEFT JOIN gmail_accounts ga
              ON ga.id = u.primary_gmail_account_id AND ga.user_id = u.id
            LEFT JOIN multi_account_migration_audits audit
              ON audit.user_id = u.id
             AND audit.gmail_account_id = u.primary_gmail_account_id
            WHERE ga.id IS NULL OR audit.user_id IS NULL
            """
        ).fetchone()
        invalid_primary_count = int(invalid_primary["count"] if invalid_primary else 0)
        if invalid_primary_count:
            failures.append(
                f"{invalid_primary_count} users are missing a verified primary Gmail account"
            )

        for table_name in MAILBOX_TABLES:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM {table_name} scoped
                LEFT JOIN gmail_accounts ga
                  ON ga.id = scoped.gmail_account_id
                 AND ga.user_id = scoped.user_id
                WHERE scoped.user_id IS NOT NULL AND ga.id IS NULL
                """
            ).fetchone()
            invalid_count = int(row["count"] if row else 0)
            if invalid_count:
                failures.append(
                    f"{table_name} has {invalid_count} rows without safe Gmail ownership"
                )

    return {
        "ok": not failures,
        "revision": revision,
        "counts": counts,
        "failures": failures,
    }


def main() -> int:
    settings = load_settings()
    if not settings.database_path:
        print(json.dumps({"ok": False, "failures": ["DATABASE_URL is required"]}))
        return 1
    result = run_check(str(settings.database_path))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
