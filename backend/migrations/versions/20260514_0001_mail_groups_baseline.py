"""clean mail groups production baseline

Revision ID: 20260514_0001
Revises:
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op

revision = "20260514_0001"
down_revision = None
branch_labels = None
depends_on = None


BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT,
  google_sub TEXT NOT NULL UNIQUE,
  access_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  google_disconnected_at TIMESTAMPTZ,
  google_data_delete_requested_at TIMESTAMPTZ,
  google_data_deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS app_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_sessions_user_expires ON app_sessions(user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS oauth_login_sessions (
  state TEXT PRIMARY KEY,
  code_verifier TEXT NOT NULL,
  redirect_to TEXT,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS mobile_login_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS google_oauth_tokens (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  token_json_encrypted TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS allowed_emails (
  email TEXT PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  invited_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS gmail_messages (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  message_id TEXT NOT NULL,
  gmail_thread_id TEXT,
  history_id TEXT,
  label_ids_json TEXT NOT NULL DEFAULT '[]',
  internal_date TIMESTAMPTZ,
  subject TEXT,
  sender TEXT,
  recipients_json TEXT NOT NULL DEFAULT '{}',
  headers_json TEXT NOT NULL DEFAULT '{}',
  snippet TEXT,
  raw_payload_json TEXT NOT NULL,
  html_body_sanitized TEXT,
  text_body TEXT,
  extracted_signals_json TEXT NOT NULL DEFAULT '{}',
  body_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(user_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_gmail_messages_user_date ON gmail_messages(user_id, internal_date DESC, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_gmail_messages_user_thread ON gmail_messages(user_id, gmail_thread_id);
CREATE INDEX IF NOT EXISTS idx_gmail_messages_user_history ON gmail_messages(user_id, history_id);

CREATE TABLE IF NOT EXISTS mail_groups (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_key TEXT NOT NULL,
  group_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  enrichment_status TEXT NOT NULL DEFAULT 'pending',
  membership_source TEXT NOT NULL DEFAULT 'deterministic_candidate',
  ai_model TEXT,
  ai_error TEXT,
  ai_generated_at TIMESTAMPTZ,
  ai_title TEXT NOT NULL,
  ai_summary TEXT NOT NULL,
  labels_json TEXT NOT NULL DEFAULT '[]',
  action_needed BOOLEAN NOT NULL DEFAULT FALSE,
  action_type TEXT NOT NULL DEFAULT 'none',
  priority INTEGER NOT NULL DEFAULT 0,
  timing_band TEXT NOT NULL DEFAULT 'later',
  dashboard_visible BOOLEAN NOT NULL DEFAULT FALSE,
  latest_message_at TIMESTAMPTZ,
  latest_message_id TEXT,
  generated_from_hash TEXT NOT NULL,
  generated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE(user_id, group_key)
);
CREATE INDEX IF NOT EXISTS idx_mail_groups_user_latest ON mail_groups(user_id, latest_message_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_mail_groups_dashboard ON mail_groups(user_id, dashboard_visible, latest_message_at DESC, priority DESC);
CREATE INDEX IF NOT EXISTS idx_mail_groups_user_enrichment_latest ON mail_groups(user_id, enrichment_status, latest_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_groups_user_dashboard_ready ON mail_groups(user_id, dashboard_visible, enrichment_status, priority DESC, latest_message_at DESC);

CREATE TABLE IF NOT EXISTS mail_group_members (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_id TEXT NOT NULL REFERENCES mail_groups(id) ON DELETE CASCADE,
  gmail_message_id TEXT NOT NULL,
  gmail_thread_id TEXT,
  reason TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(user_id, group_id, gmail_message_id)
);
CREATE INDEX IF NOT EXISTS idx_mail_group_members_group ON mail_group_members(user_id, group_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_mail_group_members_message ON mail_group_members(user_id, gmail_message_id);

CREATE TABLE IF NOT EXISTS gmail_import_state (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  last_history_id TEXT,
  full_backfill_cursor TEXT,
  first_batch_imported_at TIMESTAMPTZ,
  first_groups_ready_at TIMESTAMPTZ,
  first_dashboard_ready_at TIMESTAMPTZ,
  last_import_started_at TIMESTAMPTZ,
  last_import_completed_at TIMESTAMPTZ,
  last_sync_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS background_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  queue TEXT NOT NULL,
  status TEXT NOT NULL,
  user_id TEXT,
  dedupe_key TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  payload_version INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL DEFAULT '{}',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  last_error TEXT,
  trace_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_background_jobs_claim ON background_jobs(status, queue, run_after, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_background_jobs_user_status_kind ON background_jobs(user_id, status, kind);
CREATE INDEX IF NOT EXISTS idx_background_jobs_lease ON background_jobs(lease_expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_active_dedupe ON background_jobs(kind, dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS background_job_events (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES background_jobs(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  message TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_background_job_events_job_created ON background_job_events(job_id, created_at ASC);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
  worker_id TEXT PRIMARY KEY,
  queues_json TEXT NOT NULL,
  current_job_id TEXT,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

TABLES = [
    "worker_heartbeats",
    "background_job_events",
    "background_jobs",
    "gmail_import_state",
    "mail_group_members",
    "mail_groups",
    "gmail_messages",
    "allowed_emails",
    "google_oauth_tokens",
    "mobile_login_codes",
    "oauth_login_sessions",
    "app_sessions",
    "users",
]


def upgrade() -> None:
    op.execute(BASELINE_SQL)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
