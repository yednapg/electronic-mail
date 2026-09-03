"""Make importer projections and job deduplication Gmail-account scoped.

Revision ID: 20260830_0039
Revises: 20260830_0038
Create Date: 2026-08-30

Only keys and foreign keys change. Existing rows and identifiers are checked
before and after and the transaction aborts if any row changes or disappears.
"""

from alembic import op


revision = "20260830_0039"
down_revision = "20260830_0038"
branch_labels = None
depends_on = None


PRESERVED = (
    ("mail_groups", "concat_ws(chr(31),id,user_id,gmail_account_id,group_key)"),
    ("mail_group_members", "concat_ws(chr(31),id,user_id,gmail_account_id,group_id,gmail_message_id)"),
    ("visible_mail_groups", "concat_ws(chr(31),id,user_id,gmail_account_id,visibility,projection_key)"),
    ("visible_mail_group_members", "concat_ws(chr(31),id,user_id,gmail_account_id,visible_group_id,gmail_message_id)"),
    ("gmail_thread_order_state", "concat_ws(chr(31),user_id,gmail_account_id,label,active_generation_id)"),
    ("gmail_thread_order_entries", "concat_ws(chr(31),user_id,gmail_account_id,label,generation_id,gmail_thread_id,position::text)"),
    ("gmail_reconcile_seen", "concat_ws(chr(31),user_id,gmail_account_id,generation_id,message_id)"),
    ("gmail_initial_window_entries", "concat_ws(chr(31),user_id,gmail_account_id,generation_id,gmail_thread_id,position::text)"),
    ("google_contact_avatar_cache", "concat_ws(chr(31),user_id,gmail_account_id,normalized_email)"),
    ("app_session_snapshots", "concat_ws(chr(31),user_id,gmail_account_id,updated_at::text)"),
    ("background_jobs", "concat_ws(chr(31),id,coalesce(user_id,''),coalesce(gmail_account_id,''),kind,coalesce(dedupe_key,''))"),
)


def _snapshot(name: str) -> None:
    queries = [
        f"SELECT '{table}'::text dataset, COUNT(*)::bigint row_count, "
        f"COALESCE(SUM(hashtextextended({key},0)::numeric),0)::text checksum FROM {table}"
        for table, key in PRESERVED
    ]
    op.execute(f"CREATE TEMP TABLE {name} ON COMMIT DROP AS " + " UNION ALL ".join(queries))


