"""beta baseline schema

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
  beta_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  last_seen_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_sessions_user_expires ON app_sessions(user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS oauth_login_sessions (
  state TEXT PRIMARY KEY,
  code_verifier TEXT NOT NULL,
  redirect_to TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mobile_login_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS google_oauth_tokens (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  token_json_encrypted TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beta_allowed_emails (
  email TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  invited_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_records (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,
  thread_id TEXT,
  subject TEXT,
  sender TEXT,
  timestamp TEXT NOT NULL,
  raw_payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_records_timestamp ON source_records(timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_source_records_user_timestamp ON source_records(user_id, timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_source_records_user_active_timestamp ON source_records(user_id, deleted_at, timestamp DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_source_records_thread ON source_records(source, thread_id);

CREATE TABLE IF NOT EXISTS source_record_summaries (
  source_record_id TEXT PRIMARY KEY REFERENCES source_records(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  model TEXT NOT NULL,
  generated_from_hash TEXT NOT NULL,
  generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_record_summaries_user_generated ON source_record_summaries(user_id, generated_at DESC, source_record_id);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_entities_created ON entities(created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_entities_user_created ON entities(user_id, created_at ASC, id ASC);

CREATE TABLE IF NOT EXISTS entity_members (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  source_record_id TEXT NOT NULL UNIQUE REFERENCES source_records(id) ON DELETE CASCADE,
  UNIQUE(entity_id, source_record_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_members_entity ON entity_members(entity_id);

CREATE TABLE IF NOT EXISTS entity_thread_memberships (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, source, thread_id),
  UNIQUE(entity_id, source, thread_id)
);

CREATE TABLE IF NOT EXISTS entity_states (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
  current_state TEXT NOT NULL,
  due_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_ai_suggestions (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  action TEXT NOT NULL,
  suggested_timing TEXT NOT NULL,
  suggested_priority INTEGER NOT NULL,
  suggested_visibility INTEGER NOT NULL,
  model TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  generated_from_updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_records (
  id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  source_record_id TEXT REFERENCES source_records(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  input TEXT NOT NULL,
  output TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_records_entity_created ON trace_records(entity_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_trace_records_source_record ON trace_records(source_record_id);

CREATE TABLE IF NOT EXISTS feed_projections (
  entity_id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  pipeline_output TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feed_projections_user_updated ON feed_projections(user_id, updated_at DESC, entity_id);

CREATE TABLE IF NOT EXISTS gmail_sync_state (
  user_id TEXT PRIMARY KEY,
  last_history_id TEXT,
  last_full_sync_at TEXT,
  watch_expiration_at TEXT,
  last_sync_started_at TEXT,
  last_sync_completed_at TEXT,
  last_sync_error TEXT
);

CREATE TABLE IF NOT EXISTS gmail_message_snapshots (
  user_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  thread_id TEXT,
  history_id TEXT,
  internal_date TEXT,
  label_ids TEXT NOT NULL,
  raw_payload TEXT NOT NULL,
  fetch_status TEXT NOT NULL,
  tombstoned INTEGER NOT NULL DEFAULT 0,
  tombstoned_at TEXT,
  last_fetched_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_thread ON gmail_message_snapshots(user_id, thread_id);
CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_history ON gmail_message_snapshots(user_id, history_id);
CREATE INDEX IF NOT EXISTS idx_gmail_message_snapshots_internal_date ON gmail_message_snapshots(user_id, internal_date);

CREATE TABLE IF NOT EXISTS gmail_history_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  history_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message_id TEXT NOT NULL,
  thread_id TEXT,
  label_ids TEXT NOT NULL,
  message_payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gmail_history_events_message ON gmail_history_events(user_id, message_id, history_id);

CREATE TABLE IF NOT EXISTS gmail_thread_projections (
  user_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  latest_message_id TEXT NOT NULL,
  latest_received_at TEXT NOT NULL,
  latest_subject TEXT,
  latest_sender TEXT,
  snippet TEXT,
  participants TEXT NOT NULL,
  label_ids TEXT NOT NULL,
  message_count INTEGER NOT NULL,
  unread INTEGER NOT NULL DEFAULT 0,
  tombstoned INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_gmail_thread_projections_latest ON gmail_thread_projections(user_id, latest_received_at DESC, thread_id);

CREATE TABLE IF NOT EXISTS dashboard_import_jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'queued',
  imported_count INTEGER NOT NULL DEFAULT 0,
  total_count INTEGER,
  source_records INTEGER NOT NULL DEFAULT 0,
  changed_entities INTEGER NOT NULL DEFAULT 0,
  refreshed_entities INTEGER NOT NULL DEFAULT 0,
  result_status TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  stage_started_at TEXT,
  stage_durations TEXT NOT NULL DEFAULT '{}',
  completed_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dashboard_import_jobs_latest ON dashboard_import_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dashboard_import_jobs_active ON dashboard_import_jobs(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_briefings (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  entity_id TEXT NOT NULL UNIQUE REFERENCES entities(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  notes TEXT,
  section TEXT NOT NULL,
  due_at TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_outcomes (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  outcome_type TEXT NOT NULL,
  snooze_until TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_outcomes_latest ON entity_outcomes(user_id, entity_id, created_at);

CREATE TABLE IF NOT EXISTS gmail_drafts (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  entity_id TEXT REFERENCES entities(id) ON DELETE SET NULL,
  gmail_draft_id TEXT NOT NULL,
  gmail_message_id TEXT,
  thread_id TEXT,
  to_recipients TEXT NOT NULL,
  cc_recipients TEXT,
  bcc_recipients TEXT,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(user_id, gmail_draft_id)
);
"""

TABLES = [
    "gmail_drafts",
    "entity_outcomes",
    "manual_tasks",
    "dashboard_briefings",
    "dashboard_import_jobs",
    "gmail_thread_projections",
    "gmail_history_events",
    "gmail_message_snapshots",
    "gmail_sync_state",
    "feed_projections",
    "trace_records",
    "entity_ai_suggestions",
    "entity_states",
    "entity_thread_memberships",
    "entity_members",
    "entities",
    "source_record_summaries",
    "source_records",
    "beta_allowed_emails",
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
