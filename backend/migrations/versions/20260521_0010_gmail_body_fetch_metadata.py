"""Track Gmail body hydration state.

Revision ID: 20260521_0010
Revises: 20260519_0009
Create Date: 2026-05-21
"""
from __future__ import annotations

from alembic import op


revision = "20260521_0010"
down_revision = "20260519_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS body_fetch_status TEXT NOT NULL DEFAULT 'missing'")
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS body_fetched_at TIMESTAMPTZ")
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS body_fetch_error TEXT")
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS render_doc_bytes INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gmail_messages_body_fetch ON gmail_messages(user_id, body_fetch_status, internal_date DESC)")
    op.execute(
        """
        UPDATE gmail_messages
        SET
          body_fetch_status = 'fetched',
          body_fetched_at = COALESCE(body_fetched_at, updated_at),
          body_fetch_error = NULL,
          render_doc_bytes = COALESCE(length(html_render_document), 0)
        WHERE html_render_document IS NOT NULL
           OR html_body_sanitized IS NOT NULL
           OR text_body IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gmail_messages_body_fetch")
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS render_doc_bytes")
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS body_fetch_error")
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS body_fetched_at")
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS body_fetch_status")
