#!/usr/bin/env bash
set +x
set -euo pipefail

raw_database_url="${DATABASE_URL-}"
backup_encryption_key="${BACKUP_ENCRYPTION_KEY-}"
expected_backup_database="${EXPECTED_BACKUP_DATABASE-}"
expected_backup_host="${EXPECTED_BACKUP_HOST-}"
expected_backup_port="${EXPECTED_BACKUP_PORT-}"
export -n raw_database_url backup_encryption_key
export -n expected_backup_database expected_backup_host expected_backup_port
unset DATABASE_URL RESTORE_DATABASE_URL BACKUP_ENCRYPTION_KEY
unset EXPECTED_BACKUP_DATABASE EXPECTED_BACKUP_HOST EXPECTED_BACKUP_PORT
unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE
unset PGSSLMODE PGSSLROOTCERT PGSSLCERT PGSSLKEY PGSSLCRL PGSSLCRLDIR PGSSLPASSWORD
unset PGGSSENCMODE PGCHANNELBINDING PGTARGETSESSIONATTRS PGOPTIONS PGAPPNAME PGCONNECT_TIMEOUT
unset SSL_CERT_FILE SSL_CERT_DIR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_CRYPTO_PYTHON="${BACKUP_CRYPTO_PYTHON:-$ROOT_DIR/.venv/bin/python}"

