from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.api.routes import auth_google as auth_routes
from app.api.routes import gmail_accounts as account_routes
from app.db.models import StoredGmailAccount, StoredUser
from app.db.account_scope import active_gmail_account_id
from app.db import repository


class MultiGmailAccountTests(unittest.TestCase):
    def test_account_metadata_requires_preservation_gate_before_enablement(self) -> None:
        user = StoredUser(
            id="user-1",
            email="primary@example.com",
            display_name="Primary",
            google_sub="google-primary",
            access_enabled=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            primary_gmail_account_id="user-1",
        )
        account = StoredGmailAccount(
            id="user-1",
            user_id="user-1",
            email="primary@example.com",
            display_name="Primary",
            google_sub="google-primary",
            state="ready",
            initial_ready_at="2026-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        fake_settings = SimpleNamespace(
            database_path="postgresql://example/db",
            multi_gmail_enabled=True,
            multi_gmail_max_accounts=5,
            multi_gmail_verified_backup_id="backup-verified-1",
        )

        with (
            patch.object(account_routes, "settings", fake_settings),
            patch.object(
                account_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(account_routes, "get_user", return_value=user),
            patch.object(account_routes, "list_gmail_accounts", return_value=[account]),
            patch.object(
                account_routes,
                "multi_account_migration_verified",
                return_value=False,
            ),
        ):
            response = account_routes.gmail_accounts(SimpleNamespace())

        self.assertFalse(response.multi_account_enabled)
        self.assertFalse(response.migration_verified)
        self.assertEqual(response.primary_gmail_account_id, "user-1")
        self.assertEqual(len(response.accounts), 1)
        self.assertTrue(response.accounts[0].is_primary)

    def test_foundation_migration_is_additive_and_covers_critical_data(self) -> None:
        migration = Path(
            "backend/migrations/versions/20260830_0035_multi_gmail_accounts_foundation.py"
        ).read_text()

        self.assertNotIn("DELETE FROM gmail_messages", migration)
        self.assertNotIn("DELETE FROM matters", migration)
        self.assertNotIn("INSERT INTO gmail_messages", migration)
        self.assertNotIn("INSERT INTO matters", migration)
        self.assertIn("id, id, email, display_name, google_sub", migration)
        self.assertIn("UPDATE {table_name} SET gmail_account_id = user_id", migration)
        self.assertIn("identifier_checksum", migration)
        self.assertIn("RAISE EXCEPTION", migration)
        self.assertIn(
            "DROP TRIGGER IF EXISTS trg_google_tokens_require_guard", migration
        )
        self.assertIn("CREATE TRIGGER trg_google_tokens_require_guard", migration)
        self.assertNotIn("SET token_json_encrypted", migration)
        self.assertIn("electronic_mail_assign_primary_gmail_account", migration)
        self.assertIn("CREATE TRIGGER trg_assign_primary_gmail_account", migration)
        self.assertIn("NEW.gmail_account_id := NEW.user_id", migration)

        for critical_table in (
            "gmail_messages",
            "gmail_import_state",
            "gmail_pending_thread_actions",
            "gmail_pending_sends",
            "gmail_client_drafts",
            "matter_profiles",
            "message_semantics",
            "matters",
            "matter_decisions",
            "ai_usage_events",
        ):
            self.assertIn(f'("{critical_table}", False)', migration)

    def test_link_start_is_authenticated_and_intent_bound(self) -> None:
        fake_settings = SimpleNamespace(
            database_path="postgresql://example/db",
            mobile_redirect_uri="electronicmail://auth/callback",
            multi_gmail_enabled=True,
            multi_gmail_max_accounts=5,
            multi_gmail_verified_backup_id="backup-verified-1",
        )
        primary = StoredGmailAccount(
            id="user-1",
            user_id="user-1",
            email="primary@example.com",
            display_name="Primary",
            google_sub="google-primary",
            state="ready",
            initial_ready_at="2026-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        with (
            patch.object(account_routes, "settings", fake_settings),
            patch.object(
                account_routes,
                "require_current_user",
                return_value=SimpleNamespace(id="user-1"),
            ),
            patch.object(account_routes, "multi_account_migration_verified", return_value=True),
            patch.object(account_routes, "list_gmail_accounts", return_value=[primary]),
            patch.object(
                account_routes,
                "get_google_auth_url",
                return_value="https://accounts.google.com/oauth",
            ) as auth_url,
        ):
            response = account_routes.start_gmail_account_link(
                account_routes.GmailAccountLinkStartRequest(
                    redirect_to="electronicmail://auth/callback"
                ),
                SimpleNamespace(),
            )

        self.assertEqual(response.authorization_url, "https://accounts.google.com/oauth")
        auth_url.assert_called_once_with(
            fake_settings,
            redirect_to="electronicmail://auth/callback",
            intent="link",
            initiating_user_id="user-1",
        )

    def test_link_callback_writes_secondary_account_without_reconnecting_primary(self) -> None:
        fake_settings = SimpleNamespace(
            database_path="postgresql://example/db",
            mobile_redirect_uri="electronicmail://auth/callback",
            multi_gmail_enabled=True,
            multi_gmail_max_accounts=5,
            multi_gmail_verified_backup_id="backup-verified-1",
        )
        oauth_session = SimpleNamespace(
            redirect_to="electronicmail://auth/callback",
            intent="link",
            initiating_user_id="user-1",
        )
        callback_result = SimpleNamespace(
            redirect_to="electronicmail://auth/callback",
            profile=SimpleNamespace(email="secondary@example.com", display_name="Secondary"),
            google_sub="google-secondary",
            tokens={"refresh_token": "secondary-token"},
            oauth_started_epoch=42,
        )
        linked = StoredGmailAccount(
            id="gmail-2",
            user_id="user-1",
            email="secondary@example.com",
            display_name="Secondary",
            google_sub="google-secondary",
            state="importing",
            initial_ready_at=None,
            created_at="2026-08-30T00:00:00+00:00",
            updated_at="2026-08-30T00:00:00+00:00",
        )
        with (
            patch.object(auth_routes, "settings", fake_settings),
            patch.object(auth_routes, "get_oauth_login_session", return_value=oauth_session),
            patch.object(auth_routes, "handle_google_callback", return_value=callback_result),
            patch.object(auth_routes, "exclusive_google_subject_lock", return_value=nullcontext()),
            patch.object(auth_routes, "oauth_session_is_after_google_subject_deletion", return_value=True),
            patch.object(auth_routes, "has_active_google_token_revocation", return_value=False),
            patch.object(auth_routes, "multi_account_migration_verified", return_value=True),
            patch.object(auth_routes, "get_gmail_account_by_google_subject", return_value=None),
            patch.object(auth_routes, "delete_oauth_login_session"),
            patch.object(auth_routes, "encrypt_json", return_value="encrypted-secondary-token"),
            patch.object(auth_routes, "link_gmail_account", return_value=linked) as link_account,
            patch.object(auth_routes, "reconnect_google_oauth_token") as reconnect_primary,
        ):
            response = auth_routes.auth_google_callback(
                code="oauth-code",
                state="oauth-state",
            )

        self.assertEqual(response.status_code, 307)
        self.assertIn("status=linked", response.headers["location"])
        self.assertIn("gmail_account_id=gmail-2", response.headers["location"])
        reconnect_primary.assert_not_called()
        link_account.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            email="secondary@example.com",
            display_name="Secondary",
            google_sub="google-secondary",
            token_json_encrypted="encrypted-secondary-token",
            oauth_started_epoch=42,
            max_accounts=5,
        )

    def test_account_scoped_oauth_migration_does_not_rewrite_encrypted_tokens(self) -> None:
        migration = Path(
            "backend/migrations/versions/20260830_0036_account_scoped_oauth.py"
        ).read_text()

        self.assertIn("PRIMARY KEY (gmail_account_id)", migration)
        self.assertIn("intent IN ('login', 'link', 'reauthorize', 'merge_verify')", migration)
        self.assertNotIn("SET token_json_encrypted", migration)
        self.assertNotIn("DELETE FROM google_oauth_tokens", migration)

    def test_jobs_accept_explicit_account_scope_and_default_primary_compatibly(self) -> None:
        jobs = Path("backend/app/db/jobs.py").read_text()

        self.assertIn("user_id, gmail_account_id", jobs)
        self.assertIn("resolved_gmail_account_id = gmail_account_id or user_id", jobs)
        self.assertIn('"gmail_account_id": resolved_gmail_account_id', jobs)

    def test_secondary_writes_wait_for_durable_account_import(self) -> None:
        account = SimpleNamespace(id="gmail-2", state="ready")
        fake_settings = SimpleNamespace(
            database_path="postgresql://example/db",
            multi_gmail_writes_enabled=True,
        )
        with (
            patch.object(account_routes, "settings", fake_settings),
            patch.object(account_routes, "_is_primary", return_value=False),
            patch.object(account_routes, "gmail_account_import_ready", return_value=False),
        ):
            with self.assertRaises(account_routes.HTTPException) as raised:
                account_routes._require_account_write_ready("user-1", account)
        self.assertEqual(raised.exception.status_code, 409)

        with (
            patch.object(account_routes, "settings", fake_settings),
            patch.object(account_routes, "_is_primary", return_value=False),
            patch.object(account_routes, "gmail_account_import_ready", return_value=True),
        ):
            account_routes._require_account_write_ready("user-1", account)

    def test_import_readiness_activates_the_explicit_account_scope(self) -> None:
        observed_scopes: list[str | None] = []

        class Result:
            @staticmethod
            def fetchone():
                return (True,)

        class Connection:
            @staticmethod
            def execute(_sql, _params):
                observed_scopes.append(active_gmail_account_id())
                return Result()

        @contextmanager
        def fake_connect(_database_path):
            observed_scopes.append(active_gmail_account_id())
            yield Connection()

        with patch.object(repository, "connect", fake_connect):
            ready = repository.gmail_account_import_ready(
                "postgresql://example/db",
                user_id="user-1",
                gmail_account_id="gmail-2",
            )

        self.assertTrue(ready)
        self.assertEqual(observed_scopes, ["gmail-2", "gmail-2"])
        self.assertIsNone(active_gmail_account_id())

    def test_durable_import_migration_does_not_touch_mail_or_ai_rows(self) -> None:
        migration = Path(
            "backend/migrations/versions/20260830_0040_require_durable_account_import.py"
        ).read_text()

        self.assertIn("NEW.initial_window_complete", migration)
        self.assertIn("NULLIF(NEW.last_history_id, '') IS NOT NULL", migration)
        self.assertIn("account.id <> owner.primary_gmail_account_id", migration)
        self.assertNotIn("UPDATE gmail_messages", migration)
        self.assertNotIn("UPDATE matters", migration)
        self.assertNotIn("DELETE FROM", migration)

    def test_ai_budget_migration_aggregates_across_gmail_accounts(self) -> None:
        migration = Path(
            "backend/migrations/versions/20260830_0041_aggregate_ai_usage_across_accounts.py"
        ).read_text()

        self.assertIn("SECURITY DEFINER", migration)
        self.assertIn("target_user_id IS NULL OR user_id = target_user_id", migration)
        self.assertNotIn("gmail_account_id =", migration)

    def test_account_realtime_endpoint_is_publicly_registered(self) -> None:
        paths = {route.path for route in account_routes.router.routes}
        self.assertIn(
            "/v1/gmail-accounts/{gmail_account_id}/events/mailbox",
            paths,
        )


if __name__ == "__main__":
    unittest.main()
