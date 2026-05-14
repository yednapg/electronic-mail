from __future__ import annotations

"""Google OAuth, authorized service creation, and explicit Gmail mutations."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import google_auth_httplib2
import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import BACKEND_DIR, Settings
from app.db.repository import (
    delete_oauth_login_session as delete_db_oauth_login_session,
    get_google_oauth_token,
    get_oauth_login_session,
    save_oauth_login_session as save_db_oauth_login_session,
    upsert_google_oauth_token,
)
from app.schemas.domain import DashboardProfile, GoogleAuthState
from app.services.token_crypto import decrypt_json, encrypt_json

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_WRITE_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_SCOPES = [GMAIL_SCOPE, GMAIL_WRITE_SCOPE, CALENDAR_SCOPE]
GOOGLE_API_TIMEOUT_SECONDS = 20
GMAIL_INBOX_LABEL = "INBOX"
GMAIL_UNREAD_LABEL = "UNREAD"

TOKEN_FILE_PATH = BACKEND_DIR / ".google-oauth.json"
ACCOUNT_PROFILE_FILE_PATH = BACKEND_DIR / ".google-account.json"


@dataclass(frozen=True)
class GoogleCallbackResult:
    redirect_to: str | None
    tokens: dict[str, object]
    profile: DashboardProfile
    google_sub: str


def build_google_service(api: str, version: str, credentials: Credentials):
    """Build a Google API service with a bounded request timeout."""
    http = httplib2.Http(timeout=GOOGLE_API_TIMEOUT_SECONDS)
    authorized_http = google_auth_httplib2.AuthorizedHttp(credentials, http=http)
    return build(api, version, http=authorized_http, cache_discovery=False)


def get_google_auth_url(settings: Settings, redirect_to: str | None = None) -> str:
    """Build and persist a PKCE Google authorization URL."""
    flow = create_flow(settings)
    authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    save_db_oauth_login_session(
        str(settings.database_path),
        state=state,
        code_verifier=flow.code_verifier,
        redirect_to=redirect_to,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    )
    return authorization_url


def handle_google_callback(settings: Settings, code: str, state: str | None = None) -> GoogleCallbackResult:
    """Exchange the OAuth callback code for user profile and token material."""
    session = (
        get_oauth_login_session(str(settings.database_path), state=state, now=datetime.now(timezone.utc).isoformat())
        if state is not None
        else None
    )
    if session is None:
        raise RuntimeError("Missing OAuth session. Start again from /auth/google.")

    if state is not None and session.state and state != session.state:
        delete_db_oauth_login_session(str(settings.database_path), state=state)
        raise RuntimeError("OAuth state mismatch. Start again from /auth/google.")

    if not session.code_verifier:
        delete_db_oauth_login_session(str(settings.database_path), state=state or session.state)
        raise RuntimeError("Missing OAuth code verifier. Start again from /auth/google.")

    flow = create_flow(settings)
    flow.code_verifier = session.code_verifier
    flow.fetch_token(code=code)
    credentials = flow.credentials
    tokens = token_payload_from_credentials(credentials)
    profile = fetch_google_account_profile_from_credentials(credentials)
    if profile is None or profile.email is None:
        raise RuntimeError("Google account profile could not be resolved")

    delete_db_oauth_login_session(str(settings.database_path), state=state or session.state)
    return GoogleCallbackResult(
        redirect_to=session.redirect_to,
        tokens=tokens,
        profile=profile,
        google_sub=profile.email,
    )


def create_flow(settings: Settings) -> Flow:
    if not settings.google_configured:
        raise RuntimeError("Google OAuth is not configured")
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )


def create_authorized_credentials(settings: Settings, *, user_id: str | None = None) -> Credentials | None:
    """Load and refresh stored OAuth credentials for the current user."""
    if not settings.google_configured:
        return None

    tokens: dict[str, object]
    if user_id is not None:
        token_row = get_google_oauth_token(str(settings.database_path), user_id=user_id)
        if token_row is None:
            return None
        try:
            tokens = decrypt_json(settings, token_row.token_json_encrypted)
        except Exception:
            return None
    elif TOKEN_FILE_PATH.exists():
        try:
            tokens = json.loads(TOKEN_FILE_PATH.read_text())
        except json.JSONDecodeError:
            clear_google_auth_state()
            return None
    else:
        return None

    normalized_tokens = {
        **tokens,
        "client_id": tokens.get("client_id") or settings.google_client_id,
        "client_secret": tokens.get("client_secret") or settings.google_client_secret,
        "token_uri": tokens.get("token_uri") or "https://oauth2.googleapis.com/token",
        "scopes": tokens.get("scopes") or GOOGLE_SCOPES,
    }

    try:
        credentials = Credentials.from_authorized_user_info(normalized_tokens, GOOGLE_SCOPES)
    except Exception:
        if user_id is None:
            clear_google_auth_state()
        return None

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            if user_id is None:
                clear_google_auth_state()
            return None
        persist_token_payload(settings, token_payload_from_credentials(credentials), user_id=user_id)

    if normalized_tokens != tokens:
        persist_token_payload(settings, normalized_tokens, user_id=user_id)

    return credentials


def create_gmail_service(settings: Settings, *, user_id: str | None = None):
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise RuntimeError("Google credentials are not connected")
    return build_google_service("gmail", "v1", credentials)


def archive_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, remove_label_ids=[GMAIL_INBOX_LABEL])


def unarchive_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, add_label_ids=[GMAIL_INBOX_LABEL])


def mark_gmail_thread_read(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, remove_label_ids=[GMAIL_UNREAD_LABEL])


def modify_gmail_thread_labels(
    settings: Settings,
    thread_id: str,
    *,
    user_id: str | None = None,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    body: dict[str, object] = {}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids
    try:
        return gmail_service.users().threads().modify(userId="me", id=thread_id, body=body).execute()
    except HttpError:
        raise


def token_payload_from_credentials(credentials: Credentials) -> dict[str, object]:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry is not None else None,
    }


def persist_token_payload(settings: Settings, tokens: dict[str, object], *, user_id: str | None) -> None:
    if user_id is None:
        TOKEN_FILE_PATH.write_text(json.dumps(tokens, indent=2))
        return
    upsert_google_oauth_token(
        str(settings.database_path),
        user_id=user_id,
        token_json_encrypted=encrypt_json(settings, tokens),
    )


def fetch_google_account_profile_from_credentials(credentials: Credentials) -> DashboardProfile | None:
    try:
        gmail_service = build_google_service("gmail", "v1", credentials)
        payload = gmail_service.users().getProfile(userId="me").execute()
    except Exception:
        return None
    email = str(payload.get("emailAddress") or "").strip() or None
    return DashboardProfile(email=email)


def has_stored_google_tokens() -> bool:
    return TOKEN_FILE_PATH.exists()


def get_google_auth_state(settings: Settings) -> GoogleAuthState:
    if not settings.google_configured:
        return GoogleAuthState(available=False, connected=False, connect_url=None)
    connected = has_stored_google_tokens()
    return GoogleAuthState(
        available=True,
        connected=connected,
        connect_url=None if connected else f"{settings.backend_origin}/auth/google",
    )


def clear_google_auth_state() -> None:
    for file_path in (TOKEN_FILE_PATH, ACCOUNT_PROFILE_FILE_PATH):
        if file_path.exists():
            file_path.unlink()


def load_google_account_profile() -> DashboardProfile | None:
    if not ACCOUNT_PROFILE_FILE_PATH.exists():
        return None
    try:
        return DashboardProfile.model_validate(json.loads(ACCOUNT_PROFILE_FILE_PATH.read_text()))
    except Exception:
        return None
