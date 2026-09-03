from __future__ import annotations

"""Small dataclass models for active auth/session repository rows."""

from dataclasses import dataclass


@dataclass
class StoredUser:
    """Signed-in app user resolved from Google OAuth."""

    id: str
    email: str
    display_name: str | None
    google_sub: str
    access_enabled: bool
    created_at: str
    updated_at: str
    # Legacy fixtures predate the additive ownership column. Runtime rows
    # always provide it after migration 0035.
    primary_gmail_account_id: str = ""


@dataclass
class StoredGmailAccount:
    """One independently-scoped Gmail mailbox owned by an app user."""

    id: str
    user_id: str
    email: str
    display_name: str | None
    google_sub: str
    state: str
    initial_ready_at: str | None
    created_at: str
    updated_at: str


@dataclass
class StoredAppSession:
    """Opaque app session used by web cookies and iOS bearer auth."""

    id: str
    user_id: str
    token_hash: str
    platform: str
    expires_at: str
    revoked_at: str | None
    last_seen_at: str | None
    created_at: str


@dataclass
class StoredOAuthLoginSession:
    """PKCE OAuth session keyed by Google state."""

    state: str
    code_verifier: str
    redirect_to: str | None
    expires_at: str
    created_at: str
    started_epoch: int
    intent: str = "login"
    initiating_user_id: str | None = None


@dataclass
class StoredMobileLoginCode:
    """One-time login code exchanged by iOS after OAuth callback."""

    code_hash: str
    user_id: str
    expires_at: str
    consumed_at: str | None
    created_at: str


@dataclass
class StoredMobileOAuthHandoff:
    """Encrypted one-time browser-to-native OAuth handoff."""

    handoff_id: str
    login_code_encrypted: str | None
    login_code_hash: str | None
    exchange_code_challenge: str | None
    status: str
    error: str | None
    expires_at: str
    consumed_at: str | None
    created_at: str


@dataclass
class StoredGoogleOAuthToken:
    """Encrypted Google OAuth credentials for one Gmail account."""

    user_id: str
    gmail_account_id: str
    token_json_encrypted: str
    updated_at: str
