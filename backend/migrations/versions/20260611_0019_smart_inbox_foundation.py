"""Add smart inbox and mail-derived work queue foundation.

Revision ID: 20260611_0019
Revises: 20260609_0018
Create Date: 2026-06-11
"""

from __future__ import annotations

from alembic import op


revision = "20260611_0019"
down_revision = "20260609_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS hot_window_started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE gmail_import_state ADD COLUMN IF NOT EXISTS hot_window_completed_at TIMESTAMPTZ")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS message_signals (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          gmail_message_id TEXT NOT NULL,
          gmail_thread_id TEXT,
          sender_domain TEXT,
          normalized_subject TEXT,
          reference_ids_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          dates_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          money_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          links_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          reply_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          list_signals_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          extraction_version TEXT NOT NULL,
          generated_from_hash TEXT NOT NULL,
          extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, gmail_message_id),
          FOREIGN KEY(user_id, gmail_message_id) REFERENCES gmail_messages(user_id, message_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_message_signals_user_thread ON message_signals(user_id, gmail_thread_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_message_signals_user_sender ON message_signals(user_id, sender_domain)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_message_signals_reference_ids ON message_signals USING GIN(reference_ids_json)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_summaries (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          gmail_thread_id TEXT NOT NULL,
          summary_level TEXT NOT NULL DEFAULT 'snippet',
          ai_title TEXT NOT NULL,
          summary TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'open',
          action_type TEXT NOT NULL DEFAULT 'none',
          confidence REAL NOT NULL DEFAULT 0,
          source_message_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          model TEXT,
          summary_version TEXT NOT NULL,
          generated_from_hash TEXT NOT NULL,
          generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, gmail_thread_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_thread_summaries_user_updated ON thread_summaries(user_id, updated_at DESC)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_objects (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          object_type TEXT NOT NULL,
          canonical_key TEXT NOT NULL,
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          lifecycle_state TEXT NOT NULL DEFAULT 'active',
          confidence REAL NOT NULL DEFAULT 0,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, canonical_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_mail_objects_user_type_updated ON mail_objects(user_id, object_type, updated_at DESC)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS object_members (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          object_id TEXT NOT NULL REFERENCES mail_objects(id) ON DELETE CASCADE,
          gmail_message_id TEXT NOT NULL,
          gmail_thread_id TEXT,
          link_type TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 0,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, object_id, gmail_message_id),
          FOREIGN KEY(user_id, gmail_message_id) REFERENCES gmail_messages(user_id, message_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_object_members_object ON object_members(user_id, object_id, created_at ASC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_object_members_thread ON object_members(user_id, gmail_thread_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS smart_inbox_rows (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          row_key TEXT NOT NULL,
          row_type TEXT NOT NULL,
          section TEXT NOT NULL DEFAULT 'inbox',
          status TEXT NOT NULL DEFAULT 'active',
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          primary_sender TEXT,
          latest_message_at TIMESTAMPTZ,
          latest_message_id TEXT,
          source_thread_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          source_message_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          confidence_tier TEXT NOT NULL DEFAULT 'unknown',
          confidence REAL NOT NULL DEFAULT 0,
          grouping_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          offline_status TEXT NOT NULL DEFAULT 'partial',
          readiness TEXT NOT NULL DEFAULT 'partial',
          action_type TEXT NOT NULL DEFAULT 'none',
          priority INTEGER NOT NULL DEFAULT 0,
          generated_from_hash TEXT NOT NULL,
          generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, row_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_smart_inbox_rows_user_latest ON smart_inbox_rows(user_id, status, latest_message_at DESC, id DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_smart_inbox_rows_type ON smart_inbox_rows(user_id, row_type, updated_at DESC)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS smart_work_items (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          smart_row_id TEXT REFERENCES smart_inbox_rows(id) ON DELETE SET NULL,
          kind TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          title TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          source_thread_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          source_message_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          due_at TIMESTAMPTZ,
          priority INTEGER NOT NULL DEFAULT 0,
          confidence REAL NOT NULL DEFAULT 0,
          reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_smart_work_items_user_kind ON smart_work_items(user_id, status, kind, priority DESC, updated_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_smart_work_items_row ON smart_work_items(user_id, smart_row_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS related_suggestions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          source_smart_row_id TEXT REFERENCES smart_inbox_rows(id) ON DELETE CASCADE,
          related_smart_row_id TEXT REFERENCES smart_inbox_rows(id) ON DELETE CASCADE,
          suggestion_key TEXT NOT NULL,
          title TEXT NOT NULL,
          reason TEXT NOT NULL,
          confidence REAL NOT NULL DEFAULT 0,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(user_id, suggestion_key)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_related_suggestions_source ON related_suggestions(user_id, source_smart_row_id, status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS grouping_audit (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          source_smart_row_id TEXT REFERENCES smart_inbox_rows(id) ON DELETE SET NULL,
          target_smart_row_id TEXT REFERENCES smart_inbox_rows(id) ON DELETE SET NULL,
          object_id TEXT REFERENCES mail_objects(id) ON DELETE SET NULL,
          projection_key TEXT,
          decision TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          actor TEXT NOT NULL DEFAULT 'system',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_grouping_audit_user_created ON grouping_audit(user_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_grouping_audit_decision ON grouping_audit(user_id, decision, created_at DESC)")

    op.execute("ALTER TABLE app_session_snapshots ADD COLUMN IF NOT EXISTS smart_inbox_json TEXT NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE app_session_snapshots ADD COLUMN IF NOT EXISTS smart_work_queue_json TEXT NOT NULL DEFAULT '{}'")
    op.execute("ALTER TABLE app_session_snapshots ADD COLUMN IF NOT EXISTS smart_readiness_json TEXT NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE app_session_snapshots DROP COLUMN IF EXISTS smart_readiness_json")
    op.execute("ALTER TABLE app_session_snapshots DROP COLUMN IF EXISTS smart_work_queue_json")
    op.execute("ALTER TABLE app_session_snapshots DROP COLUMN IF EXISTS smart_inbox_json")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS hot_window_completed_at")
    op.execute("ALTER TABLE gmail_import_state DROP COLUMN IF EXISTS hot_window_started_at")
    op.execute("DROP INDEX IF EXISTS idx_grouping_audit_decision")
    op.execute("DROP INDEX IF EXISTS idx_grouping_audit_user_created")
    op.execute("DROP TABLE IF EXISTS grouping_audit")
    op.execute("DROP INDEX IF EXISTS idx_related_suggestions_source")
    op.execute("DROP TABLE IF EXISTS related_suggestions")
    op.execute("DROP INDEX IF EXISTS idx_smart_work_items_row")
    op.execute("DROP INDEX IF EXISTS idx_smart_work_items_user_kind")
    op.execute("DROP TABLE IF EXISTS smart_work_items")
    op.execute("DROP INDEX IF EXISTS idx_smart_inbox_rows_type")
    op.execute("DROP INDEX IF EXISTS idx_smart_inbox_rows_user_latest")
    op.execute("DROP TABLE IF EXISTS smart_inbox_rows")
    op.execute("DROP INDEX IF EXISTS idx_object_members_thread")
    op.execute("DROP INDEX IF EXISTS idx_object_members_object")
    op.execute("DROP TABLE IF EXISTS object_members")
    op.execute("DROP INDEX IF EXISTS idx_mail_objects_user_type_updated")
    op.execute("DROP TABLE IF EXISTS mail_objects")
    op.execute("DROP INDEX IF EXISTS idx_thread_summaries_user_updated")
    op.execute("DROP TABLE IF EXISTS thread_summaries")
    op.execute("DROP INDEX IF EXISTS idx_message_signals_reference_ids")
    op.execute("DROP INDEX IF EXISTS idx_message_signals_user_sender")
    op.execute("DROP INDEX IF EXISTS idx_message_signals_user_thread")
    op.execute("DROP TABLE IF EXISTS message_signals")
