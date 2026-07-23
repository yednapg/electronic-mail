"""Record the immutable release that emitted each worker heartbeat.

Revision ID: 20260721_0022
Revises: 20260714_0021
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op


revision = "20260721_0022"
down_revision = "20260714_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing and briefly overlapping pre-release workers remain writable, but
    # their explicit sentinel can never satisfy release-matched readiness.
    op.execute(
        """
        ALTER TABLE worker_heartbeats
        ADD COLUMN IF NOT EXISTS release_sha TEXT NOT NULL DEFAULT 'unknown'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE worker_heartbeats DROP COLUMN IF EXISTS release_sha")
