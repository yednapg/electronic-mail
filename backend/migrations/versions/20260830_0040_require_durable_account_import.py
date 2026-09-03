"""Require a durable account-scoped import before enabling writes or AI.

Revision ID: 20260830_0040
Revises: 20260830_0039
"""

from alembic import op


revision = "20260830_0040"
down_revision = "20260830_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OAuth verification makes a linked account readable through Gmail, but it
    # must not make writes/AI ready. Readiness requires the account-scoped
    # importer to durably store both its initial window and Gmail history cursor.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_gmail_account_initial_ready()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.first_batch_imported_at IS NOT NULL
             AND NEW.initial_window_complete
             AND NULLIF(NEW.last_history_id, '') IS NOT NULL THEN
            UPDATE gmail_accounts
            SET state = CASE
                  WHEN state IN ('connecting', 'importing') THEN 'ready'
                  ELSE state
                END,
                initial_ready_at = COALESCE(initial_ready_at, NEW.first_batch_imported_at),
                updated_at = now()
            WHERE id = NEW.gmail_account_id AND user_id = NEW.user_id;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_gmail_import_state_initial_ready ON gmail_import_state")
    op.execute(
        """
        CREATE TRIGGER trg_gmail_import_state_initial_ready
        AFTER INSERT OR UPDATE OF first_batch_imported_at, initial_window_complete, last_history_id
        ON gmail_import_state
        FOR EACH ROW
        EXECUTE FUNCTION mark_gmail_account_initial_ready()
        """
    )
    # Repair only rollout metadata for secondary accounts that were previously
    # marked ready after OAuth verification. No mail, token, draft, action, sync,
    # or AI row is modified.
    op.execute(
        """
        UPDATE gmail_accounts account
        SET state = 'importing', initial_ready_at = NULL, updated_at = now()
        FROM users owner
        WHERE account.user_id = owner.id
          AND account.id <> owner.primary_gmail_account_id
          AND account.state = 'ready'
          AND NOT EXISTS (
            SELECT 1
            FROM gmail_import_state state
            WHERE state.user_id = account.user_id
              AND state.gmail_account_id = account.id
              AND state.first_batch_imported_at IS NOT NULL
              AND state.initial_window_complete
              AND NULLIF(state.last_history_id, '') IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError("Forward fix or database restore is required after multi-account activation")
