"""Add app-facing session snapshots for fast reads."""

from alembic import op


revision = "20260515_0005"
down_revision = "20260515_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_session_snapshots (
          user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          dashboard_json TEXT NOT NULL,
          mailbox_json TEXT NOT NULL,
          sync_json TEXT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_session_snapshots")
