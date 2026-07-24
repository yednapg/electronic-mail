from __future__ import annotations

"""Fail-fast deployment guard for production-like Railway starts."""

import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from app.core.config import load_settings


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


REQUIRED_PRODUCTION_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "APP_SESSION_SECRET",
    "SESSION_COOKIE_DOMAIN",
    "SESSION_COOKIE_SAMESITE",
    "APP_ENCRYPTION_KEY",
    "REGISTRATION_MODE",
    "CORS_ORIGIN",
    "WEB_APP_URL",
    "GOOGLE_REDIRECT_URI",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GMAIL_SYNC_SCOPE",
    "GMAIL_PUBSUB_TOPIC",
    "GMAIL_PUBSUB_SUBSCRIPTION",
    "GMAIL_PUBSUB_PUSH_AUDIENCE",
    "GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL",
    "MOBILE_REDIRECT_URI",
    "OPS_ADMIN_EMAILS",
    "RATE_LIMIT_ENABLED",
)


def main() -> int:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    hosted_on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT", "").strip())
    if not app_env and not hosted_on_railway:
        return 0
    if app_env == "local" and not hosted_on_railway:
        return 0

    missing = [name for name in REQUIRED_PRODUCTION_ENV_VARS if not os.getenv(name, "").strip()]
    if not (
        os.getenv("RELEASE_SHA", "").strip()
        or os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
    ):
        missing.append("RELEASE_SHA or RAILWAY_GIT_COMMIT_SHA")
    if missing:
        print(f"Missing required deployment env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL must be a Postgres URL for staging/production.", file=sys.stderr)
        return 1

    if app_env not in {"staging", "production"}:
        print("APP_ENV must be staging or production outside local development.", file=sys.stderr)
        return 1

    registration_mode = os.getenv("REGISTRATION_MODE", "").strip().lower()
    if registration_mode not in {"allowlist", "open"}:
        print("REGISTRATION_MODE must be explicitly set to allowlist or open.", file=sys.stderr)
        return 1

    ops_admin_emails = [
        email.strip().lower()
        for email in os.getenv("OPS_ADMIN_EMAILS", "").split(",")
        if email.strip()
    ]
    if not ops_admin_emails or any("@" not in email for email in ops_admin_emails):
        print("OPS_ADMIN_EMAILS must contain at least one valid admin email.", file=sys.stderr)
        return 1

    try:
        poll_interval = float(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "30"))
    except ValueError:
        poll_interval = 0
    if not 1 <= poll_interval <= 3600:
        print("GMAIL_POLL_INTERVAL_SECONDS must be between 1 and 3600.", file=sys.stderr)
        return 1

    try:
        errors = load_settings().readiness_errors()
    except (TypeError, ValueError) as exc:
        print(f"Invalid deployment configuration: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"Deployment configuration error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