def upgrade() -> None:
    _snapshot("account_projection_before")

    # Parent identities used by account-matching foreign keys.
    op.execute("ALTER TABLE mail_groups ADD CONSTRAINT uq_mail_groups_id_account UNIQUE (id,gmail_account_id)")
    op.execute("ALTER TABLE visible_mail_groups ADD CONSTRAINT uq_visible_mail_groups_id_account UNIQUE (id,gmail_account_id)")

    for table, constraint in (
        ("mail_group_members", "mail_group_members_group_id_fkey"),
        ("visible_mail_groups", "visible_mail_groups_source_group_id_fkey"),
        ("visible_mail_group_members", "visible_mail_group_members_visible_group_id_fkey"),
        ("grouping_decision_audit", "grouping_decision_audit_source_group_id_fkey"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")

    op.execute("ALTER TABLE mail_groups ADD CONSTRAINT uq_mail_groups_account_key UNIQUE (gmail_account_id,group_key)")
    op.execute("ALTER TABLE mail_groups DROP CONSTRAINT mail_groups_user_id_group_key_key")
    op.execute("ALTER TABLE mail_group_members ADD CONSTRAINT uq_mail_group_members_account_message UNIQUE (gmail_account_id,group_id,gmail_message_id)")
    op.execute("ALTER TABLE mail_group_members DROP CONSTRAINT mail_group_members_user_id_group_id_gmail_message_id_key")
    op.execute("ALTER TABLE visible_mail_groups ADD CONSTRAINT uq_visible_groups_account_key UNIQUE (gmail_account_id,visibility,projection_key)")
    op.execute("ALTER TABLE visible_mail_groups DROP CONSTRAINT visible_mail_groups_user_id_visibility_projection_key_key")
    op.execute("ALTER TABLE visible_mail_group_members ADD CONSTRAINT uq_visible_members_account_group_message UNIQUE (gmail_account_id,visible_group_id,gmail_message_id)")
    op.execute("ALTER TABLE visible_mail_group_members ADD CONSTRAINT uq_visible_members_account_visibility_message UNIQUE (gmail_account_id,visibility,gmail_message_id)")
    op.execute("ALTER TABLE visible_mail_group_members DROP CONSTRAINT visible_mail_group_members_user_id_visible_group_id_gmail_m_key")
    op.execute("ALTER TABLE visible_mail_group_members DROP CONSTRAINT visible_mail_group_members_user_id_visibility_gmail_message_key")

    op.execute("ALTER TABLE gmail_thread_order_state DROP CONSTRAINT gmail_thread_order_state_pkey")
    op.execute("ALTER TABLE gmail_thread_order_state ADD CONSTRAINT gmail_thread_order_state_pkey PRIMARY KEY (gmail_account_id,label)")
    op.execute("ALTER TABLE gmail_thread_order_entries DROP CONSTRAINT gmail_thread_order_entries_pkey")
    op.execute("ALTER TABLE gmail_thread_order_entries ADD CONSTRAINT gmail_thread_order_entries_pkey PRIMARY KEY (gmail_account_id,label,generation_id,gmail_thread_id)")
    op.execute("ALTER TABLE gmail_thread_order_entries ADD CONSTRAINT uq_thread_order_account_position UNIQUE (gmail_account_id,label,generation_id,position)")
    op.execute("ALTER TABLE gmail_thread_order_entries DROP CONSTRAINT gmail_thread_order_entries_user_id_label_generation_id_posi_key")
    op.execute("ALTER TABLE gmail_reconcile_seen DROP CONSTRAINT gmail_reconcile_seen_pkey")
    op.execute("ALTER TABLE gmail_reconcile_seen ADD CONSTRAINT gmail_reconcile_seen_pkey PRIMARY KEY (gmail_account_id,generation_id,message_id)")
    op.execute("ALTER TABLE gmail_initial_window_entries DROP CONSTRAINT gmail_initial_window_entries_pkey")
    op.execute("ALTER TABLE gmail_initial_window_entries ADD CONSTRAINT gmail_initial_window_entries_pkey PRIMARY KEY (gmail_account_id,generation_id,gmail_thread_id)")
    op.execute("ALTER TABLE gmail_initial_window_entries ADD CONSTRAINT uq_initial_window_account_position UNIQUE (gmail_account_id,generation_id,position)")
    op.execute("ALTER TABLE gmail_initial_window_entries DROP CONSTRAINT gmail_initial_window_entries_user_id_generation_id_position_key")
    op.execute("ALTER TABLE google_contact_avatar_cache DROP CONSTRAINT google_contact_avatar_cache_pkey")
    op.execute("ALTER TABLE google_contact_avatar_cache ADD CONSTRAINT google_contact_avatar_cache_pkey PRIMARY KEY (gmail_account_id,normalized_email)")
    op.execute("ALTER TABLE app_session_snapshots DROP CONSTRAINT app_session_snapshots_pkey")
    op.execute("ALTER TABLE app_session_snapshots ADD CONSTRAINT app_session_snapshots_pkey PRIMARY KEY (gmail_account_id)")

    # Cross-account projections are rejected even if application filtering fails.
    op.execute("ALTER TABLE mail_group_members ADD CONSTRAINT fk_mail_group_members_account_group FOREIGN KEY (group_id,gmail_account_id) REFERENCES mail_groups(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE mail_group_members ADD CONSTRAINT fk_mail_group_members_account_message FOREIGN KEY (gmail_account_id,gmail_message_id) REFERENCES gmail_messages(gmail_account_id,message_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE visible_mail_groups ADD CONSTRAINT fk_visible_groups_account_source FOREIGN KEY (source_group_id,gmail_account_id) REFERENCES mail_groups(id,gmail_account_id) ON DELETE SET NULL (source_group_id)")
    op.execute("ALTER TABLE visible_mail_group_members ADD CONSTRAINT fk_visible_members_account_group FOREIGN KEY (visible_group_id,gmail_account_id) REFERENCES visible_mail_groups(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE visible_mail_group_members ADD CONSTRAINT fk_visible_members_account_message FOREIGN KEY (gmail_account_id,gmail_message_id) REFERENCES gmail_messages(gmail_account_id,message_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE grouping_decision_audit ADD CONSTRAINT fk_grouping_audit_account_source FOREIGN KEY (source_group_id,gmail_account_id) REFERENCES mail_groups(id,gmail_account_id) ON DELETE SET NULL (source_group_id)")

    op.execute("DROP INDEX idx_background_jobs_active_dedupe")
    op.execute("CREATE UNIQUE INDEX idx_background_jobs_active_account_dedupe ON background_jobs(gmail_account_id,kind,dedupe_key) WHERE gmail_account_id IS NOT NULL AND dedupe_key IS NOT NULL AND status IN ('queued','running')")
    op.execute("CREATE UNIQUE INDEX idx_background_jobs_active_system_dedupe ON background_jobs(kind,dedupe_key) WHERE gmail_account_id IS NULL AND dedupe_key IS NOT NULL AND status IN ('queued','running')")

    _snapshot("account_projection_after")
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (
          (SELECT * FROM account_projection_before EXCEPT SELECT * FROM account_projection_after)
          UNION ALL
          (SELECT * FROM account_projection_after EXCEPT SELECT * FROM account_projection_before)
        ) THEN RAISE EXCEPTION 'Account projection migration changed existing identifiers';
        END IF;
      END $$
    """)


def downgrade() -> None:
    raise RuntimeError("Forward fix or database restore is required after multi-account projection activation")
