"""Make send, action, and draft idempotency Gmail-account scoped.

Revision ID: 20260830_0037
Revises: 20260830_0036
Create Date: 2026-08-30

This migration changes constraints only. It neither rewrites nor deletes a
mailbox row. A transaction-local preservation snapshot makes any count or
identifier mismatch abort the migration.
"""

from alembic import op


revision = "20260830_0037"
down_revision = "20260830_0036"
branch_labels = None
depends_on = None


SNAPSHOT_SQL = """
SELECT dataset, row_count, checksum
FROM (
  SELECT 'gmail_pending_sends'::text AS dataset, COUNT(*)::bigint AS row_count,
         COALESCE(SUM(hashtextextended(concat_ws(chr(31),server_send_id,client_send_id,user_id,gmail_account_id),0)::numeric),0)::text AS checksum
  FROM gmail_pending_sends
  UNION ALL
  SELECT 'gmail_pending_thread_actions', COUNT(*)::bigint,
         COALESCE(SUM(hashtextextended(concat_ws(chr(31),server_action_id,client_action_id,user_id,gmail_account_id),0)::numeric),0)::text
  FROM gmail_pending_thread_actions
  UNION ALL
  SELECT 'gmail_client_drafts', COUNT(*)::bigint,
         COALESCE(SUM(hashtextextended(concat_ws(chr(31),client_draft_id,coalesce(gmail_draft_id,''),user_id,gmail_account_id),0)::numeric),0)::text
  FROM gmail_client_drafts
) preserved
"""


def upgrade() -> None:
    op.execute(f"CREATE TEMP TABLE account_write_before ON COMMIT DROP AS {SNAPSHOT_SQL}")

    op.execute("ALTER TABLE gmail_pending_sends ADD CONSTRAINT uq_gmail_pending_sends_account_client UNIQUE (gmail_account_id, client_send_id)")
    op.execute("ALTER TABLE gmail_pending_thread_actions ADD CONSTRAINT uq_gmail_pending_actions_account_client UNIQUE (gmail_account_id, client_action_id)")
    op.execute("ALTER TABLE gmail_client_drafts ADD CONSTRAINT uq_gmail_client_drafts_account_client UNIQUE (gmail_account_id, client_draft_id)")
    op.execute("ALTER TABLE gmail_client_drafts ADD CONSTRAINT uq_gmail_client_drafts_account_provider UNIQUE (gmail_account_id, gmail_draft_id)")

    op.execute("ALTER TABLE gmail_pending_sends DROP CONSTRAINT gmail_pending_sends_user_id_client_send_id_key")
    op.execute("ALTER TABLE gmail_pending_thread_actions DROP CONSTRAINT gmail_pending_thread_actions_user_id_client_action_id_key")
    op.execute("ALTER TABLE gmail_client_drafts DROP CONSTRAINT gmail_client_drafts_pkey")
    op.execute("ALTER TABLE gmail_client_drafts ADD CONSTRAINT gmail_client_drafts_pkey PRIMARY KEY (gmail_account_id, client_draft_id)")
    op.execute("ALTER TABLE gmail_client_drafts DROP CONSTRAINT gmail_client_drafts_user_id_gmail_draft_id_key")

    op.execute(f"CREATE TEMP TABLE account_write_after ON COMMIT DROP AS {SNAPSHOT_SQL}")
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (
          (SELECT * FROM account_write_before EXCEPT SELECT * FROM account_write_after)
          UNION ALL
          (SELECT * FROM account_write_after EXCEPT SELECT * FROM account_write_before)
        ) THEN
          RAISE EXCEPTION 'Account-scoped write migration changed existing identifiers';
        END IF;
      END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_client_drafts DROP CONSTRAINT gmail_client_drafts_pkey")
    op.execute("ALTER TABLE gmail_client_drafts ADD CONSTRAINT gmail_client_drafts_pkey PRIMARY KEY (user_id, client_draft_id)")
    op.execute("ALTER TABLE gmail_client_drafts ADD CONSTRAINT gmail_client_drafts_user_id_gmail_draft_id_key UNIQUE (user_id, gmail_draft_id)")
    op.execute("ALTER TABLE gmail_pending_sends ADD CONSTRAINT gmail_pending_sends_user_id_client_send_id_key UNIQUE (user_id, client_send_id)")
    op.execute("ALTER TABLE gmail_pending_thread_actions ADD CONSTRAINT gmail_pending_thread_actions_user_id_client_action_id_key UNIQUE (user_id, client_action_id)")
    op.execute("ALTER TABLE gmail_client_drafts DROP CONSTRAINT uq_gmail_client_drafts_account_provider")
    op.execute("ALTER TABLE gmail_client_drafts DROP CONSTRAINT uq_gmail_client_drafts_account_client")
    op.execute("ALTER TABLE gmail_pending_thread_actions DROP CONSTRAINT uq_gmail_pending_actions_account_client")
    op.execute("ALTER TABLE gmail_pending_sends DROP CONSTRAINT uq_gmail_pending_sends_account_client")
