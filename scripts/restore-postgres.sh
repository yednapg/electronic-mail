#!/usr/bin/env bash
set +x
set -euo pipefail

raw_restore_database_url="${RESTORE_DATABASE_URL-}"
raw_restore_maintenance_database_url="${RESTORE_MAINTENANCE_DATABASE_URL-}"
backup_encryption_key="${BACKUP_ENCRYPTION_KEY-}"
export -n raw_restore_database_url raw_restore_maintenance_database_url backup_encryption_key
unset DATABASE_URL RESTORE_DATABASE_URL RESTORE_MAINTENANCE_DATABASE_URL BACKUP_ENCRYPTION_KEY
unset PGDATABASE PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE
unset PGSSLMODE PGSSLROOTCERT PGSSLCERT PGSSLKEY PGSSLCRL PGSSLCRLDIR PGSSLPASSWORD
unset PGGSSENCMODE PGCHANNELBINDING PGTARGETSESSIONATTRS PGOPTIONS PGAPPNAME PGCONNECT_TIMEOUT
unset SSL_CERT_FILE SSL_CERT_DIR

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_CRYPTO_PYTHON="${BACKUP_CRYPTO_PYTHON:-$ROOT_DIR/.venv/bin/python}"
RESTORE_TARGET_CLASS="${RESTORE_TARGET_CLASS:-isolated}"
ALLOW_UNVERIFIED_RESTORE="${ALLOW_UNVERIFIED_RESTORE:-0}"
RESTORE_FINALIZE_TIMEOUT_SECONDS="${RESTORE_FINALIZE_TIMEOUT_SECONDS:-3600}"
expected_restore_host="${EXPECTED_RESTORE_HOST-}"
expected_restore_port="${EXPECTED_RESTORE_PORT-}"

