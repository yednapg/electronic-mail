from __future__ import annotations

"""App-owned auth/session helpers for web and iOS users."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import secrets

from fastapi import HTTPException, Request, status

from app.core.config import Settings
from app.db.models import StoredUser
from app.db.user_mail_guard import UserMailWorkBlocked
from app.db.repository import (
    DEFAULT_USER_ID,
    IdentityConflictError,
    consume_bound_mobile_login_code,
    create_app_session,
    create_mobile_login_code,
    get_active_app_session,
    get_google_oauth_token,
    get_user,
    get_user_by_email,
    is_allowed_email,
    revoke_app_session,
    upsert_google_oauth_token,
    upsert_user,
)
from app.schemas.domain import DashboardProfile, GoogleAuthState
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    check_user_google_credentials,
    missing_google_scopes,
)
from app.services.token_crypto import decrypt_json, encrypt_json


SESSION_COOKIE_NAME = "dp_session"
WEB_SESSION_DAYS = 30
MOBILE_SESSION_DAYS = 90
MOBILE_LOGIN_CODE_MINUTES = 5


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated app user available to request handlers."""

    id: str
    email: str
    display_name: str | None
    legacy_local: bool = False

    @property
    def profile(self) -> DashboardProfile:
        return DashboardProfile(email=self.email, display_name=self.display_name)


@dataclass(frozen=True)
class IssuedSession:
    """Raw session token plus metadata for client handoff."""

    token: str
    expires_at: str
    user: CurrentUser


