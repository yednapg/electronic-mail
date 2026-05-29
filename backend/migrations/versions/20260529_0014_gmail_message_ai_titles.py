"""Track AI titles for individual Gmail messages.

Revision ID: 20260529_0014
Revises: 20260529_0013
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op


revision = "20260529_0014"
down_revision = "20260529_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS ai_title TEXT")
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS ai_title_generated_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS ai_title_generated_at")
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS ai_title")
