"""Persist Gmail's authoritative per-label thread ordering.

Revision ID: 20260714_0021
Revises: 20260713_0020
Create Date: 2026-07-14
"""

from __future__ import annotations

from alembic import op


revision = "20260714_0021"
down_revision = "20260713_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_thread_order_state (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          label TEXT NOT NULL,
          active_generation_id TEXT NOT NULL,
          previous_generation_id TEXT,
          refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, label)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS gmail_thread_order_entries (
          user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          label TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          gmail_thread_id TEXT NOT NULL,
          position INTEGER NOT NULL CHECK (position >= 0),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, label, generation_id, gmail_thread_id),
          UNIQUE (user_id, label, generation_id, position)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gmail_thread_order_entries")
    op.execute("DROP TABLE IF EXISTS gmail_thread_order_state")