def require_current_user(settings: Settings, request: Request) -> CurrentUser:
    """Return the current authenticated user or raise 401."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def get_current_user(settings: Settings, request: Request) -> CurrentUser | None:
    """Resolve a web cookie or iOS bearer token to a current user."""
    token = request.cookies.get(SESSION_COOKIE_NAME) or _bearer_token(request)
    if token:
        session = get_active_app_session(
            str(settings.database_path),
            token_hash=hash_token(settings, token),
            now=_utc_now_iso(),
        )
        if session is not None:
            user = get_user(str(settings.database_path), session.user_id)
            if user is not None and user.access_enabled:
                return CurrentUser(id=user.id, email=user.email, display_name=user.display_name)

    return None


def auth_state_for_request(settings: Settings, request: Request, *, verify_google_credentials: bool = False) -> GoogleAuthState:
    """Return auth state, optionally verifying stored Google credentials for explicit auth checks."""
    if not settings.google_configured:
        return GoogleAuthState(available=False, connected=False, connect_url=None)

    user = get_current_user(settings, request)
    if user is None:
        return GoogleAuthState(available=True, connected=False, connect_url=f"{settings.backend_origin}/auth/google")

    if user.legacy_local:
        return GoogleAuthState(available=True, connected=True, connect_url=None, can_send_mail=True)

    has_token = get_google_oauth_token(str(settings.database_path), user_id=user.id) is not None
    if verify_google_credentials and has_token:
        try:
            credential_status = check_user_google_credentials(settings, user_id=user.id, refresh_expired=True)
        except UserMailWorkBlocked:
            # Disconnect and destructive-cleanup paths can deliberately retain
            # the encrypted token when provider revocation has not yet been
            # secured. The provider guard must continue rejecting that token,
            # but the read-only state endpoint should report the durable
            # disconnected state instead of turning it into a 500 response.
            return GoogleAuthState(
                available=True,
                connected=False,
                connect_url=f"{settings.backend_origin}/auth/google",
                reauth_required=True,
                error="Google is disconnected. Please sign in with Google again.",
            )
        if not credential_status.connected:
            return GoogleAuthState(
                available=True,
                connected=False,
                connect_url=f"{settings.backend_origin}/auth/google",
                reauth_required=credential_status.reauth_required or credential_status.has_stored_tokens,
                error=credential_status.error,
            )

    missing_scopes = missing_google_scopes(settings, user_id=user.id, required_scopes=[GMAIL_FULL_SCOPE]) if has_token else [GMAIL_FULL_SCOPE]
    reauth_required = has_token and bool(missing_scopes)
    return GoogleAuthState(
        available=True,
        connected=has_token,
        connect_url=f"{settings.backend_origin}/auth/google" if not has_token or reauth_required else None,
        can_send_mail=has_token and not missing_scopes,
        missing_scopes=missing_scopes if has_token else [],
        reauth_required=reauth_required,
        error="Google needs full mail permission. Please sign in with Google again." if reauth_required else None,
    )


def create_or_update_user(
    settings: Settings,
    *,
    profile: DashboardProfile,
    google_sub: str,
    oauth_started_epoch: int,
) -> StoredUser:
    """Create or update the user represented by a Google profile."""
    email = (profile.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google profile did not include an email")
    verified_sub = google_sub.strip()
    if not verified_sub:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google identity could not be verified")
    if not is_email_allowed(settings, email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This Google account is not allowed")
    try:
        return upsert_user(
            str(settings.database_path),
            email=email,
            google_sub=verified_sub,
            display_name=profile.display_name,
            access_enabled=True,
            oauth_started_epoch=oauth_started_epoch,
        )
    except IdentityConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already linked to a different Google identity. Contact support to re-link it safely.",
        ) from exc


def is_email_allowed(settings: Settings, email: str) -> bool:
    """Check env and DB allowlists."""
    normalized = email.strip().lower()
    if getattr(settings, "registration_mode", "allowlist") == "open":
        return True
    if getattr(settings, "app_env", "local") == "local" and not getattr(settings, "allowed_emails", set()):
        return True
    if normalized in settings.allowed_emails:
        return True
    table_value = is_allowed_email(str(settings.database_path), normalized)
    return table_value is True


def issue_session(settings: Settings, *, user: StoredUser, platform: str) -> IssuedSession:
    """Create an app session and return the raw token for the caller."""
    token = secrets.token_urlsafe(32)
    lifetime_days = MOBILE_SESSION_DAYS if platform in {"ios", "macos"} else WEB_SESSION_DAYS
    expires_at = (datetime.now(timezone.utc) + timedelta(days=lifetime_days)).isoformat()
    create_app_session(
        str(settings.database_path),
        user_id=user.id,
        token_hash=hash_token(settings, token),
        platform=platform,
        expires_at=expires_at,
    )
    return IssuedSession(
        token=token,
        expires_at=expires_at,
        user=CurrentUser(id=user.id, email=user.email, display_name=user.display_name),
    )


def revoke_request_session(settings: Settings, request: Request) -> None:
    """Revoke the session sent by this request if present."""
    token = request.cookies.get(SESSION_COOKIE_NAME) or _bearer_token(request)
    if token:
        revoke_app_session(str(settings.database_path), token_hash=hash_token(settings, token))


def create_mobile_code(settings: Settings, *, user_id: str) -> str:
    """Create a one-time mobile login code."""
    code = secrets.token_urlsafe(32)
    create_mobile_login_code(
        str(settings.database_path),
        code_hash=hash_token(settings, code),
        user_id=user_id,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=MOBILE_LOGIN_CODE_MINUTES)).isoformat(),
    )
    return code


def exchange_mobile_code(
    settings: Settings,
    *,
    code: str,
    handoff_id: str,
    code_verifier: str,
) -> IssuedSession:
    """Exchange one verifier-bound native login code for a bearer session."""
    login_code = consume_bound_mobile_login_code(
        str(settings.database_path),
        handoff_id=handoff_id,
        exchange_code_challenge=mobile_exchange_code_challenge(code_verifier),
        code_hash=hash_token(settings, code),
        now=_utc_now_iso(),
    )
    if login_code is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired login code")
    user = get_user(str(settings.database_path), login_code.user_id)
    if user is None or not user.access_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return issue_session(settings, user=user, platform="macos")


def mobile_exchange_code_challenge(code_verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for a native handoff verifier."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def save_user_google_tokens(
    settings: Settings,
    *,
    user_id: str,
    tokens: dict[str, object],
    oauth_started_epoch: int,
) -> None:
    """Encrypt and persist one user's Google OAuth credentials."""
    upsert_google_oauth_token(
        str(settings.database_path),
        user_id=user_id,
        token_json_encrypted=encrypt_json(settings, tokens),
        oauth_started_epoch=oauth_started_epoch,
    )


def load_user_google_tokens(settings: Settings, *, user_id: str) -> dict[str, object] | None:
    """Load and decrypt one user's Google OAuth credentials."""
    row = get_google_oauth_token(str(settings.database_path), user_id=user_id)
    if row is None:
        return None
    return decrypt_json(settings, row.token_json_encrypted)


def hash_token(settings: Settings, token: str) -> str:
    """Hash an opaque session/login token with app secret material."""
    return hashlib.sha256(f"{settings.app_session_secret}:{token}".encode("utf-8")).hexdigest()


def _legacy_local_google_fallback_enabled(settings: Settings) -> bool:
    """Legacy file-backed local auth is disabled in the Postgres-only runtime."""
    return False


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        token = header[len(prefix) :].strip()
        return token or None
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
