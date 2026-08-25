from __future__ import annotations

"""Prepare and validate the local-only private beta environment."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
LOCAL_ENV = BACKEND_DIR / ".env.local"
BASE_ENV = BACKEND_DIR / ".env"
EXPECTED_REDIRECT_URI = "http://localhost:3001/auth/google/callback"
EXPECTED_MOBILE_REDIRECT_URI = "electronicmail://auth/callback"
PLACEHOLDER_MARKERS = (
    "replace-me",
    "replace-in-secret-store",
    "local-dev-",
    "generate-at-least-",
    "placeholder",
)
EMAIL_PATTERN = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def resolved_environment(backend_dir: Path = BACKEND_DIR, *, include_process: bool = False) -> dict[str, str]:
    """Mirror backend dotenv precedence: .env.local, then .env."""
    values = dict(os.environ) if include_process else {}
    for key, value in parse_env_file(backend_dir / ".env.local").items():
        values.setdefault(key, value)
    for key, value in parse_env_file(backend_dir / ".env").items():
        values.setdefault(key, value)
    return values


def looks_like_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return not normalized or any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    written: set[str] = set()
    output: list[str] = []
    for line in existing_lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match and match.group(1) in updates:
            key = match.group(1)
            if key not in written:
                output.append(f"{key}={updates[key]}")
                written.add(key)
        else:
            output.append(line)
    if output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in written)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def prepare_environment(backend_dir: Path = BACKEND_DIR, *, email: str = "") -> tuple[str, ...]:
    normalized_email = email.strip().lower()
    if normalized_email and EMAIL_PATTERN.fullmatch(normalized_email) is None:
        raise ValueError("The beta tester email is not a valid email address.")

    current = resolved_environment(backend_dir)
    updates = {
        "APP_ENV": "local",
        "REGISTRATION_MODE": "allowlist",
        "GMAIL_SYNC_SCOPE": "full",
        "GOOGLE_REDIRECT_URI": EXPECTED_REDIRECT_URI,
        "MOBILE_REDIRECT_URI": EXPECTED_MOBILE_REDIRECT_URI,
        "AI_GROUPING_ENABLED": "false",
        "OPENAI_REQUIRED": "false",
        "OPENAI_DEBUG_LOGS": "false",
        "OPENAI_API_KEY": "",
    }
    if normalized_email:
        updates["ALLOWED_EMAILS"] = normalized_email

    session_secret = current.get("APP_SESSION_SECRET", "")
    encryption_key = current.get("APP_ENCRYPTION_KEY", "")
    if len(session_secret) < 32 or looks_like_placeholder(session_secret):
        updates["APP_SESSION_SECRET"] = secrets.token_hex(32)
    if (
        len(encryption_key) < 32
        or looks_like_placeholder(encryption_key)
        or encryption_key == session_secret
    ):
        updates["APP_ENCRYPTION_KEY"] = secrets.token_hex(32)
    if (
        "APP_SESSION_SECRET" in updates
        and updates.get("APP_SESSION_SECRET") == updates.get("APP_ENCRYPTION_KEY")
    ):
        updates["APP_ENCRYPTION_KEY"] = secrets.token_hex(32)

    set_env_values(backend_dir / ".env.local", updates)
    return tuple(updates)


def import_google_client_credentials(credentials_json: Path, backend_dir: Path = BACKEND_DIR) -> str:
    """Import a Google OAuth web client without exposing its secret in output."""
    try:
        document = json.loads(credentials_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Google OAuth credentials JSON could not be read.") from error

    web = document.get("web")
    if not isinstance(web, dict):
        raise ValueError("Google OAuth credentials must be for a Web application.")

    client_id = str(web.get("client_id", "")).strip()
    client_secret = str(web.get("client_secret", "")).strip()
    redirect_uris = web.get("redirect_uris", [])
    if not client_id.endswith(".apps.googleusercontent.com") or looks_like_placeholder(client_id):
        raise ValueError("Google OAuth credentials contain an invalid client ID.")
    if looks_like_placeholder(client_secret):
        raise ValueError("Google OAuth credentials contain an invalid client secret.")
    if not isinstance(redirect_uris, list) or EXPECTED_REDIRECT_URI not in redirect_uris:
        raise ValueError(f"Google OAuth client must allow {EXPECTED_REDIRECT_URI}.")

    set_env_values(
        backend_dir / ".env.local",
        {
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
        },
    )
    return str(web.get("project_id", "")).strip()


def environment_errors(backend_dir: Path = BACKEND_DIR) -> list[str]:
    values = resolved_environment(backend_dir, include_process=True)
    errors: list[str] = []

    def require_exact(name: str, expected: str) -> None:
        if values.get(name, "").strip() != expected:
            errors.append(f"{name} must be {expected}")

    require_exact("APP_ENV", "local")
    require_exact("REGISTRATION_MODE", "allowlist")
    require_exact("GMAIL_SYNC_SCOPE", "full")
    require_exact("GOOGLE_REDIRECT_URI", EXPECTED_REDIRECT_URI)
    require_exact("MOBILE_REDIRECT_URI", EXPECTED_MOBILE_REDIRECT_URI)

    allowed_emails = [item.strip().lower() for item in values.get("ALLOWED_EMAILS", "").split(",") if item.strip()]
    if not allowed_emails:
        errors.append("ALLOWED_EMAILS must contain the Gmail test account")
    elif any(EMAIL_PATTERN.fullmatch(email) is None for email in allowed_emails):
        errors.append("ALLOWED_EMAILS contains an invalid email address")

    client_id = values.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = values.get("GOOGLE_CLIENT_SECRET", "").strip()
    if looks_like_placeholder(client_id):
        errors.append("GOOGLE_CLIENT_ID is missing")
    elif not client_id.endswith(".apps.googleusercontent.com"):
        errors.append("GOOGLE_CLIENT_ID is not a Google OAuth web client ID")
    if looks_like_placeholder(client_secret):
        errors.append("GOOGLE_CLIENT_SECRET is missing")

    session_secret = values.get("APP_SESSION_SECRET", "").strip()
    encryption_key = values.get("APP_ENCRYPTION_KEY", "").strip()
    if len(session_secret) < 32 or looks_like_placeholder(session_secret):
        errors.append("APP_SESSION_SECRET must be generated local key material")
    if len(encryption_key) < 32 or looks_like_placeholder(encryption_key):
        errors.append("APP_ENCRYPTION_KEY must be generated local key material")
    if session_secret and session_secret == encryption_key:
        errors.append("APP_SESSION_SECRET and APP_ENCRYPTION_KEY must be different")

    database_url = values.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        errors.append("DATABASE_URL must point to the local Postgres database")

    for name in ("AI_GROUPING_ENABLED", "OPENAI_REQUIRED", "OPENAI_DEBUG_LOGS"):
        if values.get(name, "").strip().lower() not in {"", "false", "0", "no", "off"}:
            errors.append(f"{name} must remain disabled in the no-AI beta")
    if values.get("OPENAI_API_KEY", "").strip():
        errors.append("OPENAI_API_KEY must remain empty in the no-AI beta")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "import-google-client"))
    parser.add_argument("--email", default="")
    parser.add_argument("--credentials-json", type=Path)
    parser.add_argument("--backend-dir", type=Path, default=BACKEND_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.command == "prepare":
        try:
            changed = prepare_environment(args.backend_dir, email=args.email)
        except ValueError as error:
            print(f"Private beta environment setup failed: {error}", file=sys.stderr)
            return 1
        print("Prepared backend/.env.local with private-beta defaults and local secrets.")
        if changed:
            print("Configured keys: " + ", ".join(changed))
        print("OAuth credential values were not read or printed.")
        return 0

    if args.command == "import-google-client":
        if args.credentials_json is None:
            parser.error("import-google-client requires --credentials-json")
        try:
            project_id = import_google_client_credentials(args.credentials_json, args.backend_dir)
        except ValueError as error:
            print(f"Google OAuth credential import failed: {error}", file=sys.stderr)
            return 1
        print("Imported Google OAuth web client into backend/.env.local (values not printed).")
        if project_id:
            print(f"Google Cloud project: {project_id}")
        return 0

    errors = environment_errors(args.backend_dir)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("[PASS] Local OAuth, allowlist, secret, Postgres, and no-AI settings are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
