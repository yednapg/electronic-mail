"""Add generation-scoped progressive Gmail bootstrap state.

Revision ID: 20260725_0028
Revises: 20260724_0027
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op


revision = "20260725_0028"
down_revision = "20260724_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS sync_generation TEXT"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS phase TEXT"
    )
    for column in (
        "initial_target_count",
        "initial_metadata_count",
        "initial_body_target_count",
        "initial_body_ready_count",
        "history_metadata_count",
        "history_body_ready_count",
        "estimated_total_count",
    ):
        op.execute(
            "ALTER TABLE gmail_import_state "
            f"ADD COLUMN IF NOT EXISTS {column} INTEGER NOT NULL DEFAULT 0"
        )
    for column in (
        "initial_window_complete",
        "history_metadata_complete",
        "history_body_complete",
    ):
        op.execute(
            "ALTER TABLE gmail_import_state "
            f"ADD COLUMN IF NOT EXISTS {column} BOOLEAN NOT NULL DEFAULT FALSE"
        )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS last_progress_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE gmail_messages "
        "ADD COLUMN IF NOT EXISTS content_revision BIGINT NOT NULL DEFAULT 1"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_initial_window_entries (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          generation_id TEXT NOT NULL,
          gmail_thread_id TEXT NOT NULL,
          position INTEGER NOT NULL,
          message_count INTEGER NOT NULL DEFAULT 0,
          metadata_ready_at TIMESTAMPTZ,
          body_ready_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, generation_id, gmail_thread_id),
          UNIQUE (user_id, generation_id, position)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gmail_initial_window_pending_metadata "
        "ON gmail_initial_window_entries(user_id, generation_id, position) "
        "WHERE metadata_ready_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gmail_initial_window_pending_body "
        "ON gmail_initial_window_entries(user_id, generation_id, position) "
        "WHERE body_ready_at IS NULL"
    )
    op.execute(
        """
        ALTER TABLE gmail_import_state
        DROP CONSTRAINT IF EXISTS ck_gmail_import_state_progress_phase
        """
    )
    op.execute(
        """
        ALTER TABLE gmail_import_state
        ADD CONSTRAINT ck_gmail_import_state_progress_phase
        CHECK (
          phase IS NULL OR phase IN (
            'discovering_recent',
            'importing_metadata',
            'hydrating_priority_content',
            'usable',
            'syncing_recent',
            'syncing_history',
            'complete',
            'failed'
          )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP CONSTRAINT IF EXISTS ck_gmail_import_state_progress_phase"
    )
    op.execute("DROP INDEX IF EXISTS idx_gmail_initial_window_pending_body")
    op.execute("DROP INDEX IF EXISTS idx_gmail_initial_window_pending_metadata")
    op.execute("DROP TABLE IF EXISTS gmail_initial_window_entries")
    op.execute(
        "ALTER TABLE gmail_messages "
        "DROP COLUMN IF EXISTS content_revision"
    )
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS last_progress_at"
    )
    for column in (
        "history_body_complete",
        "history_metadata_complete",
        "initial_window_complete",
        "estimated_total_count",
        "history_body_ready_count",
        "history_metadata_count",
        "initial_body_ready_count",
        "initial_body_target_count",
        "initial_metadata_count",
        "initial_target_count",
        "phase",
        "sync_generation",
    ):
        op.execute(
            "ALTER TABLE gmail_import_state "
            f"DROP COLUMN IF EXISTS {column}"
        )
