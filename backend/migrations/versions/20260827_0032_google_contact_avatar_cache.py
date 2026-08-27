"""Add the optional per-user Google Contact avatar cache.

Revision ID: 20260827_0032
Revises: 20260725_0031
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op


revision = "20260827_0032"
down_revision = "20260725_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS google_contact_avatar_cache (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          normalized_email TEXT NOT NULL,
          source_url TEXT,
          expires_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, normalized_email)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_google_contact_avatar_cache_expiry "
        "ON google_contact_avatar_cache (user_id, expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_contact_avatar_cache")
