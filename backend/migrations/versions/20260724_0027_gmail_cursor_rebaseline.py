"""Require durable provenance for every authoritative Gmail cursor.

Revision ID: 20260724_0027
Revises: 20260724_0026
Create Date: 2026-07-24
"""

from __future__ import annotations

from alembic import op


revision = "20260724_0027"
down_revision = "20260724_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing values came from releases that mixed listing hydration IDs with
    # fully consumed history cursors, so every row starts untrusted. The false
    # default also makes inserts from an older rolling-deployment worker safe.
    op.execute(
        "ALTER TABLE gmail_import_state "
        "ADD COLUMN IF NOT EXISTS history_cursor_authoritative "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # If an old worker changes last_history_id after a new worker has published
    # a trusted cursor, it cannot also advance last_delta_sync_at. This trigger
    # revokes provenance for that mixed-version write. New delta/reconciliation
    # writes advance both fields atomically and remain trusted.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_gmail_history_cursor_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.last_history_id IS DISTINCT FROM OLD.last_history_id
             AND NEW.last_delta_sync_at IS NOT DISTINCT FROM OLD.last_delta_sync_at
          THEN
            NEW.history_cursor_authoritative := FALSE;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_gmail_history_cursor_provenance
        ON gmail_import_state
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_gmail_history_cursor_provenance
        BEFORE UPDATE OF last_history_id, last_delta_sync_at,
                         history_cursor_authoritative
        ON gmail_import_state
        FOR EACH ROW
        EXECUTE FUNCTION enforce_gmail_history_cursor_provenance()
        """
    )


def downgrade() -> None:
    # Once the provenance column is gone, older code would otherwise treat an
    # unsafe legacy value as a valid delta cursor again. Remove both pieces of
    # cursor state while the trust decision is still available.
    op.execute(
        """
        UPDATE gmail_import_state
        SET last_history_id = NULL,
            last_delta_sync_at = NULL
        WHERE history_cursor_authoritative IS NOT TRUE
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_gmail_history_cursor_provenance "
        "ON gmail_import_state"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_gmail_history_cursor_provenance()")
    op.execute(
        "ALTER TABLE gmail_import_state "
        "DROP COLUMN IF EXISTS history_cursor_authoritative"
    )
