from __future__ import annotations

"""Google OAuth, authorized service creation, and explicit Gmail mutations."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

import google_auth_httplib2
import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import BACKEND_DIR, Settings
from app.core.error_safety import GoogleCredentialsUnavailable
from app.db.repository import (
    delete_oauth_login_session as delete_db_oauth_login_session,
    get_google_oauth_token,
    get_oauth_login_session,
    save_oauth_login_session as save_db_oauth_login_session,
)
from app.db.user_mail_guard import UserMailWorkBlocked, update_connected_google_oauth_token
from app.schemas.domain import DashboardProfile, GoogleAuthState
from app.services.token_crypto import decrypt_json, encrypt_json

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_WRITE_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_FULL_SCOPE = "https://mail.google.com/"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_PROFILE_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
# Full mailbox access is intentionally the only Gmail scope. Gmail's permanent
# delete endpoints require it; it also subsumes read, modify, draft, and send.
GOOGLE_SCOPES = [*GOOGLE_PROFILE_SCOPES, GMAIL_FULL_SCOPE]
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
    oauth_started_epoch: int


@dataclass(frozen=True)
class GoogleAccountIdentity:
    """Verified immutable Google identity plus display profile."""

    profile: DashboardProfile
    google_sub: str


@dataclass(frozen=True)
class GoogleCredentialStatus:
    connected: bool
    has_stored_tokens: bool
    reauth_required: bool = False
    error: str | None = None


@dataclass(frozen=True)
class _CredentialLoadResult:
    credentials: Credentials | None
    status: GoogleCredentialStatus


GOOGLE_REAUTH_REQUIRED_MESSAGE = "Google credentials have expired or were revoked. Please sign in with Google again."
GOOGLE_SCOPE_REAUTH_REQUIRED_MESSAGE = "Google mail permissions changed. Please sign in with Google again."


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
    identity = fetch_google_account_identity_from_credentials(credentials)
    if identity is None:
        raise RuntimeError("Google account identity could not be verified. Please try again.")

    delete_db_oauth_login_session(str(settings.database_path), state=state or session.state)
    return GoogleCallbackResult(
        redirect_to=session.redirect_to,
        tokens=tokens,
        profile=identity.profile,
        google_sub=identity.google_sub,
        oauth_started_epoch=session.started_epoch,
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
    return _load_authorized_credentials(settings, user_id=user_id, refresh_expired=True).credentials


def check_user_google_credentials(settings: Settings, *, user_id: str, refresh_expired: bool = True) -> GoogleCredentialStatus:
    """Return whether stored Google credentials are usable, refreshing only on this explicit check."""
    return _load_authorized_credentials(settings, user_id=user_id, refresh_expired=refresh_expired).status


def _load_authorized_credentials(settings: Settings, *, user_id: str | None, refresh_expired: bool) -> _CredentialLoadResult:
    if not settings.google_configured:
        return _credential_result(False, False, error="Google OAuth is not configured")

    tokens: dict[str, object]
    if user_id is not None:
        token_row = get_google_oauth_token(str(settings.database_path), user_id=user_id)
        if token_row is None:
            return _credential_result(False, False)
        try:
            tokens = decrypt_json(settings, token_row.token_json_encrypted)
        except Exception:
            return _credential_result(
                False,
                True,
                reauth_required=True,
                error="Stored Google credentials could not be read. Please sign in with Google again.",
            )
    elif TOKEN_FILE_PATH.exists():
        try:
            tokens = json.loads(TOKEN_FILE_PATH.read_text())
        except json.JSONDecodeError:
            clear_google_auth_state()
            return _credential_result(
                False,
                True,
                reauth_required=True,
                error="Stored Google credentials are invalid. Please sign in with Google again.",
            )
    else:
        return _credential_result(False, False)

    stored_scopes = _normalized_scope_set(tokens.get("scopes"))
    if GMAIL_FULL_SCOPE not in stored_scopes:
        # Never infer newly requested permissions for an old token. A refresh
        # token cannot silently gain the restricted full-mail grant; the user
        # must complete OAuth consent again.
        return _credential_result(
            False,
            True,
            reauth_required=True,
            error=GOOGLE_SCOPE_REAUTH_REQUIRED_MESSAGE,
        )

    normalized_tokens = {
        **tokens,
        "client_id": tokens.get("client_id") or settings.google_client_id,
        "client_secret": tokens.get("client_secret") or settings.google_client_secret,
        "token_uri": tokens.get("token_uri") or "https://oauth2.googleapis.com/token",
        "scopes": tokens.get("scopes"),
    }

    try:
        credentials = Credentials.from_authorized_user_info(normalized_tokens, GOOGLE_SCOPES)
    except Exception:
        if user_id is None:
            clear_google_auth_state()
        return _credential_result(
            False,
            True,
            reauth_required=True,
            error="Stored Google credentials are invalid. Please sign in with Google again.",
        )

    refreshed_tokens: dict[str, object] | None = None
    if credentials.expired and credentials.refresh_token:
        if refresh_expired:
            try:
                credentials.refresh(Request())
            except Exception:
                if user_id is None:
                    clear_google_auth_state()
                return _credential_result(False, True, reauth_required=True, error=GOOGLE_REAUTH_REQUIRED_MESSAGE)
            refreshed_tokens = token_payload_from_credentials(credentials)
            try:
                persist_token_payload(settings, refreshed_tokens, user_id=user_id)
            except UserMailWorkBlocked:
                return _credential_result(False, False)
        else:
            return _CredentialLoadResult(
                credentials=credentials,
                status=GoogleCredentialStatus(connected=True, has_stored_tokens=True),
            )
    elif credentials.expired:
        return _credential_result(False, True, reauth_required=True, error=GOOGLE_REAUTH_REQUIRED_MESSAGE)

    if normalized_tokens != tokens and refreshed_tokens is None:
        try:
            persist_token_payload(settings, normalized_tokens, user_id=user_id)
        except UserMailWorkBlocked:
            return _credential_result(False, False)

    return _CredentialLoadResult(
        credentials=credentials,
        status=GoogleCredentialStatus(connected=True, has_stored_tokens=True),
    )


def _credential_result(
    connected: bool,
    has_stored_tokens: bool,
    *,
    reauth_required: bool = False,
    error: str | None = None,
) -> _CredentialLoadResult:
    return _CredentialLoadResult(
        credentials=None,
        status=GoogleCredentialStatus(
            connected=connected,
            has_stored_tokens=has_stored_tokens,
            reauth_required=reauth_required,
            error=error,
        ),
    )


def create_gmail_service(settings: Settings, *, user_id: str | None = None):
    credentials = create_authorized_credentials(settings, user_id=user_id)
    if credentials is None:
        raise GoogleCredentialsUnavailable("Google credentials are not connected")
    return build_google_service("gmail", "v1", credentials)


def missing_google_scopes(settings: Settings, *, user_id: str, required_scopes: list[str] | None = None) -> list[str]:
    """Return OAuth scopes absent from the persisted token payload."""
    required = required_scopes or [GMAIL_FULL_SCOPE]
    token_row = get_google_oauth_token(str(settings.database_path), user_id=user_id)
    if token_row is None:
        return required
    try:
        tokens = decrypt_json(settings, token_row.token_json_encrypted)
    except Exception:
        return required
    scopes = _normalized_scope_set(tokens.get("scopes"))
    return [scope for scope in required if scope not in scopes]


def _normalized_scope_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {scope for scope in value.split() if scope}
    if isinstance(value, (list, tuple, set)):
        return {str(scope) for scope in value if str(scope)}
    return set()


def user_can_send_gmail(settings: Settings, *, user_id: str) -> bool:
    return not missing_google_scopes(settings, user_id=user_id, required_scopes=[GMAIL_FULL_SCOPE])


def start_gmail_watch(settings: Settings, *, user_id: str) -> dict[str, object]:
    """Ask Gmail to push future mailbox changes to the configured Pub/Sub topic."""
    if not settings.gmail_pubsub_topic:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC is not configured")
    gmail_service = create_gmail_service(settings, user_id=user_id)
    body = {"topicName": settings.gmail_pubsub_topic}
    return gmail_service.users().watch(userId="me", body=body).execute()


def stop_gmail_watch(settings: Settings, *, user_id: str) -> None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    gmail_service.users().stop(userId="me").execute()


def revoke_stored_google_token(settings: Settings, *, user_id: str) -> bool:
    """Best-effort provider revocation before local encrypted token deletion."""
    token_row = get_google_oauth_token(str(settings.database_path), user_id=user_id)
    if token_row is None:
        return False
    tokens = decrypt_json(settings, token_row.token_json_encrypted)
    token = str(tokens.get("refresh_token") or tokens.get("token") or "").strip()
    if not token:
        return False
    request = UrlRequest(
        "https://oauth2.googleapis.com/revoke",
        data=urlencode({"token": token}).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        response.read()
    return True


def archive_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, remove_label_ids=[GMAIL_INBOX_LABEL])


def unarchive_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, add_label_ids=[GMAIL_INBOX_LABEL])


def mark_gmail_thread_read(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, remove_label_ids=[GMAIL_UNREAD_LABEL])


def mark_gmail_message_read(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(settings, message_id, user_id=user_id, remove_label_ids=[GMAIL_UNREAD_LABEL])


def mark_gmail_thread_unread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, add_label_ids=[GMAIL_UNREAD_LABEL])


def mark_gmail_message_unread(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(settings, message_id, user_id=user_id, add_label_ids=[GMAIL_UNREAD_LABEL])


def move_gmail_thread_to_trash(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().threads().trash(userId="me", id=thread_id).execute()


def restore_gmail_thread_from_trash(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().threads().untrash(userId="me", id=thread_id).execute()


def delete_gmail_thread_forever(settings: Settings, thread_id: str, *, user_id: str | None = None) -> None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    gmail_service.users().threads().delete(userId="me", id=thread_id).execute()


def move_gmail_message_to_trash(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().messages().trash(userId="me", id=message_id).execute()


def restore_gmail_message_from_trash(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().messages().untrash(userId="me", id=message_id).execute()


def mark_gmail_thread_spam(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(
        settings,
        thread_id,
        user_id=user_id,
        add_label_ids=["SPAM"],
        remove_label_ids=[GMAIL_INBOX_LABEL],
    )


def mark_gmail_message_spam(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(
        settings,
        message_id,
        user_id=user_id,
        add_label_ids=["SPAM"],
        remove_label_ids=[GMAIL_INBOX_LABEL],
    )


def mark_gmail_thread_not_spam(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(
        settings,
        thread_id,
        user_id=user_id,
        add_label_ids=[GMAIL_INBOX_LABEL],
        remove_label_ids=["SPAM"],
    )


def mark_gmail_message_not_spam(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(
        settings,
        message_id,
        user_id=user_id,
        add_label_ids=[GMAIL_INBOX_LABEL],
        remove_label_ids=["SPAM"],
    )


def star_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, add_label_ids=["STARRED"])


def unstar_gmail_thread(settings: Settings, thread_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_thread_labels(settings, thread_id, user_id=user_id, remove_label_ids=["STARRED"])


def star_gmail_message(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(settings, message_id, user_id=user_id, add_label_ids=["STARRED"])


def unstar_gmail_message(settings: Settings, message_id: str, *, user_id: str | None = None) -> dict[str, object]:
    return modify_gmail_message_labels(settings, message_id, user_id=user_id, remove_label_ids=["STARRED"])


def delete_gmail_message_forever(settings: Settings, message_id: str, *, user_id: str | None = None) -> None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    gmail_service.users().messages().delete(userId="me", id=message_id).execute()


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


def modify_gmail_message_labels(
    settings: Settings,
    message_id: str,
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
        return gmail_service.users().messages().modify(userId="me", id=message_id, body=body).execute()
    except HttpError:
        raise


def send_gmail_raw_message(
    settings: Settings,
    *,
    user_id: str,
    raw_message: str,
    gmail_thread_id: str | None = None,
) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    body: dict[str, object] = {"raw": raw_message}
    if gmail_thread_id:
        body["threadId"] = gmail_thread_id
    return gmail_service.users().messages().send(userId="me", body=body).execute()


def create_gmail_draft(
    settings: Settings,
    *,
    user_id: str,
    raw_message: str,
    gmail_thread_id: str | None = None,
) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    message: dict[str, object] = {"raw": raw_message}
    if gmail_thread_id:
        message["threadId"] = gmail_thread_id
    return gmail_service.users().drafts().create(userId="me", body={"message": message}).execute()


def update_gmail_draft(
    settings: Settings,
    *,
    user_id: str,
    gmail_draft_id: str,
    raw_message: str,
    gmail_thread_id: str | None = None,
) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    message: dict[str, object] = {"raw": raw_message}
    if gmail_thread_id:
        message["threadId"] = gmail_thread_id
    return gmail_service.users().drafts().update(
        userId="me",
        id=gmail_draft_id,
        body={"id": gmail_draft_id, "message": message},
    ).execute()


def delete_gmail_draft(settings: Settings, *, user_id: str, gmail_draft_id: str) -> None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    gmail_service.users().drafts().delete(userId="me", id=gmail_draft_id).execute()


def send_gmail_draft(settings: Settings, *, user_id: str, gmail_draft_id: str) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().drafts().send(userId="me", body={"id": gmail_draft_id}).execute()


def fetch_gmail_draft(settings: Settings, *, user_id: str, gmail_draft_id: str, format: str = "full") -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().drafts().get(userId="me", id=gmail_draft_id, format=format).execute()


def find_gmail_draft_by_rfc822_message_id(
    settings: Settings,
    *,
    user_id: str,
    rfc822_message_id: str,
) -> dict[str, object] | None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    response = gmail_service.users().drafts().list(
        userId="me",
        q=f"rfc822msgid:{rfc822_message_id}",
        maxResults=2,
    ).execute()
    drafts = response.get("drafts") if isinstance(response, dict) else None
    if not isinstance(drafts, list):
        return None
    return next((draft for draft in drafts if isinstance(draft, dict) and draft.get("id")), None)


def find_gmail_draft_by_message_id(
    settings: Settings,
    *,
    user_id: str,
    message_id: str,
) -> dict[str, object] | None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    page_token: str | None = None
    while True:
        kwargs: dict[str, object] = {"userId": "me", "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        response = gmail_service.users().drafts().list(**kwargs).execute()
        drafts = response.get("drafts") if isinstance(response, dict) else None
        if isinstance(drafts, list):
            match = next(
                (
                    draft
                    for draft in drafts
                    if isinstance(draft, dict)
                    and isinstance(draft.get("message"), dict)
                    and str(draft["message"].get("id") or "") == message_id
                ),
                None,
            )
            if match is not None:
                return match
        page_token = str(response.get("nextPageToken") or "") if isinstance(response, dict) else ""
        if not page_token:
            return None


def find_gmail_message_by_rfc822_message_id(
    settings: Settings,
    *,
    user_id: str,
    rfc822_message_id: str,
) -> dict[str, object] | None:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    response = gmail_service.users().messages().list(
        userId="me",
        q=f"rfc822msgid:{rfc822_message_id}",
        maxResults=2,
        includeSpamTrash=True,
    ).execute()
    messages = response.get("messages") if isinstance(response, dict) else None
    if not isinstance(messages, list):
        return None
    return next((message for message in messages if isinstance(message, dict) and message.get("id")), None)


def fetch_gmail_message(settings: Settings, *, user_id: str, message_id: str, format: str = "full") -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return gmail_service.users().messages().get(userId="me", id=message_id, format=format).execute()


def fetch_gmail_attachment(settings: Settings, *, user_id: str, message_id: str, attachment_id: str) -> dict[str, object]:
    gmail_service = create_gmail_service(settings, user_id=user_id)
    return (
        gmail_service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )


def token_payload_from_credentials(credentials: Credentials) -> dict[str, object]:
    payload: dict[str, object] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
        "expiry": credentials.expiry.isoformat() if credentials.expiry is not None else None,
    }
    id_token = getattr(credentials, "id_token", None)
    if id_token:
        payload["id_token"] = id_token
    return payload


def persist_token_payload(settings: Settings, tokens: dict[str, object], *, user_id: str | None) -> None:
    if user_id is None:
        TOKEN_FILE_PATH.write_text(json.dumps(tokens, indent=2))
        return
    # Refresh/normalization may finish after a disconnect. This path must only
    # update the token row observed at the start; initial OAuth connection is
    # the sole code path allowed to insert credentials.
    update_connected_google_oauth_token(
        str(settings.database_path),
        user_id=user_id,
        token_json_encrypted=encrypt_json(settings, tokens),
    )


def fetch_google_account_identity_from_credentials(credentials: Credentials) -> GoogleAccountIdentity | None:
    """Resolve verified email and immutable OIDC subject from Google userinfo."""
    if not credentials.token:
        return None
    try:
        request = UrlRequest(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    google_sub = str(payload.get("sub") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    email_verified = payload.get("email_verified") is True
    if not google_sub or not email or not email_verified:
        return None
    display_name = str(payload.get("given_name") or payload.get("name") or "").strip() or None
    return GoogleAccountIdentity(
        profile=DashboardProfile(email=email, display_name=display_name),
        google_sub=google_sub,
    )


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
        can_send_mail=connected,
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
