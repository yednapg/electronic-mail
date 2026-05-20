"""Add preserved HTML render documents for mail reader.

Revision ID: 20260519_0009
Revises: 20260516_0008
Create Date: 2026-05-19
"""
from __future__ import annotations

from alembic import op


revision = "20260519_0009"
down_revision = "20260516_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_messages ADD COLUMN IF NOT EXISTS html_render_document TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE gmail_messages DROP COLUMN IF EXISTS html_render_document")
