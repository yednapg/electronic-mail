"""Add manual tasks and entity outcomes.

Revision ID: 20260516_0008
Revises: 20260516_0007
Create Date: 2026-05-16
"""
from __future__ import annotations

from alembic import op


revision = "20260516_0008"
down_revision = "20260516_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_tasks (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          entity_id TEXT NOT NULL,
          title TEXT NOT NULL,
          notes TEXT,
          section TEXT NOT NULL DEFAULT 'today',
          due_at TIMESTAMPTZ,
          status TEXT NOT NULL DEFAULT 'open',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, entity_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_manual_tasks_user_status_section ON manual_tasks(user_id, status, section, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_manual_tasks_user_entity ON manual_tasks(user_id, entity_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_outcomes (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          entity_id TEXT NOT NULL,
          outcome_type TEXT NOT NULL,
          snooze_until TIMESTAMPTZ,
          note TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_entity_outcomes_latest ON entity_outcomes(user_id, entity_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_entity_outcomes_user_type ON entity_outcomes(user_id, outcome_type, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS entity_outcomes")
    op.execute("DROP TABLE IF EXISTS manual_tasks")
