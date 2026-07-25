"""Index the bounded Gmail thread reader.

Revision ID: 20260725_0030
Revises: 20260725_0029
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op


revision = "20260725_0030"
down_revision = "20260725_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gmail_messages_user_thread_reader
        ON gmail_messages (
          user_id,
          (COALESCE(NULLIF(gmail_thread_id, ''), message_id)),
          internal_date ASC NULLS LAST,
          created_at ASC,
          message_id ASC
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gmail_messages_user_thread_reader")
