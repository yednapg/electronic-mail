#!/usr/bin/env bash
set -euo pipefail

[ -n "${DATABASE_URL:-}" ] || { echo "DATABASE_URL is required" >&2; exit 1; }
[[ "$DATABASE_URL" == postgres://* || "$DATABASE_URL" == postgresql://* ]] || { echo "DATABASE_URL must be Postgres" >&2; exit 1; }
command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

checksum_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1"
  else
    echo "sha256sum or shasum is required" >&2
    return 1
  fi
}

umask 077
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-${LEGAL_BACKUP_RETENTION_DAYS:-14}}"
[[ "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]] \
  && [ "${#RETENTION_DAYS}" -le 3 ] \
  && [ "$RETENTION_DAYS" -le 365 ] \
  || { echo "RETENTION_DAYS must be an integer from 1 through 365" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

lock_dir="$BACKUP_DIR/.electronic-mail-backup.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "Another backup is already running (lock: $lock_dir)" >&2
  exit 1
fi

credential_dir="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-backup.XXXXXX")"
pgpass_file="$credential_dir/pgpass"
temporary=""
temporary_checksum=""
cleanup() {
  local status=$?
  [ -z "$temporary" ] || rm -f "$temporary"
  [ -z "$temporary_checksum" ] || rm -f "$temporary_checksum"
  rm -rf "$credential_dir"
  rmdir "$lock_dir" 2>/dev/null || true
  return "$status"
}
trap cleanup EXIT

sanitized_database_url="$(python3 - "$pgpass_file" <<'PY'
import os
import sys
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

raw_url = os.environ["DATABASE_URL"]
pgpass_path = sys.argv[1]
if any(ord(character) < 32 for character in raw_url):
    raise SystemExit("DATABASE_URL must not contain control characters")

parts = urlsplit(raw_url)
if parts.scheme not in {"postgres", "postgresql"} or parts.fragment:
    raise SystemExit("DATABASE_URL must be a Postgres URL without a fragment")

authority_password = None
sanitized_netloc = parts.netloc
userinfo, separator, hostinfo = parts.netloc.rpartition("@")
if separator:
    encoded_username, password_separator, encoded_password = userinfo.partition(":")
    sanitized_netloc = f"{encoded_username}@{hostinfo}"
    if password_separator:
        authority_password = unquote(encoded_password)

query_items = parse_qsl(parts.query, keep_blank_values=True)
query_passwords = [value for key, value in query_items if key.lower() == "password"]
if len(query_passwords) > 1 or (authority_password is not None and query_passwords):
    raise SystemExit("DATABASE_URL contains ambiguous password settings")
password = authority_password if authority_password is not None else (query_passwords[0] if query_passwords else None)
sanitized_query = urlencode(
    [(key, value) for key, value in query_items if key.lower() != "password"]
)
sanitized_url = urlunsplit((parts.scheme, sanitized_netloc, parts.path, sanitized_query, ""))

with open(pgpass_path, "w", encoding="utf-8", newline="\n") as pgpass:
    if password is not None:
        if any(character in password for character in "\r\n\0"):
            raise SystemExit("DATABASE_URL password must not contain control characters")
        escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
        pgpass.write(f"*:*:*:*:{escaped_password}\n")
os.chmod(pgpass_path, 0o600)
print(sanitized_url)
PY
)"
unset DATABASE_URL

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="$BACKUP_DIR/electronic-mail-$timestamp.dump"
temporary="$output.partial"
[ ! -e "$output" ] && [ ! -e "$temporary" ] || {
  echo "Backup output already exists for timestamp $timestamp" >&2
  exit 1
}

if [ -s "$pgpass_file" ]; then
  PGDATABASE="$sanitized_database_url" PGPASSFILE="$pgpass_file" \
    pg_dump --format=custom --compress=9 --no-owner --no-acl --file="$temporary"
else
  PGDATABASE="$sanitized_database_url" \
    pg_dump --format=custom --compress=9 --no-owner --no-acl --file="$temporary"
fi
mv "$temporary" "$output"
temporary=""
temporary_checksum="$output.sha256.partial"
(
  cd "$BACKUP_DIR"
  checksum_file "$(basename "$output")" > "$(basename "$temporary_checksum")"
)
mv "$temporary_checksum" "$output.sha256"
temporary_checksum=""

# Retention runs only after a complete dump and checksum exist. A failed new
# backup must never remove the last known-good recovery point.
find "$BACKUP_DIR" -type f \( -name 'electronic-mail-*.dump' -o -name 'electronic-mail-*.dump.sha256' \) \
  -mtime "+$RETENTION_DAYS" -delete

echo "Backup completed: $output"
