from __future__ import annotations

from contextlib import contextmanager, nullcontext
from io import BytesIO
import json
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

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


class GoogleOAuthUrlTests(unittest.TestCase):
    def test_auth_url_forces_account_selection_before_consent(self) -> None:
        flow = MagicMock(code_verifier="verifier")
        flow.authorization_url.return_value = ("https://accounts.google.com/oauth", "oauth-state")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        with (
            patch.object(google_service, "create_flow", return_value=flow),
            patch.object(google_service, "save_db_oauth_login_session") as save_session,
        ):
            result = google_service.get_google_auth_url(settings)

        self.assertEqual(result, "https://accounts.google.com/oauth")
        flow.authorization_url.assert_called_once_with(
            access_type="offline",
            prompt="select_account consent",
        )
        save_session.assert_called_once_with(
            "postgresql://example/db",
            state="oauth-state",
            code_verifier="verifier",
            redirect_to=None,
            expires_at=ANY,
        )


class GoogleAuthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.settings = SimpleNamespace(
            google_configured=True,
            cors_origin="http://localhost:5173",
            mobile_redirect_uri="decisionpipeline://auth/callback",
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
        self.revocation_guard_patch = patch.object(
            auth_routes,
            "has_active_google_token_revocation",
            return_value=False,
        )
        self.mock_revocation_guard = self.revocation_guard_patch.start()
        self.addCleanup(self.revocation_guard_patch.stop)
        self.subject_user_patch = patch.object(
            auth_routes,
            "get_user_by_google_subject",
            return_value=None,
        )
        self.mock_subject_user = self.subject_user_patch.start()
        self.addCleanup(self.subject_user_patch.stop)

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
            params={"redirect_to": "decisionpipeline://auth/callback"},
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
        mock_callback.return_value = "decisionpipeline://auth/callback"

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "code-1", "state": "state-1"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "decisionpipeline://auth/callback")
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
    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_allowlist_failure_stores_terminal_durable_handoff(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_cleanup_payload: Mock,
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
        mock_cleanup_payload.assert_called_once_with(
            self.settings,
            subject_hash=auth_routes.google_subject_tombstone_hash("google-sub-blocked"),
            tokens={"token": "secret-provider-token"},
            on_tracked=None,
        )
        self.assert_no_store(response)

    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_callback_started_before_account_deletion_cannot_recreate_user(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_cleanup_payload: Mock,
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
        mock_cleanup_payload.assert_called_once_with(
            self.settings,
            subject_hash=subject_hash,
            tokens={"token": "stale-provider-token"},
            on_tracked=None,
        )
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

    @patch("app.services.integrations.google.enqueue_job")
    @patch("app.services.integrations.google.urlopen")
    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_stale_callback_revocation_failure_does_not_leak_provider_tokens(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_create_user: Mock,
        _mock_delete_session: Mock,
        mock_urlopen: Mock,
        mock_enqueue: Mock,
    ) -> None:
        access_token = "stale-access-token-must-not-leak"
        refresh_token = "stale-refresh-token-must-not-leak"
        mock_session.return_value = SimpleNamespace(redirect_to=None, started_epoch=41)
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens={"token": access_token, "refresh_token": refresh_token},
            oauth_started_epoch=41,
        )
        self.mock_oauth_freshness.return_value = False
        mock_urlopen.side_effect = RuntimeError(f"provider rejected {refresh_token}")

        with self.assertLogs("electronic_mail.http", level=logging.INFO) as captured:
            response = self.client.get(
                "/auth/google/callback",
                params={"code": "stale-code", "state": "old-state"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/auth/google")
        mock_create_user.assert_not_called()
        mock_urlopen.assert_called_once()
        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "google_token_revoke")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://oauth2.googleapis.com/revoke")
        self.assertEqual(request.method, "POST")
        self.assertEqual(parse_qs(request.data.decode("ascii")), {"token": [refresh_token]})
        public_output = "\n".join([response.text, str(dict(response.headers)), *captured.output])
        self.assertNotIn(access_token, public_output)
        self.assertNotIn(refresh_token, public_output)

    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload", return_value=False)
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_callback_rejects_and_tracks_transient_grant_while_subject_revocation_is_active(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_cleanup_payload: Mock,
    ) -> None:
        tokens = {"token": "new-access-token", "refresh_token": "new-refresh-token"}
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens=tokens,
            oauth_started_epoch=42,
        )
        self.mock_revocation_guard.return_value = True

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "fresh-code"},
            follow_redirects=False,
        )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 400)
        mock_create_user.assert_not_called()
        self.mock_revocation_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )
        mock_cleanup_payload.assert_called_once_with(
            self.settings,
            subject_hash=subject_hash,
            tokens=tokens,
            on_tracked=None,
        )

    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload", return_value=False)
    @patch("app.api.routes.auth_google.delete_oauth_login_session", side_effect=RuntimeError("delete failed"))
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_session_cleanup_failure_keeps_verified_callback_grant_durably_tracked(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_delete_session: Mock,
        mock_cleanup_payload: Mock,
    ) -> None:
        tokens = {"token": "new-access-token", "refresh_token": "new-refresh-token"}
        mock_session.return_value = SimpleNamespace(redirect_to=None, started_epoch=42)
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens=tokens,
            oauth_started_epoch=42,
        )

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "fresh-code", "state": "state-1"},
            follow_redirects=False,
        )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 400)
        mock_delete_session.assert_called_once_with(self.settings.database_path, state="state-1")
        mock_create_user.assert_not_called()
        mock_cleanup_payload.assert_called_once_with(
            self.settings,
            subject_hash=subject_hash,
            tokens=tokens,
            on_tracked=None,
        )

    def test_callback_tracking_failure_cleans_owned_grant_before_subject_unlock(self) -> None:
        tokens = {"token": "new-access-token", "refresh_token": "new-refresh-token"}
        subject_state = {"active": False}
        self.mock_subject_guard.side_effect = lambda *_args, **_kwargs: _recording_guard(subject_state)

        def assert_subject_owned(*_args, **_kwargs) -> None:
            self.assertTrue(subject_state["active"])

        with (
            patch.object(
                auth_routes,
                "handle_google_callback",
                return_value=SimpleNamespace(
                    redirect_to=None,
                    profile=DashboardProfile(email="blocked@example.com", display_name="Blocked"),
                    google_sub="google-sub-blocked",
                    tokens=tokens,
                    oauth_started_epoch=42,
                ),
            ),
            patch.object(
                auth_routes,
                "create_or_update_user",
                side_effect=HTTPException(status_code=403, detail="blocked"),
            ),
            patch.object(
                auth_routes,
                "revoke_or_enqueue_google_token_payload",
                side_effect=RuntimeError("queue unavailable"),
            ) as mock_track,
            patch.object(
                auth_routes,
                "_cleanup_unpersisted_callback_grant",
                side_effect=assert_subject_owned,
            ) as mock_fallback,
        ):
            response = self.client.get(
                "/auth/google/callback",
                params={"code": "fresh-code"},
                follow_redirects=False,
            )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-blocked")
        self.assertEqual(response.status_code, 403)
        mock_track.assert_called_once_with(
            self.settings,
            subject_hash=subject_hash,
            tokens=tokens,
            on_tracked=None,
        )
        mock_fallback.assert_called_once_with(
            database_url=self.settings.database_path,
            subject_hash=subject_hash,
            tokens=tokens,
            user_id=None,
        )
        self.mock_subject_guard.assert_called_once_with(
            self.settings.database_path,
            subject_hash=subject_hash,
        )

    def test_callback_subject_lock_contention_queues_cleanup_without_unordered_revocation(self) -> None:
        tokens = {"token": "new-access-token", "refresh_token": "new-refresh-token"}
        self.mock_subject_guard.side_effect = auth_routes.AdvisoryLockUnavailable("busy")

        with (
            patch.object(
                auth_routes,
                "handle_google_callback",
                return_value=SimpleNamespace(
                    redirect_to=None,
                    profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
                    google_sub="google-sub-owner",
                    tokens=tokens,
                    oauth_started_epoch=42,
                ),
            ),
            patch.object(
                auth_routes,
                "enqueue_google_token_revocation_payload",
                side_effect=RuntimeError("queue unavailable"),
            ) as mock_enqueue,
            patch.object(auth_routes, "revoke_google_token_payload") as mock_direct_revoke,
            patch.object(auth_routes, "revoke_or_enqueue_google_token_payload") as mock_track_and_revoke,
        ):
            response = self.client.get(
                "/auth/google/callback",
                params={"code": "fresh-code"},
                follow_redirects=False,
            )

        subject_hash = auth_routes.google_subject_tombstone_hash("google-sub-owner")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.mock_subject_guard.call_count, 2)
        mock_enqueue.assert_called_once_with(
            self.settings,
            subject_hash=subject_hash,
            tokens=tokens,
        )
        mock_direct_revoke.assert_not_called()
        mock_track_and_revoke.assert_not_called()

    @patch("app.api.routes.auth_google.delete_google_oauth_token")
    @patch("app.api.routes.auth_google.mark_google_disconnected")
    @patch("app.api.routes.auth_google.cancel_user_jobs")
    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.reconnect_google_oauth_token", side_effect=RuntimeError("save failed"))
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_callback_tracks_payload_and_disconnects_existing_grant_when_token_save_fails(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        _mock_reconnect: Mock,
        mock_watch: Mock,
        mock_cleanup_payload: Mock,
        mock_cancel_jobs: Mock,
        mock_mark_disconnected: Mock,
        mock_delete_token: Mock,
    ) -> None:
        tokens = {"token": "unpersisted-access-token", "refresh_token": "unpersisted-refresh-token"}
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens=tokens,
            oauth_started_epoch=42,
        )
        mock_create_user.return_value = SimpleNamespace(id="user-1")
        mock_cleanup_payload.side_effect = lambda *_args, **kwargs: kwargs["on_tracked"]() or False

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "fresh-code"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        mock_cleanup_payload.assert_called_once_with(
            self.settings,
            subject_hash=auth_routes.google_subject_tombstone_hash("google-sub-owner"),
            tokens=tokens,
            on_tracked=ANY,
        )
        mock_delete_token.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_mark_disconnected.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_cancel_jobs.assert_called_once_with(self.settings.database_path, user_id="user-1")
        mock_watch.assert_not_called()

    @patch("app.api.routes.auth_google.revoke_or_enqueue_google_token_payload")
    @patch("app.api.routes.auth_google.ensure_gmail_watch", side_effect=RuntimeError("watch failed"))
    @patch("app.api.routes.auth_google.reconnect_google_oauth_token")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    def test_callback_never_revokes_payload_after_token_save_succeeds(
        self,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_reconnect: Mock,
        _mock_watch: Mock,
        mock_cleanup_payload: Mock,
    ) -> None:
        tokens = {"token": "persisted-access-token", "refresh_token": "persisted-refresh-token"}
        mock_callback.return_value = SimpleNamespace(
            redirect_to=None,
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
            tokens=tokens,
            oauth_started_epoch=42,
        )
        mock_create_user.return_value = SimpleNamespace(id="user-1")

        response = self.client.get(
            "/auth/google/callback",
            params={"code": "fresh-code"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        mock_reconnect.assert_called_once()
        mock_cleanup_payload.assert_not_called()

    @patch("app.api.routes.auth_google.issue_session")
    @patch("app.api.routes.auth_google.enqueue_first_run")
    @patch("app.api.routes.auth_google.ensure_gmail_watch")
    @patch("app.api.routes.auth_google.delete_oauth_login_session")
    @patch("app.api.routes.auth_google.encrypt_json", return_value="encrypted-provider-token")
    @patch("app.api.routes.auth_google.reconnect_google_oauth_token")
    @patch("app.api.routes.auth_google.create_or_update_user")
    @patch("app.api.routes.auth_google.handle_google_callback")
    @patch("app.api.routes.auth_google.get_oauth_login_session")
    def test_fresh_callback_serializes_subject_then_user_before_saving_tokens(
        self,
        mock_session: Mock,
        mock_callback: Mock,
        mock_create_user: Mock,
        mock_reconnect: Mock,
        _mock_encrypt: Mock,
        mock_delete_session: Mock,
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
        mock_reconnect.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(state["subject"]),
            self.assertTrue(state["user"]),
        )
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
        mock_delete_session.assert_called_once_with(self.settings.database_path, state="new-state")
        mock_reconnect.assert_called_once_with(
            self.settings.database_path,
            user_id="user-1",
            token_json_encrypted="encrypted-provider-token",
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
        self.assertIn("decisionpipeline://auth/callback?login_code=login-1", response.text)
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
    @patch("app.api.routes.auth_google.revoke_or_enqueue_stored_google_token", return_value=False)
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

        def provider_unavailable(*_args, **_kwargs):
            assert_nested_guards()
            raise OSError("provider unavailable")

        self.mock_mail_guard.side_effect = enter_user_guard
        _mock_stop.side_effect = provider_unavailable
        _mock_revoke_google.side_effect = lambda *_args, **_kwargs: assert_nested_guards() or False
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
    @patch("app.api.routes.auth_google.revoke_or_enqueue_stored_google_token", return_value=True)
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
        mock_stop.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(guard_state["subject"]),
            self.assertTrue(guard_state["user"]),
        )
        mock_revoke_google.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(guard_state["subject"]),
            self.assertTrue(guard_state["user"]),
        ) or True
        mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_mail_data.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google/data")

        self.assertEqual(response.status_code, 204)
        mock_stop.assert_called_once_with(self.settings, user_id="user-1")
        mock_revoke_google.assert_called_once_with(
            self.settings,
            user_id="user-1",
            subject_hash=auth_routes.google_subject_tombstone_hash("google-sub-owner"),
        )
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
    @patch("app.api.routes.auth_google.revoke_or_enqueue_stored_google_token", return_value=True)
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
        mock_stop.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(guard_state["subject"]),
            self.assertTrue(guard_state["user"]),
        )
        mock_revoke_google.side_effect = lambda *_args, **_kwargs: (
            self.assertTrue(guard_state["subject"]),
            self.assertTrue(guard_state["user"]),
        ) or True
        mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google")

        self.assertEqual(response.status_code, 204)
        mock_stop.assert_called_once_with(self.settings, user_id="user-1")
        mock_revoke_google.assert_called_once_with(
            self.settings,
            user_id="user-1",
            subject_hash=auth_routes.google_subject_tombstone_hash("google-sub-owner"),
        )
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
    @patch("app.api.routes.auth_google.revoke_or_enqueue_stored_google_token", return_value=True)
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
        _mock_stop.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        _mock_revoke_google.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"]) or True
        _mock_mark_disconnected.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        _mock_cancel.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        _mock_delete_token.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])
        mock_delete_mail_data.side_effect = lambda *_args, **_kwargs: self.assertTrue(guard_state["user"])

        response = self.client.delete("/v1/auth/google?deleteData=true")

        self.assertEqual(response.status_code, 204)
        mock_delete_mail_data.assert_called_once_with(self.settings.database_path, user_id="user-1")
        self.mock_mail_guard.assert_called_once_with(self.settings.database_path, user_id="user-1")


