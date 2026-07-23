#!/usr/bin/env bash
set -euo pipefail

[ "${CONFIRM_RESTORE:-}" = "RESTORE" ] || { echo "Set CONFIRM_RESTORE=RESTORE to acknowledge the destructive restore." >&2; exit 1; }
[ -n "${RESTORE_DATABASE_URL:-}" ] || { echo "RESTORE_DATABASE_URL is required" >&2; exit 1; }
[[ "$RESTORE_DATABASE_URL" == postgres://* || "$RESTORE_DATABASE_URL" == postgresql://* ]] || { echo "RESTORE_DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "${EXPECTED_RESTORE_DATABASE:-}" ] || { echo "EXPECTED_RESTORE_DATABASE is required to bind the confirmation to one database" >&2; exit 1; }
[[ "$EXPECTED_RESTORE_DATABASE" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "EXPECTED_RESTORE_DATABASE contains unsupported characters" >&2; exit 1; }
case "${RESTORE_TARGET_CLASS:-isolated}" in
  isolated) ;;
  production)
    [ "${CONFIRM_PRODUCTION_RESTORE:-}" = "RESTORE_PRODUCTION_DATABASE" ] || {
      echo "Set CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE for a reviewed production restore" >&2
      exit 1
    }
    ;;
  *) echo "RESTORE_TARGET_CLASS must be isolated or production" >&2; exit 1 ;;
esac
[ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ] || { echo "BACKUP_FILE must point to a dump" >&2; exit 1; }
command -v pg_restore >/dev/null 2>&1 || { echo "pg_restore is required" >&2; exit 1; }
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

if [ -f "$BACKUP_FILE.sha256" ]; then
  expected_checksum="$(awk 'NR == 1 { print $1 }' "$BACKUP_FILE.sha256")"
  actual_checksum="$(checksum_file "$BACKUP_FILE" | awk '{ print $1 }')"
  [ -n "$expected_checksum" ] && [ "$actual_checksum" = "$expected_checksum" ] || {
    echo "Backup checksum verification failed" >&2
    exit 1
  }
elif [ "${ALLOW_UNVERIFIED_RESTORE:-0}" != "1" ]; then
  echo "Backup checksum is required; set ALLOW_UNVERIFIED_RESTORE=1 only for a reviewed legacy dump." >&2
  exit 1
fi

umask 077
credential_dir="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-restore.XXXXXX")"
pgpass_file="$credential_dir/pgpass"
database_name_file="$credential_dir/database-name"
cleanup() {
  local status=$?
  rm -rf "$credential_dir"
  return "$status"
}
trap cleanup EXIT

sanitized_database_url="$(python3 - "$pgpass_file" "$database_name_file" <<'PY'
import os
import sys
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

raw_url = os.environ["RESTORE_DATABASE_URL"]
pgpass_path = sys.argv[1]
database_name_path = sys.argv[2]
if any(ord(character) < 32 for character in raw_url):
    raise SystemExit("RESTORE_DATABASE_URL must not contain control characters")

parts = urlsplit(raw_url)
if parts.scheme not in {"postgres", "postgresql"} or parts.fragment:
    raise SystemExit("RESTORE_DATABASE_URL must be a Postgres URL without a fragment")
database_name = unquote(parts.path.removeprefix("/"))
if not database_name or "/" in database_name:
    raise SystemExit("RESTORE_DATABASE_URL must identify exactly one database")

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
    raise SystemExit("RESTORE_DATABASE_URL contains ambiguous password settings")
password = authority_password if authority_password is not None else (query_passwords[0] if query_passwords else None)
sanitized_query = urlencode(
    [(key, value) for key, value in query_items if key.lower() != "password"]
)
sanitized_url = urlunsplit((parts.scheme, sanitized_netloc, parts.path, sanitized_query, ""))

with open(pgpass_path, "w", encoding="utf-8", newline="\n") as pgpass:
    if password is not None:
        if any(character in password for character in "\r\n\0"):
            raise SystemExit("RESTORE_DATABASE_URL password must not contain control characters")
        escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
        pgpass.write(f"*:*:*:*:{escaped_password}\n")
os.chmod(pgpass_path, 0o600)
with open(database_name_path, "w", encoding="utf-8", newline="\n") as database_name_handle:
    database_name_handle.write(database_name)
os.chmod(database_name_path, 0o600)
print(sanitized_url)
PY
)"
unset RESTORE_DATABASE_URL
actual_database_name="$(cat "$database_name_file")"
[ "$actual_database_name" = "$EXPECTED_RESTORE_DATABASE" ] || {
  echo "Restore database mismatch: expected $EXPECTED_RESTORE_DATABASE, URL targets $actual_database_name" >&2
  exit 1
}
if [ "${RESTORE_TARGET_CLASS:-isolated}" = "isolated" ]; then
  case "$actual_database_name" in
    postgres | template0 | template1 | electronic_mail)
      echo "Refusing to treat protected database $actual_database_name as an isolated restore target" >&2
      exit 1
      ;;
  esac
fi

restore_arguments=(
  --clean --if-exists --no-owner --no-acl
  --exit-on-error
  "$BACKUP_FILE"
)
if [ -s "$pgpass_file" ]; then
  PGDATABASE="$sanitized_database_url" PGPASSFILE="$pgpass_file" \
    pg_restore "${restore_arguments[@]}"
else
  PGDATABASE="$sanitized_database_url" \
    pg_restore "${restore_arguments[@]}"
fi

echo "Restore completed; run migrations and launch verification before directing traffic."
