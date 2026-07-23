"""Add launch-ready Gmail draft identities and queued attachments.

Revision ID: 20260713_0019
Revises: 20260609_0018
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op


revision = "20260713_0019"
down_revision = "20260609_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_client_drafts (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          client_draft_id TEXT NOT NULL,
          gmail_draft_id TEXT,
          gmail_message_id TEXT,
          gmail_thread_id TEXT,
          content_hash TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT 'saved',
          last_client_send_id TEXT,
          sent_message_id TEXT,
          error TEXT,
          created_at TIMESTAMPTZ NOT NULL,
          saved_at TIMESTAMPTZ,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, client_draft_id),
          UNIQUE (user_id, gmail_draft_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gmail_client_drafts_message "
        "ON gmail_client_drafts(user_id, gmail_message_id)"
    )
    op.execute(
        "ALTER TABLE gmail_pending_sends "
        "ADD COLUMN IF NOT EXISTS attachments_json JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mobile_oauth_handoffs (
          handoff_id TEXT PRIMARY KEY,
          login_code_encrypted TEXT,
          status TEXT NOT NULL DEFAULT 'ready',
          error TEXT,
          expires_at TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_mobile_oauth_handoffs_expiry ON mobile_oauth_handoffs(expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mobile_oauth_handoffs_expiry")
    op.execute("DROP TABLE IF EXISTS mobile_oauth_handoffs")
    op.execute("ALTER TABLE gmail_pending_sends DROP COLUMN IF EXISTS attachments_json")
    op.execute("DROP INDEX IF EXISTS idx_gmail_client_drafts_message")
    op.execute("DROP TABLE IF EXISTS gmail_client_drafts")
