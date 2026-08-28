"""Add the first production AI Inbox matter projection.

Revision ID: 20260827_0033
Revises: 20260827_0032
Create Date: 2026-08-27
"""

from __future__ import annotations

from alembic import op


revision = "20260827_0033"
down_revision = "20260827_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Production is intentionally pinned to a Postgres service that bundles
    # pgvector. Failing here is safer than silently deploying a low-recall
    # fallback representation for semantic retrieval.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE matter_generations (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          status TEXT NOT NULL CHECK (status IN ('shadow', 'building', 'active', 'superseded', 'failed')),
          grouping_style TEXT NOT NULL CHECK (grouping_style IN ('focused', 'broader')),
          source_generation_id TEXT REFERENCES matter_generations(id) ON DELETE SET NULL,
          prompt_version TEXT NOT NULL,
          embedding_model TEXT NOT NULL,
          classifier_model TEXT NOT NULL,
          review_model TEXT NOT NULL,
          stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          last_error_code TEXT,
          started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_matter_generations_active_user
        ON matter_generations (user_id)
        WHERE status = 'active'
        """
    )
    op.execute(
        """
        CREATE TABLE matter_profiles (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          consented_at TIMESTAMPTZ,
          enabled BOOLEAN NOT NULL DEFAULT FALSE,
          grouping_style TEXT NOT NULL DEFAULT 'focused'
            CHECK (grouping_style IN ('focused', 'broader')),
          rollout_mode TEXT NOT NULL DEFAULT 'disabled'
            CHECK (rollout_mode IN ('disabled', 'shadow', 'preview', 'live')),
          active_generation_id TEXT,
          revision BIGINT NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (active_generation_id, user_id)
            REFERENCES matter_generations(id, user_id) ON DELETE SET NULL (active_generation_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE message_semantics (
          user_id TEXT NOT NULL,
          message_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          content_revision INTEGER NOT NULL,
          normalized_sha256 TEXT NOT NULL,
          reference_tokens JSONB NOT NULL DEFAULT '[]'::jsonb,
          extracted_facts JSONB NOT NULL DEFAULT '{}'::jsonb,
          embedding vector(1024),
          processing_state TEXT NOT NULL
            CHECK (processing_state IN ('queued', 'processing', 'ready', 'failed', 'skipped')),
          error_code TEXT,
          embedding_model TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          processed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, message_id, generation_id),
          FOREIGN KEY (user_id, message_id)
            REFERENCES gmail_messages(user_id, message_id) ON DELETE CASCADE,
          FOREIGN KEY (generation_id, user_id)
            REFERENCES matter_generations(id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_message_semantics_ready
        ON message_semantics (user_id, generation_id, processing_state, updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE attachment_text_extractions (
          user_id TEXT NOT NULL,
          message_id TEXT NOT NULL,
          attachment_id TEXT NOT NULL,
          content_revision INTEGER NOT NULL,
          filename TEXT,
          mime_type TEXT,
          source_bytes INTEGER NOT NULL DEFAULT 0,
          extracted_text TEXT,
          extracted_sha256 TEXT,
          status TEXT NOT NULL CHECK (status IN ('ready', 'ignored', 'failed')),
          truncated BOOLEAN NOT NULL DEFAULT FALSE,
          error_code TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, message_id, attachment_id),
          FOREIGN KEY (user_id, message_id)
            REFERENCES gmail_messages(user_id, message_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE matters (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          stable_goal TEXT NOT NULL,
          dynamic_title TEXT NOT NULL,
          summary TEXT NOT NULL,
          status TEXT NOT NULL CHECK (
            status IN ('needs_you', 'waiting_on_others', 'in_progress', 'completed', 'failed', 'cancelled', 'informational')
          ),
          evidence_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
          confidence_state TEXT NOT NULL
            CHECK (confidence_state IN ('automatic', 'provisional', 'confirmed')),
          embedding vector(1024),
          classifier_model TEXT NOT NULL,
          review_model TEXT,
          prompt_version TEXT NOT NULL,
          revision BIGINT NOT NULL DEFAULT 1,
          latest_message_at TIMESTAMPTZ,
          visible BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (id, user_id, generation_id),
          FOREIGN KEY (generation_id, user_id)
            REFERENCES matter_generations(id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_matters_projection
        ON matters (user_id, generation_id, visible, latest_message_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE matter_members (
          user_id TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          matter_id TEXT NOT NULL,
          message_id TEXT NOT NULL,
          membership_source TEXT NOT NULL
            CHECK (membership_source IN ('model', 'reference', 'gmail_continuity', 'user')),
          confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
          provisional BOOLEAN NOT NULL DEFAULT FALSE,
          user_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
          membership_locked BOOLEAN NOT NULL DEFAULT FALSE,
          added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, generation_id, message_id),
          FOREIGN KEY (matter_id, user_id, generation_id)
            REFERENCES matters(id, user_id, generation_id) ON DELETE CASCADE,
          FOREIGN KEY (user_id, message_id)
            REFERENCES gmail_messages(user_id, message_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_matter_members_matter ON matter_members (user_id, generation_id, matter_id)")
    op.execute(
        """
        CREATE TABLE matter_decisions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          generation_id TEXT NOT NULL,
          decision_type TEXT NOT NULL
            CHECK (decision_type IN ('model_assign', 'model_review', 'confirm', 'separate', 'merge', 'move')),
          matter_id TEXT,
          target_matter_id TEXT,
          message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          constraints_json JSONB NOT NULL DEFAULT '{}'::jsonb,
          outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'provisional', 'rejected', 'conflict')),
          expected_revision BIGINT,
          idempotency_key TEXT,
          model TEXT,
          confidence DOUBLE PRECISION,
          evidence_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (user_id, idempotency_key),
          FOREIGN KEY (generation_id, user_id)
            REFERENCES matter_generations(id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_matter_decisions_constraints ON matter_decisions (user_id, generation_id, decision_type, created_at DESC)")
    op.execute(
        """
        CREATE TABLE ai_usage_events (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          generation_id TEXT,
          message_id TEXT,
          operation TEXT NOT NULL,
          model TEXT NOT NULL,
          input_tokens INTEGER NOT NULL DEFAULT 0,
          cached_input_tokens INTEGER NOT NULL DEFAULT 0,
          output_tokens INTEGER NOT NULL DEFAULT 0,
          latency_ms INTEGER NOT NULL DEFAULT 0,
          estimated_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
          success BOOLEAN NOT NULL,
          error_code TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (generation_id, user_id)
            REFERENCES matter_generations(id, user_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_usage_events_budget ON ai_usage_events (user_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_usage_events")
    op.execute("DROP TABLE IF EXISTS matter_decisions")
    op.execute("DROP TABLE IF EXISTS matter_members")
    op.execute("DROP TABLE IF EXISTS matters")
    op.execute("DROP TABLE IF EXISTS attachment_text_extractions")
    op.execute("DROP TABLE IF EXISTS message_semantics")
    op.execute("DROP TABLE IF EXISTS matter_profiles")
    op.execute("DROP TABLE IF EXISTS matter_generations")
    # The extension may be shared by other products in the same database.
    # Deliberately leave it installed on downgrade.