[ "${CONFIRM_RESTORE:-}" = "RESTORE" ] || { echo "Set CONFIRM_RESTORE=RESTORE to acknowledge the destructive restore." >&2; exit 1; }
[ "${CONFIRM_DIRECT_RESTORE_ENDPOINTS:-}" = "DIRECT_SINGLE_CLUSTER" ] || {
  echo "Set CONFIRM_DIRECT_RESTORE_ENDPOINTS=DIRECT_SINGLE_CLUSTER to confirm both restore URLs use direct, non-pooler endpoints for one PostgreSQL cluster." >&2
  exit 1
}
unset CONFIRM_DIRECT_RESTORE_ENDPOINTS
[ -n "$raw_restore_database_url" ] || { echo "RESTORE_DATABASE_URL is required" >&2; exit 1; }
[[ "$raw_restore_database_url" == postgres://* || "$raw_restore_database_url" == postgresql://* ]] || { echo "RESTORE_DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "$raw_restore_maintenance_database_url" ] || { echo "RESTORE_MAINTENANCE_DATABASE_URL is required to isolate the restore target" >&2; exit 1; }
[[ "$raw_restore_maintenance_database_url" == postgres://* || "$raw_restore_maintenance_database_url" == postgresql://* ]] || { echo "RESTORE_MAINTENANCE_DATABASE_URL must be Postgres" >&2; exit 1; }
[ -n "${EXPECTED_RESTORE_DATABASE:-}" ] || { echo "EXPECTED_RESTORE_DATABASE is required to bind the confirmation to one database" >&2; exit 1; }
[[ "$EXPECTED_RESTORE_DATABASE" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "EXPECTED_RESTORE_DATABASE contains unsupported characters" >&2; exit 1; }
[ -n "$backup_encryption_key" ] || { echo "BACKUP_ENCRYPTION_KEY is required" >&2; exit 1; }
case "$ALLOW_UNVERIFIED_RESTORE" in
  0 | 1) ;;
  *) echo "ALLOW_UNVERIFIED_RESTORE must be 0 or 1" >&2; exit 1 ;;
esac
if [[ ! "$RESTORE_FINALIZE_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
  || [ "${#RESTORE_FINALIZE_TIMEOUT_SECONDS}" -gt 5 ] \
  || [ "$RESTORE_FINALIZE_TIMEOUT_SECONDS" -lt 60 ] \
  || [ "$RESTORE_FINALIZE_TIMEOUT_SECONDS" -gt 86400 ]; then
  echo "RESTORE_FINALIZE_TIMEOUT_SECONDS must be an integer from 60 through 86400" >&2
  exit 1
fi
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
    [ -n "$expected_restore_host" ] || {
      echo "EXPECTED_RESTORE_HOST is required to bind a production restore to one database server" >&2
      exit 1
    }
    [ -n "$expected_restore_port" ] || {
      echo "EXPECTED_RESTORE_PORT is required to bind a production restore to one database server" >&2
      exit 1
    }
    ;;
  *) echo "RESTORE_TARGET_CLASS must be isolated or production" >&2; exit 1 ;;
esac
[ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ] || { echo "BACKUP_FILE must point to a dump" >&2; exit 1; }
[[ "$BACKUP_FILE" == *.dump.enc ]] || { echo "BACKUP_FILE must point to an authenticated encrypted .dump.enc artifact" >&2; exit 1; }
command -v pg_restore >/dev/null 2>&1 || { echo "pg_restore is required" >&2; exit 1; }
command -v psql >/dev/null 2>&1 || { echo "psql is required to prove the restore target is empty" >&2; exit 1; }
command -v mkfifo >/dev/null 2>&1 || { echo "mkfifo is required to keep restore SQL off disk" >&2; exit 1; }
for postgres_client in pg_restore psql; do
  postgres_client_version="$(LC_ALL=C "$postgres_client" --version 2>/dev/null)" || {
    echo "Could not determine the $postgres_client version" >&2
    exit 1
  }
  if [[ "$postgres_client_version" =~ \(PostgreSQL\)[[:space:]]+([0-9]+)\.([0-9]+)([[:space:]]|$) ]]; then
    postgres_client_major="${BASH_REMATCH[1]}"
    postgres_client_minor="${BASH_REMATCH[2]}"
  else
    echo "Could not parse the $postgres_client version" >&2
    exit 1
  fi
  [ "$postgres_client_major" = "17" ] && [ "$postgres_client_minor" -ge 10 ] || {
    echo "Restores require the reviewed PostgreSQL 17.10 or newer 17.x $postgres_client client" >&2
    exit 1
  }
done
[ -x "$BACKUP_CRYPTO_PYTHON" ] || { echo "BACKUP_CRYPTO_PYTHON must point to the canonical installed Python environment" >&2; exit 1; }
[ -f "$ROOT_DIR/scripts/postgres-backup-crypto.py" ] || { echo "postgres-backup-crypto.py is required" >&2; exit 1; }

umask 077
credential_dir="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-restore.XXXXXX")"
pgpass_file="$credential_dir/target.pgpass"
maintenance_pgpass_file="$credential_dir/maintenance.pgpass"
database_name_file="$credential_dir/database-name"
maintenance_database_url_file="$credential_dir/maintenance-database-url"
target_sql_fifo="$credential_dir/target.sql.fifo"
target_output_fifo="$credential_dir/target.output.fifo"
target_status_fifo="$credential_dir/target.status.fifo"
maintenance_sql_fifo="$credential_dir/maintenance.sql.fifo"
maintenance_output_fifo="$credential_dir/maintenance.output.fifo"
target_psql_pid=""
target_output_drain_pid=""
maintenance_psql_pid=""
target_connections_may_be_disabled=0
target_sql_fd_open=0
target_output_fd_open=0
target_status_fd_open=0
maintenance_sql_fd_open=0
maintenance_output_fd_open=0
maintenance_session_active=0
maintenance_session_lost=0
maintenance_response=""
target_reenable_attempted=0
critical_reenable_reported=0
commit_may_have_been_sent=0
commit_confirmed=0
critical_commit_reported=0
handshake_watchdog_pid=""
restore_session_nonce="$("$BACKUP_CRYPTO_PYTHON" -c 'import secrets; print(secrets.token_hex(16))')"
[[ "$restore_session_nonce" =~ ^[0-9a-f]{32}$ ]] || {
  echo "Could not create the restore-session isolation nonce" >&2
  exit 1
}
restore_status_nonce="$("$BACKUP_CRYPTO_PYTHON" -c 'import secrets; print(secrets.token_hex(16))')"
[[ "$restore_status_nonce" =~ ^[0-9a-f]{32}$ ]] || {
  echo "Could not create the restore-status acknowledgment nonce" >&2
  exit 1
}
[ "$restore_status_nonce" != "$restore_session_nonce" ] || {
  echo "Could not create independent restore-session and acknowledgment nonces" >&2
  exit 1
}
restore_application_name="electronic-mail-restore-$restore_session_nonce"
restore_precommit_marker="RESTORE_PRECOMMIT_OK_$restore_status_nonce"
restore_commit_marker="RESTORE_COMMIT_OK_$restore_status_nonce"

arm_handshake_watchdog() {
  local timeout_seconds="$1"
  (
    exec 5<&- 6>&- 7<&- 8>&- 9<&- 2>/dev/null || true
    sleep "$timeout_seconds"
    kill -TERM "$$" >/dev/null 2>&1 || true
  ) &
  handshake_watchdog_pid=$!
}

disarm_handshake_watchdog() {
  if [ -n "$handshake_watchdog_pid" ]; then
    kill "$handshake_watchdog_pid" >/dev/null 2>&1 || true
    wait "$handshake_watchdog_pid" >/dev/null 2>&1 || true
    handshake_watchdog_pid=""
  fi
}

maintenance_round_trip() {
  local sql="$1"
  local expected_response="$2"
  maintenance_response=""
  if [ "$maintenance_session_active" != "1" ] \
    || [ "$maintenance_session_lost" = "1" ] \
    || [ "$maintenance_sql_fd_open" != "1" ] \
    || [ "$maintenance_output_fd_open" != "1" ]; then
    maintenance_session_lost=1
    return 1
  fi
  if ! (
    trap '' PIPE
    printf '%s\n' "$sql" >&6 2>/dev/null
  ); then
    maintenance_session_lost=1
    return 1
  fi
  if ! IFS= read -r -t 45 maintenance_response <&7; then
    maintenance_session_lost=1
    return 1
  fi
  [ "$maintenance_response" = "$expected_response" ]
}

reenable_target_connections() {
  local escaped_database_name
  target_reenable_attempted=1
  escaped_database_name="${actual_database_name//\"/\"\"}"
  if ! maintenance_round_trip \
    "ALTER DATABASE \"$escaped_database_name\" ALLOW_CONNECTIONS true;
SELECT 'MAINTENANCE_REENABLE_OK';" \
    "MAINTENANCE_REENABLE_OK"; then
    return 1
  fi
  target_connections_may_be_disabled=0
}

report_critical_reenable_failure() {
  if [ "$critical_reenable_reported" = "0" ]; then
    echo "CRITICAL: the persistent maintenance session could not confirm re-enabling connections to restore target $actual_database_name. Do not reconnect through either restore URL; use a reviewed direct endpoint to inspect the cluster and run ALTER DATABASE ... ALLOW_CONNECTIONS true manually if required." >&2
    critical_reenable_reported=1
  fi
}

report_indeterminate_commit() {
  if [ "$critical_commit_reported" = "0" ]; then
    echo "CRITICAL: the restore COMMIT outcome is indeterminate because its acknowledgment was not observed. Target connections remain disabled; inspect the target through a reviewed direct maintenance endpoint before deciding whether to re-enable traffic or retry." >&2
    critical_commit_reported=1
  fi
}

terminate_target_psql() {
  local attempt
  if [ -z "$target_psql_pid" ]; then
    return
  fi
  kill -TERM "$target_psql_pid" >/dev/null 2>&1 || true
  for attempt in {1..50}; do
    if ! kill -0 "$target_psql_pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
  if kill -0 "$target_psql_pid" >/dev/null 2>&1; then
    kill -KILL "$target_psql_pid" >/dev/null 2>&1 || true
  fi
  wait "$target_psql_pid" >/dev/null 2>&1 || true
  target_psql_pid=""
}

terminate_maintenance_psql() {
  if [ -n "$maintenance_psql_pid" ]; then
    kill -TERM "$maintenance_psql_pid" >/dev/null 2>&1 || true
    sleep 0.1
    kill -KILL "$maintenance_psql_pid" >/dev/null 2>&1 || true
    wait "$maintenance_psql_pid" >/dev/null 2>&1 || true
    maintenance_psql_pid=""
  fi
  if [ "$maintenance_sql_fd_open" = "1" ]; then
    exec 6>&-
    maintenance_sql_fd_open=0
  fi
  if [ "$maintenance_output_fd_open" = "1" ]; then
    exec 7<&-
    maintenance_output_fd_open=0
  fi
  maintenance_session_active=0
}

cleanup() {
  local status=$?
  local reenable_status=0
  set +e
  disarm_handshake_watchdog
  # Never signal clean EOF while a restore transaction may still be open: psql
  # could interpret EOF as success. Terminate and reap the session first so
  # PostgreSQL rolls the uncommitted transaction back, then release the FIFO.
  terminate_target_psql
  if [ "$target_sql_fd_open" = "1" ]; then
    exec 8>&-
    target_sql_fd_open=0
  fi
  if [ "$target_output_fd_open" = "1" ]; then
    exec 9<&-
    target_output_fd_open=0
  fi
  if [ -n "$target_output_drain_pid" ]; then
    wait "$target_output_drain_pid" >/dev/null 2>&1 || true
    target_output_drain_pid=""
  fi
  if [ "$target_status_fd_open" = "1" ]; then
    exec 5<&-
    target_status_fd_open=0
  fi
  if [ "$target_connections_may_be_disabled" = "1" ]; then
    if [ "$commit_may_have_been_sent" = "1" ] && [ "$commit_confirmed" != "1" ]; then
      report_indeterminate_commit
      status=1
    elif [ "$target_reenable_attempted" = "1" ]; then
      report_critical_reenable_failure
      status=1
    elif [ "$maintenance_session_active" != "1" ] || [ "$maintenance_session_lost" = "1" ]; then
      report_critical_reenable_failure
      status=1
    else
      reenable_target_connections >/dev/null 2>&1 || reenable_status=$?
      if [ "$reenable_status" -ne 0 ]; then
        report_critical_reenable_failure
        status=1
      fi
    fi
  fi
  terminate_maintenance_psql
  rm -rf "$credential_dir"
  set -e
  return "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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

sanitized_database_url="$( \
  RESTORE_DATABASE_URL="$raw_restore_database_url" \
  RESTORE_MAINTENANCE_DATABASE_URL="$raw_restore_maintenance_database_url" \
  "$BACKUP_CRYPTO_PYTHON" - \
    "$pgpass_file" "$maintenance_pgpass_file" "$database_name_file" \
    "$maintenance_database_url_file" "$expected_restore_host" \
    "$expected_restore_port" "$RESTORE_TARGET_CLASS" <<'PY'
import ipaddress
import os
import re
import sys
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

target_pgpass_path = sys.argv[1]
maintenance_pgpass_path = sys.argv[2]
database_name_path = sys.argv[3]
maintenance_database_url_path = sys.argv[4]
expected_host = sys.argv[5]
expected_port_text = sys.argv[6]
restore_target_class = sys.argv[7]


def canonical_host(authority_host: str, authority_port: int | None, raw_hostinfo: str, label: str) -> str:
    if (
        not authority_host
        or any(character in authority_host for character in ",%/\\")
        or any(character in unquote(raw_hostinfo) for character in ",%/\\")
        or any(character.isspace() for character in unquote(raw_hostinfo))
    ):
        raise SystemExit(f"{label} must identify one explicit host and database")
    if (
        authority_host != authority_host.lower()
        or authority_host.endswith(".")
        or any(ord(character) < 33 for character in authority_host)
    ):
        raise SystemExit(f"{label} must identify one canonical host")
    try:
        address = ipaddress.ip_address(authority_host)
    except ValueError:
        address = None
    if address is not None:
        if authority_host != address.compressed:
            raise SystemExit(f"{label} IP address must use its canonical compressed representation")
        canonical = f"[{authority_host}]" if address.version == 6 else authority_host
    else:
        labels = authority_host.split(".")
        if (
            len(authority_host) > 253
            or re.fullmatch(r"[0-9.]+", authority_host)
            or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", item)
                is None
                for item in labels
            )
        ):
            raise SystemExit(f"{label} must use canonical lowercase ASCII DNS labels")
        canonical = authority_host
    if authority_port is not None:
        canonical = f"{canonical}:{authority_port}"
    if raw_hostinfo != canonical:
        raise SystemExit(f"{label} host and port must use their canonical representation")
    return authority_host


def parse_connection(raw_url: str, label: str, pgpass_path: str) -> dict[str, object]:
    if any(ord(character) < 32 for character in raw_url):
        raise SystemExit(f"{label} must not contain control characters")
    parts = urlsplit(raw_url)
    if parts.scheme not in {"postgres", "postgresql"} or parts.fragment:
        raise SystemExit(f"{label} must be a Postgres URL without a fragment")
    database_name = unquote(parts.path.removeprefix("/"))
    authority_host = parts.hostname or ""
    _userinfo, authority_separator, raw_hostinfo = parts.netloc.rpartition("@")
    if not authority_separator:
        raw_hostinfo = parts.netloc
    decoded_hostinfo = unquote(raw_hostinfo)
    if (
        not database_name
        or "/" in database_name
        or any(character in raw_hostinfo for character in ",%/\\")
        or any(character in decoded_hostinfo for character in ",%/\\")
        or any(character.isspace() for character in decoded_hostinfo)
    ):
        raise SystemExit(f"{label} must identify one explicit host and database")
    try:
        authority_port = parts.port
    except ValueError as error:
        raise SystemExit(f"{label} has an invalid port: {error}") from error
    if authority_port is not None and authority_port < 1:
        raise SystemExit(f"{label} port must be between 1 and 65535")
    authority_host = canonical_host(authority_host, authority_port, raw_hostinfo, label)
    effective_port = 5432 if authority_port is None else authority_port

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
            f"{label} contains duplicate query parameters: " + ", ".join(duplicate_query_keys)
        )
    if "sslpassword" in query_keys:
        raise SystemExit(
            f"{label} sslpassword is forbidden; encrypted TLS client keys are unsupported by this restore path"
        )
    routing_query_keys = {
        "connect_timeout", "dbname", "host", "hostaddr", "options", "passfile",
        "port", "service", "servicefile", "user"
    }
    forbidden_routing_keys = sorted(set(query_keys) & routing_query_keys)
    if forbidden_routing_keys:
        raise SystemExit(
            f"{label} connection-routing query parameters are forbidden: "
            + ", ".join(forbidden_routing_keys)
        )
    query_values = {key.lower(): value for key, value in query_items}
    if restore_target_class == "production":
        policy_subject = "restore" if label == "RESTORE_DATABASE_URL" else "restore maintenance connection"
        if query_values.get("sslmode") != "verify-full":
            raise SystemExit(f"production {policy_subject} requires sslmode=verify-full")
        if query_values.get("gssencmode") != "disable":
            raise SystemExit(
                f"production {policy_subject} requires gssencmode=disable so TLS verification cannot be bypassed"
            )
        ssl_root_cert = query_values.get("sslrootcert", "")
        if not ssl_root_cert:
            raise SystemExit(f"production {policy_subject} requires an explicit sslrootcert trust source")
        if ssl_root_cert != "system" and (
            not os.path.isabs(ssl_root_cert) or not os.path.isfile(ssl_root_cert)
        ):
            if label == "RESTORE_DATABASE_URL":
                raise SystemExit(
                    "production sslrootcert must be 'system' or an existing absolute CA bundle path"
                )
            raise SystemExit(
                "production restore maintenance connection sslrootcert must be 'system' "
                "or an existing absolute CA bundle path"
            )

    query_passwords = [value for key, value in query_items if key.lower() == "password"]
    if len(query_passwords) > 1 or (authority_password is not None and query_passwords):
        raise SystemExit(f"{label} contains ambiguous password settings")
    password = authority_password if authority_password is not None else (
        query_passwords[0] if query_passwords else None
    )
    if password is not None and any(character in password for character in "\r\n\0"):
        raise SystemExit(f"{label} password must not contain control characters")
    with open(pgpass_path, "w", encoding="utf-8", newline="\n") as pgpass:
        if password is not None:
            escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
            pgpass.write(f"*:*:*:*:{escaped_password}\n")
    os.chmod(pgpass_path, 0o600)
    sanitized_query = urlencode(
        [(key, value) for key, value in query_items if key.lower() != "password"]
    )
    return {
        "database": database_name,
        "host": authority_host,
        "port": effective_port,
        "url": urlunsplit((parts.scheme, sanitized_netloc, parts.path, sanitized_query, "")),
    }


def validate_expected_host(value: str) -> None:
    if (
        value != value.strip()
        or value != value.lower()
        or value.endswith(".")
        or any(ord(character) < 33 for character in value)
        or any(character in value for character in "/,@%\\")
    ):
        raise SystemExit("EXPECTED_RESTORE_HOST must be one canonical lowercase hostname or IP address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is not None:
        if value != address.compressed:
            raise SystemExit(
                "EXPECTED_RESTORE_HOST IP address must use its canonical compressed representation"
            )
        return
    labels = value.split(".")
    if (
        len(value) > 253
        or len(labels) < 2
        or re.fullmatch(r"[0-9.]+", value)
        or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", item) is None
            for item in labels
        )
    ):
        raise SystemExit("EXPECTED_RESTORE_HOST must use canonical lowercase ASCII DNS labels")


if expected_host:
    validate_expected_host(expected_host)
target = parse_connection(os.environ["RESTORE_DATABASE_URL"], "RESTORE_DATABASE_URL", target_pgpass_path)
maintenance = parse_connection(
    os.environ["RESTORE_MAINTENANCE_DATABASE_URL"],
    "RESTORE_MAINTENANCE_DATABASE_URL",
    maintenance_pgpass_path,
)
if maintenance["database"] != "postgres":
    raise SystemExit("RESTORE_MAINTENANCE_DATABASE_URL must target the postgres maintenance database")
if target["database"] == "postgres":
    raise SystemExit("RESTORE_DATABASE_URL cannot target the postgres maintenance database")
if target["host"] != maintenance["host"] or target["port"] != maintenance["port"]:
    raise SystemExit("Restore target and maintenance URLs must use the same canonical host and port")

if expected_host:
    if target["host"] != expected_host:
        raise SystemExit(
            f"Restore host mismatch: expected {expected_host}, URL targets {target['host']}"
        )
if expected_port_text:
    if (
        re.fullmatch(r"[1-9][0-9]{0,4}", expected_port_text) is None
        or int(expected_port_text) > 65535
    ):
        raise SystemExit("EXPECTED_RESTORE_PORT must be an integer from 1 through 65535")
    if target["port"] != int(expected_port_text):
        raise SystemExit(
            f"Restore port mismatch: expected {expected_port_text}, URL targets {target['port']}"
        )

with open(database_name_path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(str(target["database"]))
os.chmod(database_name_path, 0o600)
with open(maintenance_database_url_path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(str(maintenance["url"]))
os.chmod(maintenance_database_url_path, 0o600)
print(target["url"])
PY
)"
export -n sanitized_database_url
unset raw_restore_database_url raw_restore_maintenance_database_url
sanitized_maintenance_database_url="$(cat "$maintenance_database_url_file")"
export -n sanitized_maintenance_database_url
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

# pg_restore --clean only removes objects represented in the archive. This
# preflight runs in the same target session and transaction as the restore,
# after the maintenance connection has disabled every new target connection and
# terminated every other target backend. Public-schema security metadata must
# also match a pristine PostgreSQL 17 database because --no-owner/--no-acl do
# not repair hostile target ownership or grants.
restore_target_preflight_sql="$(cat <<'SQL'
DO $electronic_mail_restore_preflight$
BEGIN
  IF pg_catalog.current_setting('server_version_num')::pg_catalog.int4 < 170010
    OR pg_catalog.current_setting('server_version_num')::pg_catalog.int4 >= 180000 THEN
    RAISE EXCEPTION 'Restore target must run reviewed PostgreSQL 17.10 or newer 17.x';
  END IF;
  IF EXISTS (
    WITH user_namespaces AS MATERIALIZED (
      SELECT oid, nspname, nspowner, nspacl
      FROM pg_catalog.pg_namespace
      WHERE nspname !~ '^pg_'
        AND nspname <> 'information_schema'
    ),
    unexpected_objects AS (
      SELECT 1 WHERE NOT EXISTS (
        SELECT 1 FROM user_namespaces WHERE nspname = 'public'
      )
      UNION ALL
      SELECT 1 FROM user_namespaces WHERE nspname <> 'public'
      UNION ALL
      SELECT 1
      FROM user_namespaces namespace
      WHERE namespace.nspname = 'public'
        AND (
          namespace.nspowner <> 'pg_database_owner'::pg_catalog.regrole
          OR pg_catalog.obj_description(namespace.oid, 'pg_namespace')
            IS DISTINCT FROM 'standard public schema'
          OR (
            SELECT pg_catalog.count(*) <> 3
              OR COALESCE(
                pg_catalog.bool_or(
                  privilege.grantor <> namespace.nspowner
                  OR privilege.is_grantable
                  OR NOT (
                    (
                      privilege.grantee = namespace.nspowner
                      AND privilege.privilege_type IN ('CREATE', 'USAGE')
                    )
                    OR (
                      privilege.grantee = 0
                      AND privilege.privilege_type = 'USAGE'
                    )
                  )
                ),
                true
              )
            FROM pg_catalog.aclexplode(
              COALESCE(namespace.nspacl, '{}'::pg_catalog.aclitem[])
            ) privilege
          )
          OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_seclabel security_label
            WHERE security_label.classoid = 'pg_catalog.pg_namespace'::pg_catalog.regclass
              AND security_label.objoid = namespace.oid
          )
        )
      UNION ALL
      SELECT 1
      FROM pg_catalog.pg_depend dependency
        JOIN user_namespaces namespace
          ON dependency.refclassid = 'pg_catalog.pg_namespace'::pg_catalog.regclass
          AND dependency.refobjid = namespace.oid
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_class object
        JOIN user_namespaces namespace ON namespace.oid = object.relnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_proc object
        JOIN user_namespaces namespace ON namespace.oid = object.pronamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_type object
        JOIN user_namespaces namespace ON namespace.oid = object.typnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_collation object
        JOIN user_namespaces namespace ON namespace.oid = object.collnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_conversion object
        JOIN user_namespaces namespace ON namespace.oid = object.connamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_operator object
        JOIN user_namespaces namespace ON namespace.oid = object.oprnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_opclass object
        JOIN user_namespaces namespace ON namespace.oid = object.opcnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_opfamily object
        JOIN user_namespaces namespace ON namespace.oid = object.opfnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_constraint object
        JOIN user_namespaces namespace ON namespace.oid = object.connamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_statistic_ext object
        JOIN user_namespaces namespace ON namespace.oid = object.stxnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_ts_config object
        JOIN user_namespaces namespace ON namespace.oid = object.cfgnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_ts_dict object
        JOIN user_namespaces namespace ON namespace.oid = object.dictnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_ts_parser object
        JOIN user_namespaces namespace ON namespace.oid = object.prsnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_ts_template object
        JOIN user_namespaces namespace ON namespace.oid = object.tmplnamespace
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_extension WHERE extname <> 'plpgsql'
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_event_trigger
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_publication
      UNION ALL
      SELECT 1
      FROM pg_catalog.pg_subscription
      WHERE subdbid = (
        SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
      )
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_foreign_server
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_user_mapping
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_largeobject_metadata
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_default_acl
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_language
        WHERE lanname NOT IN ('internal', 'c', 'sql', 'plpgsql')
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_cast WHERE oid >= 16384
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_transform WHERE oid >= 16384
      UNION ALL
      SELECT 1 FROM pg_catalog.pg_am WHERE oid >= 16384
      UNION ALL
      SELECT 1
      FROM pg_catalog.pg_db_role_setting
      WHERE setdatabase = (
        SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
      )
    )
    SELECT 1 FROM unexpected_objects
  ) THEN
    RAISE EXCEPTION 'Refusing restore: target database is not empty or its public schema metadata is not pristine';
  END IF;
END
$electronic_mail_restore_preflight$;
SQL
)"

start_maintenance_session() {
  arm_handshake_watchdog 20
  (
    # This process must never keep the target FIFOs alive.
    exec 8>&- 9<&-
    export PGCONNECT_TIMEOUT=10
    export PGOPTIONS='-c statement_timeout=30000 -c lock_timeout=10000 -c idle_session_timeout=0'
    if [ -s "$maintenance_pgpass_file" ]; then
      export PGPASSFILE="$maintenance_pgpass_file"
    else
      unset PGPASSFILE
    fi
    exec psql --dbname="$sanitized_maintenance_database_url" \
      --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --quiet
  ) < "$maintenance_sql_fifo" > "$maintenance_output_fifo" &
  maintenance_psql_pid=$!
  exec 6> "$maintenance_sql_fifo"
  maintenance_sql_fd_open=1
  exec 7< "$maintenance_output_fifo"
  maintenance_output_fd_open=1
  maintenance_session_active=1
  disarm_handshake_watchdog
}

mkfifo "$target_sql_fifo" "$target_output_fifo" "$target_status_fifo" \
  "$maintenance_sql_fifo" "$maintenance_output_fifo"
arm_handshake_watchdog 20
(
  # The target starts first, but close any inherited descriptors that could
  # otherwise keep later maintenance FIFOs alive.
  exec 5<&- 6>&- 7<&-
  export PGCONNECT_TIMEOUT=10
  export PGOPTIONS='-c statement_timeout=30000 -c lock_timeout=10000'
  if [ -s "$pgpass_file" ]; then
    export PGPASSFILE="$pgpass_file"
  else
    unset PGPASSFILE
  fi
  exec psql --dbname="$sanitized_database_url" \
    --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --quiet
) < "$target_sql_fifo" > "$target_output_fifo" &
target_psql_pid=$!
exec 8> "$target_sql_fifo"
target_sql_fd_open=1
exec 9< "$target_output_fifo"
target_output_fd_open=1

printf "SET application_name = '%s';\n" "$restore_application_name" >&8
printf "SELECT CASE WHEN pg_catalog.pg_try_advisory_lock(1162691924, 1296126017) THEN 'TARGET_PID=' || pg_catalog.pg_backend_pid() ELSE 'RESTORE_LOCK_UNAVAILABLE' END;\n" >&8
if ! IFS= read -r target_backend_line <&9; then
  echo "Could not establish the isolated target restore session." >&2
  exit 1
fi
if [ "$target_backend_line" = "RESTORE_LOCK_UNAVAILABLE" ]; then
  echo "Another restore is already running on this target database." >&2
  exit 1
fi
target_backend_pid="${target_backend_line#TARGET_PID=}"
if [ "$target_backend_line" = "$target_backend_pid" ] || [[ ! "$target_backend_pid" =~ ^[1-9][0-9]*$ ]]; then
  echo "Could not validate the target restore backend identity." >&2
  exit 1
fi
disarm_handshake_watchdog

# Open the transaction before maintenance isolation so every failure after the
# cluster-wide lock has one unambiguous rollback path. Disable inherited
# transaction-idle limits before waiting on the persistent maintenance session.
arm_handshake_watchdog 20
printf "BEGIN;\n" >&8
printf "SET transaction_timeout=0;\nSET idle_in_transaction_session_timeout=0;\n" >&8
printf "SELECT 'TARGET_TRANSACTION_READY';\n" >&8
if ! IFS= read -r target_transaction_ready <&9 \
  || [ "$target_transaction_ready" != "TARGET_TRANSACTION_READY" ]; then
  echo "Could not begin the isolated target restore transaction." >&2
  exit 1
fi
disarm_handshake_watchdog

# The target name is constrained to [A-Za-z0-9_.-], so quoting it as an SQL
# identifier/literal here cannot introduce SQL. The PID and nonce are validated
# above. One persistent maintenance process proves the target identity before
# any mutation and cannot be silently routed to a different server later.
maintenance_identity_sql="SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_catalog.pg_stat_activity
  WHERE datname = '$actual_database_name'
    AND pid = $target_backend_pid
    AND backend_type = 'client backend'
    AND application_name = '$restore_application_name'
) THEN 'MAINTENANCE_IDENTITY_OK' ELSE 'MAINTENANCE_IDENTITY_MISMATCH' END;"
maintenance_termination_identity_sql="SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_catalog.pg_stat_activity
  WHERE datname = '$actual_database_name'
    AND pid = $target_backend_pid
    AND backend_type = 'client backend'
    AND application_name = '$restore_application_name'
) THEN 'MAINTENANCE_TERMINATION_IDENTITY_OK' ELSE 'MAINTENANCE_TERMINATION_IDENTITY_MISMATCH' END;"
disable_connections_sql="ALTER DATABASE \"$actual_database_name\" ALLOW_CONNECTIONS false;
SELECT 'MAINTENANCE_DISABLE_OK';"
terminate_other_backends_sql="SELECT CASE
  WHEN COALESCE(pg_catalog.bool_and(pg_catalog.pg_terminate_backend(pid, 5000)), true)
    THEN 'MAINTENANCE_TERMINATION_DONE'
    ELSE 'MAINTENANCE_TERMINATION_FAILED'
  END
