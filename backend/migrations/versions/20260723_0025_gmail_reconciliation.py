"""Add durable Gmail reconciliation and immutable send fingerprints.

Revision ID: 20260723_0025
Revises: 20260723_0024
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op


revision = "20260723_0025"
down_revision = "20260723_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_pending_sends "
        "ADD COLUMN IF NOT EXISTS request_hash TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS reconcile_generation TEXT"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS reconcile_cursor TEXT"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS reconcile_baseline_history_id TEXT"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS reconcile_started_at TIMESTAMPTZ"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_reconcile_seen (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          generation_id TEXT NOT NULL,
          message_id TEXT NOT NULL,
          seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, generation_id, message_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gmail_reconcile_seen_generation "
        "ON gmail_reconcile_seen(user_id, generation_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gmail_reconcile_seen_generation")
    op.execute("DROP TABLE IF EXISTS gmail_reconcile_seen")
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS reconcile_started_at"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS reconcile_baseline_history_id"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS reconcile_cursor"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS reconcile_generation"
    )
    op.execute(
        "ALTER TABLE gmail_pending_sends "
        "DROP COLUMN IF EXISTS request_hash"
    )
