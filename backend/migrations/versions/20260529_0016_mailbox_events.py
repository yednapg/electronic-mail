"""add durable mailbox events

Revision ID: 20260529_0016
Revises: 20260529_0015
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op


revision = "20260529_0016"
down_revision = "20260529_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mailbox_events (
          id BIGSERIAL PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL,
          mailbox_label TEXT,
          payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_mailbox_events_user_id ON mailbox_events(user_id, id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mailbox_events_user_created ON mailbox_events(user_id, created_at, id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mailbox_events_user_created")
    op.execute("DROP INDEX IF EXISTS idx_mailbox_events_user_id")
    op.execute("DROP TABLE IF EXISTS mailbox_events")