[ -n "$raw_database_url" ] || { echo "DATABASE_URL is required" >&2; exit 1; }
[[ "$raw_database_url" == postgres://* || "$raw_database_url" == postgresql://* ]] || { echo "DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "$expected_backup_database" ] || { echo "EXPECTED_BACKUP_DATABASE is required to bind the backup to one database" >&2; exit 1; }
[[ "$expected_backup_database" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "EXPECTED_BACKUP_DATABASE contains unsupported characters" >&2; exit 1; }
[ -n "$expected_backup_host" ] || { echo "EXPECTED_BACKUP_HOST is required to bind the backup to one database server" >&2; exit 1; }
[ -n "$expected_backup_port" ] || { echo "EXPECTED_BACKUP_PORT is required to bind the backup to one database server" >&2; exit 1; }
[ -n "$backup_encryption_key" ] || { echo "BACKUP_ENCRYPTION_KEY is required" >&2; exit 1; }
command -v pg_dump >/dev/null 2>&1 || { echo "pg_dump is required" >&2; exit 1; }
pg_dump_version="$(LC_ALL=C pg_dump --version 2>/dev/null)" || {
  echo "Could not determine the production pg_dump version" >&2
  exit 1
}
if [[ "$pg_dump_version" =~ \(PostgreSQL\)[[:space:]]+([0-9]+)\.([0-9]+)([[:space:]]|$) ]]; then
  pg_dump_major="${BASH_REMATCH[1]}"
  pg_dump_minor="${BASH_REMATCH[2]}"
else
  echo "Could not parse the production pg_dump version" >&2
  exit 1
fi
[ "$pg_dump_major" = "17" ] && [ "$pg_dump_minor" -ge 10 ] || {
  echo "Production backups require the reviewed PostgreSQL 17.10 or newer 17.x pg_dump client" >&2
  exit 1
}
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
  "$BACKUP_CRYPTO_PYTHON" - \
    "$pgpass_file" "$expected_backup_database" "$expected_backup_host" "$expected_backup_port" <<'PY'
import ipaddress
import os
import re
import sys
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

raw_url = os.environ["DATABASE_URL"]
pgpass_path = sys.argv[1]
expected_database = sys.argv[2]
expected_host = sys.argv[3]
expected_port_text = sys.argv[4]
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

if database_name != expected_database:
    raise SystemExit(
        f"Backup database mismatch: expected {expected_database}, URL targets {database_name}"
    )

try:
    authority_port = parts.port
except ValueError as error:
    raise SystemExit(f"DATABASE_URL has an invalid port: {error}") from error
if authority_port is not None and authority_port < 1:
    raise SystemExit("DATABASE_URL port must be between 1 and 65535")
effective_port = 5432 if authority_port is None else authority_port

if (
    expected_host != expected_host.strip()
    or expected_host != expected_host.lower()
    or expected_host.endswith(".")
    or any(ord(character) < 33 for character in expected_host)
    or any(character in expected_host for character in "/,@%\\")
):
    raise SystemExit("EXPECTED_BACKUP_HOST must be one canonical lowercase hostname or IP address")
try:
    expected_address = ipaddress.ip_address(expected_host)
except ValueError:
    expected_address = None
if expected_address is not None:
    if expected_host != expected_address.compressed:
        raise SystemExit("EXPECTED_BACKUP_HOST IP address must use its canonical compressed representation")
    canonical_hostinfo = f"[{expected_host}]" if expected_address.version == 6 else expected_host
else:
    labels = expected_host.split(".")
    if (
        len(expected_host) > 253
        or len(labels) < 2
        or re.fullmatch(r"[0-9.]+", expected_host)
        or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            is None
            for label in labels
        )
    ):
        raise SystemExit("EXPECTED_BACKUP_HOST must use canonical lowercase ASCII DNS labels")
    canonical_hostinfo = expected_host
if authority_host != expected_host:
    raise SystemExit(
        f"Backup host mismatch: expected {expected_host}, URL targets {authority_host}"
    )
if authority_port is not None:
    canonical_hostinfo = f"{canonical_hostinfo}:{authority_port}"
if raw_hostinfo != canonical_hostinfo:
    raise SystemExit("DATABASE_URL host and port must use their canonical representation")

if (
    re.fullmatch(r"[1-9][0-9]{0,4}", expected_port_text) is None
    or int(expected_port_text) > 65535
):
    raise SystemExit("EXPECTED_BACKUP_PORT must be an integer from 1 through 65535")
if effective_port != int(expected_port_text):
    raise SystemExit(
        f"Backup port mismatch: expected {expected_port_text}, URL targets {effective_port}"
    )

authority_password = None
sanitized_netloc = parts.netloc
userinfo, separator, hostinfo = parts.netloc.rpartition("@")
if separator:
    encoded_username, password_separator, encoded_password = userinfo.partition(":")
    sanitized_netloc = f"{encoded_username}@{hostinfo}"
    if password_separator:
        authority_password = unquote(encoded_password)

query_items = parse_qsl(parts.query, keep_blank_values=True)
query_keys = [key.lower() for key, _value in query_items]
duplicate_query_keys = sorted({key for key in query_keys if query_keys.count(key) > 1})
if duplicate_query_keys:
    raise SystemExit(
        "DATABASE_URL contains duplicate query parameters: "
        + ", ".join(duplicate_query_keys)
    )
if "sslpassword" in query_keys:
    raise SystemExit(
        "DATABASE_URL sslpassword is forbidden; encrypted TLS client keys are unsupported by this backup path"
    )
routing_query_keys = {
    "dbname", "host", "hostaddr", "port", "user", "service", "servicefile", "passfile"
}
forbidden_routing_keys = sorted({key.lower() for key, _value in query_items} & routing_query_keys)
if forbidden_routing_keys:
    raise SystemExit(
        "DATABASE_URL connection-routing query parameters are forbidden: "
        + ", ".join(forbidden_routing_keys)
    )
query_values = {key.lower(): value for key, value in query_items}
if query_values.get("sslmode") != "verify-full":
    raise SystemExit("production backup requires sslmode=verify-full")
if query_values.get("gssencmode") != "disable":
    raise SystemExit("production backup requires gssencmode=disable so TLS verification cannot be bypassed")
ssl_root_cert = query_values.get("sslrootcert", "")
if not ssl_root_cert:
    raise SystemExit("production backup requires an explicit sslrootcert trust source")
if ssl_root_cert != "system":
    if not os.path.isabs(ssl_root_cert) or not os.path.isfile(ssl_root_cert):
        raise SystemExit(
            "production sslrootcert must be 'system' or an existing absolute CA bundle path"
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
  PGDATABASE="$sanitized_database_url" PGPASSFILE="$pgpass_file" PGCONNECT_TIMEOUT=10 \
    pg_dump --format=custom --compress=9 --no-owner --no-acl --lock-wait-timeout=10s | \
    BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
      "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      encrypt-stdin --key-env --output "$temporary_encrypted"
else
  PGDATABASE="$sanitized_database_url" PGCONNECT_TIMEOUT=10 \
    pg_dump --format=custom --compress=9 --no-owner --no-acl --lock-wait-timeout=10s | \
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