FROM pg_catalog.pg_stat_activity
WHERE datname = '$actual_database_name'
  AND pid <> $target_backend_pid;"
confirm_target_isolation_sql="SELECT CASE WHEN NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_stat_activity
  WHERE datname = '$actual_database_name'
    AND pid <> $target_backend_pid
) AND EXISTS (
  SELECT 1 FROM pg_catalog.pg_stat_activity
  WHERE datname = '$actual_database_name'
    AND pid = $target_backend_pid
    AND backend_type = 'client backend'
    AND application_name = '$restore_application_name'
) THEN 'MAINTENANCE_ISOLATION_OK' ELSE 'MAINTENANCE_ISOLATION_FAILED' END;"

start_maintenance_session
if ! maintenance_round_trip "$maintenance_identity_sql" "MAINTENANCE_IDENTITY_OK"; then
  echo "Persistent maintenance session could not identify the nonce-bound target restore session; no target mutation was attempted." >&2
  exit 1
fi

target_connections_may_be_disabled=1
if ! maintenance_round_trip "$disable_connections_sql" "MAINTENANCE_DISABLE_OK"; then
  echo "Could not isolate the restore target; restore was not started." >&2
  exit 1
fi
if ! maintenance_round_trip \
  "$maintenance_termination_identity_sql" "MAINTENANCE_TERMINATION_IDENTITY_OK"; then
  echo "Could not revalidate the target identity before terminating other backends; restore was not started." >&2
  exit 1
