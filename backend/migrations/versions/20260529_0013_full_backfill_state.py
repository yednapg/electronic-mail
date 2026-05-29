"""Track full Gmail historical backfill state.

Revision ID: 20260529_0013
Revises: 20260521_0012
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op


revision = "20260529_0013"
down_revision = "20260521_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS full_backfill_started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS full_backfill_completed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS full_backfill_completed_at")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS full_backfill_started_at")
