"""Add durable Gmail compose and reply sends.

Revision ID: 20260521_0012
Revises: 20260521_0011
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op


revision = "20260521_0012"
down_revision = "20260521_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_pending_sends (
          server_send_id TEXT PRIMARY KEY,
          client_send_id TEXT NOT NULL,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          send_type TEXT NOT NULL,
          mailbox_thread_id TEXT,
          gmail_thread_id TEXT,
          to_json TEXT NOT NULL DEFAULT '[]',
          cc_json TEXT NOT NULL DEFAULT '[]',
          bcc_json TEXT NOT NULL DEFAULT '[]',
          subject TEXT NOT NULL,
          body_text TEXT NOT NULL,
          body_html TEXT,
          headers_json TEXT NOT NULL DEFAULT '{}',
          state TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          queued_at TIMESTAMPTZ NOT NULL,
          sent_at TIMESTAMPTZ,
          gmail_message_id TEXT,
          error TEXT,
          updated_at TIMESTAMPTZ NOT NULL,
          UNIQUE(user_id, client_send_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_gmail_pending_sends_state ON gmail_pending_sends(user_id, state, queued_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gmail_pending_sends_state")
    op.execute("DROP TABLE IF EXISTS gmail_pending_sends")
