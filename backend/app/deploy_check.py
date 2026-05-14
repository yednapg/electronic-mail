from __future__ import annotations

"""Fail-fast deployment guard for production-like Railway starts."""

import os
from pathlib import Path
import sys

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


REQUIRED_PRODUCTION_ENV_VARS = (
    "APP_ENV",
    "DATABASE_URL",
    "APP_SESSION_SECRET",
    "SESSION_COOKIE_DOMAIN",
    "SESSION_COOKIE_SAMESITE",
    "APP_ENCRYPTION_KEY",
    "ALLOWED_EMAILS",
    "CORS_ORIGIN",
    "WEB_APP_URL",
    "GOOGLE_REDIRECT_URI",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GMAIL_PUBSUB_TOPIC",
    "GMAIL_PUBSUB_SUBSCRIPTION",
    "MOBILE_REDIRECT_URI",
)


def main() -> int:
    app_env = os.getenv("APP_ENV", "").strip().lower()
    if not app_env and not os.getenv("RAILWAY_ENVIRONMENT"):
        return 0
    if app_env == "local":
        return 0

    missing = [name for name in REQUIRED_PRODUCTION_ENV_VARS if not os.getenv(name, "").strip()]
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
