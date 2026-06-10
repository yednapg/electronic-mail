"""Add final visible mail grouping projection.

Revision ID: 20260609_0018
Revises: 20260605_0017
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op


revision = "20260609_0018"
down_revision = "20260605_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visible_mail_groups (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          projection_key TEXT NOT NULL,
          visibility TEXT NOT NULL DEFAULT 'inbox',
          group_kind TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          canonical_entity TEXT NOT NULL,
          contact_channel TEXT,
          title TEXT NOT NULL,
          summary TEXT NOT NULL,
          workflow_type TEXT NOT NULL DEFAULT 'other',
          confidence REAL NOT NULL DEFAULT 0,
          source_group_id TEXT REFERENCES mail_groups(id) ON DELETE SET NULL,
          source TEXT NOT NULL DEFAULT 'validated_projection',
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          latest_message_at TIMESTAMPTZ,
          latest_message_id TEXT,
          generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, visibility, projection_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_visible_mail_groups_user_latest ON visible_mail_groups(user_id, visibility, status, latest_message_at DESC, id DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_visible_mail_groups_source ON visible_mail_groups(user_id, source_group_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS visible_mail_group_members (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          visible_group_id TEXT NOT NULL REFERENCES visible_mail_groups(id) ON DELETE CASCADE,
          visibility TEXT NOT NULL DEFAULT 'inbox',
          gmail_message_id TEXT NOT NULL,
          gmail_thread_id TEXT,
          reason TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, visible_group_id, gmail_message_id),
          UNIQUE(user_id, visibility, gmail_message_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_visible_mail_group_members_group ON visible_mail_group_members(user_id, visible_group_id, created_at ASC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_visible_mail_group_members_thread ON visible_mail_group_members(user_id, visibility, gmail_thread_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS grouping_decision_audit (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          source_group_id TEXT REFERENCES mail_groups(id) ON DELETE SET NULL,
          projection_key TEXT,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_grouping_decision_audit_user_created ON grouping_decision_audit(user_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_grouping_decision_audit_user_created")
    op.execute("DROP TABLE IF EXISTS grouping_decision_audit")
    op.execute("DROP INDEX IF EXISTS idx_visible_mail_group_members_thread")
    op.execute("DROP INDEX IF EXISTS idx_visible_mail_group_members_group")
    op.execute("DROP TABLE IF EXISTS visible_mail_group_members")
    op.execute("DROP INDEX IF EXISTS idx_visible_mail_groups_source")
    op.execute("DROP INDEX IF EXISTS idx_visible_mail_groups_user_latest")
    op.execute("DROP TABLE IF EXISTS visible_mail_groups")
