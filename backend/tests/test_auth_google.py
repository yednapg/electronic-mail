from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api.routes import auth_google as auth_routes
from app.main import app


class GoogleAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.settings = SimpleNamespace(
            google_configured=True,
            cors_origin="http://localhost:5173",
            mobile_redirect_uri="decisionpipeline://auth/callback",
        )
        self.settings_patch = patch.object(auth_routes, "settings", self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

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
            params={"redirect_to": "decisionpipeline://auth/callback"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "https://accounts.google.com/o/oauth2/auth")
        mock_auth_url.assert_called_once_with(
            self.settings,
            redirect_to="decisionpipeline://auth/callback",
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
        mock_callback.return_value = "decisionpipeline://auth/callback"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "decisionpipeline://auth/callback")
        mock_callback.assert_called_once_with(self.settings, "code-1", "state-1")


if __name__ == "__main__":
    unittest.main()
