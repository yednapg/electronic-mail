"""Require AI-ready mail groups for visible product output."""

from alembic import op


revision = "20260515_0004"
down_revision = "20260514_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mail_groups
        ADD COLUMN IF NOT EXISTS enrichment_status TEXT NOT NULL DEFAULT 'pending'
        """
    )
    op.execute(
        """
        ALTER TABLE mail_groups
        ADD COLUMN IF NOT EXISTS membership_source TEXT NOT NULL DEFAULT 'deterministic_candidate'
        """
    )
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS ai_model TEXT")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS ai_error TEXT")
    op.execute("ALTER TABLE mail_groups ADD COLUMN IF NOT EXISTS ai_generated_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mail_groups_user_enrichment_latest
        ON mail_groups(user_id, enrichment_status, latest_message_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mail_groups_user_dashboard_ready
        ON mail_groups(user_id, dashboard_visible, enrichment_status, priority DESC, latest_message_at DESC)
        """
    )

    # Existing deterministic/fallback groups were allowed into the product too early.
    # Force a fresh AI-ready first-run pass after this migration instead of preserving
    # low-quality visible state.
    op.execute(
        """
        UPDATE mail_groups
        SET enrichment_status = 'pending',
            membership_source = 'deterministic_candidate',
            dashboard_visible = false,
            ai_error = NULL,
            ai_generated_at = NULL,
            generated_at = NULL
        """
    )
    op.execute(
        """
        UPDATE gmail_import_state
        SET first_groups_ready_at = NULL,
            first_dashboard_ready_at = NULL,
            last_sync_error = NULL,
            updated_at = NOW()
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mail_groups_user_dashboard_ready")
    op.execute("DROP INDEX IF EXISTS idx_mail_groups_user_enrichment_latest")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS ai_generated_at")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS ai_error")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS ai_model")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS membership_source")
    op.execute("ALTER TABLE mail_groups DROP COLUMN IF EXISTS enrichment_status")
