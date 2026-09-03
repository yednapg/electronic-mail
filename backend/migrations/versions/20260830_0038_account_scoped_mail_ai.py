"""Account-scoped canonical mail and AI identities with enforced isolation.

Revision ID: 20260830_0038
Revises: 20260830_0037
Create Date: 2026-08-30

Existing rows keep their identifiers and contents. Constraint replacement and
RLS activation are transactional, and preservation checks abort on mismatch.
"""

from alembic import op


revision = "20260830_0038"
down_revision = "20260830_0037"
branch_labels = None
depends_on = None


PRESERVED = (
    ("gmail_messages", "concat_ws(chr(31),user_id,gmail_account_id,message_id,coalesce(gmail_thread_id,''))"),
    ("gmail_import_state", "concat_ws(chr(31),user_id,gmail_account_id,coalesce(last_history_id,''))"),
    ("matter_profiles", "concat_ws(chr(31),user_id,gmail_account_id,revision::text,enabled::text)"),
    ("matter_generations", "concat_ws(chr(31),id,user_id,gmail_account_id,status)"),
    ("message_semantics", "concat_ws(chr(31),user_id,gmail_account_id,message_id,generation_id)"),
    ("attachment_text_extractions", "concat_ws(chr(31),user_id,gmail_account_id,message_id,attachment_id)"),
    ("matters", "concat_ws(chr(31),id,user_id,gmail_account_id,generation_id)"),
    ("matter_members", "concat_ws(chr(31),user_id,gmail_account_id,generation_id,message_id)"),
    ("matter_decisions", "concat_ws(chr(31),id,user_id,gmail_account_id,coalesce(idempotency_key,''))"),
    ("ai_usage_events", "concat_ws(chr(31),id,user_id,gmail_account_id,coalesce(message_id,''))"),
    ("matter_subgoals", "concat_ws(chr(31),id,user_id,gmail_account_id,generation_id,matter_id)"),
)


RLS_TABLES = tuple(dict.fromkeys([
    *(table for table, _key in PRESERVED),
    "mail_groups", "mail_group_members", "gmail_pending_thread_actions",
    "gmail_pending_sends", "mailbox_events", "visible_mail_groups",
    "visible_mail_group_members", "grouping_decision_audit",
    "gmail_client_drafts", "gmail_thread_order_state",
    "gmail_thread_order_entries", "gmail_reconcile_seen",
    "gmail_initial_window_entries", "google_contact_avatar_cache",
    "entity_outcomes", "attachment_text_extractions",
]))


def _snapshot(name: str) -> None:
    parts = [
        f"SELECT '{table}'::text dataset, COUNT(*)::bigint row_count, "
        f"COALESCE(SUM(hashtextextended({key},0)::numeric),0)::text checksum FROM {table}"
        for table, key in PRESERVED
    ]
    op.execute(f"CREATE TEMP TABLE {name} ON COMMIT DROP AS " + " UNION ALL ".join(parts))