class GoogleTokenRevocationTests(unittest.TestCase):
    def test_provider_http_holds_deletion_barrier_across_authorized_request(self) -> None:
        events: list[str] = []

        @contextmanager
        def guard(_database_url: str, *, user_id: str):
            self.assertEqual(user_id, "user-1")
            events.append("guard-enter")
            try:
                yield
            finally:
                events.append("guard-exit")

        authorized_http = SimpleNamespace(
            request=lambda *_args, **_kwargs: events.append("request") or ("response", b"body")
        )
        guarded = google_service._ProviderGuardedHttp(
            authorized_http,
            database_url="postgresql://example/db",
            user_id="user-1",
        )

        with patch("app.services.integrations.google.shared_user_mail_lock", side_effect=guard):
            result = guarded.request("https://gmail.googleapis.test/messages")

        self.assertEqual(result, ("response", b"body"))
        self.assertEqual(events, ["guard-enter", "request", "guard-exit"])

    @patch("app.services.integrations.google.urlopen")
    def test_transient_payload_revokes_refresh_token_with_post_body(self, mock_urlopen: Mock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b""
        mock_urlopen.return_value = response

        revoked = google_service.revoke_google_token_payload(
            {"token": "transient-access-token", "refresh_token": "transient-refresh-token"}
        )

        self.assertTrue(revoked)
        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://oauth2.googleapis.com/revoke")
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            parse_qs(request.data.decode("ascii")),
            {"token": ["transient-refresh-token"]},
        )
        self.assertEqual(mock_urlopen.call_args.kwargs, {"timeout": 10})

    @patch("app.services.integrations.google.urlopen")
    def test_already_revoked_invalid_token_response_completes_cleanup(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = HTTPError(
            "https://oauth2.googleapis.com/revoke",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"error":"invalid_token"}'),
        )

        revoked = google_service.revoke_google_token_payload({"refresh_token": "already-revoked"})

        self.assertTrue(revoked)

    @patch("app.services.integrations.google.urlopen")
    def test_other_provider_400_remains_pending(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = HTTPError(
            "https://oauth2.googleapis.com/revoke",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"error":"invalid_request"}'),
        )

        revoked = google_service.revoke_google_token_payload({"refresh_token": "still-uncertain"})

        self.assertFalse(revoked)

    @patch("app.services.integrations.google.revoke_google_token_payload", return_value=True)
    @patch("app.services.integrations.google.decrypt_json")
    @patch("app.services.integrations.google.get_google_oauth_token")
    def test_stored_revocation_reuses_payload_helper(
        self,
        mock_get_token: Mock,
        mock_decrypt: Mock,
        mock_revoke_payload: Mock,
    ) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        mock_get_token.return_value = SimpleNamespace(token_json_encrypted="encrypted")
        payload = {"token": "stored-access-token", "refresh_token": "stored-refresh-token"}
        mock_decrypt.return_value = payload

        revoked = google_service.revoke_stored_google_token(settings, user_id="user-1")

        self.assertTrue(revoked)
        mock_get_token.assert_called_once_with(settings.database_path, user_id="user-1")
        mock_decrypt.assert_called_once_with(settings, "encrypted")
        mock_revoke_payload.assert_called_once_with(payload)

    def test_failed_provider_revocation_is_durably_queued_as_encrypted_material(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        encrypted = "encrypted-token-payload"
        tokens = {"token": "private-access-token", "refresh_token": "private-refresh-token"}
        events: list[str] = []
        with (
            patch.object(
                google_service,
                "get_google_oauth_token",
                return_value=SimpleNamespace(token_json_encrypted=encrypted),
            ),
            patch.object(google_service, "decrypt_json", return_value=tokens),
            patch.object(
                google_service,
                "enqueue_job",
                side_effect=lambda *_args, **_kwargs: events.append("enqueue")
                or SimpleNamespace(id="job-1"),
            ) as mock_enqueue,
            patch.object(
                google_service,
                "exclusive_google_subject_lock",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch.object(
                google_service,
                "get_job",
                return_value=SimpleNamespace(id="job-1", status="queued"),
            ),
            patch.object(
                google_service,
                "_revoke_google_token_payload_outcome",
                side_effect=lambda *_args, **_kwargs: events.append("revoke")
                or google_service._GoogleTokenRevocationOutcome.RETRY,
            ),
            patch.object(google_service, "record_google_subject_revocation") as mock_barrier,
            patch.object(google_service, "complete_google_token_revocations_for_subject") as mock_complete_subject,
            patch.object(google_service, "complete_google_token_revocation_job") as mock_complete_job,
        ):
            revoked = google_service.revoke_or_enqueue_stored_google_token(
                settings,
                user_id="user-1",
                subject_hash="subject-hash",
            )

        self.assertFalse(revoked)
        self.assertEqual(events, ["enqueue", "revoke"])
        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["kind"], "google_token_revoke")
        self.assertEqual(kwargs["queue"], "critical")
        self.assertEqual(kwargs["priority"], 100)
        self.assertEqual(kwargs["max_attempts"], google_service.GOOGLE_TOKEN_REVOCATION_MAX_ATTEMPTS)
        self.assertEqual(
            kwargs["payload"],
            {"subject_hash": "subject-hash", "token_json_encrypted": encrypted},
        )
        self.assertTrue(kwargs["dedupe_key"].startswith("google-token-revoke:subject-hash:"))
        self.assertNotIn(tokens["token"], str(mock_enqueue.call_args))
        self.assertNotIn(tokens["refresh_token"], str(mock_enqueue.call_args))
        mock_barrier.assert_not_called()
        mock_complete_subject.assert_not_called()
        mock_complete_job.assert_not_called()

    def test_transient_revocation_is_tracked_before_provider_and_resolves_subject_jobs(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        tokens = {"token": "new-access", "refresh_token": "new-refresh"}
        events: list[str] = []
        with (
            patch.object(google_service, "encrypt_json", return_value="encrypted-new-grant"),
            patch.object(
                google_service,
                "enqueue_job",
                side_effect=lambda *_args, **_kwargs: events.append("enqueue")
                or SimpleNamespace(id="job-1"),
            ) as mock_enqueue,
            patch.object(
                google_service,
                "exclusive_google_subject_lock",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch.object(
                google_service,
                "get_job",
                return_value=SimpleNamespace(id="job-1", status="queued"),
            ),
            patch.object(
                google_service,
                "_revoke_google_token_payload_outcome",
                side_effect=lambda *_args, **_kwargs: events.append("revoke")
                or google_service._GoogleTokenRevocationOutcome.PROJECT_REVOKED,
            ),
            patch.object(
                google_service,
                "record_google_subject_revocation",
                side_effect=lambda *_args, **_kwargs: events.append("barrier"),
            ) as mock_barrier,
            patch.object(
                google_service,
                "complete_google_token_revocations_for_subject",
                side_effect=lambda *_args, **_kwargs: events.append("complete"),
            ) as mock_complete_subject,
            patch.object(google_service, "complete_google_token_revocation_job") as mock_complete_job,
        ):
            revoked = google_service.revoke_or_enqueue_google_token_payload(
                settings,
                subject_hash="subject-hash",
                tokens=tokens,
            )

        self.assertTrue(revoked)
        self.assertEqual(events, ["enqueue", "revoke", "barrier", "complete"])
        self.assertEqual(
            mock_enqueue.call_args.kwargs["payload"],
            {"subject_hash": "subject-hash", "token_json_encrypted": "encrypted-new-grant"},
        )
        mock_complete_subject.assert_called_once_with(
            settings.database_path,
            subject_hash="subject-hash",
        )
        mock_barrier.assert_called_once_with(settings.database_path, subject_hash="subject-hash")
        mock_complete_job.assert_not_called()

    def test_invalid_credential_completes_only_its_own_revocation_job(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        tokens = {"refresh_token": "expired-refresh-token"}
        with (
            patch.object(google_service, "encrypt_json", return_value="encrypted-expired-grant"),
            patch.object(google_service, "enqueue_job", return_value=SimpleNamespace(id="job-expired")),
            patch.object(
                google_service,
                "exclusive_google_subject_lock",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch.object(
                google_service,
                "get_job",
                return_value=SimpleNamespace(id="job-expired", status="queued"),
            ),
            patch.object(
                google_service,
                "_revoke_google_token_payload_outcome",
                return_value=google_service._GoogleTokenRevocationOutcome.CREDENTIAL_INVALID,
            ),
            patch.object(google_service, "record_google_subject_revocation") as mock_barrier,
            patch.object(google_service, "complete_google_token_revocations_for_subject") as mock_complete_subject,
            patch.object(google_service, "complete_google_token_revocation_job") as mock_complete_job,
        ):
            revoked = google_service.revoke_or_enqueue_google_token_payload(
                settings,
                subject_hash="subject-hash",
                tokens=tokens,
            )

        self.assertTrue(revoked)
        mock_barrier.assert_called_once_with(settings.database_path, subject_hash="subject-hash")
        mock_complete_job.assert_called_once_with(settings.database_path, job_id="job-expired")
        mock_complete_subject.assert_not_called()

    def test_worker_confirmation_advances_subject_barrier_before_completing_siblings(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        tokens = {"refresh_token": "queued-refresh-token"}
        events: list[str] = []

        @contextmanager
        def subject_guard(*_args, **_kwargs):
            events.append("subject-enter")
            try:
                yield
            finally:
                events.append("subject-exit")

        with (
            patch.object(google_service, "decrypt_json", return_value=tokens),
            patch.object(google_service, "exclusive_google_subject_lock", side_effect=subject_guard),
            patch.object(
                google_service,
                "get_job",
                return_value=SimpleNamespace(id="job-1", status="running"),
            ),
            patch.object(
                google_service,
                "_revoke_google_token_payload_outcome",
                side_effect=lambda *_args, **_kwargs: events.append("provider-confirmed")
                or google_service._GoogleTokenRevocationOutcome.PROJECT_REVOKED,
            ),
            patch.object(
                google_service,
                "record_google_subject_revocation",
                side_effect=lambda *_args, **_kwargs: events.append("barrier"),
            ) as mock_barrier,
            patch.object(
                google_service,
                "complete_google_token_revocations_for_subject",
                side_effect=lambda *_args, **_kwargs: events.append("complete-subject"),
            ) as mock_complete_subject,
            patch.object(google_service, "complete_google_token_revocation_job") as mock_complete_job,
        ):
            google_service.retry_encrypted_google_token_revocation(
                settings,
                job_id="job-1",
                subject_hash="subject-hash",
                token_json_encrypted="encrypted-token-payload",
            )

        self.assertEqual(
            events,
            ["subject-enter", "provider-confirmed", "barrier", "complete-subject", "subject-exit"],
        )
        mock_barrier.assert_called_once_with(settings.database_path, subject_hash="subject-hash")
        mock_complete_subject.assert_called_once_with(settings.database_path, subject_hash="subject-hash")
        mock_complete_job.assert_not_called()

    def test_worker_invalid_credential_keeps_sibling_revocations_active(self) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        with (
            patch.object(google_service, "decrypt_json", return_value={"refresh_token": "expired"}),
            patch.object(
                google_service,
                "exclusive_google_subject_lock",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
            patch.object(
                google_service,
                "get_job",
                return_value=SimpleNamespace(id="job-expired", status="running"),
            ),
            patch.object(
                google_service,
                "_revoke_google_token_payload_outcome",
                return_value=google_service._GoogleTokenRevocationOutcome.CREDENTIAL_INVALID,
            ),
            patch.object(google_service, "record_google_subject_revocation") as mock_barrier,
            patch.object(google_service, "complete_google_token_revocations_for_subject") as mock_complete_subject,
            patch.object(google_service, "complete_google_token_revocation_job") as mock_complete_job,
        ):
            google_service.retry_encrypted_google_token_revocation(
                settings,
                job_id="job-expired",
                subject_hash="subject-hash",
                token_json_encrypted="encrypted-token-payload",
            )

        mock_barrier.assert_called_once_with(settings.database_path, subject_hash="subject-hash")
        mock_complete_job.assert_called_once_with(settings.database_path, job_id="job-expired")
        mock_complete_subject.assert_not_called()

    @patch("app.services.integrations.google.revoke_google_token_payload")
    @patch(
        "app.services.integrations.google.persist_token_payload",
        side_effect=google_service.UserMailWorkBlocked("disconnected"),
    )
    @patch("app.services.integrations.google.Credentials.from_authorized_user_info")
    @patch("app.services.integrations.google.decrypt_json")
    @patch("app.services.integrations.google.get_google_oauth_token")
    def test_refreshed_payload_is_revoked_when_guard_rejects_persistence(
        self,
        mock_get_token: Mock,
        mock_decrypt: Mock,
        mock_from_info: Mock,
        _mock_persist: Mock,
        mock_revoke: Mock,
    ) -> None:
        settings = SimpleNamespace(
            google_configured=True,
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        mock_get_token.return_value = SimpleNamespace(token_json_encrypted="encrypted")
        mock_decrypt.return_value = {
            "token": "expired-access-token",
            "refresh_token": "refresh-token",
            "scopes": [google_service.GMAIL_FULL_SCOPE],
        }
        credentials = SimpleNamespace(
            expired=True,
            token="refreshed-access-token",
            refresh_token="refresh-token",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="client-id",
            client_secret="client-secret",
            scopes=[google_service.GMAIL_FULL_SCOPE],
            expiry=None,
            refresh=Mock(),
        )
        mock_from_info.return_value = credentials

        result = google_service._load_authorized_credentials(
            settings,
            user_id="user-1",
            refresh_expired=True,
            persist_updates=True,
        )

        self.assertFalse(result.status.connected)
        self.assertIsInstance(credentials.refresh.call_args.args[0], google_service._BoundedGoogleAuthRequest)
        mock_revoke.assert_called_once_with(
            {
                "token": "refreshed-access-token",
                "refresh_token": "refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": [google_service.GMAIL_FULL_SCOPE],
                "expiry": None,
            }
        )

    @patch("app.services.integrations.google.revoke_google_token_payload")
    @patch("app.services.integrations.google.fetch_google_account_identity_from_credentials", return_value=None)
    @patch("app.services.integrations.google.token_payload_from_credentials")
    @patch("app.services.integrations.google.create_flow")
    @patch("app.services.integrations.google.get_oauth_login_session")
    def test_unverified_callback_identity_revokes_unpersisted_payload(
        self,
        mock_session: Mock,
        mock_create_flow: Mock,
        mock_token_payload: Mock,
        _mock_identity: Mock,
        mock_revoke_payload: Mock,
    ) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        mock_session.return_value = SimpleNamespace(
            state="state-1",
            code_verifier="verifier-1",
            redirect_to=None,
            started_epoch=1,
        )
        credentials = SimpleNamespace(token="transient-access-token")
        mock_create_flow.return_value = SimpleNamespace(
            code_verifier=None,
            fetch_token=Mock(),
            credentials=credentials,
        )
        payload = {"token": "transient-access-token", "refresh_token": "transient-refresh-token"}
        mock_token_payload.return_value = payload

        with self.assertRaisesRegex(RuntimeError, "identity could not be verified"):
            google_service.handle_google_callback(settings, "callback-code", "state-1")

        mock_revoke_payload.assert_called_once_with(payload)

    @patch("app.services.integrations.google.revoke_google_token_payload")
    @patch("app.services.integrations.google.delete_db_oauth_login_session")
    @patch("app.services.integrations.google.fetch_google_account_identity_from_credentials")
    @patch("app.services.integrations.google.token_payload_from_credentials")
    @patch("app.services.integrations.google.create_flow")
    @patch("app.services.integrations.google.get_oauth_login_session")
    def test_verified_callback_result_transfers_session_and_token_cleanup_to_route(
        self,
        mock_session: Mock,
        mock_create_flow: Mock,
        mock_token_payload: Mock,
        mock_identity: Mock,
        mock_delete_session: Mock,
        mock_revoke_payload: Mock,
    ) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        mock_session.return_value = SimpleNamespace(
            state="state-1",
            code_verifier="verifier-1",
            redirect_to=None,
            started_epoch=1,
        )
        credentials = SimpleNamespace(token="transient-access-token")
        mock_create_flow.return_value = SimpleNamespace(
            code_verifier=None,
            fetch_token=Mock(),
            credentials=credentials,
        )
        payload = {"token": "transient-access-token", "refresh_token": "transient-refresh-token"}
        mock_token_payload.return_value = payload
        mock_identity.return_value = SimpleNamespace(
            profile=DashboardProfile(email="owner@example.com", display_name="Owner"),
            google_sub="google-sub-owner",
        )

        result = google_service.handle_google_callback(settings, "callback-code", "state-1")

        self.assertEqual(result.google_sub, "google-sub-owner")
        self.assertEqual(result.tokens, payload)
        mock_delete_session.assert_not_called()
        mock_revoke_payload.assert_not_called()

    @patch("app.services.integrations.google.build_google_service")
    @patch("app.services.integrations.google._load_authorized_credentials")
    def test_stop_watch_loads_credentials_without_persisted_refresh(
        self,
        mock_load_credentials: Mock,
        mock_build_service: Mock,
    ) -> None:
        settings = SimpleNamespace()
        credentials = object()
        mock_load_credentials.return_value = SimpleNamespace(credentials=credentials)
        stop = Mock()
        mock_build_service.return_value = SimpleNamespace(
            users=Mock(return_value=SimpleNamespace(stop=Mock(return_value=SimpleNamespace(execute=stop))))
        )

        google_service.stop_gmail_watch(settings, user_id="user-1")

        mock_load_credentials.assert_called_once_with(
            settings,
            user_id="user-1",
            refresh_expired=False,
            persist_updates=False,
        )
        mock_build_service.assert_called_once_with("gmail", "v1", credentials)
        stop.assert_called_once_with()

    @patch("app.services.integrations.google.build_google_service")
    @patch("app.services.integrations.google.create_authorized_credentials")
    def test_normal_gmail_service_keeps_persisted_refresh_enabled(
        self,
        mock_credentials: Mock,
        _mock_build_service: Mock,
    ) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        mock_credentials.return_value = object()

        google_service.create_gmail_service(settings, user_id="user-1")

        mock_credentials.assert_called_once_with(
            settings,
            user_id="user-1",
            refresh_expired=True,
            persist_updates=True,
        )

    @patch("app.services.integrations.google.persist_token_payload")
    @patch("app.services.integrations.google.Credentials.from_authorized_user_info")
    @patch("app.services.integrations.google.decrypt_json")
    @patch("app.services.integrations.google.get_google_oauth_token")
    @patch("app.services.integrations.google.shared_user_mail_lock", return_value=nullcontext())
    def test_read_only_load_does_not_persist_nonexpired_normalization(
        self,
        _mock_guard: Mock,
        mock_get_token: Mock,
        mock_decrypt: Mock,
        mock_from_info: Mock,
        mock_persist: Mock,
    ) -> None:
        settings = SimpleNamespace(
            google_configured=True,
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        mock_get_token.return_value = SimpleNamespace(token_json_encrypted="encrypted")
        mock_decrypt.return_value = {
            "token": "access-token",
            "refresh_token": "refresh-token",
            "scopes": [google_service.GMAIL_FULL_SCOPE],
        }
        credentials = SimpleNamespace(expired=False, refresh_token="refresh-token")
        mock_from_info.return_value = credentials

        loaded = google_service.create_authorized_credentials(
            settings,
            user_id="user-1",
            refresh_expired=False,
            persist_updates=False,
        )

        self.assertIs(loaded, credentials)
        mock_persist.assert_not_called()

    @patch("app.services.integrations.google.persist_token_payload")
    @patch("app.services.integrations.google.Credentials.from_authorized_user_info")
    @patch("app.services.integrations.google.decrypt_json")
    @patch("app.services.integrations.google.get_google_oauth_token")
    @patch("app.services.integrations.google.shared_user_mail_lock", return_value=nullcontext())
    def test_read_only_load_does_not_refresh_or_persist_expired_credentials(
        self,
        _mock_guard: Mock,
        mock_get_token: Mock,
        mock_decrypt: Mock,
        mock_from_info: Mock,
        mock_persist: Mock,
    ) -> None:
        settings = SimpleNamespace(
            google_configured=True,
            database_path="postgresql://example/db",
            google_client_id="client-id",
            google_client_secret="client-secret",
        )
        mock_get_token.return_value = SimpleNamespace(token_json_encrypted="encrypted")
        mock_decrypt.return_value = {
            "token": "expired-access-token",
            "refresh_token": "refresh-token",
            "scopes": [google_service.GMAIL_FULL_SCOPE],
        }
        refresh = Mock()
        credentials = SimpleNamespace(expired=True, refresh_token="refresh-token", refresh=refresh)
        mock_from_info.return_value = credentials

        loaded = google_service.create_authorized_credentials(
            settings,
            user_id="user-1",
            refresh_expired=False,
            persist_updates=False,
        )

        self.assertIs(loaded, credentials)
        refresh.assert_not_called()
        mock_persist.assert_not_called()


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
