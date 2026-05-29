"""Allow queued Gmail actions to target one message.

Revision ID: 20260529_0015
Revises: 20260529_0014
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op


revision = "20260529_0015"
down_revision = "20260529_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_pending_thread_actions ADD COLUMN IF NOT EXISTS target_message_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_pending_thread_actions DROP COLUMN IF EXISTS target_message_id")
