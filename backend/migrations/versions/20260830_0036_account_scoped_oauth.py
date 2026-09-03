"""Scope Google OAuth credentials and OAuth intent to Gmail accounts.

Revision ID: 20260830_0036
Revises: 20260830_0035
Create Date: 2026-08-30

The primary credential row keeps the same user_id and gmail_account_id values
created by revision 0035. Only its key changes; the encrypted token payload is
not rewritten. Additional Gmail credentials can therefore be inserted without
replacing the primary credential.
"""

from __future__ import annotations

from alembic import op


revision = "20260830_0036"
down_revision = "20260830_0035"
branch_labels = None
depends_on = None


def _install_account_scoped_token_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION electronic_mail_require_guarded_google_token_write()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
          subject TEXT;
          enabled BOOLEAN;
          account_disconnected_at TIMESTAMPTZ;
          account_delete_requested_at TIMESTAMPTZ;
          account_deleted_at TIMESTAMPTZ;
          user_disconnected_at TIMESTAMPTZ;
          user_delete_requested_at TIMESTAMPTZ;
          user_deleted_at TIMESTAMPTZ;
          revocation_epoch BIGINT;
          oauth_epoch TEXT;
          guarded_user_id TEXT;
        BEGIN
          SELECT
            account.google_sub,
            owner.access_enabled,
            account.google_disconnected_at,
            account.google_data_delete_requested_at,
            account.google_data_deleted_at,
            owner.google_disconnected_at,
            owner.google_data_delete_requested_at,
            owner.google_data_deleted_at
          INTO
            subject,
            enabled,
            account_disconnected_at,
            account_delete_requested_at,
            account_deleted_at,
            user_disconnected_at,
            user_delete_requested_at,
            user_deleted_at
          FROM gmail_accounts account
          JOIN users owner ON owner.id = account.user_id
          WHERE account.id = NEW.gmail_account_id
            AND owner.id = NEW.user_id;

          IF subject IS NULL THEN
            RAISE EXCEPTION 'Google credentials require an owned Gmail account'
              USING ERRCODE = '23503';
          END IF;
          IF NOT enabled
             OR account_disconnected_at IS NOT NULL
             OR account_delete_requested_at IS NOT NULL
             OR account_deleted_at IS NOT NULL
             OR user_disconnected_at IS NOT NULL
             OR user_delete_requested_at IS NOT NULL
             OR user_deleted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Google credentials cannot be written for a guarded Gmail account'
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


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE oauth_login_sessions
        ADD COLUMN intent TEXT NOT NULL DEFAULT 'login'
          CHECK (intent IN ('login', 'link', 'reauthorize', 'merge_verify')),
        ADD COLUMN initiating_user_id TEXT REFERENCES users(id) ON DELETE CASCADE
        """
    )
    op.execute(
        """
        ALTER TABLE oauth_login_sessions
        ADD CONSTRAINT ck_oauth_login_sessions_intent_owner
        CHECK (
          (intent = 'login' AND initiating_user_id IS NULL)
          OR (intent <> 'login' AND initiating_user_id IS NOT NULL)
        ) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE oauth_login_sessions "
        "VALIDATE CONSTRAINT ck_oauth_login_sessions_intent_owner"
    )
    op.execute(
        "CREATE INDEX idx_oauth_login_sessions_initiating_user "
        "ON oauth_login_sessions (initiating_user_id, expires_at) "
        "WHERE initiating_user_id IS NOT NULL"
    )

    op.execute("DROP TRIGGER IF EXISTS trg_google_tokens_require_guard ON google_oauth_tokens")
    op.execute("ALTER TABLE google_oauth_tokens DROP CONSTRAINT google_oauth_tokens_pkey")
    op.execute(
        "ALTER TABLE google_oauth_tokens "
        "ADD CONSTRAINT google_oauth_tokens_pkey PRIMARY KEY (gmail_account_id)"
    )
    _install_account_scoped_token_guard()
    op.execute(
        """
        CREATE TRIGGER trg_google_tokens_require_guard
        BEFORE INSERT OR UPDATE ON google_oauth_tokens
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_require_guarded_google_token_write()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM gmail_accounts WHERE id <> user_id) THEN
            RAISE EXCEPTION
              'Cannot restore user-keyed OAuth tokens after an additional Gmail account was linked';
          END IF;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_google_tokens_require_guard ON google_oauth_tokens")
    op.execute("ALTER TABLE google_oauth_tokens DROP CONSTRAINT google_oauth_tokens_pkey")
    op.execute(
        "ALTER TABLE google_oauth_tokens "
        "ADD CONSTRAINT google_oauth_tokens_pkey PRIMARY KEY (user_id)"
    )
    # Revision 0023's function is restored for the one-account schema.
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
            'electronic_mail.user_mail_write_user_id', TRUE
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
        CREATE TRIGGER trg_google_tokens_require_guard
        BEFORE INSERT OR UPDATE ON google_oauth_tokens
        FOR EACH ROW
        EXECUTE FUNCTION electronic_mail_require_guarded_google_token_write()
        """
    )
    op.execute("DROP INDEX idx_oauth_login_sessions_initiating_user")
    op.execute(
        "ALTER TABLE oauth_login_sessions "
        "DROP CONSTRAINT ck_oauth_login_sessions_intent_owner"
    )
    op.execute(
        "ALTER TABLE oauth_login_sessions "
        "DROP COLUMN initiating_user_id, DROP COLUMN intent"
    )
