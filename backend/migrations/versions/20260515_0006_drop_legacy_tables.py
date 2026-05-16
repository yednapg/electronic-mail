"""Drop legacy dashboard/entity/feed tables.

Revision ID: 20260515_0006
Revises: 20260515_0005
Create Date: 2026-05-15
"""
from __future__ import annotations

from alembic import op


revision = "20260515_0006"
down_revision = "20260515_0005"
branch_labels = None
depends_on = None


LEGACY_TABLES = [
    "dashboard_briefings",
    "dashboard_import_jobs",
    "entities",
    "entity_ai_suggestions",
    "entity_members",
    "entity_outcomes",
    "entity_states",
    "entity_thread_memberships",
    "feed_projections",
    "first_run_import_jobs",
    "gmail_drafts",
    "gmail_history_events",
    "gmail_message_snapshots",
    "gmail_sync_state",
    "gmail_thread_projections",
    "manual_tasks",
    "source_record_summaries",
    "source_records",
    "trace_records",
    "beta_allowed_emails",
]


def upgrade() -> None:
    for table in LEGACY_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')


def downgrade() -> None:
    raise RuntimeError("Legacy table cleanup is irreversible. Restore from backup if these tables are needed.")
