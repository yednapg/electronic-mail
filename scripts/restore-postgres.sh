#!/usr/bin/env bash
set +x
set -euo pipefail

raw_restore_database_url="${RESTORE_DATABASE_URL-}"
backup_encryption_key="${BACKUP_ENCRYPTION_KEY-}"
export -n raw_restore_database_url backup_encryption_key
unset RESTORE_DATABASE_URL BACKUP_ENCRYPTION_KEY
unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_CRYPTO_PYTHON="${BACKUP_CRYPTO_PYTHON:-$ROOT_DIR/.venv/bin/python}"
RESTORE_TARGET_CLASS="${RESTORE_TARGET_CLASS:-isolated}"
ALLOW_UNVERIFIED_RESTORE="${ALLOW_UNVERIFIED_RESTORE:-0}"

[ "${CONFIRM_RESTORE:-}" = "RESTORE" ] || { echo "Set CONFIRM_RESTORE=RESTORE to acknowledge the destructive restore." >&2; exit 1; }
[ -n "$raw_restore_database_url" ] || { echo "RESTORE_DATABASE_URL is required" >&2; exit 1; }
[[ "$raw_restore_database_url" == postgres://* || "$raw_restore_database_url" == postgresql://* ]] || { echo "RESTORE_DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "${EXPECTED_RESTORE_DATABASE:-}" ] || { echo "EXPECTED_RESTORE_DATABASE is required to bind the confirmation to one database" >&2; exit 1; }
[[ "$EXPECTED_RESTORE_DATABASE" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "EXPECTED_RESTORE_DATABASE contains unsupported characters" >&2; exit 1; }
[ -n "$backup_encryption_key" ] || { echo "BACKUP_ENCRYPTION_KEY is required" >&2; exit 1; }
case "$ALLOW_UNVERIFIED_RESTORE" in
  0 | 1) ;;
  *) echo "ALLOW_UNVERIFIED_RESTORE must be 0 or 1" >&2; exit 1 ;;
esac
case "$RESTORE_TARGET_CLASS" in
  isolated) ;;
  production)
    [ "$ALLOW_UNVERIFIED_RESTORE" = "0" ] || {
      echo "ALLOW_UNVERIFIED_RESTORE is forbidden for production restores" >&2
      exit 1
    }
    [ "${CONFIRM_PRODUCTION_RESTORE:-}" = "RESTORE_PRODUCTION_DATABASE" ] || {
      echo "Set CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE for a reviewed production restore" >&2
      exit 1
    }
    ;;
  *) echo "RESTORE_TARGET_CLASS must be isolated or production" >&2; exit 1 ;;
esac
[ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ] || { echo "BACKUP_FILE must point to a dump" >&2; exit 1; }
[[ "$BACKUP_FILE" == *.dump.enc ]] || { echo "BACKUP_FILE must point to an authenticated encrypted .dump.enc artifact" >&2; exit 1; }
command -v pg_restore >/dev/null 2>&1 || { echo "pg_restore is required" >&2; exit 1; }
[ -x "$BACKUP_CRYPTO_PYTHON" ] || { echo "BACKUP_CRYPTO_PYTHON must point to the canonical installed Python environment" >&2; exit 1; }
[ -f "$ROOT_DIR/scripts/postgres-backup-crypto.py" ] || { echo "postgres-backup-crypto.py is required" >&2; exit 1; }

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

BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
  "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
  validate-key --key-env

decrypt_arguments=(decrypt-stdout --key-env --input "$BACKUP_FILE")
if [ -f "$BACKUP_FILE.sha256" ]; then
  expected_checksum="$(awk 'NR == 1 { print $1 }' "$BACKUP_FILE.sha256")"
  [[ "$expected_checksum" =~ ^[0-9a-f]{64}$ ]] || {
    echo "Backup checksum verification failed" >&2
    exit 1
  }
  decrypt_arguments+=(--expected-sha256 "$expected_checksum")
elif [ "$ALLOW_UNVERIFIED_RESTORE" != "1" ]; then
  echo "Backup checksum is required; ALLOW_UNVERIFIED_RESTORE=1 is limited to reviewed isolated recovery." >&2
  exit 1
fi

sanitized_database_url="$(RESTORE_DATABASE_URL="$raw_restore_database_url" \
  "$BACKUP_CRYPTO_PYTHON" - "$pgpass_file" "$database_name_file" <<'PY'
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
    raise SystemExit("RESTORE_DATABASE_URL must identify one explicit host and database")

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
        "RESTORE_DATABASE_URL connection-routing query parameters are forbidden: "
        + ", ".join(forbidden_routing_keys)
    )
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
export -n sanitized_database_url
unset raw_restore_database_url
actual_database_name="$(cat "$database_name_file")"
[ "$actual_database_name" = "$EXPECTED_RESTORE_DATABASE" ] || {
  echo "Restore database mismatch: expected $EXPECTED_RESTORE_DATABASE, URL targets $actual_database_name" >&2
  exit 1
}
if [ "$RESTORE_TARGET_CLASS" = "isolated" ]; then
  case "$actual_database_name" in
    postgres | template0 | template1 | electronic_mail)
      echo "Refusing to treat protected database $actual_database_name as an isolated restore target" >&2
      exit 1
      ;;
  esac
fi

restore_arguments=(
  --clean --if-exists --no-owner --no-acl
  --exit-on-error --single-transaction
)
if [ -s "$pgpass_file" ]; then
  BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
    "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      "${decrypt_arguments[@]}" | \
    PGDATABASE="$sanitized_database_url" PGPASSFILE="$pgpass_file" \
      pg_restore "${restore_arguments[@]}"
else
  BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
    "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      "${decrypt_arguments[@]}" | \
    PGDATABASE="$sanitized_database_url" \
      pg_restore "${restore_arguments[@]}"
fi
unset backup_encryption_key

echo "Authenticated encrypted restore completed; run migrations and launch verification before directing traffic."
