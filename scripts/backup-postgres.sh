#!/usr/bin/env bash
set +x
set -euo pipefail

raw_database_url="${DATABASE_URL-}"
backup_encryption_key="${BACKUP_ENCRYPTION_KEY-}"
export -n raw_database_url backup_encryption_key
unset DATABASE_URL BACKUP_ENCRYPTION_KEY
unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_CRYPTO_PYTHON="${BACKUP_CRYPTO_PYTHON:-$ROOT_DIR/.venv/bin/python}"

[ -n "$raw_database_url" ] || { echo "DATABASE_URL is required" >&2; exit 1; }
[[ "$raw_database_url" == postgres://* || "$raw_database_url" == postgresql://* ]] || { echo "DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "$backup_encryption_key" ] || { echo "BACKUP_ENCRYPTION_KEY is required" >&2; exit 1; }
command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is required" >&2; exit 1; }
[ -x "$BACKUP_CRYPTO_PYTHON" ] || { echo "BACKUP_CRYPTO_PYTHON must point to the canonical installed Python environment" >&2; exit 1; }
[ -f "$ROOT_DIR/scripts/postgres-backup-crypto.py" ] || { echo "postgres-backup-crypto.py is required" >&2; exit 1; }

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
if [ -n "${RETENTION_DAYS:-}" ] \
  && [ -n "${LEGAL_BACKUP_RETENTION_DAYS:-}" ] \
  && [ "$RETENTION_DAYS" != "$LEGAL_BACKUP_RETENTION_DAYS" ]; then
  echo "RETENTION_DAYS must match LEGAL_BACKUP_RETENTION_DAYS when both are set" >&2
  exit 1
fi
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

credential_dir=""
temporary_encrypted=""
temporary_checksum=""
incomplete_output=""
cleanup() {
  local status=$?
  [ -z "$temporary_encrypted" ] || rm -f "$temporary_encrypted"
  [ -z "$temporary_checksum" ] || rm -f "$temporary_checksum"
  if [ -n "$incomplete_output" ] && [ ! -e "$incomplete_output.sha256" ]; then
    rm -f "$incomplete_output"
  fi
  [ -z "$credential_dir" ] || rm -rf "$credential_dir"
  rmdir "$lock_dir" 2>/dev/null || true
  return "$status"
}
trap cleanup EXIT

credential_dir="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-backup.XXXXXX")"
pgpass_file="$credential_dir/pgpass"
BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
  "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" validate-key --key-env

sanitized_database_url="$(DATABASE_URL="$raw_database_url" \
  "$BACKUP_CRYPTO_PYTHON" - "$pgpass_file" <<'PY'
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
database_name = unquote(parts.path.removeprefix("/"))
authority_host = parts.hostname or ""
_userinfo, authority_separator, raw_hostinfo = parts.netloc.rpartition("@")
if not authority_separator:
    raw_hostinfo = parts.netloc
decoded_hostinfo = unquote(raw_hostinfo)
if (
    not authority_host
    or any(character in authority_host for character in ",%/\\")
    or any(character.isspace() for character in authority_host)
    or any(character in decoded_hostinfo for character in ",%/\\")
    or any(character.isspace() for character in decoded_hostinfo)
    or not database_name
    or "/" in database_name
):
    raise SystemExit("DATABASE_URL must identify one explicit host and database")

authority_password = None
sanitized_netloc = parts.netloc
userinfo, separator, hostinfo = parts.netloc.rpartition("@")
if separator:
    encoded_username, password_separator, encoded_password = userinfo.partition(":")
    sanitized_netloc = f"{encoded_username}@{hostinfo}"
    if password_separator:
        authority_password = unquote(encoded_password)

query_items = parse_qsl(parts.query, keep_blank_values=True)
routing_query_keys = {
    "dbname", "host", "hostaddr", "port", "user", "service", "servicefile", "passfile"
}
forbidden_routing_keys = sorted({key.lower() for key, _value in query_items} & routing_query_keys)
if forbidden_routing_keys:
    raise SystemExit(
        "DATABASE_URL connection-routing query parameters are forbidden: "
        + ", ".join(forbidden_routing_keys)
    )
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
export -n sanitized_database_url
unset raw_database_url

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="$BACKUP_DIR/electronic-mail-$timestamp.dump.enc"
temporary_encrypted="$output.partial"
checksum_output="$output.sha256"
checksum_partial="$checksum_output.partial"
[ ! -e "$output" ] \
  && [ ! -e "$temporary_encrypted" ] \
  && [ ! -e "$checksum_output" ] \
  && [ ! -e "$checksum_partial" ] || {
  echo "Backup output already exists for timestamp $timestamp" >&2
  exit 1
}

if [ -s "$pgpass_file" ]; then
  PGDATABASE="$sanitized_database_url" PGPASSFILE="$pgpass_file" \
    pg_dump --format=custom --compress=9 --no-owner --no-acl | \
    BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
      "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      encrypt-stdin --key-env --output "$temporary_encrypted"
else
  PGDATABASE="$sanitized_database_url" \
    pg_dump --format=custom --compress=9 --no-owner --no-acl | \
    BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
      "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      encrypt-stdin --key-env --output "$temporary_encrypted"
fi
unset backup_encryption_key
mv "$temporary_encrypted" "$output"
temporary_encrypted=""
incomplete_output="$output"
temporary_checksum="$checksum_partial"
(
  cd "$BACKUP_DIR"
  checksum_file "$(basename "$output")" > "$(basename "$temporary_checksum")"
)
mv "$temporary_checksum" "$checksum_output"
temporary_checksum=""
incomplete_output=""

# Retention runs only after a complete dump and checksum exist. A failed new
# backup must never remove the last known-good recovery point. Compare exact
# epoch timestamps because find(1) rounds -mtime differently across platforms.
"$BACKUP_CRYPTO_PYTHON" - "$BACKUP_DIR" "$RETENTION_DAYS" <<'PY'
import fnmatch
import os
from pathlib import Path
import stat
import sys
import time

backup_dir = Path(sys.argv[1])
retention_seconds = int(sys.argv[2]) * 86_400
cutoff = time.time() - retention_seconds
patterns = ("electronic-mail-*.dump.enc", "electronic-mail-*.dump.enc.sha256")

for directory, _subdirectories, filenames in os.walk(backup_dir, followlinks=False):
    for filename in filenames:
        if not any(fnmatch.fnmatchcase(filename, pattern) for pattern in patterns):
            continue
        path = Path(directory, filename)
        try:
            metadata = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if metadata.st_mtime < cutoff:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
PY

echo "Authenticated encrypted backup completed: $output"
