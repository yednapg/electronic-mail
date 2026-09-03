"""Add the preservation-first multi-Gmail account ownership foundation.

Revision ID: 20260830_0035
Revises: 20260828_0034
Create Date: 2026-08-30

This migration is intentionally additive. Existing mailbox and AI rows are not
deleted, copied, regenerated, or assigned new domain identifiers. Each row only
receives the id of the newly-created primary Gmail account. For an existing
user that account id is the user's existing id, which also preserves legacy
cache and lock namespaces during the compatibility rollout.
"""

from __future__ import annotations

from alembic import op


revision = "20260830_0035"
down_revision = "20260828_0034"
branch_labels = None
depends_on = None


# Every current table whose rows belong to a Gmail mailbox. `background_jobs`
# is nullable because system-wide jobs intentionally have no user or mailbox.
MAILBOX_TABLES: tuple[tuple[str, bool], ...] = (
    ("google_oauth_tokens", False),
    ("gmail_messages", False),
    ("mail_groups", False),
    ("mail_group_members", False),
    ("gmail_import_state", False),
    ("background_jobs", True),
    ("app_session_snapshots", False),
    ("manual_tasks", False),
    ("entity_outcomes", False),
    ("gmail_pending_thread_actions", False),
    ("gmail_pending_sends", False),
    ("mailbox_events", False),
    ("visible_mail_groups", False),
    ("visible_mail_group_members", False),
    ("grouping_decision_audit", False),
    ("gmail_client_drafts", False),
    ("gmail_thread_order_state", False),
    ("gmail_thread_order_entries", False),
    ("gmail_reconcile_seen", False),
    ("gmail_initial_window_entries", False),
    ("google_contact_avatar_cache", False),
    ("matter_generations", False),
    ("matter_profiles", False),
    ("message_semantics", False),
    ("attachment_text_extractions", False),
    ("matters", False),
    ("matter_members", False),
    ("matter_decisions", False),
    ("ai_usage_events", False),
    ("matter_subgoals", False),
)


# Stable identifiers cover the acceptance-critical mail, sync, draft/action,
# and AI projections. The checksum is only compared inside this transaction;
# it is not treated as a long-term cryptographic content digest.
PRESERVATION_KEYS: dict[str, str] = {
    "google_oauth_tokens": "user_id",
    "gmail_messages": "concat_ws(chr(31), user_id, message_id, coalesce(gmail_thread_id, ''))",
    "mail_groups": "concat_ws(chr(31), user_id, id, group_key)",
    "mail_group_members": "concat_ws(chr(31), user_id, id, group_id, gmail_message_id)",
    "gmail_import_state": (
        "concat_ws(chr(31), user_id, coalesce(last_history_id, ''), "
        "coalesce(full_backfill_cursor, ''), coalesce(sync_generation, ''), coalesce(phase, ''))"
    ),
    "app_session_snapshots": "concat_ws(chr(31), user_id, dashboard_json, mailbox_json, sync_json)",
    "gmail_pending_thread_actions": "concat_ws(chr(31), user_id, server_action_id, client_action_id, mailbox_thread_id)",
    "gmail_pending_sends": "concat_ws(chr(31), user_id, server_send_id, client_send_id, coalesce(gmail_message_id, ''))",
    "gmail_client_drafts": "concat_ws(chr(31), user_id, client_draft_id, coalesce(gmail_draft_id, ''), coalesce(gmail_message_id, ''))",
    "gmail_thread_order_state": "concat_ws(chr(31), user_id, label, active_generation_id)",
    "gmail_thread_order_entries": "concat_ws(chr(31), user_id, label, generation_id, gmail_thread_id, position::text)",
    "matter_generations": "concat_ws(chr(31), user_id, id, status)",
    "matter_profiles": "concat_ws(chr(31), user_id, revision::text, enabled::text, grouping_style, rollout_mode)",
    "message_semantics": "concat_ws(chr(31), user_id, message_id, generation_id, content_revision::text)",
    "attachment_text_extractions": "concat_ws(chr(31), user_id, message_id, attachment_id, content_revision::text)",
    "matters": "concat_ws(chr(31), user_id, id, generation_id, revision::text)",
    "matter_members": "concat_ws(chr(31), user_id, generation_id, matter_id, message_id)",
    "matter_decisions": "concat_ws(chr(31), user_id, id, generation_id, coalesce(idempotency_key, ''))",
    "ai_usage_events": "concat_ws(chr(31), user_id, id, coalesce(generation_id, ''), coalesce(message_id, ''))",
    "matter_subgoals": "concat_ws(chr(31), user_id, id, generation_id, matter_id, revision::text)",
}


def _preservation_snapshot_sql(table_name: str, key_expression: str) -> str:
    return f"""
        SELECT
          user_id,
          '{table_name}'::text AS dataset,
          COUNT(*)::bigint AS row_count,
          COALESCE(SUM(hashtextextended({key_expression}, 0)::numeric), 0)::text AS identifier_checksum
        FROM {table_name}
        WHERE user_id IS NOT NULL
        GROUP BY user_id
    """


