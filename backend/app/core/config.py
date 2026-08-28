from __future__ import annotations

"""Runtime configuration loading for the Python backend."""

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
from typing import Literal
from urllib.parse import parse_qsl, urlparse

from dotenv import load_dotenv
import os


BACKEND_DIR = Path(__file__).resolve().parents[2]
POSTGRES_URL_PREFIXES = ("postgres://", "postgresql://")
RESERVED_SERVICE_HOSTS = {
    "0.0.0.0",
    "127.0.0.1",
    "::1",
    "example.com",
    "example.net",
    "example.org",
    "localhost",
}
RESERVED_SERVICE_HOST_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".invalid",
    ".local",
    ".localhost",
    ".test",
)
RELEASE_SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
CANONICAL_DNS_HOSTNAME = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*"
)


def _load_environment_files() -> None:
    """Load developer defaults without ever replacing process environment."""
    # Explicit process variables are the deployment contract. Loading the local
    # override first preserves its precedence over .env while override=False
    # ensures a developer file cannot redirect a production migration or worker.
    load_dotenv(BACKEND_DIR / ".env.local", override=False)
    load_dotenv(BACKEND_DIR / ".env", override=False)


_load_environment_files()


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
    registration_mode: Literal["allowlist", "open"]
    allowed_emails: tuple[str, ...]
    gmail_sync_scope: str
    gmail_recent_days: int
    gmail_pubsub_topic: str
    gmail_pubsub_subscription: str
    gmail_pubsub_push_audience: str
    gmail_pubsub_push_service_account_email: str
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
    ai_grouping_enabled: bool
    ai_inbox_enabled: bool
    ai_inbox_local_shadow_preview: bool
    ai_inbox_text_provider: Literal["openai", "codex"]
    ai_inbox_embedding_provider: Literal["openai", "local"]
    ai_inbox_classifier_model: str
    ai_inbox_review_model: str
    ai_inbox_embedding_model: str
    ai_inbox_embedding_dimensions: int
    ai_inbox_codex_service_tier: Literal["default", "fast"]
    ai_inbox_codex_timeout_seconds: float
    ai_inbox_prompt_version: str
    ai_inbox_job_cost_limit_usd: float
    ai_inbox_user_monthly_cost_limit_usd: float
    ai_inbox_project_monthly_cost_limit_usd: float
    ai_inbox_classifier_input_usd_per_million: float
    ai_inbox_classifier_output_usd_per_million: float
    ai_inbox_review_input_usd_per_million: float
    ai_inbox_review_output_usd_per_million: float
    ai_inbox_embedding_usd_per_million: float
    log_level: str
    release_sha: str
    rate_limit_enabled: bool
    explicit_runtime_configuration_errors: tuple[str, ...]

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def ai_inbox_configured(self) -> bool:
        """Return whether the selected AI Inbox providers can run here."""
        if self.ai_inbox_text_provider == "codex" and self.app_env != "local":
            return False
        text_ready = self.ai_inbox_text_provider == "codex" or self.openai_configured
        embedding_ready = self.ai_inbox_embedding_provider == "local" or self.openai_configured
        return bool(text_ready and embedding_ready)

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
    def resolved_gmail_pubsub_push_audience(self) -> str:
        return self.gmail_pubsub_push_audience or f"{self.backend_origin}/v1/mailbox/pubsub"

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
            errors.extend(self.explicit_runtime_configuration_errors)
            if self.gmail_sync_scope != "full":
                errors.append("GMAIL_SYNC_SCOPE=full is required outside local development")
            if self.registration_mode not in {"allowlist", "open"}:
                errors.append("REGISTRATION_MODE must be either allowlist or open")
            if self.app_env == "staging" and self.registration_mode != "allowlist":
                errors.append("REGISTRATION_MODE=allowlist is required in staging")
            if not self.google_configured:
                errors.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")
            if not self.gmail_pubsub_topic:
                errors.append("GMAIL_PUBSUB_TOPIC is required")
            if not self.gmail_pubsub_subscription:
                errors.append("GMAIL_PUBSUB_SUBSCRIPTION is required")
            if not self.gmail_pubsub_push_service_account_email:
                errors.append("GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL is required")
            if self.ai_grouping_enabled:
                errors.append("Legacy AI_GROUPING_ENABLED must remain false; AI Inbox uses its own isolated projection")
            if self.openai_debug_logs:
                errors.append("OPENAI_DEBUG_LOGS must remain false so email content cannot enter logs")
            if self.ai_inbox_text_provider != "openai":
                errors.append("AI_INBOX_TEXT_PROVIDER=openai is required outside local development")
            if self.ai_inbox_embedding_provider != "openai":
                errors.append("AI_INBOX_EMBEDDING_PROVIDER=openai is required outside local development")
            if self.ai_inbox_local_shadow_preview:
                errors.append("AI_INBOX_LOCAL_SHADOW_PREVIEW must remain false outside local development")
            if self.ai_inbox_enabled and not self.ai_inbox_configured:
                errors.append("OPENAI_API_KEY is required when AI_INBOX_ENABLED=true")
            if self.openai_required and not self.ai_inbox_enabled:
                errors.append("OPENAI_REQUIRED may only be true when AI_INBOX_ENABLED=true")
            if self.ai_inbox_embedding_dimensions != 1024:
                errors.append("AI_INBOX_EMBEDDING_DIMENSIONS must be 1024")
            if self.ai_inbox_job_cost_limit_usd <= 0:
                errors.append("AI_INBOX_JOB_COST_LIMIT_USD must be greater than zero")
            try:
                google_redirect = urlparse(self.google_redirect_uri)
            except ValueError:
                google_redirect = None
            if (
                google_redirect is None
                or not _is_https_url(self.google_redirect_uri)
                or google_redirect.path != "/auth/google/callback"
                or google_redirect.params
                or google_redirect.query
                or google_redirect.fragment
            ):
                errors.append("GOOGLE_REDIRECT_URI must be an HTTPS /auth/google/callback URL outside local development")
            elif not _uses_public_dns_hostname(self.google_redirect_uri):
                errors.append("GOOGLE_REDIRECT_URI must use a fully qualified public DNS hostname")
            elif _uses_reserved_service_hostname(self.google_redirect_uri):
                errors.append("GOOGLE_REDIRECT_URI must use a concrete non-reserved hostname")
            cors_origin_is_valid = _is_https_origin(self.cors_origin)
            if not cors_origin_is_valid:
                errors.append("CORS_ORIGIN must be one HTTPS origin outside local development")
            elif not _uses_public_dns_hostname(self.cors_origin):
                errors.append("CORS_ORIGIN must use a fully qualified public DNS hostname")
            elif _uses_reserved_service_hostname(self.cors_origin):
                errors.append("CORS_ORIGIN must use a concrete non-reserved hostname")
            web_origin_is_valid = _is_https_origin(self.web_app_url)
            if not web_origin_is_valid:
                errors.append("WEB_APP_URL must be one HTTPS origin outside local development")
            elif not _uses_public_dns_hostname(self.web_app_url):
                errors.append("WEB_APP_URL must use a fully qualified public DNS hostname")
            elif _uses_reserved_service_hostname(self.web_app_url):
                errors.append("WEB_APP_URL must use a concrete non-reserved hostname")
            if cors_origin_is_valid and web_origin_is_valid and self.cors_origin != self.web_app_url:
                errors.append("CORS_ORIGIN and WEB_APP_URL must identify the same web origin")
            if self.mobile_redirect_uri != "electronicmail://auth/callback":
                errors.append("MOBILE_REDIRECT_URI must be electronicmail://auth/callback")
            if self.registration_mode == "allowlist" and not self.allowed_emails:
                errors.append("ALLOWED_EMAILS is required when REGISTRATION_MODE=allowlist")
            if not self.session_cookie_domain:
                errors.append("SESSION_COOKIE_DOMAIN is required outside local development")
            elif not _cookie_domain_matches(self.session_cookie_domain, self.web_app_url, self.google_redirect_uri):
                errors.append("SESSION_COOKIE_DOMAIN must cover both the web and API hosts")
            if self.session_cookie_samesite != "lax":
                errors.append(
                    "SESSION_COOKIE_SAMESITE=lax is required for hosted auth so OAuth redirects work without exposing app sessions to cross-site requests"
                )
            if len(self.app_session_secret) < 32 or _looks_like_placeholder(self.app_session_secret):
                errors.append("APP_SESSION_SECRET must be a unique secret of at least 32 characters")
            if len(self.app_encryption_key) < 32 or _looks_like_placeholder(self.app_encryption_key):
                errors.append("APP_ENCRYPTION_KEY must be unique key material of at least 32 characters")
            if self.app_session_secret == self.app_encryption_key:
                errors.append("APP_SESSION_SECRET and APP_ENCRYPTION_KEY must use different key material")
            if _looks_like_placeholder(self.database_url):
                errors.append("DATABASE_URL still contains placeholder connection details")
            if self.app_env == "production":
                errors.extend(_production_database_transport_errors(self.database_url))
            if _looks_like_placeholder(self.google_client_id) or _looks_like_placeholder(self.google_client_secret):
                errors.append("Google OAuth credentials still contain placeholder values")
            if _looks_like_placeholder(self.gmail_pubsub_topic) or not re.fullmatch(
                r"projects/[^/]+/topics/[^/]+", self.gmail_pubsub_topic
            ):
                errors.append("GMAIL_PUBSUB_TOPIC must be a concrete projects/.../topics/... resource")
            if self.gmail_pubsub_subscription and _looks_like_placeholder(
                self.gmail_pubsub_subscription
            ):
                errors.append(
                    "GMAIL_PUBSUB_SUBSCRIPTION still contains a placeholder value"
                )
            expected_push_audience = f"{self.backend_origin}/v1/mailbox/pubsub"
            if not _is_https_url(self.gmail_pubsub_push_audience) or self.gmail_pubsub_push_audience != expected_push_audience:
                errors.append("GMAIL_PUBSUB_PUSH_AUDIENCE must exactly match the deployed /v1/mailbox/pubsub URL")
            if _uses_reserved_service_hostname(self.gmail_pubsub_push_audience):
                errors.append("GMAIL_PUBSUB_PUSH_AUDIENCE must use a concrete non-reserved hostname")
            if self.gmail_pubsub_push_service_account_email and (
                _looks_like_placeholder(self.gmail_pubsub_push_service_account_email)
                or re.fullmatch(
                    r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com",
                    self.gmail_pubsub_push_service_account_email,
                )
                is None
            ):
                errors.append(
                    "GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL must be a concrete Google service-account email"
                )
            if self.cors_origin == "*":
                errors.append("CORS_ORIGIN cannot be '*' when credentialed authentication is enabled")
            if not self.rate_limit_enabled:
                errors.append("RATE_LIMIT_ENABLED=true is required outside local development")
            if RELEASE_SHA_PATTERN.fullmatch(self.release_sha) is None:
                errors.append("RELEASE_SHA must be a full lowercase 40- or 64-hex immutable revision")

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
    ai_inbox_text_provider = _resolve_ai_inbox_text_provider()
    ai_inbox_embedding_provider = _resolve_ai_inbox_embedding_provider()
    web_app_url = os.getenv("WEB_APP_URL", os.getenv("CORS_ORIGIN", "http://localhost:5173"))
    configured_release_sha = os.getenv("RELEASE_SHA", "")
    railway_release_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")
    # A GitHub-triggered Railway deployment already carries the immutable
    # source revision. Prefer that platform identity so every API and worker
    # heartbeat is bound to the image Railway actually built, not a manually
    # copied variable that can drift between services.
    release_sha = railway_release_sha or configured_release_sha or "local"
    if app_env == "local":
        web_app_url = web_app_url.strip().rstrip("/")
        release_sha = release_sha.strip()[:64] or "local"

    return Settings(
        app_env=app_env,  # type: ignore[arg-type]
        port=int(os.getenv("PORT", "3001")),
        database_url=database_url,
        database_path=_resolve_database_path(database_url),
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:5173"),
        web_app_url=web_app_url,
        app_session_secret=os.getenv("APP_SESSION_SECRET", "local-dev-session-secret").strip().strip("\"'"),
        session_cookie_domain=os.getenv("SESSION_COOKIE_DOMAIN", "").strip().strip("\"'"),
        session_cookie_samesite=_resolve_session_cookie_samesite(),
        app_encryption_key=os.getenv("APP_ENCRYPTION_KEY", "local-dev-encryption-key").strip().strip("\"'"),
        registration_mode=_resolve_registration_mode(),
        allowed_emails=_parse_email_list(os.getenv("ALLOWED_EMAILS", "")),
        gmail_sync_scope=os.getenv("GMAIL_SYNC_SCOPE", "full").strip().lower() or "full",
        gmail_recent_days=int(os.getenv("GMAIL_RECENT_DAYS", "90")),
        gmail_pubsub_topic=os.getenv("GMAIL_PUBSUB_TOPIC", "").strip().strip("\"'"),
        gmail_pubsub_subscription=os.getenv("GMAIL_PUBSUB_SUBSCRIPTION", "").strip().strip("\"'"),
        gmail_pubsub_push_audience=os.getenv("GMAIL_PUBSUB_PUSH_AUDIENCE", "").strip().strip("\"'"),
        gmail_pubsub_push_service_account_email=os.getenv("GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", "").strip().strip("\"'").lower(),
        gmail_watch_renewal_hours=int(os.getenv("GMAIL_WATCH_RENEWAL_HOURS", "24")),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip().strip("\"'"),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip("\"'"),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:3001/auth/google/callback",
        ),
        mobile_redirect_uri=os.getenv("MOBILE_REDIRECT_URI", "electronicmail://auth/callback").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip().strip("\"'"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip().strip("\"'") or "gpt-5.6-luna",
        openai_reasoning_effort=_resolve_openai_reasoning_effort(),
        openai_required=os.getenv("OPENAI_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"},
        openai_debug_logs=os.getenv("OPENAI_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"},
        ai_grouping_enabled=os.getenv("AI_GROUPING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        ai_inbox_enabled=_resolve_boolean("AI_INBOX_ENABLED", default=False),
        ai_inbox_local_shadow_preview=_resolve_boolean("AI_INBOX_LOCAL_SHADOW_PREVIEW", default=False),
        ai_inbox_text_provider=ai_inbox_text_provider,
        ai_inbox_embedding_provider=ai_inbox_embedding_provider,
        ai_inbox_classifier_model=os.getenv("AI_INBOX_CLASSIFIER_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna",
        ai_inbox_review_model=os.getenv("AI_INBOX_REVIEW_MODEL", "gpt-5.6-terra").strip() or "gpt-5.6-terra",
        ai_inbox_embedding_model=(
            "local-hash-v1"
            if ai_inbox_embedding_provider == "local"
            else os.getenv("AI_INBOX_EMBEDDING_MODEL", "text-embedding-3-large").strip()
            or "text-embedding-3-large"
        ),
        ai_inbox_embedding_dimensions=int(os.getenv("AI_INBOX_EMBEDDING_DIMENSIONS", "1024")),
        ai_inbox_codex_service_tier=_resolve_ai_inbox_codex_service_tier(),
        ai_inbox_codex_timeout_seconds=_resolve_positive_float("AI_INBOX_CODEX_TIMEOUT_SECONDS", default=90.0),
        ai_inbox_prompt_version=os.getenv(
            "AI_INBOX_PROMPT_VERSION",
            "matter-v6-event-chain-counterpart-identity",
        ).strip()
        or "matter-v6-event-chain-counterpart-identity",
        ai_inbox_job_cost_limit_usd=float(os.getenv("AI_INBOX_JOB_COST_LIMIT_USD", "0.10")),
        ai_inbox_user_monthly_cost_limit_usd=float(os.getenv("AI_INBOX_USER_MONTHLY_COST_LIMIT_USD", "25")),
        ai_inbox_project_monthly_cost_limit_usd=float(os.getenv("AI_INBOX_PROJECT_MONTHLY_COST_LIMIT_USD", "250")),
        ai_inbox_classifier_input_usd_per_million=float(os.getenv("AI_INBOX_CLASSIFIER_INPUT_USD_PER_MILLION", "0.20")),
        ai_inbox_classifier_output_usd_per_million=float(os.getenv("AI_INBOX_CLASSIFIER_OUTPUT_USD_PER_MILLION", "1.20")),
        ai_inbox_review_input_usd_per_million=float(os.getenv("AI_INBOX_REVIEW_INPUT_USD_PER_MILLION", "2.00")),
        ai_inbox_review_output_usd_per_million=float(os.getenv("AI_INBOX_REVIEW_OUTPUT_USD_PER_MILLION", "12.00")),
        ai_inbox_embedding_usd_per_million=float(os.getenv("AI_INBOX_EMBEDDING_USD_PER_MILLION", "0.13")),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        release_sha=release_sha,
        rate_limit_enabled=_resolve_boolean("RATE_LIMIT_ENABLED", default=app_env in {"staging", "production"}),
        explicit_runtime_configuration_errors=_explicit_runtime_configuration_errors(
            app_env=app_env,
            environment=os.environ,
        ),
    )


def _resolve_openai_reasoning_effort() -> Literal["low", "medium", "high"]:
    """Keep reasoning effort explicit and constrained to OpenAI-supported values."""
    value = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip().strip("\"'").lower()

    if value in {"low", "medium", "high"}:
        return value

    return "medium"


def _resolve_ai_inbox_text_provider() -> Literal["openai", "codex"]:
    value = os.getenv("AI_INBOX_TEXT_PROVIDER", "openai").strip().strip("\"'").lower()
    if value not in {"openai", "codex"}:
        raise ValueError("AI_INBOX_TEXT_PROVIDER must be openai or codex")
    return value  # type: ignore[return-value]


def _resolve_ai_inbox_embedding_provider() -> Literal["openai", "local"]:
    value = os.getenv("AI_INBOX_EMBEDDING_PROVIDER", "openai").strip().strip("\"'").lower()
    if value not in {"openai", "local"}:
        raise ValueError("AI_INBOX_EMBEDDING_PROVIDER must be openai or local")
    return value  # type: ignore[return-value]


def _resolve_positive_float(name: str, *, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip().strip("\"'")
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _resolve_ai_inbox_codex_service_tier() -> Literal["default", "fast"]:
    value = os.getenv("AI_INBOX_CODEX_SERVICE_TIER", "default").strip().strip("\"'").lower()
    if value not in {"default", "fast"}:
        raise ValueError("AI_INBOX_CODEX_SERVICE_TIER must be default or fast")
    return value  # type: ignore[return-value]


def _resolve_session_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().strip("\"'").lower()
    if value in {"lax", "strict", "none"}:
        return value
    return "lax"


def _resolve_registration_mode() -> Literal["allowlist", "open"]:
    value = os.getenv("REGISTRATION_MODE", "allowlist").strip().strip("\"'").lower()
    if value in {"allowlist", "open"}:
        return value
    return "allowlist"


def _parse_email_list(value: str) -> tuple[str, ...]:
    """Parse comma-separated allowed email values."""
    emails = []
    for item in value.split(","):
        email = item.strip().strip("\"'").lower()
        if email:
            emails.append(email)
    return tuple(dict.fromkeys(emails))


def _resolve_boolean(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _explicit_runtime_configuration_errors(
    *,
    app_env: str,
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    """Reject ambiguous production values for launch-critical feature switches.

    Boolean convenience parsing is useful during local development, but a
    missing value or typo must not silently become the launch configuration.
    The legacy grouping switch stays disabled. AI Inbox has a separate explicit
    kill switch and may hold an OpenAI key only when that switch is enabled.
    """
    if app_env not in {"staging", "production"}:
        return ()

    errors: list[str] = []
    for name in ("AI_GROUPING_ENABLED", "OPENAI_REQUIRED", "OPENAI_DEBUG_LOGS"):
        raw_value = environment.get(name)
        if raw_value != "false":
            errors.append(f"{name} must be explicitly set to false outside local development")

    if environment.get("AI_INBOX_ENABLED") not in {"true", "false"}:
        errors.append("AI_INBOX_ENABLED must be explicitly set to true or false outside local development")

    if "OPENAI_API_KEY" not in environment:
        if environment.get("AI_INBOX_ENABLED") == "true":
            errors.append("OPENAI_API_KEY must be set when AI_INBOX_ENABLED=true")
        else:
            errors.append("OPENAI_API_KEY must be explicitly set to an empty value outside local development")
    elif environment.get("AI_INBOX_ENABLED") == "true" and not environment["OPENAI_API_KEY"].strip():
        errors.append("OPENAI_API_KEY must be set when AI_INBOX_ENABLED=true")
    elif environment.get("AI_INBOX_ENABLED") != "true" and environment["OPENAI_API_KEY"] != "":
        errors.append("OPENAI_API_KEY must be empty outside local development when AI Inbox is disabled")

    if environment.get("RATE_LIMIT_ENABLED") != "true":
        errors.append("RATE_LIMIT_ENABLED must be explicitly set to true outside local development")

    configured_release_sha = environment.get("RELEASE_SHA", "")
    railway_release_sha = environment.get("RAILWAY_GIT_COMMIT_SHA", "")
    if (
        configured_release_sha
        and railway_release_sha
        and configured_release_sha != railway_release_sha
    ):
        errors.append(
            "RELEASE_SHA must match Railway's RAILWAY_GIT_COMMIT_SHA when both are set"
        )

    return tuple(errors)


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(
        marker in normalized
        for marker in (
            "replace-me",
            "replace-in-secret-store",
            "local-dev-",
            "generate-at-least-",
            "generate-separate-",
            "set-to-deployed-",
            "user:password@host",
            "project_id",
            "placeholder",
        )
    )


def _production_database_transport_errors(database_url: str) -> list[str]:
    """Require hostname-authenticated PostgreSQL TLS for production traffic."""
    if _contains_control_character(database_url) or database_url != database_url.strip():
        return ["DATABASE_URL must be a valid PostgreSQL URL in production"]

    try:
        parsed = urlparse(database_url)
        hostname = parsed.hostname
        # Accessing port is intentionally part of validation because urlparse
        # otherwise leaves malformed ports latent until the first DB request.
        port = parsed.port
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=16,
        )
    except ValueError:
        return ["DATABASE_URL must be a valid PostgreSQL URL in production"]

    errors: list[str] = []
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not hostname
        or not _is_canonical_database_hostname(hostname)
        or port == 0
    ):
        errors.append("DATABASE_URL must include a database host for production TLS verification")
    if parsed.params or parsed.fragment:
        errors.append("DATABASE_URL must not contain URL parameters or a fragment in production")

    required_query_keys = {"sslmode", "sslrootcert", "gssencmode"}
    if len(query_pairs) != len(required_query_keys) or {key for key, _ in query_pairs} != required_query_keys:
        errors.append(
            "DATABASE_URL query must contain only exact lowercase sslmode, sslrootcert, and gssencmode parameters once in production"
        )

    security_parameters: dict[str, list[tuple[str, str]]] = {
        "sslmode": [],
        "sslrootcert": [],
        "gssencmode": [],
    }
    for key, value in query_pairs:
        canonical_key = key.casefold()
        if canonical_key in security_parameters:
            security_parameters[canonical_key].append((key, value))

    sslmode_values = security_parameters["sslmode"]
    if len(sslmode_values) != 1 or sslmode_values[0][0] != "sslmode" or sslmode_values[0][1] != "verify-full":
        errors.append("DATABASE_URL must set sslmode=verify-full exactly once in production")

    gssencmode_values = security_parameters["gssencmode"]
    if (
        len(gssencmode_values) != 1
        or gssencmode_values[0][0] != "gssencmode"
        or gssencmode_values[0][1] != "disable"
    ):
        errors.append("DATABASE_URL must set gssencmode=disable exactly once in production")

    sslrootcert_values = security_parameters["sslrootcert"]
    if len(sslrootcert_values) != 1 or sslrootcert_values[0][0] != "sslrootcert":
        errors.append(
            "DATABASE_URL must set sslrootcert=system or one readable absolute CA-bundle path exactly once in production"
        )
    else:
        sslrootcert = sslrootcert_values[0][1]
        trusted_ca_path = Path(sslrootcert)
        if sslrootcert != "system" and not (
            trusted_ca_path.is_absolute()
            and trusted_ca_path.is_file()
            and os.access(trusted_ca_path, os.R_OK)
        ):
            errors.append(
                "DATABASE_URL sslrootcert must be system or a readable existing absolute CA-bundle path in production"
            )

    return errors


def _is_canonical_database_hostname(hostname: str) -> bool:
    """Reject libpq multi-host and non-canonical authority ambiguities."""
    if not hostname.isascii() or hostname.endswith("."):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Numeric-looking invalid IPv4 values must not fall through as DNS.
        return (
            re.fullmatch(r"[0-9.]+", hostname) is None
            and CANONICAL_DNS_HOSTNAME.fullmatch(hostname) is not None
        )
    return hostname == address.compressed


def _is_https_url(value: str) -> bool:
    if _contains_control_character(value) or value != value.strip():
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    return parsed.netloc == _canonical_https_netloc(parsed)


def _is_https_origin(value: str) -> bool:
    if not _is_https_url(value):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.path == ""
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and value == f"https://{_canonical_https_netloc(parsed)}"
    )


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _canonical_https_netloc(parsed: object) -> str:
    try:
        hostname = getattr(parsed, "hostname", None)
    except ValueError:
        return ""
    if not isinstance(hostname, str) or not hostname:
        return ""
    hostname = hostname.lower()
    if hostname.endswith("."):
        return ""
    try:
        canonical_ip = ipaddress.ip_address(hostname).compressed
    except ValueError:
        if CANONICAL_DNS_HOSTNAME.fullmatch(hostname) is None:
            return ""
    else:
        if hostname != canonical_ip:
            return ""
    try:
        port = getattr(parsed, "port", None)
    except ValueError:
        return ""
    if port == 443:
        return ""
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{rendered_host}:{port}" if port is not None else rendered_host


def _uses_reserved_service_hostname(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return True
    return host in RESERVED_SERVICE_HOSTS or host.endswith(RESERVED_SERVICE_HOST_SUFFIXES)


def _uses_public_dns_hostname(value: str) -> bool:
    """Return whether a service URL names a canonical, dotted DNS host."""
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return "." in host and CANONICAL_DNS_HOSTNAME.fullmatch(host) is not None
    return False


def _cookie_domain_matches(cookie_domain: str, *urls: str) -> bool:
    domain = cookie_domain.strip().lower().lstrip(".")
    if not domain or "/" in domain or ":" in domain:
        return False
    for value in urls:
        try:
            host = (urlparse(value).hostname or "").lower()
        except ValueError:
            return False
        if host != domain and not host.endswith(f".{domain}"):
            return False
    return True
