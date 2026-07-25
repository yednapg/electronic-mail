"""Persist bounded attachment metadata for mailbox list projections.

Revision ID: 20260725_0031
Revises: 20260725_0030
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op


revision = "20260725_0031"
down_revision = "20260725_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_messages "
        "ADD COLUMN IF NOT EXISTS attachment_descriptors_json TEXT NOT NULL DEFAULT '[]'"
    )
    op.execute(
        "ALTER TABLE gmail_messages "
        "ADD COLUMN IF NOT EXISTS attachment_descriptors_ready BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS attachment_descriptors_complete BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS attachment_descriptors_complete"
    )
    op.execute(
        "ALTER TABLE gmail_messages "
        "DROP COLUMN IF EXISTS attachment_descriptors_ready"
    )
    op.execute(
        "ALTER TABLE gmail_messages "
        "DROP COLUMN IF EXISTS attachment_descriptors_json"
    )
