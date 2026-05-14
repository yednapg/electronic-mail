"""first run import jobs

Revision ID: 20260514_0002
Revises: 20260514_0001
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op

revision = "20260514_0002"
down_revision = "20260514_0001"
branch_labels = None
depends_on = None


FIRST_RUN_SQL = """
CREATE TABLE IF NOT EXISTS first_run_import_jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'queued',
  fetched_count INTEGER NOT NULL DEFAULT 0,
  total_count INTEGER,
  thread_count INTEGER NOT NULL DEFAULT 0,
  dashboard_item_count INTEGER NOT NULL DEFAULT 0,
  inbox_ready_at TEXT,
  dashboard_ready_at TEXT,
  full_import_started_at TEXT,
  full_import_completed_at TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_first_run_import_jobs_latest
  ON first_run_import_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_first_run_import_jobs_active
  ON first_run_import_jobs(user_id, status, updated_at DESC);
"""


def upgrade() -> None:
    op.execute(FIRST_RUN_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS first_run_import_jobs")
