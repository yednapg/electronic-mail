"""Shared fixed-window counters for abuse-sensitive API routes."""

from alembic import op

revision = "20261013_0045"
down_revision = "20261013_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_buckets (
            rule TEXT NOT NULL,
            identity_hash TEXT NOT NULL,
            window_start TIMESTAMPTZ NOT NULL,
            request_count INTEGER NOT NULL,
            PRIMARY KEY (rule, identity_hash, window_start)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rate_limit_buckets")
