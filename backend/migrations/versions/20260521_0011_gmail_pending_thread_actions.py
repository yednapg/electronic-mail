"""Add queued Gmail thread actions.

Revision ID: 20260521_0011
Revises: 20260521_0010
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op


revision = "20260521_0011"
down_revision = "20260521_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_pending_thread_actions (
          server_action_id TEXT PRIMARY KEY,
          client_action_id TEXT NOT NULL,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          mailbox_thread_id TEXT NOT NULL,
          action TEXT NOT NULL,
          state TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          queued_at TIMESTAMPTZ NOT NULL,
          applied_at TIMESTAMPTZ,
          error TEXT,
          updated_at TIMESTAMPTZ NOT NULL,
          UNIQUE(user_id, client_action_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_gmail_pending_thread_actions_state ON gmail_pending_thread_actions(user_id, state, queued_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gmail_pending_thread_actions_state")
    op.execute("DROP TABLE IF EXISTS gmail_pending_thread_actions")
