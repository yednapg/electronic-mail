"""Bind native login-code exchange to a client-held verifier.

Revision ID: 20260713_0020
Revises: 20260713_0019
Create Date: 2026-07-13
"""

from __future__ import annotations

from alembic import op


revision = "20260713_0020"
down_revision = "20260713_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE mobile_oauth_handoffs ADD COLUMN IF NOT EXISTS login_code_hash TEXT")
    op.execute("ALTER TABLE mobile_oauth_handoffs ADD COLUMN IF NOT EXISTS exchange_code_challenge TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE mobile_oauth_handoffs DROP COLUMN IF EXISTS exchange_code_challenge")
    op.execute("ALTER TABLE mobile_oauth_handoffs DROP COLUMN IF EXISTS login_code_hash")
