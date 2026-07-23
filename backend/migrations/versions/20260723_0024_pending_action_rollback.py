"""Persist optimistic mailbox-action snapshots for terminal rollback.

Revision ID: 20260723_0024
Revises: 20260721_0023
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op


revision = "20260723_0024"
down_revision = "20260721_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_pending_thread_actions "
        "ADD COLUMN IF NOT EXISTS previous_labels_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_pending_thread_actions "
        "DROP COLUMN IF EXISTS previous_labels_json"
    )