fi
if ! maintenance_round_trip "$terminate_other_backends_sql" "MAINTENANCE_TERMINATION_DONE"; then
  echo "Could not terminate every other target backend; restore was not started." >&2
  exit 1
fi
if ! maintenance_round_trip "$confirm_target_isolation_sql" "MAINTENANCE_ISOLATION_OK"; then
  echo "Could not make the restore session the sole target backend; restore was not started." >&2
  exit 1
fi

arm_handshake_watchdog 45
printf '%s\n' "$restore_target_preflight_sql" >&8
printf "SET statement_timeout=0;\nSET lock_timeout=0;\nSET transaction_timeout=0;\nSET idle_in_transaction_session_timeout=0;\n" >&8
printf "SELECT 'RESTORE_PREFLIGHT_OK';\n" >&8
if ! IFS= read -r restore_preflight_result <&9 \
  || [ "$restore_preflight_result" != "RESTORE_PREFLIGHT_OK" ]; then
  echo "Could not prove the isolated restore target is pristine; restore was not started." >&2
  exit 1
fi
disarm_handshake_watchdog

arm_handshake_watchdog 20
(
  # The drain must not keep either SQL input FIFO (or the maintenance output)
  # alive. It forwards only random transaction-status sentinels to the parent.
  exec 5<&- 6>&- 7<&- 8>&-
  while IFS= read -r target_output_line <&9; do
    case "$target_output_line" in
      "$restore_precommit_marker" | "$restore_commit_marker")
        printf '%s\n' "$target_output_line"
        ;;
    esac
  done
) > "$target_status_fifo" &
target_output_drain_pid=$!
exec 9<&-
target_output_fd_open=0
exec 5< "$target_status_fifo"
target_status_fd_open=1
disarm_handshake_watchdog

