"""Prevent pre-deletion OAuth callbacks from recreating deleted accounts.

Revision ID: 20260721_0023
Revises: 20260721_0022
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op


revision = "20260721_0023"
down_revision = "20260721_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OAuth starts and account deletions share one monotonic database epoch.
    # That makes ordering exact even if application clocks differ or move.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SEQUENCE IF NOT EXISTS google_identity_event_epoch_seq AS BIGINT")
    op.execute(
        """
        ALTER TABLE oauth_login_sessions
        ADD COLUMN IF NOT EXISTS started_epoch BIGINT NOT NULL
          DEFAULT nextval('google_identity_event_epoch_seq')
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS google_subject_deletion_tombstones (
          subject_hash TEXT PRIMARY KEY,
          deleted_epoch BIGINT NOT NULL DEFAULT nextval('google_identity_event_epoch_seq'),
          deleted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_google_subject_key(subject TEXT)
        RETURNS TEXT
        LANGUAGE SQL
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$
          SELECT 'sha256:v1:' || encode(
            digest(
              convert_to('electronic-mail:google-subject:v1:' || subject, 'UTF8'),
              'sha256'
            ),
            'hex'
          )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_tombstone_deleted_user()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
          INSERT INTO google_subject_deletion_tombstones (
            subject_hash, deleted_epoch, deleted_at
          )
          VALUES (
            electronic_mail_google_subject_key(OLD.google_sub),
            nextval('google_identity_event_epoch_seq'),
            clock_timestamp()
          )
          ON CONFLICT(subject_hash) DO UPDATE SET
            deleted_epoch = excluded.deleted_epoch,
            deleted_at = excluded.deleted_at;
          RETURN OLD;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_require_fresh_oauth_user()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
          revocation_epoch BIGINT;
          oauth_epoch TEXT;
        BEGIN
          SELECT deleted_epoch
          INTO revocation_epoch
          FROM google_subject_deletion_tombstones
          WHERE subject_hash = electronic_mail_google_subject_key(NEW.google_sub);

          IF revocation_epoch IS NULL THEN
            RETURN NEW;
          END IF;

          oauth_epoch := current_setting('electronic_mail.oauth_started_epoch', TRUE);
          IF oauth_epoch IS NULL
             OR oauth_epoch !~ '^[0-9]+$'
             OR oauth_epoch::NUMERIC <= revocation_epoch THEN
            RAISE EXCEPTION 'OAuth session is not newer than the Google subject revocation'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_tombstone_google_guard_transition()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF (
               OLD.google_disconnected_at IS NULL
               AND NEW.google_disconnected_at IS NOT NULL
             )
             OR (
               OLD.google_data_delete_requested_at IS NULL
               AND NEW.google_data_delete_requested_at IS NOT NULL
             ) THEN
            INSERT INTO google_subject_deletion_tombstones (
              subject_hash, deleted_epoch, deleted_at
            )
            VALUES (
              electronic_mail_google_subject_key(NEW.google_sub),
              nextval('google_identity_event_epoch_seq'),
              clock_timestamp()
            )
            ON CONFLICT(subject_hash) DO UPDATE SET
              deleted_epoch = excluded.deleted_epoch,
              deleted_at = excluded.deleted_at;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_require_guarded_google_token_write()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
          subject TEXT;
          enabled BOOLEAN;
          disconnected_at TIMESTAMPTZ;
          delete_requested_at TIMESTAMPTZ;
          deleted_at TIMESTAMPTZ;
          revocation_epoch BIGINT;
          oauth_epoch TEXT;
          guarded_user_id TEXT;
        BEGIN
          SELECT
            google_sub,
            access_enabled,
            google_disconnected_at,
            google_data_delete_requested_at,
            google_data_deleted_at
          INTO
            subject,
            enabled,
            disconnected_at,
            delete_requested_at,
            deleted_at
          FROM users
          WHERE id = NEW.user_id;

          IF subject IS NULL THEN
            RETURN NEW;
          END IF;
          IF NOT enabled
             OR disconnected_at IS NOT NULL
             OR delete_requested_at IS NOT NULL
             OR deleted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Google credentials cannot be written for a guarded user'
              USING ERRCODE = '55000';
          END IF;

          SELECT tombstone.deleted_epoch
          INTO revocation_epoch
          FROM google_subject_deletion_tombstones AS tombstone
          WHERE tombstone.subject_hash = electronic_mail_google_subject_key(subject);

          IF revocation_epoch IS NULL THEN
            RETURN NEW;
          END IF;

          oauth_epoch := current_setting('electronic_mail.oauth_started_epoch', TRUE);
          guarded_user_id := current_setting(
            'electronic_mail.user_mail_write_user_id',
            TRUE
          );
          IF (
               oauth_epoch IS NOT NULL
               AND oauth_epoch ~ '^[0-9]+$'
               AND oauth_epoch::NUMERIC > revocation_epoch
             )
             OR guarded_user_id = NEW.user_id THEN
            RETURN NEW;
          END IF;

          RAISE EXCEPTION 'Google credential write is not guarded by current OAuth state'
            USING ERRCODE = '55000';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_require_fresh_oauth_guard_clear()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
          revocation_epoch BIGINT;
          oauth_epoch TEXT;
        BEGIN
          SELECT deleted_epoch
          INTO revocation_epoch
          FROM google_subject_deletion_tombstones
          WHERE subject_hash = electronic_mail_google_subject_key(NEW.google_sub);

          IF revocation_epoch IS NULL THEN
            RETURN NEW;
          END IF;

          oauth_epoch := current_setting('electronic_mail.oauth_started_epoch', TRUE);
          IF oauth_epoch IS NULL
             OR oauth_epoch !~ '^[0-9]+$'
             OR oauth_epoch::NUMERIC <= revocation_epoch THEN
            RAISE EXCEPTION 'OAuth session cannot clear a newer Google subject guard'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_users_tombstone_google_subject ON users;
        CREATE TRIGGER trg_users_tombstone_google_subject
        BEFORE DELETE ON users
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_tombstone_deleted_user()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_users_require_fresh_oauth ON users;
        CREATE TRIGGER trg_users_require_fresh_oauth
        BEFORE INSERT ON users
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_require_fresh_oauth_user()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_users_tombstone_google_guard ON users;
        CREATE TRIGGER trg_users_tombstone_google_guard
        BEFORE UPDATE OF google_disconnected_at, google_data_delete_requested_at ON users
        FOR EACH ROW
        WHEN (
          (OLD.google_disconnected_at IS NULL AND NEW.google_disconnected_at IS NOT NULL)
          OR (
            OLD.google_data_delete_requested_at IS NULL
            AND NEW.google_data_delete_requested_at IS NOT NULL
          )
        )
        EXECUTE FUNCTION electronic_mail_tombstone_google_guard_transition()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_google_tokens_require_guard ON google_oauth_tokens;
        CREATE TRIGGER trg_google_tokens_require_guard
        BEFORE INSERT OR UPDATE ON google_oauth_tokens
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_require_guarded_google_token_write()
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_users_require_fresh_guard_clear ON users;
        CREATE TRIGGER trg_users_require_fresh_guard_clear
        BEFORE UPDATE OF
          google_disconnected_at,
          google_data_delete_requested_at,
          google_data_deleted_at
        ON users
        FOR EACH ROW
        WHEN (
          (OLD.google_disconnected_at IS NOT NULL AND NEW.google_disconnected_at IS NULL)
          OR (
            OLD.google_data_delete_requested_at IS NOT NULL
            AND NEW.google_data_delete_requested_at IS NULL
          )
          OR (
            OLD.google_data_deleted_at IS NOT NULL
            AND NEW.google_data_deleted_at IS NULL
          )
        )
        EXECUTE FUNCTION electronic_mail_require_fresh_oauth_guard_clear()
        """
    )
    # Preserve disconnect/data-deletion ordering that predates this migration.
    op.execute(
        """
        INSERT INTO google_subject_deletion_tombstones (
          subject_hash, deleted_epoch, deleted_at
        )
        SELECT
          electronic_mail_google_subject_key(users.google_sub),
          nextval('google_identity_event_epoch_seq'),
          clock_timestamp()
        FROM users
        WHERE users.google_disconnected_at IS NOT NULL
           OR users.google_data_delete_requested_at IS NOT NULL
           OR users.google_data_deleted_at IS NOT NULL
        ON CONFLICT(subject_hash) DO UPDATE SET
          deleted_epoch = excluded.deleted_epoch,
          deleted_at = excluded.deleted_at
        """
    )
    # A callback initiated before this revision cannot be compared with a
    # deletion performed by the old code, because that code retained no
    # subject tombstone. Expire the small set of 15-minute PKCE sessions once
    # at migration so every accepted callback has a trustworthy start epoch.
    op.execute("DELETE FROM oauth_login_sessions")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_users_require_fresh_guard_clear ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_google_tokens_require_guard ON google_oauth_tokens")
    op.execute("DROP TRIGGER IF EXISTS trg_users_tombstone_google_guard ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_users_require_fresh_oauth ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_users_tombstone_google_subject ON users")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_require_guarded_google_token_write()")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_require_fresh_oauth_guard_clear()")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_tombstone_google_guard_transition()")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_require_fresh_oauth_user()")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_tombstone_deleted_user()")
    op.execute("DROP FUNCTION IF EXISTS electronic_mail_google_subject_key(TEXT)")
    op.execute("DROP TABLE IF EXISTS google_subject_deletion_tombstones")
    op.execute("ALTER TABLE oauth_login_sessions DROP COLUMN IF EXISTS started_epoch")
    op.execute("DROP SEQUENCE IF EXISTS google_identity_event_epoch_seq")
