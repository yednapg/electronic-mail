"""Session-bound Apple push registrations and durable new-mail deliveries."""

from alembic import op

revision = "20260908_0043"
down_revision = "20260903_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE push_devices (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES app_sessions(id) ON DELETE CASCADE,
            platform TEXT NOT NULL CHECK (platform IN ('macos', 'ios')),
            environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
            token_hash TEXT NOT NULL,
            token_encrypted TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            sound_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            preview_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (platform, environment, token_hash)
        )
    """)
    op.execute("CREATE INDEX idx_push_devices_user ON push_devices(user_id)")
    op.execute("""
        CREATE TABLE push_deliveries (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            gmail_account_id TEXT NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
            device_id TEXT NOT NULL REFERENCES push_devices(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL REFERENCES app_sessions(id) ON DELETE CASCADE,
            message_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sent', 'skipped', 'invalid_token')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (gmail_account_id, message_id, device_id)
        )
    """)
    op.execute("CREATE INDEX idx_push_deliveries_user ON push_deliveries(user_id, gmail_account_id)")
    scope = "gmail_account_id = COALESCE(NULLIF(current_setting('electronic_mail.gmail_account_id', TRUE), ''), (SELECT primary_gmail_account_id FROM users WHERE id=push_deliveries.user_id))"
    op.execute("ALTER TABLE push_deliveries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE push_deliveries FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY isolate_push_deliveries_by_gmail_account ON push_deliveries USING ({scope}) WITH CHECK ({scope})")


def downgrade() -> None:
    op.execute("DROP TABLE push_deliveries")
    op.execute("DROP TABLE push_devices")
