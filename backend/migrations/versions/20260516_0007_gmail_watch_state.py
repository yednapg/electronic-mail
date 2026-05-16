"""Track Gmail push watch state.

Revision ID: 20260516_0007
Revises: 20260515_0006
Create Date: 2026-05-16
"""
from __future__ import annotations

from alembic import op


revision = "20260516_0007"
down_revision = "20260515_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS gmail_watch_history_id TEXT")
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS gmail_watch_expiration_at TIMESTAMPTZ")
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS gmail_watch_started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS gmail_watch_error TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS gmail_watch_error")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS gmail_watch_started_at")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS gmail_watch_expiration_at")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS gmail_watch_history_id")
