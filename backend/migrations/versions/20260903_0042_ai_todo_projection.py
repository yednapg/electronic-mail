"""Add an independent AI-derived to-do projection.

Revision ID: 20260903_0042
Revises: 20260830_0041
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op


revision = "20260903_0042"
down_revision = "20260830_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_todo_items (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          gmail_account_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          matter_id TEXT NOT NULL,
          source_key TEXT NOT NULL,
          source_subgoal_id TEXT,
          display_kind TEXT NOT NULL
            CHECK (display_kind IN ('todo', 'worth_knowing', 'hidden')),
          title TEXT,
          detail TEXT,
          owner TEXT NOT NULL DEFAULT 'unknown'
            CHECK (owner IN ('user', 'other', 'unknown')),
          action_type TEXT NOT NULL DEFAULT 'none'
            CHECK (action_type IN (
              'reply', 'upload', 'pay', 'confirm', 'review', 'track',
              'register', 'schedule', 'send', 'sign', 'submit', 'call',
              'attend', 'renew', 'cancel', 'open', 'other', 'none'
            )),
          requirement TEXT NOT NULL DEFAULT 'informational'
            CHECK (requirement IN (
              'required', 'committed', 'necessary', 'optional',
              'informational', 'waiting'
            )),
          due_at TIMESTAMPTZ,
          due_date_source TEXT NOT NULL DEFAULT 'none'
            CHECK (due_date_source IN ('explicit', 'none')),
          urgency TEXT NOT NULL DEFAULT 'unscheduled'
            CHECK (urgency IN ('now', 'today', 'upcoming', 'unscheduled')),
          confidence DOUBLE PRECISION NOT NULL DEFAULT 0
            CHECK (confidence >= 0 AND confidence <= 1),
          evidence_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          evidence_text TEXT,
          exclusion_reason TEXT,
          status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'completed', 'snoozed', 'dismissed')),
          snoozed_until TIMESTAMPTZ,
          source_matter_revision BIGINT NOT NULL,
          user_modified BOOLEAN NOT NULL DEFAULT FALSE,
          classifier_model TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          revision BIGINT NOT NULL DEFAULT 1,
          completed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (gmail_account_id, generation_id, matter_id, source_key),
          FOREIGN KEY (matter_id, gmail_account_id, generation_id)
            REFERENCES matters(id, gmail_account_id, generation_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_ai_todo_items_projection
        ON ai_todo_items (
          user_id, gmail_account_id, generation_id, status,
          display_kind, due_at, updated_at DESC
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_ai_todo_items_stale
        ON ai_todo_items (
          user_id, gmail_account_id, generation_id, matter_id,
          source_matter_revision
        )
        """
    )

    expression = (
        "gmail_account_id = COALESCE("
        "NULLIF(current_setting('electronic_mail.gmail_account_id', TRUE), ''),"
        "(SELECT primary_gmail_account_id FROM users WHERE id=ai_todo_items.user_id))"
    )
    op.execute("ALTER TABLE ai_todo_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_todo_items FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY isolate_ai_todo_items_by_gmail_account "
        f"ON ai_todo_items USING ({expression}) WITH CHECK ({expression})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_todo_items")
