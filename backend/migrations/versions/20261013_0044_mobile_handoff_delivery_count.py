"""Count code-bearing native handoff polls without making them single-use."""

from alembic import op

revision = "20261013_0044"
down_revision = "20260908_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mobile_oauth_handoffs
        ADD COLUMN IF NOT EXISTS delivered_count INTEGER NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mobile_oauth_handoffs DROP COLUMN IF EXISTS delivered_count")
