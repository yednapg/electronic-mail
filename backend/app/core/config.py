from __future__ import annotations

"""Runtime configuration loading for the Python backend."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
import os


BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")
POSTGRES_URL_PREFIXES = ("postgres://", "postgresql://")


@dataclass(frozen=True)
class Settings:
    """Resolved environment settings used across the backend."""

    app_env: Literal["local", "staging", "production"]
    port: int
    database_url: str
    database_path: str
    cors_origin: str
    web_app_url: str
    app_session_secret: str
    session_cookie_domain: str
    session_cookie_samesite: Literal["lax", "strict", "none"]
    app_encryption_key: str
    allowed_emails: tuple[str, ...]
    gmail_sync_scope: str
    gmail_recent_days: int
    gmail_pubsub_topic: str
    gmail_pubsub_subscription: str
    gmail_watch_renewal_hours: int
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    mobile_redirect_uri: str
    openai_api_key: str
    openai_model: str
    openai_reasoning_effort: Literal["low", "medium", "high"]
    openai_required: bool
    openai_debug_logs: bool

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def is_production_like(self) -> bool:
        return self.app_env in {"staging", "production"}

    @property
    def backend_origin(self) -> str:
        redirect_suffix = "/auth/google/callback"

        if self.google_redirect_uri.endswith(redirect_suffix):
            return self.google_redirect_uri[: -len(redirect_suffix)]

        return f"http://localhost:{self.port}"

    @property
    def database_backend(self) -> Literal["postgres", "sqlite"]:
        return "postgres" if self.database_url.strip().startswith(POSTGRES_URL_PREFIXES) else "sqlite"

    @property
    def sqlite_database_path(self) -> Path | None:
        if self.database_backend == "postgres":
            return None
        return Path(self.database_path)

    def readiness_errors(self) -> list[str]:
        """Return blocking config issues for production-like deployments."""
        errors: list[str] = []

        if self.app_env not in {"local", "staging", "production"}:
            errors.append("APP_ENV must be one of local, staging, or production")

        if not self.database_url.strip():
            errors.append("DATABASE_URL is required")

        if not self.app_session_secret:
            errors.append("APP_SESSION_SECRET is required")

        if not self.app_encryption_key:
            errors.append("APP_ENCRYPTION_KEY is required")

        if self.gmail_sync_scope not in {"full", "recent"}:
            errors.append("GMAIL_SYNC_SCOPE must be either full or recent")

        if self.gmail_recent_days < 1:
            errors.append("GMAIL_RECENT_DAYS must be greater than 0")

        if self.gmail_watch_renewal_hours < 1:
            errors.append("GMAIL_WATCH_RENEWAL_HOURS must be greater than 0")

        if not self.database_url.startswith(("postgres://", "postgresql://")):
            errors.append("DATABASE_URL must be Postgres. SQLite is no longer supported.")

        if self.is_production_like:
            if not self.google_configured:
                errors.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")
            if not self.gmail_pubsub_topic:
                errors.append("GMAIL_PUBSUB_TOPIC is required")
            if not self.gmail_pubsub_subscription:
                errors.append("GMAIL_PUBSUB_SUBSCRIPTION is required")
            if self.openai_required and not self.openai_configured:
                errors.append("OPENAI_API_KEY is required because OPENAI_REQUIRED is enabled")
            if not self.google_redirect_uri.startswith("https://"):
                errors.append("GOOGLE_REDIRECT_URI must be HTTPS outside local development")
            if not self.cors_origin.startswith("https://"):
                errors.append("CORS_ORIGIN must be HTTPS outside local development")
            if not self.web_app_url.startswith("https://"):
                errors.append("WEB_APP_URL must be HTTPS outside local development")
            if not self.mobile_redirect_uri:
                errors.append("MOBILE_REDIRECT_URI is required")
            if not self.allowed_emails:
                errors.append("ALLOWED_EMAILS is required outside local development")
            if not self.session_cookie_domain:
                errors.append("SESSION_COOKIE_DOMAIN is required outside local development")
            if self.session_cookie_samesite != "none":
                errors.append("SESSION_COOKIE_SAMESITE=none is required for hosted web/backend auth")

        return errors


def _resolve_database_path(database_url: str) -> str:
    """Normalize the Postgres URL used by the runtime repository layer."""
    normalized = database_url.strip().strip("\"'")

    if normalized.startswith(POSTGRES_URL_PREFIXES):
        return normalized

    return normalized


def load_settings() -> Settings:
    """Load environment variables once and expose a typed settings object."""
    database_url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    app_env = os.getenv("APP_ENV", "local").strip().lower() or "local"

    return Settings(
        app_env=app_env,  # type: ignore[arg-type]
        port=int(os.getenv("PORT", "3001")),
        database_url=database_url,
        database_path=_resolve_database_path(database_url),
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:5173"),
        web_app_url=os.getenv("WEB_APP_URL", os.getenv("CORS_ORIGIN", "http://localhost:5173")).strip().rstrip("/"),
        app_session_secret=os.getenv("APP_SESSION_SECRET", "local-dev-session-secret").strip().strip("\"'"),
        session_cookie_domain=os.getenv("SESSION_COOKIE_DOMAIN", "").strip().strip("\"'"),
        session_cookie_samesite=_resolve_session_cookie_samesite(),
        app_encryption_key=os.getenv("APP_ENCRYPTION_KEY", "local-dev-encryption-key").strip().strip("\"'"),
        allowed_emails=_parse_email_list(os.getenv("ALLOWED_EMAILS", "")),
        gmail_sync_scope=os.getenv("GMAIL_SYNC_SCOPE", "recent").strip().lower() or "recent",
        gmail_recent_days=int(os.getenv("GMAIL_RECENT_DAYS", "90")),
        gmail_pubsub_topic=os.getenv("GMAIL_PUBSUB_TOPIC", "").strip().strip("\"'"),
        gmail_pubsub_subscription=os.getenv("GMAIL_PUBSUB_SUBSCRIPTION", "").strip().strip("\"'"),
        gmail_watch_renewal_hours=int(os.getenv("GMAIL_WATCH_RENEWAL_HOURS", "24")),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip().strip("\"'"),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip("\"'"),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:3001/auth/google/callback",
        ),
        mobile_redirect_uri=os.getenv("MOBILE_REDIRECT_URI", "decisionpipeline://auth/callback").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip().strip("\"'"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini").strip().strip("\"'") or "gpt-5.4-mini",
        openai_reasoning_effort=_resolve_openai_reasoning_effort(),
        openai_required=os.getenv("OPENAI_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"},
        openai_debug_logs=os.getenv("OPENAI_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"},
    )


def _resolve_openai_reasoning_effort() -> Literal["low", "medium", "high"]:
    """Keep reasoning effort explicit and constrained to OpenAI-supported values."""
    value = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().strip("\"'").lower()

    if value in {"low", "medium", "high"}:
        return value

    return "medium"


def _resolve_session_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().strip("\"'").lower()
    if value in {"lax", "strict", "none"}:
        return value
    return "lax"


def _parse_email_list(value: str) -> tuple[str, ...]:
    """Parse comma-separated allowed email values."""
    emails = []
    for item in value.split(","):
        email = item.strip().strip("\"'").lower()
        if email:
            emails.append(email)
    return tuple(dict.fromkeys(emails))