def _create_preservation_snapshot(name: str) -> None:
    queries = [
        _preservation_snapshot_sql(table_name, expression)
        for table_name, expression in PRESERVATION_KEYS.items()
    ]
    op.execute(
        f"CREATE TEMP TABLE {name} ON COMMIT DROP AS " + " UNION ALL ".join(queries)
    )


def upgrade() -> None:
    # Capture acceptance-critical identifiers before touching the schema. Any
    # exception below aborts the enclosing Alembic transaction.
    _create_preservation_snapshot("multi_gmail_preservation_before")

    op.execute(
        """
        CREATE TABLE gmail_accounts (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          email TEXT NOT NULL,
          display_name TEXT,
          google_sub TEXT NOT NULL UNIQUE,
          state TEXT NOT NULL DEFAULT 'connecting' CHECK (
            state IN (
              'connecting', 'importing', 'ready', 'reauth_required',
              'disconnected', 'deleting'
            )
          ),
          initial_ready_at TIMESTAMPTZ,
          google_disconnected_at TIMESTAMPTZ,
          google_data_delete_requested_at TIMESTAMPTZ,
          google_data_deleted_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_gmail_accounts_user_created "
        "ON gmail_accounts (user_id, created_at, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_gmail_accounts_user_email "
        "ON gmail_accounts (user_id, lower(email))"
    )

    # Reusing the current user id is the key compatibility guarantee: existing
    # cache namespaces, advisory-lock names, and primary inbox identifiers do
    # not change while account-aware code rolls out behind its feature flag.
    op.execute(
        """
        INSERT INTO gmail_accounts (
          id, user_id, email, display_name, google_sub, state,
          initial_ready_at, google_disconnected_at,
          google_data_delete_requested_at, google_data_deleted_at,
          created_at, updated_at
        )
        SELECT
          id, id, email, display_name, google_sub,
          CASE WHEN google_disconnected_at IS NULL THEN 'ready' ELSE 'disconnected' END,
          CASE WHEN google_disconnected_at IS NULL THEN created_at ELSE NULL END,
          google_disconnected_at, google_data_delete_requested_at,
          google_data_deleted_at, created_at, updated_at
        FROM users
        """
    )

    op.execute("ALTER TABLE users ADD COLUMN primary_gmail_account_id TEXT")
    op.execute("UPDATE users SET primary_gmail_account_id = id")
    op.execute("ALTER TABLE users ALTER COLUMN primary_gmail_account_id SET NOT NULL")
    op.execute(
        """
        ALTER TABLE users
        ADD CONSTRAINT fk_users_primary_gmail_account
        FOREIGN KEY (primary_gmail_account_id, id)
        REFERENCES gmail_accounts(id, user_id)
        DEFERRABLE INITIALLY DEFERRED
        """
    )

    # The existing token-write trigger deliberately rejects updates for
    # revoked or disabled identities. This migration only adds ownership
    # metadata; it does not replace or alter encrypted credentials. Remove the
    # trigger transactionally while that metadata is backfilled, then restore
    # it immediately. Any migration failure rolls both operations back.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_google_tokens_require_guard "
        "ON google_oauth_tokens"
    )
    op.execute(
        """
        CREATE FUNCTION electronic_mail_assign_primary_gmail_account()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.gmail_account_id IS NULL AND NEW.user_id IS NOT NULL THEN
            NEW.gmail_account_id := NEW.user_id;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )

    for table_name, nullable in MAILBOX_TABLES:
        op.execute(f"ALTER TABLE {table_name} ADD COLUMN gmail_account_id TEXT")
        op.execute(
            f"UPDATE {table_name} SET gmail_account_id = user_id "
            "WHERE user_id IS NOT NULL"
        )
        if nullable:
            op.execute(
                f"ALTER TABLE {table_name} ADD CONSTRAINT "
                f"ck_{table_name}_gmail_account_scope "
                "CHECK ((user_id IS NULL) = (gmail_account_id IS NULL)) NOT VALID"
            )
            op.execute(
                f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
                f"ck_{table_name}_gmail_account_scope"
            )
        else:
            op.execute(
                f"ALTER TABLE {table_name} ADD CONSTRAINT "
                f"ck_{table_name}_gmail_account_present "
                "CHECK (gmail_account_id IS NOT NULL) NOT VALID"
            )
            op.execute(
                f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
                f"ck_{table_name}_gmail_account_present"
            )
            op.execute(
                f"ALTER TABLE {table_name} ALTER COLUMN gmail_account_id SET NOT NULL"
            )
            op.execute(
                f"ALTER TABLE {table_name} DROP CONSTRAINT "
                f"ck_{table_name}_gmail_account_present"
            )
        op.execute(
            f"ALTER TABLE {table_name} ADD CONSTRAINT "
            f"fk_{table_name}_gmail_account_owner "
            "FOREIGN KEY (gmail_account_id, user_id) "
            "REFERENCES gmail_accounts(id, user_id) ON DELETE CASCADE NOT VALID"
        )
        op.execute(
            f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
            f"fk_{table_name}_gmail_account_owner"
        )
        op.execute(
            f"CREATE INDEX idx_{table_name}_gmail_account "
            f"ON {table_name} (gmail_account_id)"
        )
        # Existing code remains user-scoped during this preservation-first
        # rollout. Primary Gmail account ids deliberately equal user ids, so
        # legacy inserts can keep working while account-aware call sites are
        # introduced incrementally. Explicit secondary account ids are kept.
        op.execute(
            f"""
            CREATE TRIGGER trg_assign_primary_gmail_account
            BEFORE INSERT ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION electronic_mail_assign_primary_gmail_account()
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_google_tokens_require_guard
        BEFORE INSERT OR UPDATE ON google_oauth_tokens
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_require_guarded_google_token_write()
        """
    )

    _create_preservation_snapshot("multi_gmail_preservation_after")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM multi_gmail_preservation_before before_snapshot
            FULL OUTER JOIN multi_gmail_preservation_after after_snapshot
              USING (user_id, dataset)
            WHERE before_snapshot.row_count IS DISTINCT FROM after_snapshot.row_count
               OR before_snapshot.identifier_checksum IS DISTINCT FROM after_snapshot.identifier_checksum
          ) THEN
            RAISE EXCEPTION
              'Multi-Gmail migration stopped: mailbox or AI identifiers changed';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM users u
            LEFT JOIN gmail_accounts ga
              ON ga.id = u.primary_gmail_account_id AND ga.user_id = u.id
            WHERE ga.id IS NULL
          ) THEN
            RAISE EXCEPTION
              'Multi-Gmail migration stopped: a user has no safe primary Gmail account';
          END IF;
        END
        $$
        """
    )

    op.execute(
        """
        CREATE TABLE multi_account_migration_audits (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          gmail_account_id TEXT NOT NULL,
          row_counts JSONB NOT NULL,
          identifier_checksums JSONB NOT NULL,
          verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (gmail_account_id, user_id)
            REFERENCES gmail_accounts(id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        INSERT INTO multi_account_migration_audits (
          user_id, gmail_account_id, row_counts, identifier_checksums
        )
        SELECT
          u.id,
          u.primary_gmail_account_id,
          COALESCE(
            jsonb_object_agg(s.dataset, s.row_count)
              FILTER (WHERE s.dataset IS NOT NULL),
            '{}'::jsonb
          ),
          COALESCE(
            jsonb_object_agg(s.dataset, s.identifier_checksum)
              FILTER (WHERE s.dataset IS NOT NULL),
            '{}'::jsonb
          )
        FROM users u
        LEFT JOIN multi_gmail_preservation_after s ON s.user_id = u.id
        GROUP BY u.id, u.primary_gmail_account_id
        """
    )
    op.execute(
        """
        CREATE FUNCTION mark_gmail_account_initial_ready()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.first_batch_imported_at IS NOT NULL THEN
            UPDATE gmail_accounts
            SET state = CASE
                  WHEN state IN ('connecting', 'importing') THEN 'ready'
                  ELSE state
                END,
                initial_ready_at = COALESCE(initial_ready_at, NEW.first_batch_imported_at),
                updated_at = now()
            WHERE id = NEW.gmail_account_id AND user_id = NEW.user_id;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_gmail_import_state_initial_ready
        AFTER INSERT OR UPDATE OF first_batch_imported_at
        ON gmail_import_state
        FOR EACH ROW
        EXECUTE FUNCTION mark_gmail_account_initial_ready()
        """
    )


def downgrade() -> None:
    # Once an additional account exists, returning to the one-mailbox schema
    # would be data-destructive. Refuse that downgrade explicitly.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM gmail_accounts WHERE id <> user_id) THEN
            RAISE EXCEPTION
              'Cannot downgrade multi-Gmail schema after an additional account was linked';
          END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE multi_account_migration_audits")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_gmail_import_state_initial_ready ON gmail_import_state"
    )
    op.execute("DROP FUNCTION IF EXISTS mark_gmail_account_initial_ready()")
    for table_name, _nullable in reversed(MAILBOX_TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_assign_primary_gmail_account "
            f"ON {table_name}"
        )
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN gmail_account_id CASCADE")
    op.execute("DROP FUNCTION electronic_mail_assign_primary_gmail_account()")
    op.execute("ALTER TABLE users DROP CONSTRAINT fk_users_primary_gmail_account")
    op.execute("ALTER TABLE users DROP COLUMN primary_gmail_account_id")
    op.execute("DROP TABLE gmail_accounts")
