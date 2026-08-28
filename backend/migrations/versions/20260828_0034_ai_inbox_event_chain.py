"""Add event-chain subgoals and partial matter outcomes.

Revision ID: 20260828_0034
Revises: 20260827_0033
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op


revision = "20260828_0034"
down_revision = "20260827_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE matters DROP CONSTRAINT IF EXISTS matters_status_check")
    op.execute(
        """
        ALTER TABLE matters ADD CONSTRAINT matters_status_check CHECK (
          status IN (
            'needs_you', 'waiting_on_others', 'in_progress', 'completed',
            'partially_completed', 'failed', 'cancelled', 'informational'
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE matter_subgoals (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          matter_id TEXT NOT NULL,
          canonical_key TEXT NOT NULL,
          goal TEXT NOT NULL,
          status TEXT NOT NULL CHECK (
            status IN (
              'needs_you', 'waiting_on_others', 'in_progress',
              'completed', 'failed', 'cancelled'
            )
          ),
          latest_development TEXT NOT NULL,
          evidence_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          revision BIGINT NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (user_id, generation_id, matter_id, canonical_key),
          FOREIGN KEY (matter_id, user_id, generation_id)
            REFERENCES matters(id, user_id, generation_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_matter_subgoals_projection
        ON matter_subgoals (user_id, generation_id, matter_id, status, updated_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS matter_subgoals")
    op.execute("UPDATE matters SET status = 'in_progress' WHERE status = 'partially_completed'")
    op.execute("ALTER TABLE matters DROP CONSTRAINT IF EXISTS matters_status_check")
    op.execute(
        """
        ALTER TABLE matters ADD CONSTRAINT matters_status_check CHECK (
          status IN (
            'needs_you', 'waiting_on_others', 'in_progress', 'completed',
            'failed', 'cancelled', 'informational'
          )
        )
        """
    )
