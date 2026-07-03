from __future__ import annotations

from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import auth_google as auth_routes
from app.main import app


class GoogleAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        auth_routes._mobile_handoffs.clear()
        self.addCleanup(auth_routes._mobile_handoffs.clear)
        self.settings = SimpleNamespace(
            google_configured=True,
            cors_origin="http://localhost:5173",
            web_app_url="http://localhost:5173",
            mobile_redirect_uri="electronicmail://auth/callback",
            backend_origin="http://localhost:3001",
            database_path="postgresql://example/db",
            is_production_like=False,
            session_cookie_domain="",
            session_cookie_samesite="lax",
        )
        self.settings_patch = patch.object(auth_routes, "settings", self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def _callback_result(self, *, redirect_to: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            redirect_to=redirect_to,
            tokens={"access_token": "token-1"},
            profile=SimpleNamespace(email="user@example.com", display_name="User"),
            google_sub="google-sub-1",
        )

    def _stored_user(self) -> SimpleNamespace:
        return SimpleNamespace(id="user-1", email="user@example.com", display_name="User")

    @patch("app.api.routes.auth_google.get_google_auth_url")
    def test_auth_google_preserves_web_oauth_flow(self, mock_auth_url: Mock) -> None:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        response = self.client.get("/auth/google", follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        mock_auth_url.assert_called_once_with(self.settings, redirect_to=None)

    @patch("app.api.routes.auth_google.get_google_auth_url")
    def test_auth_google_accepts_configured_mobile_redirect(self, mock_auth_url: Mock) -> None:
        mock_auth_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        response = self.client.get(
            "/auth/google",
            params={"redirect_to": "electronicmail://auth/callback"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        mock_auth_url.assert_called_once_with(
            self.settings,
            redirect_to="electronicmail://auth/callback",
        )

    def test_auth_google_rejects_unknown_redirect_targets(self) -> None:
        response = self.client.get(
            "/auth/google",
            params={"redirect_to": "otherapp://auth/callback"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)

    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_uses_service_redirect(self, mock_callback: Mock) -> None:
        mock_callback.return_value = "electronicmail://auth/callback"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "electronicmail://auth/callback")
        mock_callback.assert_called_once_with(self.settings, "code-1", "state-1")

    @patch("app.api.routes.auth_google.issue_session")
    @patch("app.api.routes.auth_google.enqueue_first_run")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.clear_google_guard_state")
    @patch("app.api.routes.auth_google.save_user_google_tokens")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_starts_web_inbox_preparation(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_save_tokens: Mock,
        mock_clear_guard: Mock,
        mock_watch: Mock,
        mock_enqueue: Mock,
        mock_issue_session: Mock,
    ) -> None:
        mock_callback.return_value = self._callback_result()
        mock_create_user.return_value = self._stored_user()
        mock_issue_session.return_value = SimpleNamespace(token="session-1")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "http://localhost:5173/post-login")
        mock_create_user.assert_called_once()
        mock_save_tokens.assert_called_once_with(
            self.settings,
            user_id="user-1",
            tokens={"access_token": "token-1"},
        )
        mock_clear_guard.assert_called_once_with(str(self.settings.database_path), user_id="user-1")
        mock_watch.assert_called_once_with(self.settings, user_id="user-1")
        mock_enqueue.assert_called_once_with(self.settings, user_id="user-1")
        mock_issue_session.assert_called_once_with(self.settings, user=self._stored_user(), platform="web")

    @patch("app.api.routes.auth_google.create_mobile_code")
    @patch("app.api.routes.auth_google.enqueue_first_run")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.clear_google_guard_state")
    @patch("app.api.routes.auth_google.save_user_google_tokens")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_starts_macos_deeplink_inbox_preparation(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        _mock_save_tokens: Mock,
        _mock_clear_guard: Mock,
        _mock_watch: Mock,
        mock_enqueue: Mock,
        mock_create_code: Mock,
    ) -> None:
        mock_callback.return_value = self._callback_result(redirect_to="electronicmail://auth/callback")
        mock_create_user.return_value = self._stored_user()
        mock_create_code.return_value = "login-code-1"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "electronicmail://auth/callback?login_code=login-code-1")
        mock_enqueue.assert_called_once_with(self.settings, user_id="user-1")
        mock_create_code.assert_called_once_with(self.settings, user_id="user-1")

    @patch("app.api.routes.auth_google.create_mobile_code")
    @patch("app.api.routes.auth_google.enqueue_first_run")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.clear_google_guard_state")
    @patch("app.api.routes.auth_google.save_user_google_tokens")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_starts_macos_handoff_inbox_preparation(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        _mock_save_tokens: Mock,
        _mock_clear_guard: Mock,
        _mock_watch: Mock,
        mock_enqueue: Mock,
        mock_create_code: Mock,
    ) -> None:
        handoff_url = "http://localhost:3001/auth/mobile/complete?handoff_id=handoff-1"
        mock_callback.return_value = self._callback_result(redirect_to=handoff_url)
        mock_create_user.return_value = self._stored_user()
        mock_create_code.return_value = "login-code-1"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], handoff_url)
        mock_enqueue.assert_called_once_with(self.settings, user_id="user-1")
        mock_create_code.assert_called_once_with(self.settings, user_id="user-1")
        self.assertEqual(auth_routes._mobile_handoffs["handoff-1"][0], "login-code-1")

    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_auth_google_callback_restarts_when_local_session_is_missing(self, mock_callback: Mock) -> None:
        mock_callback.side_effect = RuntimeError("Missing OAuth session. Start again from /auth/google.")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/auth/google")

    def test_mobile_complete_opens_registered_app_callback(self) -> None:
        auth_routes._mobile_handoffs["handoff-1"] = ("login-1", time() + 60)

        response = self.client.get(
            "/auth/mobile/complete",
            params={"handoff_id": "handoff-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Connected. Returning to Electronic Mail", response.text)
        self.assertIn("electronicmail://auth/callback?login_code=login-1", response.text)


if __name__ == "__main__":
    unittest.main()
