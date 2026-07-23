from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.routes import auth_google as auth_routes
from app.core.observability import _request_log_path
from app.db import repository
from app.main import app
from app.schemas.domain import DashboardProfile
from app.services import auth as auth_service
from app.services.integrations import google as google_service


HANDOFF_ID = "11111111-1111-4111-8111-111111111111"
CODE_VERIFIER = "v" * 43
CODE_CHALLENGE = auth_service.mobile_exchange_code_challenge(CODE_VERIFIER)


@contextmanager
def _recording_guard(state: dict[str, bool], key: str = "active"):
    state[key] = True
    try:
        yield
    finally:
        state[key] = False


class GoogleAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.settings = SimpleNamespace(
            google_configured=True,
            cors_origin="http://localhost:5173",
            mobile_redirect_uri="electronicmail://auth/callback",
            backend_origin="http://localhost:3001",
            database_path="postgresql://example/db",
            session_cookie_domain="",
            app_session_secret="test-session-secret",
            app_encryption_key="test-encryption-key",
            web_app_url="http://localhost:5173",
            is_production_like=False,
            session_cookie_samesite="lax",
        )
        self.settings_patch = patch.object(auth_routes, "settings", self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.mail_guard_patch = patch.object(
            auth_routes,
            "exclusive_user_mail_lock",
            side_effect=lambda *_args, **_kwargs: nullcontext(),
        )
        self.mock_mail_guard = self.mail_guard_patch.start()
        self.addCleanup(self.mail_guard_patch.stop)
        self.subject_guard_patch = patch.object(
            auth_routes,
            "exclusive_google_subject_lock",
            side_effect=lambda *_args, **_kwargs: nullcontext(),
        )
        self.mock_subject_guard = self.subject_guard_patch.start()
        self.addCleanup(self.subject_guard_patch.stop)
        self.oauth_freshness_patch = patch.object(
            auth_routes,
            "oauth_session_is_after_google_subject_deletion",
            return_value=True,
        )
        self.mock_oauth_freshness = self.oauth_freshness_patch.start()
        self.addCleanup(self.oauth_freshness_patch.stop)

    def assert_no_store(self, response) -> None:
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(response.headers["pragma"], "no-cache")

    @property
    def handoff_redirect(self) -> str:
        return (
            f"{self.settings.backend_origin}/auth/mobile/complete"
            f"?handoff_id={HANDOFF_ID}&code_challenge={CODE_CHALLENGE}"
        )

    @patch("app.api.routes.auth_google.get_google_auth_url")
    def test_auth_google_preserves_web_oauth_flow(self, mock_auth_url: Mock) -> None:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        response = self.client.get("/auth/google", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        mock_auth_url.assert_called_once_with(self.settings, redirect_to=None)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.get_google_auth_url")
    def test_auth_google_rejects_unbound_custom_scheme_redirect(self, mock_auth_url: Mock) -> None:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        response = self.client.get(
            "/auth/google",
            params={"redirect_to": "electronicmail://auth/callback"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        mock_auth_url.assert_not_called()
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.get_google_auth_url")
    def test_auth_google_accepts_verifier_bound_native_handoff(self, mock_auth_url: Mock) -> None:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        response = self.client.get(
            "/auth/google",
            params={"redirect_to": self.handoff_redirect},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        mock_auth_url.assert_called_once_with(self.settings, redirect_to=self.handoff_redirect)
        self.assert_no_store(response)

    def test_auth_google_rejects_unknown_redirect_targets(self) -> None:
        response = self.client.get(
            "/auth/google",
            params={"redirect_to": "otherapp://auth/callback"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.get_oauth_login_session", return_value=None)
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_uses_service_redirect(self, mock_callback: Mock, _mock_session: Mock) -> None:
        mock_callback.return_value = "electronicmail://auth/callback"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "electronicmail://auth/callback")
        mock_callback.assert_called_once_with(self.settings, "code-1", "state-1")
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.get_oauth_login_session", return_value=None)
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_restarts_when_local_session_is_missing(
        self,
        mock_callback: Mock,
        _mock_session: Mock,
        mock_delete: Mock,
    ) -> None:
        mock_callback.side_effect = RuntimeError("Missing OAuth session. Start again from /auth/google.")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/auth/google")
        mock_delete.assert_called_once()
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.create_mobile_oauth_handoff")
    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_cancelled_oauth_stores_terminal_durable_handoff(
        self,
        mock_session: Mock,
        mock_delete: Mock,
        mock_create: Mock,
    ) -> None:
        mock_session.return_value = SimpleNamespace(redirect_to=self.handoff_redirect, started_epoch=1)

        response = self.client.get(
            "/auth/google/callback",
            params={"error": "access_denied", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertIn(f"/auth/mobile/complete?handoff_id={HANDOFF_ID}", response.headers["location"])
        mock_delete.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["status"], "cancelled")
        self.assertIsNone(mock_create.call_args.kwargs["login_code_encrypted"])
        self.assertEqual(mock_create.call_args.kwargs["exchange_code_challenge"], CODE_CHALLENGE)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.create_mobile_oauth_handoff")
    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_allowlist_failure_stores_terminal_durable_handoff(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_user: Mock,
        mock_delete: Mock,
        mock_create: Mock,
    ) -> None:
        mock_session.return_value = SimpleNamespace(redirect_to=self.handoff_redirect, started_epoch=2)
        mock_callback.return_value = SimpleNamespace(
            redirect_to=self.handoff_redirect,
            profile=DashboardProfile(email="blocked@example.com", display_name="Blocked"),
            google_sub="google-sub-blocked",
            tokens={"token": "secret-provider-token"},
            oauth_started_epoch=2,
        )
        mock_user.side_effect = HTTPException(status_code=403, detail="This Google account is not allowed")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertIn(f"handoff_id={HANDOFF_ID}", response.headers["location"])
        mock_delete.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["status"], "failed")
        self.assertEqual(mock_create.call_args.kwargs["error"], "Google sign-in failed. Please try again.")
        self.assertIsNone(mock_create.call_args.kwargs["login_code_hash"])
        self.assertEqual(mock_create.call_args.kwargs["exchange_code_challenge"], CODE_CHALLENGE)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_callback_started_before_account_deletion_cannot_recreate_user(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_delete_session: Mock,
    ) -> None:
        mock_session.return_value = SimpleNamespace(redirect_to=None, started_epoch=41)
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens={"token": "stale-provider-token"},
            oauth_started_epoch=41,
        )
        self.mock_oauth_freshness.return_value = False

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "stale-code", "state": "old-state"},
            follow_redirects=False,
        )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/auth/google")
        mock_create_user.assert_not_called()
        mock_delete_session.assert_called_once_with(self.settings.database_path, state="old-state")
        self.mock_oauth_freshness.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
            oauth_started_epoch=41,
        )
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.issue_session")
    @patch("app.api.routes.auth_google.enqueue_first_run")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.clear_google_guard_state")
    @patch("app.api.routes.auth_google.save_user_google_tokens")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_fresh_callback_serializes_subject_then_user_before_saving_tokens(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_save_tokens: Mock,
        mock_clear_guard: Mock,
        mock_watch: Mock,
        _mock_enqueue: Mock,
        mock_issue_session: Mock,
    ) -> None:
        state = {"subject": False, "user": False}
        user = SimpleNamespace(id="user-1", email="owner@example.com", display_name="Owner")
        mock_session.return_value = SimpleNamespace(redirect_to=None, started_epoch=42)
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens={"token": "fresh-provider-token"},
            oauth_started_epoch=42,
        )
        mock_issue_session.return_value = SimpleNamespace(token="app-session-token")
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(state, "subject")

        def enter_user_guard(*_args, **_kwargs):
            self.assertTrue(state["subject"])
            return _recording_guard(state, "user")

        self.mock_mail_guard.side_effect = enter_user_guard
        self.mock_oauth_freshness.side_effect = lambda *_args, **_kwargs: self.assertTrue(state["subject"]) or True
        mock_create_user.side_effect = lambda *_args, **_kwargs: self.assertTrue(state["subject"]) or user
        mock_save_tokens.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(state["subject"]),
            self.assertTrue(state["user"]),
        )
        mock_clear_guard.side_effect = lambda *_args, **_kwargs: self.assertTrue(state["user"])
        mock_watch.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(state["subject"]),
            self.assertFalse(state["user"]),
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "fresh-code", "state": "new-state"},
            follow_redirects=False,
        )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], f"{self.settings.web_app_url}/post-login")
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_save_tokens.assert_called_once_with(
            self.settings,
            user_id="user-1",
            tokens={"token": "fresh-provider-token"},
            oauth_started_epoch=42,
        )
        mock_clear_guard.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            oauth_started_epoch=42,
        )
        mock_create_user.assert_called_once_with(
            self.settings,
            profile=mock_callback.return_value.profile,
            google_sub="google-sub-owner",
            oauth_started_epoch=42,
        )

    @patch("app.api.routes.auth_google.create_mobile_oauth_handoff")
    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.handle_google_callback", side_effect=RuntimeError("provider failed"))
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_provider_failure_stores_terminal_durable_handoff(
        self,
        mock_session: Mock,
        _mock_callback: Mock,
        mock_delete: Mock,
        mock_create: Mock,
    ) -> None:
        mock_session.return_value = SimpleNamespace(redirect_to=self.handoff_redirect)

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        mock_delete.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["status"], "failed")
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google._read_handoff", return_value=("ready", "login-1", None))
    def test_mobile_complete_opens_registered_app_callback(self, mock_read: Mock) -> None:
        response = self.client.get(
            "/auth/mobile/complete",
            params={"handoff_id": HANDOFF_ID},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Connected. Returning to Electronic Mail", response.text)
        self.assertIn("electronicmail://auth/callback?login_code=login-1", response.text)
        self.assertIn(f"handoff_id={HANDOFF_ID}", response.text)
        mock_read.assert_called_once_with(HANDOFF_ID)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google._read_handoff", return_value=("ready", "login-1", None))
    def test_mobile_poll_reads_durable_handoff(self, mock_read: Mock) -> None:
        response = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "login_code": "login-1", "handoff_id": HANDOFF_ID},
        )
        mock_read.assert_called_once_with(HANDOFF_ID)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google._read_handoff", return_value=None)
    def test_mobile_poll_is_pending_before_callback(self, _mock_read: Mock) -> None:
        response = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "pending"})
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google._read_handoff", return_value=("ready", "login-1", None))
    def test_browser_completion_does_not_consume_handoff_before_native_poll(self, mock_read: Mock) -> None:
        browser = self.client.get(
            "/auth/mobile/complete",
            params={"handoff_id": HANDOFF_ID},
            follow_redirects=False,
        )
        poll = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")

        self.assertEqual(browser.status_code, 200)
        self.assertIn("login_code=login-1", browser.text)
        self.assertEqual(poll.status_code, 200)
        self.assertEqual(
            poll.json(),
            {"status": "ready", "login_code": "login-1", "handoff_id": HANDOFF_ID},
        )
        self.assertEqual(mock_read.call_count, 2)

    @patch("app.api.routes.auth_google._read_handoff", return_value=("ready", "login-1", None))
    def test_native_may_poll_ready_handoff_repeatedly_until_login_code_exchange(self, mock_read: Mock) -> None:
        first = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")
        second = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")

        self.assertEqual(
            first.json(),
            {"status": "ready", "login_code": "login-1", "handoff_id": HANDOFF_ID},
        )
        self.assertEqual(second.json(), first.json())
        self.assertEqual(mock_read.call_count, 2)

    @patch("app.api.routes.auth_google._read_handoff", return_value=("cancelled", None, "Google sign-in was cancelled."))
    def test_mobile_poll_returns_terminal_cancelled_state(self, _mock_read: Mock) -> None:
        response = self.client.get(f"/v1/auth/mobile/handoff/{HANDOFF_ID}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "cancelled",
                "error": "Google sign-in was cancelled.",
                "handoff_id": HANDOFF_ID,
            },
        )
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.exchange_mobile_code")
    def test_mobile_exchange_requires_and_forwards_verifier_binding(self, mock_exchange: Mock) -> None:
        mock_exchange.return_value = SimpleNamespace(
            token="native-session-token",
            expires_at="2026-10-11T12:00:00+00:00",
            user=SimpleNamespace(id="user-1", email="owner@example.com", display_name="Owner"),
        )

        response = self.client.post(
            "/v1/auth/mobile/exchange",
            json={
                "login_code": "l" * 32,
                "handoff_id": HANDOFF_ID,
                "code_verifier": CODE_VERIFIER,
            },
        )

        self.assertEqual(response.status_code, 200)
        mock_exchange.assert_called_once_with(
            self.settings,
            code="l" * 32,
            handoff_id=HANDOFF_ID,
            code_verifier=CODE_VERIFIER,
        )
        self.assertEqual(response.json()["session_token"], "native-session-token")
        self.assert_no_store(response)

    def test_mobile_exchange_validation_error_is_never_cacheable(self) -> None:
        response = self.client.post(
            "/v1/auth/mobile/exchange",
            json={"login_code": "l" * 32, "handoff_id": HANDOFF_ID},
        )

        self.assertEqual(response.status_code, 422)
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.delete_user_account_with_google_subject_tombstone", return_value=True)
    @patch("app.api.routes.auth_google.revoke_user_app_sessions")
    @patch("app.api.routes.auth_google.delete_user_mail_data")
    @patch("app.api.routes.auth_google.cancel_user_jobs")
    @patch("app.api.routes.auth_google.revoke_stored_google_token", side_effect=OSError("provider unavailable"))
    @patch("app.api.routes.auth_google.stop_gmail_watch", side_effect=OSError("provider unavailable"))
    @patch("app.api.routes.auth_google.record_google_subject_revocation")
    @patch("app.api.routes.auth_google.get_user")
    @patch("app.api.routes.auth_google.get_current_user")
    def test_delete_account_removes_local_account_even_when_google_is_unavailable(
        self,
        mock_current_user: Mock,
        mock_stored_user: Mock,
        mock_record_revocation: Mock,
        _mock_stop: Mock,
        _mock_revoke_google: Mock,
        mock_cancel: Mock,
        mock_delete_mail_data: Mock,
        mock_revoke_sessions: Mock,
        mock_delete: Mock,
    ) -> None:
        mock_current_user.return_value = SimpleNamespace(id="user-1")
        mock_stored_user.return_value = SimpleNamespace(id="user-1", google_sub="google-sub-owner")
        guard_state = {"subject": False, "user": False}
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "subject")
        mock_record_revocation.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(guard_state["subject"]),
            self.assertFalse(guard_state["user"]),
        )

        def enter_user_guard(*_args, **_kwargs):
            self.assertTrue(guard_state["subject"])
            return _recording_guard(guard_state, "user")

        def assert_nested_guards(*_args, **_kwargs):
            self.assertTrue(guard_state["subject"])
            self.assertTrue(guard_state["user"])

        self.mock_mail_guard.side_effect = enter_user_guard
        mock_cancel.side_effect = assert_nested_guards
        mock_delete_mail_data.side_effect = assert_nested_guards
        mock_revoke_sessions.side_effect = assert_nested_guards
        mock_delete.side_effect = lambda *_args, **_kwargs: assert_nested_guards() or True

        response = self.client.delete("/v1/auth/account")

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 204)
        mock_stored_user.assert_called_once_with(self.settings.database_path, "user-1")
        mock_cancel.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_delete_mail_data.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_revoke_sessions.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_delete.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_record_revocation.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")

    @patch("app.api.routes.auth_google.delete_user_mail_data")
    @patch("app.api.routes.auth_google.delete_google_oauth_token")
    @patch("app.api.routes.auth_google.cancel_user_jobs")
    @patch("app.api.routes.auth_google.mark_google_disconnected")
    @patch("app.api.routes.auth_google.revoke_stored_google_token", return_value=True)
    @patch("app.api.routes.auth_google.stop_gmail_watch")
    @patch("app.api.routes.auth_google.record_google_subject_revocation")
    @patch("app.api.routes.auth_google.get_user")
    @patch("app.api.routes.auth_google.get_current_user")
    def test_delete_google_data_serializes_cancel_and_purge(
        self,
        mock_current_user: Mock,
        mock_stored_user: Mock,
        mock_record_revocation: Mock,
        mock_stop: Mock,
        mock_revoke_google: Mock,
        mock_mark_disconnected: Mock,
        mock_cancel: Mock,
        mock_delete_token: Mock,
        mock_delete_mail_data: Mock,
    ) -> None:
        mock_current_user.return_value = SimpleNamespace(id="user-1")
        mock_stored_user.return_value = SimpleNamespace(id="user-1", google_sub="google-sub-owner")
        guard_state = {"subject": False, "user": False}
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "subject")
        self.mock_mail_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "user")
        mock_record_revocation.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"])
        mock_stop.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"])
        mock_revoke_google.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"]) or True
        mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_mail_data.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google/data")

        self.assertEqual(response.status_code, 204)
        mock_stop.assert_called_once_with(self.settings, user_id="user-1")
        mock_revoke_google.assert_called_once_with(self.settings, user_id="user-1")
        mock_mark_disconnected.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_cancel.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_delete_token.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_delete_mail_data.assert_called_once_with(self.settings.database_path, user_id="user-1")
        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        mock_record_revocation.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")

    @patch("app.api.routes.auth_google.delete_google_oauth_token")
    @patch("app.api.routes.auth_google.cancel_user_jobs")
    @patch("app.api.routes.auth_google.mark_google_disconnected")
    @patch("app.api.routes.auth_google.revoke_stored_google_token", return_value=True)
    @patch("app.api.routes.auth_google.stop_gmail_watch")
    @patch("app.api.routes.auth_google.record_google_subject_revocation")
    @patch("app.api.routes.auth_google.get_user")
    @patch("app.api.routes.auth_google.get_current_user")
    def test_disconnect_stops_watch_and_revokes_provider_token_before_local_cleanup(
        self,
        mock_current_user: Mock,
        mock_stored_user: Mock,
        mock_record_revocation: Mock,
        mock_stop: Mock,
        mock_revoke_google: Mock,
        mock_mark_disconnected: Mock,
        mock_cancel: Mock,
        mock_delete_token: Mock,
    ) -> None:
        mock_current_user.return_value = SimpleNamespace(id="user-1")
        mock_stored_user.return_value = SimpleNamespace(id="user-1", google_sub="google-sub-owner")
        guard_state = {"subject": False, "user": False}
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "subject")

        def enter_user_guard(*_args, **_kwargs):
            self.assertTrue(guard_state["subject"])
            return _recording_guard(guard_state, "user")

        self.mock_mail_guard.side_effect = enter_user_guard
        mock_record_revocation.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"])
        mock_stop.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"])
        mock_revoke_google.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["subject"]) or True
        mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google")

        self.assertEqual(response.status_code, 204)
        mock_stop.assert_called_once_with(self.settings, user_id="user-1")
        mock_revoke_google.assert_called_once_with(self.settings, user_id="user-1")
        mock_mark_disconnected.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_cancel.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_delete_token.assert_called_once_with(self.settings.database_path, user_id="user-1")
        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        mock_record_revocation.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")

    @patch("app.api.routes.auth_google.delete_user_mail_data")
    @patch("app.api.routes.auth_google.delete_google_oauth_token")
    @patch("app.api.routes.auth_google.cancel_user_jobs")
    @patch("app.api.routes.auth_google.mark_google_disconnected")
    @patch("app.api.routes.auth_google.revoke_stored_google_token", return_value=True)
    @patch("app.api.routes.auth_google.stop_gmail_watch")
    @patch("app.api.routes.auth_google.record_google_subject_revocation")
    @patch("app.api.routes.auth_google.get_user")
    @patch("app.api.routes.auth_google.get_current_user")
    def test_disconnect_delete_data_purges_mail_data_but_keeps_account_flow(
        self,
        mock_current_user: Mock,
        mock_stored_user: Mock,
        _mock_record_revocation: Mock,
        _mock_stop: Mock,
        _mock_revoke_google: Mock,
        _mock_mark_disconnected: Mock,
        _mock_cancel: Mock,
        _mock_delete_token: Mock,
        mock_delete_mail_data: Mock,
    ) -> None:
        mock_current_user.return_value = SimpleNamespace(id="user-1")
        mock_stored_user.return_value = SimpleNamespace(id="user-1", google_sub="google-sub-owner")
        guard_state = {"subject": False, "user": False}
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "subject")
        self.mock_mail_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(guard_state, "user")
        _mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        _mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        _mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_mail_data.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google?deleteData=true")

        self.assertEqual(response.status_code, 204)
        mock_delete_mail_data.assert_called_once_with(self.settings.database_path, user_id="user-1")
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")


