"""Add attention classification metadata to mail groups.

Revision ID: 20260605_0017
Revises: 20260529_0016
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op


revision = "20260605_0017"
down_revision = "20260529_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS classification_version TEXT")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS classification_json JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS classification_confidence REAL NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS ranking_reason TEXT")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS suppression_reason TEXT")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mail_groups_classification ON mail_groups(user_id, classification_version, classified_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mail_groups_classification")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS classified_at")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS suppression_reason")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS ranking_reason")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS classification_confidence")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS classification_json")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS classification_version")
