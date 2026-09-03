"""Keep AI budget enforcement aggregated at the app-user level.

Revision ID: 20260830_0041
Revises: 20260830_0040
"""

from alembic import op


revision = "20260830_0041"
down_revision = "20260830_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AI content stays behind account RLS. This narrowly scoped aggregate
    # returns only a cost total so app-user and project budgets still count all
    # independently processed Gmail accounts.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_monthly_ai_usage(target_user_id TEXT)
        RETURNS DOUBLE PRECISION
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT COALESCE(SUM(estimated_cost_usd), 0)::DOUBLE PRECISION
          FROM public.ai_usage_events
          WHERE created_at >= date_trunc('month', now())
            AND (target_user_id IS NULL OR user_id = target_user_id)
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION electronic_mail_monthly_ai_usage(TEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION electronic_mail_monthly_ai_usage(TEXT) TO PUBLIC")


def downgrade() -> None:
    raise RuntimeError("Forward fix or database restore is required after multi-account AI activation")