class VerifiedGoogleIdentityTests(unittest.TestCase):
    def _userinfo_response(self, payload: object) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    @patch("app.services.integrations.google.urlopen")
    def test_userinfo_requires_verified_email_and_preserves_immutable_sub(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = self._userinfo_response(
            {
                "sub": "google-immutable-sub-123",
                "email": "Owner@Example.com",
                "email_verified": True,
                "name": "Mailbox Owner",
            }
        )

        identity = google_service.fetch_google_account_identity_from_credentials(
            SimpleNamespace(token="provider-access-token")
        )

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.google_sub, "google-immutable-sub-123")
        self.assertEqual(identity.profile.email, "owner@example.com")
        self.assertEqual(identity.profile.display_name, "Mailbox Owner")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://openidconnect.googleapis.com/v1/userinfo")
        self.assertEqual(request.headers["Authorization"], "Bearer provider-access-token")

    @patch("app.services.integrations.google.urlopen")
    def test_userinfo_fails_closed_for_unverified_or_malformed_identity(self, mock_urlopen: Mock) -> None:
        invalid_payloads = [
            {"sub": "google-sub", "email": "owner@example.com", "email_verified": False},
            {"sub": "google-sub", "email": "owner@example.com", "email_verified": "true"},
            {"email": "owner@example.com", "email_verified": True},
            {"sub": "google-sub", "email_verified": True},
            ["not", "an", "object"],
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                mock_urlopen.return_value = self._userinfo_response(payload)
                identity = google_service.fetch_google_account_identity_from_credentials(
                    SimpleNamespace(token="provider-access-token")
                )
                self.assertIsNone(identity)

    def test_repository_refuses_legacy_email_keyed_account_relink(self) -> None:
        legacy_row = {
            "id": "legacy-user",
            "email": "owner@example.com",
            "google_sub": "owner@example.com",
            "display_name": "Owner",
            "access_enabled": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        connection = SimpleNamespace(
            execute=Mock(
                side_effect=[
                    SimpleNamespace(fetchone=Mock(return_value=None)),
                    SimpleNamespace(fetchone=Mock(return_value=legacy_row)),
                ]
            )
        )

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            with self.assertRaises(repository.IdentityConflictError):
                repository.upsert_user(
                    "postgresql://example/db",
                    email="owner@example.com",
                    google_sub="verified-google-sub",
                )

        self.assertEqual(connection.execute.call_count, 2)

    def test_repository_updates_by_sub_when_verified_email_changes(self) -> None:
        existing_row = {
            "id": "user-1",
            "email": "old@example.com",
            "google_sub": "verified-google-sub",
            "display_name": "Owner",
            "access_enabled": True,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        updated_row = {**existing_row, "email": "new@example.com"}
        connection = SimpleNamespace(
            execute=Mock(
                side_effect=[
                    SimpleNamespace(fetchone=Mock(return_value=existing_row)),
                    SimpleNamespace(fetchone=Mock(return_value=None)),
                    SimpleNamespace(fetchone=Mock(return_value=None)),
                    SimpleNamespace(fetchone=Mock(return_value=updated_row)),
                ]
            )
        )

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            user = repository.upsert_user(
                "postgresql://example/db",
                email="NEW@example.com",
                google_sub="verified-google-sub",
                display_name="Owner",
            )

        self.assertEqual(user.id, "user-1")
        self.assertEqual(user.email, "new@example.com")
        insert_params = connection.execute.call_args_list[2].args[1]
        self.assertEqual(insert_params[0], "user-1")
        self.assertEqual(insert_params[1], "new@example.com")
        self.assertEqual(insert_params[3], "verified-google-sub")

    def test_repository_never_merges_two_verified_subjects_on_email_collision(self) -> None:
        subject_row = {"id": "user-1"}
        email_row = {"id": "user-2"}
        connection = SimpleNamespace(
            execute=Mock(
                side_effect=[
                    SimpleNamespace(fetchone=Mock(return_value=subject_row)),
                    SimpleNamespace(fetchone=Mock(return_value=email_row)),
                ]
            )
        )

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            with self.assertRaises(repository.IdentityConflictError):
                repository.upsert_user(
                    "postgresql://example/db",
                    email="collision@example.com",
                    google_sub="verified-google-sub",
                )

        self.assertEqual(connection.execute.call_count, 2)


class MobileLoginCodeRepositoryTests(unittest.TestCase):
    def test_login_code_exchange_is_one_atomic_update_returning_statement(self) -> None:
        row = {
            "code_hash": "hash-1",
            "user_id": "user-1",
            "expires_at": "2026-07-13T18:00:00+00:00",
            "consumed_at": "2026-07-13T17:00:00+00:00",
            "created_at": "2026-07-13T16:55:00+00:00",
        }
        result = SimpleNamespace(fetchone=Mock(return_value=row))
        connection = SimpleNamespace(execute=Mock(return_value=result))

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            consumed = repository.consume_mobile_login_code(
                "postgresql://example/db",
                code_hash="hash-1",
                now="2026-07-13T17:00:00+00:00",
            )

        self.assertIsNotNone(consumed)
        self.assertEqual(connection.execute.call_count, 1)
        sql, params = connection.execute.call_args.args
        self.assertIn("UPDATE mobile_login_codes", sql)
        self.assertIn("consumed_at IS NULL", sql)
        self.assertIn("RETURNING *", sql)
        self.assertEqual(
            params,
            ("2026-07-13T17:00:00+00:00", "hash-1", "2026-07-13T17:00:00+00:00"),
        )

    def test_verifier_bound_exchange_consumes_code_and_handoff_atomically(self) -> None:
        row = {
            "code_hash": "hash-1",
            "user_id": "user-1",
            "expires_at": "2026-07-13T18:00:00+00:00",
            "consumed_at": "2026-07-13T17:00:00+00:00",
            "created_at": "2026-07-13T16:55:00+00:00",
        }
        result = SimpleNamespace(fetchone=Mock(return_value=row))
        connection = SimpleNamespace(execute=Mock(return_value=result))

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            consumed = repository.consume_bound_mobile_login_code(
                "postgresql://example/db",
                handoff_id=HANDOFF_ID,
                exchange_code_challenge=CODE_CHALLENGE,
                code_hash="hash-1",
                now="2026-07-13T17:00:00+00:00",
            )

        self.assertIsNotNone(consumed)
        self.assertEqual(connection.execute.call_count, 1)
        sql, params = connection.execute.call_args.args
        self.assertIn("WITH eligible_handoff", sql)
        self.assertIn("exchange_code_challenge = ?", sql)
        self.assertIn("login_code_hash = ?", sql)
        self.assertIn("UPDATE mobile_login_codes", sql)
        self.assertIn("UPDATE mobile_oauth_handoffs", sql)
        self.assertIn("consumed_at IS NULL", sql)
        self.assertEqual(
            params,
            (
                HANDOFF_ID,
                "2026-07-13T17:00:00+00:00",
                CODE_CHALLENGE,
                "hash-1",
                "2026-07-13T17:00:00+00:00",
                "hash-1",
                "2026-07-13T17:00:00+00:00",
                "2026-07-13T17:00:00+00:00",
                HANDOFF_ID,
            ),
        )

    def test_consumed_handoff_is_not_delivered_again(self) -> None:
        result = SimpleNamespace(fetchone=Mock(return_value=None))
        connection = SimpleNamespace(execute=Mock(return_value=result))

        with patch.object(repository, "connect", return_value=nullcontext(connection)):
            handoff = repository.get_mobile_oauth_handoff(
                "postgresql://example/db",
                handoff_id=HANDOFF_ID,
                now="2026-07-13T17:00:00+00:00",
            )

        self.assertIsNone(handoff)
        sql, _params = connection.execute.call_args.args
        self.assertIn("consumed_at IS NULL", sql)


class SensitiveRequestLoggingTests(unittest.TestCase):
    def test_unmatched_path_uses_fixed_safe_structured_log_value(self) -> None:
        scope = {"path": f"/v1/auth/mobile/handoff/{HANDOFF_ID}"}

        self.assertEqual(_request_log_path(scope), "<unmatched>")

    def test_secret_bearing_404_path_never_appears_in_captured_logs(self) -> None:
        secret = "oauth-code-do-not-log"
        with self.assertLogs("electronic_mail.http", level=logging.INFO) as captured:
            response = TestClient(app).get(f"/missing/{secret}?token={secret}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(captured.records[-1].event_fields["path"], "<unmatched>")
        self.assertNotIn(secret, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