def upgrade() -> None:
    _snapshot("account_ai_before")

    # New referenced identities can be installed before legacy keys are removed.
    op.execute("ALTER TABLE gmail_messages ADD CONSTRAINT uq_gmail_messages_account_message UNIQUE (gmail_account_id, message_id)")
    op.execute("ALTER TABLE matter_generations ADD CONSTRAINT uq_matter_generations_account_id UNIQUE (id, gmail_account_id)")
    op.execute("ALTER TABLE matters ADD CONSTRAINT uq_matters_account_generation UNIQUE (id, gmail_account_id, generation_id)")

    # Remove foreign keys that depend on legacy user-only identities.
    for table, constraint in (
        ("message_semantics", "message_semantics_user_id_message_id_fkey"),
        ("message_semantics", "message_semantics_generation_id_user_id_fkey"),
        ("attachment_text_extractions", "attachment_text_extractions_user_id_message_id_fkey"),
        ("matter_members", "matter_members_user_id_message_id_fkey"),
        ("matter_members", "matter_members_matter_id_user_id_generation_id_fkey"),
        ("matter_profiles", "matter_profiles_active_generation_id_user_id_fkey"),
        ("matters", "matters_generation_id_user_id_fkey"),
        ("matter_decisions", "matter_decisions_generation_id_user_id_fkey"),
        ("ai_usage_events", "ai_usage_events_generation_id_user_id_fkey"),
        ("matter_subgoals", "matter_subgoals_matter_id_user_id_generation_id_fkey"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")

    op.execute("ALTER TABLE gmail_messages DROP CONSTRAINT gmail_messages_pkey")
    op.execute("ALTER TABLE gmail_messages ADD CONSTRAINT gmail_messages_pkey PRIMARY KEY (gmail_account_id, message_id)")
    op.execute("ALTER TABLE gmail_import_state DROP CONSTRAINT gmail_import_state_pkey")
    op.execute("ALTER TABLE gmail_import_state ADD CONSTRAINT gmail_import_state_pkey PRIMARY KEY (gmail_account_id)")
    op.execute("ALTER TABLE matter_profiles DROP CONSTRAINT matter_profiles_pkey")
    op.execute("ALTER TABLE matter_profiles ADD CONSTRAINT matter_profiles_pkey PRIMARY KEY (gmail_account_id)")
    op.execute("ALTER TABLE message_semantics DROP CONSTRAINT message_semantics_pkey")
    op.execute("ALTER TABLE message_semantics ADD CONSTRAINT message_semantics_pkey PRIMARY KEY (gmail_account_id,message_id,generation_id)")
    op.execute("ALTER TABLE attachment_text_extractions DROP CONSTRAINT attachment_text_extractions_pkey")
    op.execute("ALTER TABLE attachment_text_extractions ADD CONSTRAINT attachment_text_extractions_pkey PRIMARY KEY (gmail_account_id,message_id,attachment_id)")
    op.execute("ALTER TABLE matter_members DROP CONSTRAINT matter_members_pkey")
    op.execute("ALTER TABLE matter_members ADD CONSTRAINT matter_members_pkey PRIMARY KEY (gmail_account_id,generation_id,message_id)")

    op.execute("DROP INDEX uq_matter_generations_active_user")
    op.execute("CREATE UNIQUE INDEX uq_matter_generations_active_account ON matter_generations(gmail_account_id) WHERE status='active'")
    op.execute("ALTER TABLE matter_decisions ADD CONSTRAINT uq_matter_decisions_account_idempotency UNIQUE (gmail_account_id,idempotency_key)")
    op.execute("ALTER TABLE matter_decisions DROP CONSTRAINT matter_decisions_user_id_idempotency_key_key")
    op.execute("ALTER TABLE matter_subgoals ADD CONSTRAINT uq_matter_subgoals_account_key UNIQUE (gmail_account_id,generation_id,matter_id,canonical_key)")
    op.execute("ALTER TABLE matter_subgoals DROP CONSTRAINT matter_subgoals_user_id_generation_id_matter_id_canonical_k_key")

    # Account-matching foreign keys make cross-account AI membership impossible.
    op.execute("ALTER TABLE message_semantics ADD CONSTRAINT fk_message_semantics_account_message FOREIGN KEY (gmail_account_id,message_id) REFERENCES gmail_messages(gmail_account_id,message_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE message_semantics ADD CONSTRAINT fk_message_semantics_account_generation FOREIGN KEY (generation_id,gmail_account_id) REFERENCES matter_generations(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE attachment_text_extractions ADD CONSTRAINT fk_attachment_text_account_message FOREIGN KEY (gmail_account_id,message_id) REFERENCES gmail_messages(gmail_account_id,message_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE matter_profiles ADD CONSTRAINT fk_matter_profiles_account_generation FOREIGN KEY (active_generation_id,gmail_account_id) REFERENCES matter_generations(id,gmail_account_id) ON DELETE SET NULL (active_generation_id)")
    op.execute("ALTER TABLE matters ADD CONSTRAINT fk_matters_account_generation FOREIGN KEY (generation_id,gmail_account_id) REFERENCES matter_generations(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE matter_members ADD CONSTRAINT fk_matter_members_account_message FOREIGN KEY (gmail_account_id,message_id) REFERENCES gmail_messages(gmail_account_id,message_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE matter_members ADD CONSTRAINT fk_matter_members_account_matter FOREIGN KEY (matter_id,gmail_account_id,generation_id) REFERENCES matters(id,gmail_account_id,generation_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE matter_decisions ADD CONSTRAINT fk_matter_decisions_account_generation FOREIGN KEY (generation_id,gmail_account_id) REFERENCES matter_generations(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE ai_usage_events ADD CONSTRAINT fk_ai_usage_account_generation FOREIGN KEY (generation_id,gmail_account_id) REFERENCES matter_generations(id,gmail_account_id) ON DELETE CASCADE")
    op.execute("ALTER TABLE matter_subgoals ADD CONSTRAINT fk_matter_subgoals_account_matter FOREIGN KEY (matter_id,gmail_account_id,generation_id) REFERENCES matters(id,gmail_account_id,generation_id) ON DELETE CASCADE")

    op.execute("""
      CREATE OR REPLACE FUNCTION electronic_mail_assign_primary_gmail_account()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.gmail_account_id IS NULL AND NEW.user_id IS NOT NULL THEN
          NEW.gmail_account_id := COALESCE(
            NULLIF(current_setting('electronic_mail.gmail_account_id', TRUE), ''),
            (SELECT primary_gmail_account_id FROM users WHERE id=NEW.user_id)
          );
        END IF;
        RETURN NEW;
      END $$
    """)

    for table in RLS_TABLES:
        policy = f"isolate_{table}_by_gmail_account"
        expression = (
            "gmail_account_id = COALESCE("
            "NULLIF(current_setting('electronic_mail.gmail_account_id', TRUE), ''),"
            f"(SELECT primary_gmail_account_id FROM users WHERE id={table}.user_id))"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {policy} ON {table} USING ({expression}) WITH CHECK ({expression})")

    _snapshot("account_ai_after")
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (
          (SELECT * FROM account_ai_before EXCEPT SELECT * FROM account_ai_after)
          UNION ALL
          (SELECT * FROM account_ai_after EXCEPT SELECT * FROM account_ai_before)
        ) THEN RAISE EXCEPTION 'Account-scoped AI migration changed existing identifiers';
        END IF;
      END $$
    """)


def downgrade() -> None:
    raise RuntimeError("Forward fix or database restore is required after multi-account AI activation")
