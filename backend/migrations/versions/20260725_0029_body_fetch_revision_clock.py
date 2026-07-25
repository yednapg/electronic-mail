"""Separate body-fetch bookkeeping from the mailbox revision clock.

Revision ID: 20260725_0029
Revises: 20260725_0028
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op


revision = "20260725_0029"
down_revision = "20260725_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_messages "
        "ADD COLUMN IF NOT EXISTS body_fetch_updated_at TIMESTAMPTZ"
    )
    op.execute(
        """
        UPDATE gmail_messages
        SET body_fetch_updated_at = COALESCE(body_fetched_at, updated_at)
        WHERE body_fetch_updated_at IS NULL
          AND body_fetch_status IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_messages "
        "DROP COLUMN IF EXISTS body_fetch_updated_at"
    )