restore_arguments=(
  --clean --if-exists --no-owner --no-acl
  --exit-on-error --file=-
)
(
  # The archive parser receives only the target SQL FIFO, never the live
  # maintenance control channel.
  exec 5<&- 6>&- 7<&-
  BACKUP_ENCRYPTION_KEY="$backup_encryption_key" \
    "$BACKUP_CRYPTO_PYTHON" "$ROOT_DIR/scripts/postgres-backup-crypto.py" \
      "${decrypt_arguments[@]}" | \
    pg_restore "${restore_arguments[@]}" >&8
)
unset backup_encryption_key
# Prove that the target psql executed every generated restore statement before
# COMMIT is even attempted. A target-side failure here is a known rollback and
# may safely use the still-bound maintenance session to re-enable connections.
if ! (
  trap '' PIPE
  printf "\nSELECT '%s';\n" "$restore_precommit_marker" >&8 2>/dev/null
); then
  echo "Atomic restore transaction failed before COMMIT; no restore changes were committed." >&2
  exit 1
fi
if ! IFS= read -r -t "$RESTORE_FINALIZE_TIMEOUT_SECONDS" restore_precommit_result <&5 \
  || [ "$restore_precommit_result" != "$restore_precommit_marker" ]; then
  echo "Atomic restore transaction failed before COMMIT; no restore changes were committed." >&2
  exit 1
