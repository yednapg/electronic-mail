"""Track completed Gmail history deltas separately from import progress.

Revision ID: 20260724_0026
Revises: 20260723_0025
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op


revision = "20260724_0026"
down_revision = "20260723_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS last_delta_sync_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS last_delta_sync_at"
    )
