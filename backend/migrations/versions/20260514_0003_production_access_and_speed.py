"""production access gate and first-run schema normalization

Revision ID: 20260514_0003
Revises: 20260514_0002
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op

revision = "20260514_0003"
down_revision = "20260514_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS access_enabled BOOLEAN;
        UPDATE users SET access_enabled = COALESCE(access_enabled, TRUE);
        ALTER TABLE users ALTER COLUMN access_enabled SET DEFAULT TRUE;
        ALTER TABLE users ALTER COLUMN access_enabled SET NOT NULL;

        CREATE TABLE IF NOT EXISTS allowed_emails (
          email TEXT PRIMARY KEY,
          enabled BOOLEAN NOT NULL DEFAULT TRUE,
          invited_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS allowed_emails")