fi
# This is the only path that can send COMMIT. From this point until the random
# acknowledgment is observed, cleanup treats the outcome as indeterminate and
# keeps traffic disabled instead of claiming rollback or retrying.
commit_may_have_been_sent=1
if ! (
  trap '' PIPE
  printf "COMMIT;\n" >&8 2>/dev/null \
    && printf "SELECT '%s';\n" "$restore_commit_marker" >&8 2>/dev/null
); then
  report_indeterminate_commit
  exit 1
fi
if ! IFS= read -r -t "$RESTORE_FINALIZE_TIMEOUT_SECONDS" restore_commit_result <&5 \
  || [ "$restore_commit_result" != "$restore_commit_marker" ]; then
  report_indeterminate_commit
  exit 1
fi
commit_confirmed=1
exec 8>&-
target_sql_fd_open=0
target_psql_status=0
wait "$target_psql_pid" || target_psql_status=$?
target_psql_pid=""
wait "$target_output_drain_pid" >/dev/null 2>&1 || true
target_output_drain_pid=""
exec 5<&-
target_status_fd_open=0
# The marker is emitted only by a statement after COMMIT in this same psql
# session, so it is authoritative even if the client exits oddly afterward.
: "$target_psql_status"

if ! reenable_target_connections; then
  report_critical_reenable_failure
  exit 1
fi
terminate_maintenance_psql

echo "Authenticated encrypted restore completed atomically; run migrations and launch verification before directing traffic."
